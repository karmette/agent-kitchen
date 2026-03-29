"""Generic OASIS simulation runner. All scenario config lives in the profile JSON.

Emits real-time JSONL events to stdout as agents send messages.
Human-readable progress goes to stderr.
"""

import asyncio
import json
import logging
import os
import sys

# Force all logging to stderr before importing noisy libraries
logging.basicConfig(stream=sys.stderr, level=logging.WARNING)

from camel.models import ModelFactory
from camel.prompts import TextPrompt
from camel.types import ModelPlatformType, ModelType

import oasis
from oasis import (ActionType, AgentGraph, LLMAction, SocialAgent, UserInfo)

from topology import build_topology
from events import emit, poll_messages

import dotenv
dotenv.load_dotenv(override=True)

DEFAULT_DB_PATH = "./db/database.db"


def log(msg: str):
    """Print to stderr (human-readable, not for Go TUI)."""
    print(msg, file=sys.stderr)


async def run_scenario(scenario_path: str, db_path: str = None, generation: int = 0):
    """Run a scenario from a JSON file.

    Args:
        scenario_path: Path to the scenario JSON file.
        db_path: Override the database path.
        generation: Generation number for event metadata.

    Returns:
        Tuple of (db_path, agents_spec)
    """
    db_path = db_path or DEFAULT_DB_PATH

    with open(scenario_path) as f:
        scenario = json.load(f)

    template = TextPrompt(scenario["template"])
    topology_config = scenario.get("topology", {"mode": "pairwise"})
    action_names = scenario.get("actions", ["SEND_TO_GROUP", "LISTEN_FROM_GROUP", "DO_NOTHING"])
    num_rounds = scenario.get("num_rounds", 5)

    available_actions = [ActionType[a] for a in action_names]

    agents_spec, groups_spec = build_topology(
        topology_config, scenario["negotiators"], scenario["counterparties"]
    )

    config = {"stream": False}
    model = ModelFactory.create(
        model_platform=ModelPlatformType.OPENAI,
        model_type=os.getenv("MODEL", ModelType.GPT_4O_MINI),
        model_config_dict=config,
    )

    agent_graph = AgentGraph()
    _real_stdout = sys.stdout
    sys.stdout = sys.stderr  # suppress CAMEL warnings from stdout
    for agent_id, profile, is_negotiator, source_idx in agents_spec:
        agent = SocialAgent(
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

    sys.stdout = _real_stdout

    # Set up environment
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    if os.path.exists(db_path):
        os.remove(db_path)

    # Suppress OASIS's stray print statements during setup
    _real_stdout = sys.stdout
    sys.stdout = sys.stderr
    env = oasis.make(
        agent_graph=agent_graph,
        platform=oasis.DefaultPlatformType.REDDIT,
        database_path=db_path,
    )
    sys.stdout = _real_stdout

    await env.reset()

    # Wire up groups
    emit({"type": "simulation_start", "generation": generation,
          "num_agents": len(agents_spec), "num_groups": len(groups_spec),
          "num_rounds": num_rounds})

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

    # Start message poller for real-time streaming
    stop_poller = asyncio.Event()
    poller_task = asyncio.create_task(
        poll_messages(db_path, agents_spec, stop_poller, generation)
    )

    # Simulation rounds
    for round_num in range(1, num_rounds + 1):
        emit({"type": "round_start", "generation": generation, "round": round_num,
              "total_rounds": num_rounds})
        log(f"\n  Round {round_num}/{num_rounds}")

        await env.step({
            agent: LLMAction()
            for _, agent in env.agent_graph.get_agents()
        })

        emit({"type": "round_complete", "generation": generation, "round": round_num})

    # Stop poller, give it one last poll cycle
    await asyncio.sleep(0.6)
    stop_poller.set()
    await poller_task

    await env.close()

    emit({"type": "simulation_complete", "generation": generation, "db_path": db_path})
    log(f"\n  Simulation complete — {db_path}")

    return db_path, agents_spec


if __name__ == "__main__":
    scenario_path = sys.argv[1] if len(sys.argv) > 1 else "./scenarios/demo_profiles.json"
    asyncio.run(run_scenario(scenario_path))
