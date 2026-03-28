import asyncio
import json
import os

from camel.models import ModelFactory
from camel.prompts import TextPrompt
from camel.types import ModelPlatformType, ModelType

import oasis
from oasis import (ActionType, AgentGraph, LLMAction, SocialAgent, UserInfo)

import dotenv
dotenv.load_dotenv()

DB_PATH = "./db/database.db"
NUM_ROUNDS = 5
PROFILES_PATH = "./demo_profiles.json"

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
    n_neg = len(negotiators)

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

    agent_graph = AgentGraph()

    # Negotiators: agent IDs 0..n_neg-1
    for i, p in enumerate(negotiators):
        agent = SocialAgent(
            agent_id=i,
            user_info=UserInfo(
                user_name=p["username"],
                name=p["name"],
                description=p["bio"],
                profile={"persona": p["persona"]},
            ),
            user_info_template=NEGOTIATION_TEMPLATE,
            agent_graph=agent_graph,
            model=model,
            available_actions=available_actions,
        )
        agent_graph.add_agent(agent)

    # Counterparties: agent IDs n_neg..total-1
    for i, p in enumerate(counterparties):
        agent = SocialAgent(
            agent_id=n_neg + i,
            user_info=UserInfo(
                user_name=p["username"],
                name=p["name"],
                description=p["bio"],
                profile={"persona": p["persona"]},
            ),
            user_info_template=NEGOTIATION_TEMPLATE,
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

    # Bootstrap: each negotiator creates a room, all counterparties join it
    # TODO: replace groups with a proper conversation channel when available
    print("=== Setting up negotiation rooms ===")
    for i, neg_profile in enumerate(negotiators):
        neg_agent = env.agent_graph.get_agent(i)
        result = await neg_agent.perform_action_by_data(
            ActionType.CREATE_GROUP,
            group_name=f"negotiation_{neg_profile['username']}",
        )
        group_id = result["group_id"]
        print(f"  [{neg_profile['username']}] created group {group_id}")

        for j in range(len(counterparties)):
            cp_agent = env.agent_graph.get_agent(n_neg + j)
            await cp_agent.perform_action_by_data(
                ActionType.JOIN_GROUP,
                group_id=group_id,
            )
        print(f"  All counterparties joined group {group_id}")

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
