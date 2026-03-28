import asyncio
import json
import os

from camel.models import ModelFactory
from camel.prompts import TextPrompt
from camel.types import ModelPlatformType, ModelType

import oasis
from oasis import (ActionType, AgentGraph, LLMAction, SocialAgent, UserInfo)

import dotenv
dotenv.load_dotenv(override=True)

DB_PATH = "./db/database.db"
NUM_ROUNDS = 5
# PROFILES_PATH = "./demo_profiles.json"
PROFILES_PATH = "./interview_profiles.json"

# Shared system prompt template — no social-media framing
NEGOTIATION_TEMPLATE = TextPrompt(
    "You are a participant in a business negotiation conducted over a group "
    "messaging channel.\n\n"
    "{persona}\n\n"
    "Use send_to_group to send messages to the negotiation channel(s) you are "
    "in. Act consistently with your role and goals at all times."
)


async def main():
    with open(PROFILES_PATH) as f:
        profiles = json.load(f)

    negotiators = profiles["negotiators"]
    counterparties = profiles["counterparties"]

    config = {"stream": False}
    model = ModelFactory.create(
        model_platform=ModelPlatformType.OPENAI,
        model_type=os.getenv("MODEL", ModelType.GPT_4O_MINI),
        model_config_dict=config,
    )

    available_actions = [
        ActionType.SEND_TO_GROUP,
        ActionType.LISTEN_FROM_GROUP,
        ActionType.DO_NOTHING,
    ]

    n_cp = len(counterparties)

    # A×B pairs — each pair gets its own dedicated agent instances so groups
    # never bleed messages across conversations.
    # Agent ID layout: pair (i,j) → negotiator = (i*n_cp + j)*2
    #                               counterparty = (i*n_cp + j)*2 + 1
    agent_graph = AgentGraph()

    for i, neg_p in enumerate(negotiators):
        for j, cp_p in enumerate(counterparties):
            pair_idx = i * n_cp + j
            neg_agent = SocialAgent(
                agent_id=pair_idx * 2,
                user_info=UserInfo(
                    user_name=f"{neg_p['username']}_{j}",
                    name=neg_p["name"],
                    description=neg_p["bio"],
                    profile={"persona": neg_p["persona"]},
                ),
                user_info_template=NEGOTIATION_TEMPLATE,
                agent_graph=agent_graph,
                model=model,
                available_actions=available_actions,
            )
            agent_graph.add_agent(neg_agent)

            cp_agent = SocialAgent(
                agent_id=pair_idx * 2 + 1,
                user_info=UserInfo(
                    user_name=f"{cp_p['username']}_{i}",
                    name=cp_p["name"],
                    description=cp_p["bio"],
                    profile={"persona": cp_p["persona"]},
                ),
                user_info_template=NEGOTIATION_TEMPLATE,
                agent_graph=agent_graph,
                model=model,
                available_actions=available_actions,
            )
            agent_graph.add_agent(cp_agent)

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

    # Bootstrap: one group per (negotiator, counterparty) pair
    # TODO: replace groups with a proper conversation channel when available
    print("=== Setting up conversation rooms ===")
    for i, neg_p in enumerate(negotiators):
        for j, cp_p in enumerate(counterparties):
            pair_idx = i * n_cp + j
            neg_agent = env.agent_graph.get_agent(pair_idx * 2)
            cp_agent = env.agent_graph.get_agent(pair_idx * 2 + 1)

            result = await neg_agent.perform_action_by_data(
                ActionType.CREATE_GROUP,
                group_name=f"{neg_p['username']}_x_{cp_p['username']}",
            )
            group_id = result["group_id"]
            await cp_agent.perform_action_by_data(
                ActionType.JOIN_GROUP,
                group_id=group_id,
            )
            print(f"  Group {group_id}: {neg_p['username']} ↔ {cp_p['username']}")

    # Simulation rounds — all agents act via LLM each round
    for round_num in range(1, NUM_ROUNDS + 1):
        print(f"\n=== Round {round_num} ===")
        await env.step({
            agent: LLMAction()
            for _, agent in env.agent_graph.get_agents()
        })

    await env.close()

    print("\n=== Simulation complete — DB contents ===")
    oasis.print_db_contents(DB_PATH)


if __name__ == "__main__":
    asyncio.run(main())
