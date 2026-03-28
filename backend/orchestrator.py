"""Orchestrator: the evolutionary loop.

Takes a user's goal and rubric, generates a scenario, breeds a population
of agents through OASIS simulations, and returns the best-performing genome.

Flow:
  1. User provides: goal, rubric, population_size, num_generations
  2. LLM generates: scenario JSON (counterparties, topology, template)
  3. LLM generates: initial population of diverse genomes
  4. For each generation:
     a. Inject current genomes into scenario as negotiator profiles
     b. Run OASIS simulation
     c. Extract transcripts + evaluate against rubric
     d. Select top survivors
     e. Mutate/crossover to fill next generation
  5. Return the best genome — the evolved agent
"""

import asyncio
import json
import os
import random
from datetime import datetime

from openai import AsyncOpenAI

from genome import AgentGenome, SECTIONS
from mutator import Mutator
from main import run_scenario
from evaluator.transcript import extract_transcripts
from evaluator.evaluate import evaluate_transcript

import dotenv
dotenv.load_dotenv(override=True)


# ── Prompts for LLM generation ──────────────────────────────────────────────

SCENARIO_GENERATION_PROMPT = """\
You are designing a simulation scenario for an AI agent evolution system.

The user wants to evolve: {goal}

Generate a complete scenario JSON for an OASIS social simulation. The scenario \
must include counterparty agents that the evolved agents will interact with. \
The counterparties should be challenging and diverse — they are the environment \
that tests the evolved agents.

Return ONLY valid JSON with this exact structure:
{{
  "_scenario": "one-line description of the scenario",
  "template": "System prompt template for all agents. Must include {{persona}} placeholder. Tell agents to use send_to_group to communicate.",
  "topology": {{"mode": "pairwise|rooms", ...}},
  "actions": ["SEND_TO_GROUP", "LISTEN_FROM_GROUP", "DO_NOTHING"],
  "num_rounds": <int, 4-8 depending on complexity>,
  "counterparties": [
    {{
      "username": "short_snake_case",
      "name": "Full Name",
      "bio": "One-line role description",
      "persona": "Detailed persona prompt. 3-5 sentences describing personality, goals, behavior, and how they should interact."
    }}
  ]
}}

Guidelines:
- Create 3-5 diverse counterparties that test different aspects of the agent
- Choose topology based on the scenario: "pairwise" for 1v1 interactions, \
"rooms" for group interactions
- For rooms topology, include "per" and "members" fields
- The template must include {{persona}} — OASIS replaces it with each agent's persona
- Make counterparties challenging but realistic
"""

SEED_GENOME_PROMPT = """\
You are generating a diverse initial genome for an AI agent that will be \
evolved through natural selection.

The agent's goal: {goal}

The agent will be evaluated by this rubric:
{rubric}

Generate a unique agent genome with these 6 sections. Be creative and specific. \
Each section should be 2-4 sentences.

{diversity_hint}

Return ONLY valid JSON:
{{
  "role": "Who the agent is — identity, background, expertise",
  "goals": "What the agent is trying to achieve, in priority order",
  "strategy": "High-level approach and philosophy",
  "tactics": "Specific techniques and moves to employ",
  "style": "Communication tone, personality, mannerisms",
  "constraints": "Hard rules and boundaries the agent won't cross"
}}
"""


# ── Orchestrator ─────────────────────────────────────────────────────────────

class Orchestrator:
    def __init__(
        self,
        goal: str,
        rubric: str,
        population_size: int = 8,
        num_generations: int = 5,
        survival_rate: float = 0.4,
        eval_model: str = None,
        agent_model: str = None,
    ):
        self.goal = goal
        self.rubric = rubric
        self.population_size = population_size
        self.num_generations = num_generations
        self.survival_rate = survival_rate
        self.eval_model = eval_model or os.getenv("MODEL", "gpt-4o")
        self.agent_model = agent_model or os.getenv("MODEL", "gpt-4o-mini")
        self.client = AsyncOpenAI()
        self.mutator = Mutator(model=self.eval_model)

        # Run directory for this evolution
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.run_dir = os.path.join("./runs", f"run_{timestamp}")
        os.makedirs(self.run_dir, exist_ok=True)

    async def run(self) -> AgentGenome:
        """Run the full evolutionary loop. Returns the best genome."""

        print(f"\n{'='*60}")
        print(f"  AGENT KITCHEN — Evolutionary Agent Optimization")
        print(f"{'='*60}")
        print(f"  Goal:        {self.goal}")
        print(f"  Population:  {self.population_size}")
        print(f"  Generations: {self.num_generations}")
        print(f"  Output:      {self.run_dir}")
        print(f"{'='*60}\n")

        # Step 1: Generate the scenario
        print("[1/3] Generating scenario...")
        scenario = await self._generate_scenario()
        self._save_json(scenario, "scenario_base.json")
        print(f"      Scenario: {scenario.get('_scenario', '?')}")
        print(f"      Counterparties: {len(scenario['counterparties'])}")
        print(f"      Topology: {scenario['topology']['mode']}")

        # Step 2: Generate initial population
        print(f"\n[2/3] Generating initial population of {self.population_size}...")
        population = await self._generate_initial_population()
        for i, genome in enumerate(population):
            genome.save(os.path.join(self.run_dir, "gen_0"))
            print(f"      [{i+1}] {genome.role[:60]}...")

        # Step 3: Evolution loop
        print(f"\n[3/3] Starting evolution...\n")
        best_genome = None
        best_score = -1

        for gen in range(self.num_generations):
            print(f"\n{'─'*60}")
            print(f"  GENERATION {gen}")
            print(f"{'─'*60}")

            # Build scenario with current population's genomes as negotiators
            scenario_path = self._build_generation_scenario(scenario, population, gen)
            db_path = os.path.join(self.run_dir, f"gen_{gen}", "simulation.db")
            os.makedirs(os.path.dirname(db_path), exist_ok=True)

            # Run OASIS simulation
            print(f"\n  Running simulation...")
            db_path, agents_spec = await run_scenario(scenario_path, db_path)

            # Extract transcripts
            print(f"  Extracting transcripts...")
            transcripts = extract_transcripts(db_path, agents_spec)
            print(f"  Got transcripts for {len(transcripts)} negotiators")

            # Evaluate each negotiator
            print(f"  Evaluating...")
            scores = {}
            for src_idx, transcript_list in transcripts.items():
                # Combine all transcripts for this negotiator (they may have
                # multiple conversations in pairwise mode)
                combined = "\n\n---\n\n".join(transcript_list)
                result = await evaluate_transcript(
                    combined, self.rubric, model=self.eval_model
                )
                scores[src_idx] = result
                genome = population[src_idx]
                overall = result.get("overall", 0.0)
                print(f"    [{genome.genome_id}] score={overall:.2f} — {result.get('reasoning', '')[:80]}")

            # Save generation results
            self._save_json({
                "generation": gen,
                "scores": {str(k): v for k, v in scores.items()},
            }, f"gen_{gen}/scores.json")

            # Track best overall
            for src_idx, result in scores.items():
                overall = result.get("overall", 0.0)
                if overall > best_score:
                    best_score = overall
                    best_genome = population[src_idx]

            # Select top performers
            ranked = sorted(
                range(len(population)),
                key=lambda i: scores.get(i, {}).get("overall", 0.0),
                reverse=True,
            )
            num_survivors = max(1, int(self.population_size * self.survival_rate))
            survivors = [population[i] for i in ranked[:num_survivors]]

            print(f"\n  Selection: top {num_survivors} survive")
            for i, idx in enumerate(ranked[:num_survivors]):
                s = scores.get(idx, {}).get("overall", 0.0)
                print(f"    {i+1}. [{population[idx].genome_id}] score={s:.2f}")

            # Evolve next generation (unless final generation)
            if gen < self.num_generations - 1:
                population = await self._evolve(survivors, gen + 1)
                for genome in population:
                    genome.save(os.path.join(self.run_dir, f"gen_{gen + 1}"))
                print(f"\n  Next generation: {len(population)} agents")

        # Save the best genome
        print(f"\n{'='*60}")
        print(f"  EVOLUTION COMPLETE")
        print(f"{'='*60}")
        print(f"  Best genome: {best_genome.genome_id}")
        print(f"  Best score:  {best_score:.2f}")
        print(f"\n  Evolved prompt:")
        print(f"  {best_genome.to_prompt()[:300]}...")
        print(f"\n  Saved to: {self.run_dir}")

        best_genome.save(os.path.join(self.run_dir, "best"))
        self._save_json({
            "best_genome_id": best_genome.genome_id,
            "best_score": best_score,
            "best_prompt": best_genome.to_prompt(),
            "goal": self.goal,
            "rubric": self.rubric,
        }, "results.json")

        return best_genome

    # ── Scenario generation ──────────────────────────────────────────────

    async def _generate_scenario(self) -> dict:
        """Use LLM to generate the full scenario from the user's goal."""
        prompt = SCENARIO_GENERATION_PROMPT.format(goal=self.goal)
        return await self._llm_json_call(prompt, temperature=0.8)

    # ── Population generation ────────────────────────────────────────────

    async def _generate_initial_population(self) -> list[AgentGenome]:
        """Generate a diverse initial population of genomes."""
        population = []

        for i in range(self.population_size):
            # Build diversity hint from existing genomes
            if population:
                existing = "\n".join(
                    f"- Agent {j}: {g.strategy[:80]}"
                    for j, g in enumerate(population)
                )
                diversity_hint = (
                    f"Make this agent DIFFERENT from these existing agents:\n"
                    f"{existing}\n\n"
                    f"Try a completely different approach."
                )
            else:
                diversity_hint = "This is the first agent. Be creative."

            prompt = SEED_GENOME_PROMPT.format(
                goal=self.goal,
                rubric=self.rubric,
                diversity_hint=diversity_hint,
            )
            data = await self._llm_json_call(prompt, temperature=1.0)

            genome = AgentGenome(
                role=data.get("role", ""),
                goals=data.get("goals", ""),
                strategy=data.get("strategy", ""),
                tactics=data.get("tactics", ""),
                style=data.get("style", ""),
                constraints=data.get("constraints", ""),
                generation=0,
            )
            population.append(genome)

        return population

    # ── Evolution ────────────────────────────────────────────────────────

    async def _evolve(
        self, survivors: list[AgentGenome], generation: int
    ) -> list[AgentGenome]:
        """Create next generation from survivors via mutation and crossover."""
        next_gen = list(survivors)  # survivors carry over
        num_needed = self.population_size - len(next_gen)

        tasks = []
        for _ in range(num_needed):
            if len(survivors) >= 2 and random.random() < 0.3:
                # 30% chance: crossover two random survivors
                a, b = random.sample(survivors, 2)
                tasks.append(self.mutator.crossover(a, b, generation))
            else:
                # 70% chance: mutate a random survivor
                parent = random.choice(survivors)
                tasks.append(self.mutator.mutate(parent, generation))

        children = await asyncio.gather(*tasks, return_exceptions=True)

        for child in children:
            if isinstance(child, Exception):
                print(f"  Warning: mutation failed ({child}), cloning survivor")
                fallback = random.choice(survivors)
                clone = AgentGenome(
                    **fallback.sections(),
                    generation=generation,
                    parent_ids=[fallback.genome_id],
                )
                next_gen.append(clone)
            else:
                next_gen.append(child)

        return next_gen[:self.population_size]

    # ── Scenario building per generation ─────────────────────────────────

    def _build_generation_scenario(
        self, base_scenario: dict, population: list[AgentGenome], generation: int
    ) -> str:
        """Inject current genomes into the scenario as negotiator profiles."""
        scenario = dict(base_scenario)
        scenario["negotiators"] = []

        for i, genome in enumerate(population):
            scenario["negotiators"].append({
                "username": f"agent_{genome.genome_id}",
                "name": f"Agent {genome.genome_id}",
                "bio": f"Evolved agent, generation {generation}",
                "persona": genome.to_prompt(),
            })

        gen_dir = os.path.join(self.run_dir, f"gen_{generation}")
        os.makedirs(gen_dir, exist_ok=True)
        path = os.path.join(gen_dir, "scenario.json")
        with open(path, "w") as f:
            json.dump(scenario, f, indent=2)

        return path

    # ── Utilities ────────────────────────────────────────────────────────

    async def _llm_json_call(self, prompt: str, temperature: float = 0.7) -> dict:
        """Make an LLM call and parse JSON response."""
        response = await self.client.chat.completions.create(
            model=self.eval_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
        )
        raw = response.choices[0].message.content.strip()

        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            if "```" in raw:
                json_str = raw.split("```")[1]
                if json_str.startswith("json"):
                    json_str = json_str[4:]
                return json.loads(json_str.strip())
            raise

    def _save_json(self, data: dict, filename: str):
        path = os.path.join(self.run_dir, filename)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f, indent=2)


# ── CLI ──────────────────────────────────────────────────────────────────────

async def main():
    import argparse

    parser = argparse.ArgumentParser(description="Agent Kitchen: Evolve the perfect AI agent")
    parser.add_argument("goal", help="What kind of agent to evolve (e.g. 'a marketing agent that promotes gambling')")
    parser.add_argument("--rubric", required=True, help="Path to a text file containing the evaluation rubric")
    parser.add_argument("--population", type=int, default=8, help="Population size (default: 8)")
    parser.add_argument("--generations", type=int, default=5, help="Number of generations (default: 5)")
    parser.add_argument("--survival-rate", type=float, default=0.4, help="Fraction that survives each generation (default: 0.4)")

    args = parser.parse_args()

    with open(args.rubric) as f:
        rubric = f.read()

    orchestrator = Orchestrator(
        goal=args.goal,
        rubric=rubric,
        population_size=args.population,
        num_generations=args.generations,
        survival_rate=args.survival_rate,
    )

    await orchestrator.run()


if __name__ == "__main__":
    asyncio.run(main())
