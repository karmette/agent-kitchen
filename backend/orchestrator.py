"""Orchestrator: the evolutionary loop.

Evolves the best AI agent for any social interaction scenario through
natural selection inside OASIS simulations.

Features:
  - LLM-generated scenarios and seed populations
  - Multi-eval scoring for reliable fitness signals
  - Elitism: all-time best genome always survives
  - Diversity tracking: detects convergence, injects fresh genomes
  - Scenario validation: retries invalid LLM-generated configs
  - Checkpointing: crashed runs can resume from last completed generation
"""

import asyncio
import json
import logging
import os
import random
import sys
from datetime import datetime
from difflib import SequenceMatcher

from genome import AgentGenome
from mutator import Mutator
from main import run_scenario
from evaluator.evaluate import evaluate_generation
from llm import create_client, get_model, complete_json
from events import emit

logger = logging.getLogger(__name__)


def log(msg: str):
    """Print to stderr (human-readable, not for Go TUI)."""
    print(msg, file=sys.stderr)

VALID_TOPOLOGY_MODES = {"pairwise", "rooms", "custom"}
VALID_PER_VALUES = {"negotiator", "counterparty"}
VALID_MEMBER_TYPES = {"negotiator", "counterparty", "all_negotiators", "all_counterparties"}
DIVERSITY_THRESHOLD = 0.3  # below this, population is too similar
SCENARIO_GEN_RETRIES = 3


# ── Prompts ──────────────────────────────────────────────────────────────────

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
  "topology": {{"mode": "pairwise"}},
  "actions": ["SEND_TO_GROUP", "LISTEN_FROM_GROUP", "DO_NOTHING"],
  "num_rounds": <int, 4-8 depending on complexity>,
  "counterparties": [
    {{
      "username": "short_snake_case",
      "name": "Full Name",
      "bio": "One-line role description",
      "persona": "Detailed persona prompt. 3-5 sentences."
    }}
  ]
}}

Topology options (choose ONE):
- 1v1 conversations: {{"mode": "pairwise"}}
- Group with all counterparties: {{"mode": "rooms", "per": "negotiator", "members": ["negotiator", "all_counterparties"]}}
- Single room everyone shares: {{"mode": "rooms", "per": "counterparty", "members": ["counterparty", "all_negotiators"]}}

The "per" field MUST be the string "negotiator" or "counterparty".
The "members" field MUST be a list of strings from: "negotiator", "counterparty", "all_negotiators", "all_counterparties".

Create 3-5 diverse counterparties. Make them challenging but realistic.
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


# ── Validation ───────────────────────────────────────────────────────────────

def validate_scenario(scenario: dict) -> list[str]:
    """Validate a generated scenario. Returns list of error strings (empty = valid)."""
    errors = []

    if "template" not in scenario:
        errors.append("Missing 'template' field")
    elif "{persona}" not in scenario.get("template", ""):
        errors.append("Template must contain {persona} placeholder")

    if "counterparties" not in scenario:
        errors.append("Missing 'counterparties' field")
    elif not isinstance(scenario["counterparties"], list) or len(scenario["counterparties"]) == 0:
        errors.append("'counterparties' must be a non-empty list")
    else:
        for i, cp in enumerate(scenario["counterparties"]):
            for field in ("username", "name", "bio", "persona"):
                if field not in cp or not isinstance(cp[field], str):
                    errors.append(f"Counterparty {i} missing or invalid '{field}'")

    topology = scenario.get("topology", {})
    mode = topology.get("mode")
    if mode not in VALID_TOPOLOGY_MODES:
        errors.append(f"Invalid topology mode: {mode}. Must be one of {VALID_TOPOLOGY_MODES}")

    if mode == "rooms":
        per = topology.get("per")
        if per not in VALID_PER_VALUES:
            errors.append(f"Invalid topology 'per': {per}. Must be one of {VALID_PER_VALUES}")
        members = topology.get("members", [])
        if not isinstance(members, list):
            errors.append(f"Topology 'members' must be a list, got {type(members)}")
        else:
            for m in members:
                if m not in VALID_MEMBER_TYPES:
                    errors.append(f"Invalid member type: {m}. Must be one of {VALID_MEMBER_TYPES}")

    if not isinstance(scenario.get("num_rounds", 0), int) or scenario.get("num_rounds", 0) < 1:
        errors.append("'num_rounds' must be a positive integer")

    return errors


# ── Diversity ────────────────────────────────────────────────────────────────

def population_diversity(population: list[AgentGenome]) -> float:
    """Measure population diversity as average pairwise dissimilarity (0-1).

    0 = all identical, 1 = all completely different.
    """
    if len(population) < 2:
        return 1.0

    prompts = [g.to_prompt() for g in population]
    similarities = []
    for i in range(len(prompts)):
        for j in range(i + 1, len(prompts)):
            ratio = SequenceMatcher(None, prompts[i], prompts[j]).quick_ratio()
            similarities.append(ratio)

    avg_similarity = sum(similarities) / len(similarities)
    return round(1.0 - avg_similarity, 3)


# ── Checkpointing ────────────────────────────────────────────────────────────

def save_checkpoint(run_dir: str, generation: int, population: list[AgentGenome],
                    best_genome: AgentGenome, best_score: float, scenario: dict):
    """Save checkpoint so a crashed run can resume."""
    checkpoint = {
        "generation": generation,
        "best_genome_id": best_genome.genome_id if best_genome else None,
        "best_score": best_score,
        "population": [g.to_dict() for g in population],
        "scenario": scenario,
    }
    path = os.path.join(run_dir, "checkpoint.json")
    with open(path, "w") as f:
        json.dump(checkpoint, f, indent=2)


def load_checkpoint(run_dir: str):
    """Load checkpoint if it exists. Returns (generation, population, best_genome, best_score, scenario) or None."""
    path = os.path.join(run_dir, "checkpoint.json")
    if not os.path.exists(path):
        return None
    with open(path) as f:
        data = json.load(f)
    population = [AgentGenome.from_dict(g) for g in data["population"]]
    best_genome = None
    if data["best_genome_id"]:
        for g in population:
            if g.genome_id == data["best_genome_id"]:
                best_genome = g
                break
    return data["generation"], population, best_genome, data["best_score"], data["scenario"]


# ── Orchestrator ─────────────────────────────────────────────────────────────

class Orchestrator:
    def __init__(
        self,
        goal: str,
        rubric: str,
        population_size: int = 8,
        num_generations: int = 5,
        survival_rate: float = 0.4,
        model: str = None,
        run_dir: str = None,
    ):
        self.goal = goal
        self.rubric = rubric
        self.population_size = population_size
        self.num_generations = num_generations
        self.survival_rate = survival_rate
        self.model = model or get_model()
        self.client = create_client()
        self.mutator = Mutator(model=self.model)

        if run_dir:
            self.run_dir = run_dir
        else:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            self.run_dir = os.path.join("./runs", f"run_{timestamp}")
        os.makedirs(self.run_dir, exist_ok=True)

    async def run(self) -> AgentGenome:
        """Run the full evolutionary loop. Returns the best genome."""
        logger.info(f"Starting evolution: goal='{self.goal}', pop={self.population_size}, gens={self.num_generations}")

        emit({"type": "evolution_start", "goal": self.goal, "population_size": self.population_size,
              "num_generations": self.num_generations, "run_dir": self.run_dir})

        log(f"\n{'='*60}")
        log(f"  AGENT KITCHEN — Evolutionary Agent Optimization")
        log(f"{'='*60}")
        log(f"  Goal:        {self.goal}")
        log(f"  Population:  {self.population_size}")
        log(f"  Generations: {self.num_generations}")
        log(f"  Output:      {self.run_dir}")
        log(f"{'='*60}\n")

        # Check for existing checkpoint
        checkpoint = load_checkpoint(self.run_dir)
        if checkpoint:
            start_gen, population, best_genome, best_score, scenario = checkpoint
            start_gen += 1
            log(f"  Resuming from generation {start_gen} (best={best_score:.2f})\n")
        else:
            start_gen = 0
            best_genome = None
            best_score = -1

            # Step 1: Generate and validate scenario
            log("[1/3] Generating scenario...")
            scenario = await self._generate_scenario_with_validation()
            self._save_json(scenario, "scenario_base.json")
            emit({"type": "scenario_ready", "scenario": scenario.get("_scenario", ""),
                  "num_counterparties": len(scenario["counterparties"]),
                  "topology": scenario["topology"]["mode"]})
            log(f"      Scenario: {scenario.get('_scenario', '?')}")
            log(f"      Counterparties: {len(scenario['counterparties'])}")
            log(f"      Topology: {scenario['topology']['mode']}")

            # Step 2: Generate initial population (parallel)
            log(f"\n[2/3] Generating initial population of {self.population_size}...")
            population = await self._generate_initial_population()
            for i, genome in enumerate(population):
                genome.save(os.path.join(self.run_dir, "gen_0"))
                log(f"      [{i+1}] {genome.role[:60]}...")

            emit({"type": "population_ready", "size": len(population),
                  "genomes": [{"genome_id": g.genome_id, "role": g.role[:80]} for g in population]})
            log(f"\n[3/3] Starting evolution...\n")

        # Evolution loop
        for gen in range(start_gen, self.num_generations):
            diversity = population_diversity(population)

            emit({"type": "generation_start", "generation": gen, "diversity": diversity})
            log(f"\n{'─'*60}")
            log(f"  GENERATION {gen}")
            log(f"{'─'*60}")
            log(f"  Population diversity: {diversity:.2f}")

            if diversity < DIVERSITY_THRESHOLD:
                log(f"  LOW — injecting fresh genomes")
                population = await self._inject_diversity(population, gen)

            # Build scenario with current genomes as negotiators
            scenario_path = self._build_generation_scenario(scenario, population, gen)
            db_path = os.path.join(self.run_dir, f"gen_{gen}", "simulation.db")
            os.makedirs(os.path.dirname(db_path), exist_ok=True)

            # Run OASIS simulation (messages stream to stdout in real time via poller)
            log(f"\n  Running simulation...")
            db_path, agents_spec = await run_scenario(scenario_path, db_path, generation=gen)

            # Evaluate (multi-eval for reliability)
            emit({"type": "evaluation_start", "generation": gen})
            log(f"\n  Evaluating (3x per agent for reliability)...")
            scores = await evaluate_generation(
                db_path=db_path,
                agents_spec=agents_spec,
                rubric=self.rubric,
                model=self.model,
            )
            for src_idx, result in scores.items():
                genome = population[src_idx]
                overall = result.get("overall", 0.0)
                spread = result.get("_eval_spread", 0)
                emit({"type": "score", "generation": gen, "genome_id": genome.genome_id,
                      "overall": overall, "scores": result.get("scores", {}),
                      "reasoning": result.get("reasoning", ""), "eval_spread": spread})
                spread_str = f" spread={spread:.2f}" if spread else ""
                log(f"    [{genome.genome_id}] score={overall:.2f}{spread_str} — {result.get('reasoning', '')[:70]}")

            # Save generation results
            self._save_json({
                "generation": gen,
                "diversity": diversity,
                "scores": {str(k): v for k, v in scores.items()},
            }, f"gen_{gen}/scores.json")

            # Track best (elitism)
            for src_idx, result in scores.items():
                overall = result.get("overall", 0.0)
                if overall > best_score:
                    best_score = overall
                    best_genome = population[src_idx]
                    logger.info(f"New best: {best_genome.genome_id} score={best_score:.3f}")

            # Select top performers
            ranked = sorted(
                range(len(population)),
                key=lambda i: scores.get(i, {}).get("overall", 0.0),
                reverse=True,
            )
            num_survivors = max(1, int(self.population_size * self.survival_rate))
            survivors = [population[i] for i in ranked[:num_survivors]]

            # Elitism: ensure all-time best is always in survivors
            if best_genome and best_genome.genome_id not in [s.genome_id for s in survivors]:
                survivors[-1] = best_genome
                log(f"  Elitism: preserved all-time best [{best_genome.genome_id}]")

            survivor_ids = [s.genome_id for s in survivors]
            eliminated_ids = [population[i].genome_id for i in ranked[num_survivors:]]
            emit({"type": "selection", "generation": gen,
                  "survivors": survivor_ids, "eliminated": eliminated_ids,
                  "best_genome_id": best_genome.genome_id, "best_score": best_score})

            log(f"\n  Selection: top {num_survivors} survive")
            for i, idx in enumerate(ranked[:num_survivors]):
                s = scores.get(idx, {}).get("overall", 0.0)
                marker = " *" if population[idx].genome_id == best_genome.genome_id else ""
                log(f"    {i+1}. [{population[idx].genome_id}] score={s:.2f}{marker}")

            # Checkpoint
            save_checkpoint(self.run_dir, gen, population, best_genome, best_score, scenario)

            emit({"type": "generation_complete", "generation": gen,
                  "best_score": best_score, "diversity": diversity})

            # Evolve next generation (unless final)
            if gen < self.num_generations - 1:
                population = await self._evolve(survivors, gen + 1)
                for genome in population:
                    genome.save(os.path.join(self.run_dir, f"gen_{gen + 1}"))
                log(f"\n  Next generation: {len(population)} agents")

        # Final results
        log(f"\n{'='*60}")
        log(f"  EVOLUTION COMPLETE")
        log(f"{'='*60}")
        log(f"  Best genome: {best_genome.genome_id}")
        log(f"  Best score:  {best_score:.2f}")
        log(f"\n  Evolved prompt:")
        log(f"  {best_genome.to_prompt()[:300]}...")
        log(f"\n  Saved to: {self.run_dir}")

        best_genome.save(os.path.join(self.run_dir, "best"))
        self._save_json({
            "best_genome_id": best_genome.genome_id,
            "best_score": best_score,
            "best_prompt": best_genome.to_prompt(),
            "goal": self.goal,
            "rubric": self.rubric,
        }, "results.json")

        emit({"type": "evolution_complete", "best_genome_id": best_genome.genome_id,
              "best_score": best_score, "best_prompt": best_genome.to_prompt(),
              "run_dir": self.run_dir})

        return best_genome

    # ── Scenario generation with validation ──────────────────────────────

    async def _generate_scenario_with_validation(self) -> dict:
        """Generate a scenario, validating and retrying on failure."""
        prompt = SCENARIO_GENERATION_PROMPT.format(goal=self.goal)

        for attempt in range(SCENARIO_GEN_RETRIES):
            scenario = await complete_json(self.client, self.model, prompt, temperature=0.8)
            errors = validate_scenario(scenario)
            if not errors:
                return scenario
            logger.warning(f"Scenario validation failed (attempt {attempt + 1}): {errors}")
            # Add errors to prompt for next attempt
            prompt = SCENARIO_GENERATION_PROMPT.format(goal=self.goal) + (
                f"\n\nYour previous attempt had these errors: {errors}\nPlease fix them."
            )

        # Last resort: use a safe pairwise default
        logger.error("Scenario generation failed after retries, fixing topology to pairwise")
        scenario["topology"] = {"mode": "pairwise"}
        remaining_errors = validate_scenario(scenario)
        if remaining_errors:
            raise ValueError(f"Cannot generate valid scenario: {remaining_errors}")
        return scenario

    # ── Population generation (parallel) ─────────────────────────────────

    async def _generate_initial_population(self) -> list[AgentGenome]:
        """Generate a diverse initial population of genomes in parallel."""

        async def generate_one(diversity_hint: str) -> AgentGenome:
            prompt = SEED_GENOME_PROMPT.format(
                goal=self.goal, rubric=self.rubric, diversity_hint=diversity_hint,
            )
            data = await complete_json(self.client, self.model, prompt, temperature=1.0)
            return AgentGenome(
                role=data.get("role", ""),
                goals=data.get("goals", ""),
                strategy=data.get("strategy", ""),
                tactics=data.get("tactics", ""),
                style=data.get("style", ""),
                constraints=data.get("constraints", ""),
                generation=0,
            )

        # Generate first genome, then use it as diversity reference for the rest
        first = await generate_one("This is the first agent. Be creative.")
        population = [first]

        # Generate remaining in parallel
        hints = []
        for i in range(1, self.population_size):
            hints.append(
                f"Make this agent DIFFERENT from existing agents.\n"
                f"Agent 0 strategy: {first.strategy[:80]}\n"
                f"Try approach #{i+1} — be creative and divergent."
            )

        tasks = [generate_one(hint) for hint in hints]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, Exception):
                logger.warning(f"Seed generation failed: {result}, retrying")
                fallback = await generate_one("Be creative. Generate a unique approach.")
                population.append(fallback)
            else:
                population.append(result)

        return population

    # ── Evolution ────────────────────────────────────────────────────────

    async def _evolve(self, survivors: list[AgentGenome], generation: int) -> list[AgentGenome]:
        """Create next generation from survivors via mutation and crossover."""
        next_gen = list(survivors)
        num_needed = self.population_size - len(next_gen)

        tasks = []
        for _ in range(num_needed):
            if len(survivors) >= 2 and random.random() < 0.3:
                a, b = random.sample(survivors, 2)
                tasks.append(self.mutator.crossover(a, b, generation))
            else:
                parent = random.choice(survivors)
                tasks.append(self.mutator.mutate(parent, generation))

        children = await asyncio.gather(*tasks, return_exceptions=True)
        for child in children:
            if isinstance(child, Exception):
                logger.warning(f"Mutation failed: {child}, cloning survivor")
                fallback = random.choice(survivors)
                clone = AgentGenome(**fallback.sections(), generation=generation, parent_ids=[fallback.genome_id])
                next_gen.append(clone)
            else:
                next_gen.append(child)

        return next_gen[:self.population_size]

    async def _inject_diversity(self, population: list[AgentGenome], generation: int) -> list[AgentGenome]:
        """Replace the weakest members with fresh random genomes when diversity is low."""
        num_inject = max(1, self.population_size // 4)
        logger.info(f"Injecting {num_inject} fresh genomes for diversity")

        tasks = []
        for _ in range(num_inject):
            prompt = SEED_GENOME_PROMPT.format(
                goal=self.goal, rubric=self.rubric,
                diversity_hint="The population has converged. Generate a RADICALLY different approach.",
            )
            tasks.append(complete_json(self.client, self.model, prompt, temperature=1.2))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        fresh = []
        for result in results:
            if isinstance(result, Exception):
                continue
            fresh.append(AgentGenome(
                role=result.get("role", ""),
                goals=result.get("goals", ""),
                strategy=result.get("strategy", ""),
                tactics=result.get("tactics", ""),
                style=result.get("style", ""),
                constraints=result.get("constraints", ""),
                generation=generation,
            ))

        # Replace the last N members (weakest after sorting)
        new_pop = population[:len(population) - len(fresh)] + fresh
        return new_pop

    # ── Scenario building per generation ─────────────────────────────────

    def _build_generation_scenario(self, base_scenario: dict, population: list[AgentGenome], generation: int) -> str:
        scenario = dict(base_scenario)
        scenario["negotiators"] = [
            {
                "username": f"agent_{genome.genome_id}",
                "name": f"Agent {genome.genome_id}",
                "bio": f"Evolved agent, generation {generation}",
                "persona": genome.to_prompt(),
            }
            for genome in population
        ]

        gen_dir = os.path.join(self.run_dir, f"gen_{generation}")
        os.makedirs(gen_dir, exist_ok=True)
        path = os.path.join(gen_dir, "scenario.json")
        with open(path, "w") as f:
            json.dump(scenario, f, indent=2)
        return path

    def _save_json(self, data: dict, filename: str):
        path = os.path.join(self.run_dir, filename)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f, indent=2)


# ── CLI ──────────────────────────────────────────────────────────────────────

def main():
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )

    parser = argparse.ArgumentParser(description="Agent Kitchen: Evolve the perfect AI agent")
    parser.add_argument("goal", help="What kind of agent to evolve")
    parser.add_argument("--rubric", required=True, help="Path to rubric text file")
    parser.add_argument("--population", type=int, default=8)
    parser.add_argument("--generations", type=int, default=5)
    parser.add_argument("--survival-rate", type=float, default=0.4)
    parser.add_argument("--model", default=None, help="LLM model override")
    parser.add_argument("--resume", default=None, help="Path to existing run directory to resume")
    args = parser.parse_args()

    with open(args.rubric) as f:
        rubric = f.read()

    orchestrator = Orchestrator(
        goal=args.goal,
        rubric=rubric,
        population_size=args.population,
        num_generations=args.generations,
        survival_rate=args.survival_rate,
        model=args.model,
        run_dir=args.resume,
    )

    asyncio.run(orchestrator.run())


if __name__ == "__main__":
    main()
