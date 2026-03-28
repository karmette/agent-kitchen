"""Generic OASIS simulation runner. All scenario config lives in the profile JSON."""

import asyncio
import json
import os
import sys

from camel.models import ModelFactory
from camel.prompts import TextPrompt
from camel.types import ModelPlatformType, ModelType

import oasis
from oasis import (ActionType, AgentGraph, LLMAction, SocialAgent, UserInfo)

from topology import build_topology

import dotenv
dotenv.load_dotenv(override=True)

DB_PATH = "./db/database.db"


async def run_scenario(scenario_path: str):
    with open(scenario_path) as f:
        scenario = json.load(f)

    # Read all config from the scenario JSON
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

    # Set up environment
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)

    env = oasis.make(
        agent_graph=agent_graph,
        platform=oasis.DefaultPlatformType.REDDIT,
        database_path=DB_PATH,
    )

    await env.reset()

    # Wire up groups
    print(f"=== {scenario.get('_scenario', scenario_path)} ===")
    print(f"    {len(agents_spec)} agents, {len(groups_spec)} groups, {num_rounds} rounds")
    print()
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
        print(f"  Group {group_id} ({group_name}): agents {member_ids}")

    # Simulation rounds
    for round_num in range(1, num_rounds + 1):
        print(f"\n=== Round {round_num}/{num_rounds} ===")
        await env.step({
            agent: LLMAction()
            for _, agent in env.agent_graph.get_agents()
        })

    await env.close()
    print(f"\n=== Done — results in {DB_PATH} ===")


if __name__ == "__main__":
    scenario_path = sys.argv[1] if len(sys.argv) > 1 else "./demo_profiles.json"
    asyncio.run(run_scenario(scenario_path))
