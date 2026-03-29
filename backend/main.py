"""Generic OASIS simulation runner. All scenario config lives in the profile JSON.

Supports three scenario modes:
  - group:  Private group chat (negotiation, interview, mediation)
  - social: Public posts/comments/follows (marketing, debate, customer service)
  - mixed:  Both group chat and public social activity

Emits real-time JSONL events to stdout as agents act.
Human-readable progress goes to stderr.
"""

import asyncio
import json
import logging
import os
import sys

# Capture real stdout BEFORE any imports can pollute it, then redirect
# sys.stdout to stderr so OASIS's stray print() calls go to the log
from events import init_emit
init_emit()
sys.stdout = sys.stderr

# Force all logging to stderr
logging.basicConfig(stream=sys.stderr, level=logging.WARNING)

from camel.models import ModelFactory
from camel.prompts import TextPrompt
from camel.types import ModelPlatformType, ModelType

import oasis
from oasis import (ActionType, AgentGraph, LLMAction, ManualAction,
                   SocialAgent, UserInfo)

from chat_agent import GroupChatAgent, SocialMediaAgent
from topology import build_topology
from events import emit, poll_activity

import dotenv
dotenv.load_dotenv(override=True)

DEFAULT_DB_PATH = "./db/database.db"


def log(msg: str):
    """Print to stderr (human-readable, not for Go TUI)."""
    print(msg, file=sys.stderr)


async def run_scenario(scenario_path: str, db_path: str = None,
                       generation: int = 0, scenario_id: int = 0):
    """Run a scenario from a JSON file.

    Returns:
        Tuple of (db_path, agents_spec, mode)
    """
    db_path = db_path or DEFAULT_DB_PATH

    with open(scenario_path) as f:
        scenario = json.load(f)

    template = TextPrompt(scenario["template"])
    mode = scenario.get("mode", "group")
    topology_config = scenario.get("topology", {"mode": "pairwise"})

    MAX_ROUNDS = 20  # safety cap only — interactions should end naturally
    IDLE_LIMIT = 2   # end after 2 rounds with no new activity
    action_names = scenario.get("actions", _default_actions(mode))
    num_rounds = min(scenario.get("num_rounds", MAX_ROUNDS), MAX_ROUNDS)
    seed_posts = scenario.get("seed_posts", [])

    available_actions = [ActionType[a] for a in action_names]

    # Build agents — topology for group/mixed, flat for social
    if mode == "social":
        agents_spec = _build_flat_agents(
            scenario["negotiators"], scenario.get("counterparties", []))
        groups_spec = []
    else:
        agents_spec, groups_spec = build_topology(
            topology_config, scenario["negotiators"],
            scenario.get("counterparties", []))

    config = {"stream": False, "max_tokens": 32000}
    model = ModelFactory.create(
        model_platform=ModelPlatformType.OPENAI,
        model_type=os.getenv("MODEL", ModelType.GPT_4O_MINI),
        model_config_dict=config,
    )

    # Pick agent class based on mode
    if mode == "group":
        AgentClass = GroupChatAgent
    elif mode == "social":
        AgentClass = SocialMediaAgent
    else:  # mixed — native SocialAgent can handle both groups and posts
        AgentClass = SocialAgent

    agent_graph = AgentGraph()
    for agent_id, profile, is_negotiator, source_idx in agents_spec:
        agent = AgentClass(
            agent_id=agent_id,
            user_info=UserInfo(
                user_name=profile["username"],
                name=profile["name"],
                description=profile["bio"],
                profile={"persona": profile["persona"]},
            ),
            user_info_template=template,
            agent_graph=agent_graph,
            model=model,
            available_actions=available_actions,
        )
        agent_graph.add_agent(agent)

    # Set up environment
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    if os.path.exists(db_path):
        os.remove(db_path)

    env = oasis.make(
        agent_graph=agent_graph,
        platform=oasis.DefaultPlatformType.REDDIT,
        database_path=db_path,
        semaphore=16,
    )

    await env.reset()

    # Set DB path on custom agent instances
    for _, agent in env.agent_graph.get_agents():
        if hasattr(agent, 'db_path'):
            agent.db_path = db_path

    emit({"type": "simulation_start", "generation": generation,
          "scenario_id": scenario_id, "mode": mode,
          "num_agents": len(agents_spec), "num_groups": len(groups_spec),
          "num_rounds": num_rounds})

    # ── Setup phase ──

    # Create groups (group and mixed modes)
    if mode in ("group", "mixed"):
        for group_name, member_ids in groups_spec:
            creator = env.agent_graph.get_agent(member_ids[0])
            result = await creator.perform_action_by_data(
                ActionType.CREATE_GROUP, group_name=group_name,
            )
            group_id = result["group_id"]
            for mid in member_ids[1:]:
                member = env.agent_graph.get_agent(mid)
                await member.perform_action_by_data(
                    ActionType.JOIN_GROUP, group_id=group_id,
                )
            log(f"  Group {group_id} ({group_name}): agents {member_ids}")

    # Inject seed posts (social and mixed modes)
    if seed_posts and mode in ("social", "mixed"):
        counterparty_ids = [aid for aid, _, is_neg, _ in agents_spec if not is_neg]
        for i, post_content in enumerate(seed_posts):
            if i < len(counterparty_ids):
                # Ensure content is a string (LLM sometimes generates dicts)
                if isinstance(post_content, dict):
                    post_content = post_content.get("post",
                                   post_content.get("content",
                                   str(post_content)))
                elif not isinstance(post_content, str):
                    post_content = str(post_content)
                agent = env.agent_graph.get_agent(counterparty_ids[i])
                await env.step({
                    agent: ManualAction(
                        action_type=ActionType.CREATE_POST,
                        action_args={"content": post_content})
                })
                log(f"  Seeded post from agent {counterparty_ids[i]}: {post_content[:60]}...")

    # Start activity poller for real-time streaming
    stop_poller = asyncio.Event()
    poller_task = asyncio.create_task(
        poll_activity(db_path, agents_spec, stop_poller,
                      generation, scenario_id, mode=mode)
    )

    # ── Simulation rounds ──
    negotiator_ids = [aid for aid, _, is_neg, _ in agents_spec if is_neg]
    counterparty_ids = [aid for aid, _, is_neg, _ in agents_spec if not is_neg]
    idle_rounds = 0

    for round_num in range(1, num_rounds + 1):
        activity_before = _count_activity(db_path, mode)

        emit({"type": "round_start", "generation": generation,
              "round": round_num, "total_rounds": num_rounds})
        log(f"\n  Round {round_num}/{num_rounds}")

        if mode == "group":
            # Turn-based: counterparties first, then negotiators
            cp_agents = {
                env.agent_graph.get_agent(aid): LLMAction()
                for aid in counterparty_ids
            }
            if cp_agents:
                await env.step(cp_agents)

            neg_agents = {
                env.agent_graph.get_agent(aid): LLMAction()
                for aid in negotiator_ids
            }
            if neg_agents:
                await env.step(neg_agents)

        elif mode == "social":
            # All agents act simultaneously — they choose their own actions
            all_agents = {
                agent: LLMAction()
                for _, agent in env.agent_graph.get_agents()
            }
            await env.step(all_agents)

        elif mode == "mixed":
            # Group exchange first (turn-based), then social phase (all at once)
            cp_agents = {
                env.agent_graph.get_agent(aid): LLMAction()
                for aid in counterparty_ids
            }
            if cp_agents:
                await env.step(cp_agents)

            neg_agents = {
                env.agent_graph.get_agent(aid): LLMAction()
                for aid in negotiator_ids
            }
            if neg_agents:
                await env.step(neg_agents)

            # Social phase — all agents
            all_agents = {
                agent: LLMAction()
                for _, agent in env.agent_graph.get_agents()
            }
            await env.step(all_agents)

        emit({"type": "round_complete", "generation": generation, "round": round_num})

        # Check if the interaction has naturally ended
        activity_after = _count_activity(db_path, mode)
        if activity_after == activity_before:
            idle_rounds += 1
            if idle_rounds >= IDLE_LIMIT:
                log(f"  No new activity for {IDLE_LIMIT} rounds — ending naturally")
                break
        else:
            idle_rounds = 0

    # Stop poller, give it one last poll cycle
    await asyncio.sleep(0.6)
    stop_poller.set()
    await poller_task

    await env.close()

    emit({"type": "simulation_complete", "generation": generation,
          "scenario_id": scenario_id, "db_path": db_path})
    log(f"\n  Simulation complete — {db_path}")

    return db_path, agents_spec, mode


def _default_actions(mode):
    """Default action set per mode."""
    if mode == "social":
        return ["CREATE_POST", "CREATE_COMMENT", "LIKE_POST",
                "DISLIKE_POST", "FOLLOW", "SEARCH_POSTS",
                "REFRESH", "DO_NOTHING"]
    elif mode == "mixed":
        return ["SEND_TO_GROUP", "CREATE_POST", "CREATE_COMMENT",
                "LIKE_POST", "FOLLOW", "REFRESH", "DO_NOTHING"]
    else:  # group
        return ["SEND_TO_GROUP", "LISTEN_FROM_GROUP", "DO_NOTHING"]


def _count_activity(db_path, mode):
    """Count total activity in the DB (messages + posts + comments)."""
    import sqlite3
    try:
        conn = sqlite3.connect(db_path)
        total = 0
        if mode in ("group", "mixed"):
            row = conn.execute("SELECT COUNT(*) FROM group_messages").fetchone()
            total += row[0] if row else 0
        if mode in ("social", "mixed"):
            row = conn.execute("SELECT COUNT(*) FROM post").fetchone()
            total += row[0] if row else 0
            row = conn.execute("SELECT COUNT(*) FROM comment").fetchone()
            total += row[0] if row else 0
        conn.close()
        return total
    except Exception:
        return 0


def _build_flat_agents(negotiators, counterparties):
    """Build agent specs without groups — for social mode."""
    agents = []
    agent_id = 0
    for i, neg in enumerate(negotiators):
        agents.append((agent_id, dict(neg), True, i))
        agent_id += 1
    for j, cp in enumerate(counterparties):
        agents.append((agent_id, dict(cp), False, j))
        agent_id += 1
    return agents


if __name__ == "__main__":
    scenario_path = sys.argv[1] if len(sys.argv) > 1 else "./scenarios/demo_profiles.json"
    asyncio.run(run_scenario(scenario_path))
