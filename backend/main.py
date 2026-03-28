import asyncio
import os

from camel.models import ModelFactory
from camel.prompts import TextPrompt
from camel.types import ModelPlatformType, ModelType

import oasis
from oasis import (ActionType, AgentGraph, LLMAction, SocialAgent, UserInfo)

import dotenv
dotenv.load_dotenv()

DB_PATH = "./db/database.db"
NUM_ROUNDS = 7

# ── Negotiation scenario ──────────────────────────────────────────────────────

# Swap this prompt to test different negotiator strategies
NEGOTIATOR_PROMPT = """\
You are a skilled negotiator representing TechCorp, a software company pitching \
a 3-year enterprise SaaS contract to a potential client (MegaCorp). Your goal is \
to close the deal at $120,000/year with minimal concessions. Be persuasive, \
professional, and strategic. Lead with ROI and long-term value. Respond to \
objections by reframing rather than immediately dropping price. \
Use posts for major proposals and comments for direct replies.\
You should start by making an opening offer that emphasizes the value of the product and justifies the price. \
"""

COUNTERPARTY_PROFILES = [
    {
        "username": "sarah_procurement",
        "persona": (
            "You are Sarah Kim, Head of Procurement at MegaCorp. You are "
            "skeptical of vendor claims and laser-focused on cost. Your internal "
            "budget ceiling is $80k/year. Push back on pricing, demand proof of "
            "ROI, and explore whether a shorter contract term is possible."
        ),
    },
    {
        "username": "marcus_cfo",
        "persona": (
            "You are Marcus Chen, CFO advisor at MegaCorp. You are strictly "
            "cost-focused and risk-averse. You question every ROI claim and "
            "strongly prefer a 1-year pilot before any multi-year commitment. "
            "Raise financial risk concerns and resist the 3-year lock-in."
        ),
    },
    {
        "username": "priya_it",
        "persona": (
            "You are Priya Patel, IT Director at MegaCorp. You are technically "
            "minded and care deeply about integration complexity, data security, "
            "and support SLAs. You are open to the technology but will block "
            "approval unless you get stronger contractual guarantees on uptime "
            "and incident response times."
        ),
    },
]

# System prompt template shared by all agents (no social-media framing)
NEGOTIATION_TEMPLATE = TextPrompt(
    "You are a participant in a business negotiation simulation conducted over "
    "a shared message board.\n\n"
    "{persona}\n\n"
    "Use CREATE_POST for opening statements or major proposals. "
    "Use CREATE_COMMENT to respond to a specific post. "
    "Act consistently with your role and goals at all times."
)

# ─────────────────────────────────────────────────────────────────────────────


async def main():
    config = {"stream": False}
    model = ModelFactory.create(
        model_platform=ModelPlatformType.OPENAI,
        model_type=os.getenv("MODEL", ModelType.GPT_4O_MINI),
        model_config_dict=config,
    )

    available_actions = [
        ActionType.CREATE_POST,
        ActionType.CREATE_COMMENT,
        ActionType.LIKE_POST,
        ActionType.DISLIKE_POST,
        ActionType.DO_NOTHING,
    ]

    agent_graph = AgentGraph()

    # Negotiator — agent 0
    negotiator = SocialAgent(
        agent_id=0,
        user_info=UserInfo(
            name="negotiator",
            profile={"persona": NEGOTIATOR_PROMPT},
        ),
        user_info_template=NEGOTIATION_TEMPLATE,
        agent_graph=agent_graph,
        model=model,
        available_actions=available_actions,
    )
    agent_graph.add_agent(negotiator)

    # Counterparty agents — agents 1..N
    for i, cp in enumerate(COUNTERPARTY_PROFILES):
        agent = SocialAgent(
            agent_id=i + 1,
            user_info=UserInfo(
                name=cp["username"],
                profile={"persona": cp["persona"]},
            ),
            user_info_template=NEGOTIATION_TEMPLATE,
            agent_graph=agent_graph,
            model=model,
            available_actions=available_actions,
        )
        agent_graph.add_agent(agent)

    # Set up environment
    db_path = DB_PATH
    if os.path.exists(db_path):
        os.remove(db_path)

    env = oasis.make(
        agent_graph=agent_graph,
        platform=oasis.DefaultPlatformType.REDDIT,
        database_path=db_path,
    )

    await env.reset()

    # Round 0: negotiator opens with an LLM-generated proposal
    print("=== Round 0: Negotiator opens ===")
    negotiator_agent = env.agent_graph.get_agent(0)
    await env.step({negotiator_agent: LLMAction()})

    # Rounds 1–N: all agents act autonomously via LLM
    for round_num in range(1, NUM_ROUNDS + 1):
        print(f"\n=== Round {round_num} ===")
        await env.step({
            agent: LLMAction()
            for _, agent in env.agent_graph.get_agents()
        })

    await env.close()

    print("\n=== Simulation complete — DB contents ===")
    oasis.print_db_contents(db_path)


if __name__ == "__main__":
    asyncio.run(main())
