"""Orchestrator: the evolutionary loop.

Evolves the best AI agent for any social interaction scenario through
natural selection inside OASIS simulations.

The user provides only a goal (e.g. "make the best negotiator"). The system
generates multiple diverse scenarios, a rubric, and an initial population.
Each generation, every agent runs through ALL scenarios. Fitness = average
score across scenarios. This produces generalist agents, not specialists.
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
from llm import create_client, get_model, complete, complete_json
from events import emit

logger = logging.getLogger(__name__)

VALID_TOPOLOGY_MODES = {"pairwise", "rooms", "custom"}
VALID_PER_VALUES = {"negotiator", "counterparty"}
VALID_MEMBER_TYPES = {"negotiator", "counterparty", "all_negotiators", "all_counterparties"}
DIVERSITY_THRESHOLD = 0.3
SCENARIO_GEN_RETRIES = 3
NUM_SCENARIOS = 3  # each cell in the TUI = one scenario


def log(msg: str):
    print(msg, file=sys.stderr)


# ── Prompts ──────────────────────────────────────────────────────────────────

SCENARIOS_GENERATION_PROMPT = """\
You are designing simulation scenarios for an AI agent evolution system.

The user wants to evolve: {goal}

Generate {num_scenarios} DIVERSE scenarios that test DIFFERENT aspects of \
this skill. Each scenario should be a distinct situation with different \
dynamics, stakes, and counterparty types.

Examples by goal type:
- "best negotiator" → salary negotiation, vendor contract, used car haggling
- "best marketing agent" → pitching to skeptics, engaging on social media, cold outreach
- "best interviewer" → technical interview panel, behavioral screening, culture fit
- "best customer service agent" → angry customer, refund request, technical support
- "best teacher" → explaining to a beginner, helping a struggling student, advanced Q&A
- "best salesperson" → cold pitch, handling objections, closing a warm lead
- "best mediator" → workplace conflict, neighbor dispute, contract disagreement

Each scenario must be CONCRETE — specific stakes, specific context, not \
vague. Each scenario independently defines its own topology and counterparties.

Choose the right topology for each scenario:
- 1v1 conversations (sales, negotiation, support) → pairwise
- Panel interactions (interviews, pitches to a committee) → rooms with multiple counterparties
- Group dynamics (team mediation, classroom) → rooms with multiple counterparties

Return ONLY valid JSON — an array of scenario objects:
[
  {{
    "_scenario": "one-line description (e.g. 'Salary negotiation for a senior engineer role')",
    "template": "You are in a conversation. [Specific situation, stakes, what both sides want.] Your approach: {{persona}} Use send_to_group to communicate. Write short, direct chat messages — no emails, no letters, no signatures, no JSON. Talk like a person in a live chat.",
    "topology": <topology object>,
    "actions": ["SEND_TO_GROUP", "LISTEN_FROM_GROUP", "DO_NOTHING"],
    "num_rounds": 4,
    "counterparties": [
      {{
        "username": "short_snake_case",
        "name": "Full Name",
        "bio": "One-line role",
        "persona": "Who they are, what they want, how they behave, their limits. 3-5 sentences."
      }}
    ]
  }}
]

Topology JSON options:
- 1v1: {{"mode": "pairwise"}}
- Panel/group: {{"mode": "rooms", "per": "negotiator", "members": ["negotiator", "all_counterparties"]}}

The "per" field MUST be "negotiator" or "counterparty".
The "members" MUST be a list from: "negotiator", "counterparty", "all_negotiators", "all_counterparties".

Keep all scenarios realistic — everyday business, workplace, or consumer \
situations. Each scenario should have 1-2 counterparties for speed.

IMPORTANT: Every scenario MUST be fundamentally different — different \
settings, different relationship dynamics, different stakes. Each should test a genuinely \
distinct skill.
"""

RUBRIC_GENERATION_PROMPT = """\
You are designing an evaluation rubric for an AI agent evolution system.

The user wants to evolve: {goal}

The agent will be tested across these scenarios:
{scenario_descriptions}

Generate a rubric that an LLM judge will use to score the evolved agent's \
performance. The rubric should be GENERAL enough to apply across all \
scenarios, but SPECIFIC enough to distinguish good from bad performance.

Have 3-5 criteria, each scored 0.0 to 1.0. For each criterion, describe \
what 0.0 and 1.0 look like.

Return ONLY the rubric text as markdown, no JSON wrapping.
"""

SEED_GENOME_PROMPT = """\
You are generating a strategy profile for an AI agent that will compete \
in social simulations and be evolved through natural selection.

The agent's goal: {goal}

The agent will face these scenarios:
{scenario_descriptions}

The agent will be evaluated by this rubric:
{rubric}

Generate a unique agent profile with these 6 sections. Each section should \
be 2-4 sentences of PRACTICAL, ACTIONABLE instructions. The agent must be \
versatile enough to perform well across ALL scenarios, not just one.

{diversity_hint}

Return ONLY valid JSON:
{{
  "role": "A realistic role with relevant expertise",
  "goals": "Concrete objectives in priority order",
  "strategy": "High-level approach that works across different situations",
  "tactics": "Specific techniques to employ",
  "style": "Communication tone and personality",
  "constraints": "Hard rules and boundaries"
}}
"""


# ── Validation ───────────────────────────────────────────────────────────────

def validate_scenario(scenario: dict) -> list[str]:
    errors = []
    if "template" not in scenario:
        errors.append("Missing 'template'")
    elif "{persona}" not in scenario.get("template", ""):
        errors.append("Template must contain {persona}")

    if "counterparties" not in scenario:
        errors.append("Missing 'counterparties'")
    elif not isinstance(scenario["counterparties"], list) or len(scenario["counterparties"]) == 0:
        errors.append("'counterparties' must be a non-empty list")
    else:
        for i, cp in enumerate(scenario["counterparties"]):
            for field in ("username", "name", "bio", "persona"):
                if field not in cp or not isinstance(cp[field], str):
                    errors.append(f"Counterparty {i} missing '{field}'")

    topology = scenario.get("topology", {})
    mode = topology.get("mode")
    if mode not in VALID_TOPOLOGY_MODES:
        errors.append(f"Invalid topology mode: {mode}")
    if mode == "rooms":
        if topology.get("per") not in VALID_PER_VALUES:
            errors.append(f"Invalid 'per': {topology.get('per')}")
        members = topology.get("members", [])
        if not isinstance(members, list):
            errors.append("'members' must be a list")
        else:
            for m in members:
                if m not in VALID_MEMBER_TYPES:
                    errors.append(f"Invalid member: {m}")

    if not isinstance(scenario.get("num_rounds", 0), int) or scenario.get("num_rounds", 0) < 1:
        errors.append("'num_rounds' must be a positive integer")
    return errors


# ── Diversity ────────────────────────────────────────────────────────────────

def population_diversity(population: list[AgentGenome]) -> float:
    if len(population) < 2:
        return 1.0
    prompts = [g.to_prompt() for g in population]
    sims = []
    for i in range(len(prompts)):
        for j in range(i + 1, len(prompts)):
            sims.append(SequenceMatcher(None, prompts[i], prompts[j]).quick_ratio())
    return round(1.0 - sum(sims) / len(sims), 3)


# ── Checkpointing ────────────────────────────────────────────────────────────

def save_checkpoint(run_dir, gen, population, best_genome, best_score, scenarios, rubric):
    data = {
        "generation": gen,
        "best_genome_id": best_genome.genome_id if best_genome else None,
        "best_score": best_score,
        "population": [g.to_dict() for g in population],
        "scenarios": scenarios,
        "rubric": rubric,
    }
    with open(os.path.join(run_dir, "checkpoint.json"), "w") as f:
        json.dump(data, f, indent=2)


def load_checkpoint(run_dir):
    path = os.path.join(run_dir, "checkpoint.json")
    if not os.path.exists(path):
        return None
    with open(path) as f:
        data = json.load(f)
    population = [AgentGenome.from_dict(g) for g in data["population"]]
    best = None
    if data["best_genome_id"]:
        for g in population:
            if g.genome_id == data["best_genome_id"]:
                best = g
                break
    return (data["generation"], population, best, data["best_score"],
            data["scenarios"], data.get("rubric", ""))


# ── Orchestrator ─────────────────────────────────────────────────────────────

class Orchestrator:
    def __init__(self, goal, rubric=None, population_size=8, num_generations=5,
                 survival_rate=0.4, num_scenarios=NUM_SCENARIOS, model=None, run_dir=None):
        self.goal = goal
        self.rubric = rubric
        self.population_size = population_size
        self.num_generations = num_generations
        self.survival_rate = survival_rate
        self.num_scenarios = num_scenarios
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
        emit({"type": "evolution_start", "goal": self.goal,
              "population_size": self.population_size,
              "num_generations": self.num_generations,
              "num_scenarios": self.num_scenarios, "run_dir": self.run_dir})

        log(f"\n{'='*60}")
        log(f"  AGENT KITCHEN — Evolutionary Agent Optimization")
        log(f"{'='*60}")
        log(f"  Goal:        {self.goal}")
        log(f"  Population:  {self.population_size}")
        log(f"  Generations: {self.num_generations}")
        log(f"  Scenarios:   {self.num_scenarios}")
        log(f"  Output:      {self.run_dir}")
        log(f"{'='*60}\n")

        checkpoint = load_checkpoint(self.run_dir)
        if checkpoint:
            start_gen, population, best_genome, best_score, scenarios, self.rubric = checkpoint
            start_gen += 1
            log(f"  Resuming from generation {start_gen} (best={best_score:.2f})\n")
        else:
            start_gen = 0
            best_genome = None
            best_score = -1

            # Step 1: Generate diverse scenarios
            log("[1/4] Generating scenarios...")
            scenarios = await self._generate_scenarios()
            self._save_json(scenarios, "scenarios.json")
            for i, s in enumerate(scenarios):
                emit({"type": "scenario_ready", "scenario_id": i,
                      "scenario": s.get("_scenario", ""),
                      "topology": s["topology"]["mode"],
                      "num_counterparties": len(s["counterparties"])})
                log(f"      [{i+1}] {s.get('_scenario', '?')} ({s['topology']['mode']}, {len(s['counterparties'])} counterparties)")

            # Step 2: Generate rubric
            if not self.rubric:
                log("\n[2/4] Generating evaluation rubric...")
                descs = "\n".join(f"- {s['_scenario']}" for s in scenarios)
                self.rubric = await self._generate_rubric(descs)
                log(f"      {self.rubric[:100]}...")
            else:
                log("\n[2/4] Using provided rubric")
            self._save_json({"rubric": self.rubric}, "rubric.json")

            # Step 3: Generate initial population
            descs = "\n".join(f"- {s['_scenario']}" for s in scenarios)
            log(f"\n[3/4] Generating initial population of {self.population_size}...")
            population = await self._generate_initial_population(descs)
            for i, g in enumerate(population):
                g.save(os.path.join(self.run_dir, "gen_0"))
                log(f"      [{i+1}] {g.role[:60]}...")

            emit({"type": "population_ready", "size": len(population),
                  "genomes": [{"genome_id": g.genome_id, "role": g.role[:80]} for g in population]})
            log(f"\n[4/4] Starting evolution...\n")

        # ── Evolution loop ───────────────────────────────────────────────
        for gen in range(start_gen, self.num_generations):
            diversity = population_diversity(population)
            emit({"type": "generation_start", "generation": gen, "diversity": diversity})
            log(f"\n{'─'*60}")
            log(f"  GENERATION {gen}")
            log(f"{'─'*60}")
            log(f"  Population diversity: {diversity:.2f}")

            if diversity < DIVERSITY_THRESHOLD:
                log(f"  LOW — injecting fresh genomes")
                descs = "\n".join(f"- {s['_scenario']}" for s in scenarios)
                population = await self._inject_diversity(population, gen, descs)

            # Run ALL scenarios in parallel
            async def run_one_scenario(scenario_idx, scenario):
                scenario_name = scenario.get("_scenario", f"Scenario {scenario_idx}")
                log(f"\n  Scenario {scenario_idx}: {scenario_name}")

                scenario_path = self._build_generation_scenario(
                    scenario, population, gen, scenario_idx)
                db_path = os.path.join(
                    self.run_dir, f"gen_{gen}", f"scenario_{scenario_idx}.db")
                os.makedirs(os.path.dirname(db_path), exist_ok=True)

                emit({"type": "scenario_start", "generation": gen,
                      "scenario_id": scenario_idx, "scenario": scenario_name})

                db_path, agents_spec = await run_scenario(
                    scenario_path, db_path, generation=gen, scenario_id=scenario_idx)

                emit({"type": "evaluation_start", "generation": gen,
                      "scenario_id": scenario_idx})
                log(f"    Evaluating scenario {scenario_idx}...")
                scores = await evaluate_generation(
                    db_path=db_path, agents_spec=agents_spec,
                    rubric=self.rubric, model=self.model)

                return scenario_idx, scores

            scenario_results = await asyncio.gather(
                *[run_one_scenario(i, s) for i, s in enumerate(scenarios)],
                return_exceptions=True,
            )

            all_scores = {}
            for src_idx in range(len(population)):
                all_scores[src_idx] = []

            for result in scenario_results:
                if isinstance(result, Exception):
                    logger.error(f"Scenario failed: {result}")
                    continue  # skip failed scenarios entirely
                scenario_idx, scores = result
                for src_idx, score_result in scores.items():
                    all_scores[src_idx].append(score_result.get("overall", 0.0))

            # Aggregate: average score across completed scenarios only
            agg_scores = {}
            for src_idx in range(len(population)):
                scenario_scores = [s for s in all_scores.get(src_idx, []) if s > 0.0]
                avg = sum(scenario_scores) / len(scenario_scores) if scenario_scores else 0.0
                agg_scores[src_idx] = round(avg, 3)
                genome = population[src_idx]
                emit({"type": "score", "generation": gen, "genome_id": genome.genome_id,
                      "overall": agg_scores[src_idx],
                      "scenario_scores": scenario_scores,
                      "parent_ids": genome.parent_ids})
                log(f"    [{genome.genome_id}] avg={agg_scores[src_idx]:.2f} scenarios={scenario_scores}")

            self._save_json({
                "generation": gen, "diversity": diversity,
                "scores": {str(k): {"overall": v, "scenarios": all_scores[k]}
                           for k, v in agg_scores.items()},
            }, f"gen_{gen}/scores.json")

            # Track best (elitism)
            for src_idx, avg in agg_scores.items():
                if avg > best_score:
                    best_score = avg
                    best_genome = population[src_idx]

            # Select
            ranked = sorted(range(len(population)),
                            key=lambda i: agg_scores.get(i, 0.0), reverse=True)
            num_survivors = max(1, int(self.population_size * self.survival_rate))
            survivors = [population[i] for i in ranked[:num_survivors]]

            if best_genome and best_genome.genome_id not in [s.genome_id for s in survivors]:
                survivors[-1] = best_genome
                log(f"  Elitism: preserved [{best_genome.genome_id}]")

            emit({"type": "selection", "generation": gen,
                  "survivors": [s.genome_id for s in survivors],
                  "eliminated": [population[i].genome_id for i in ranked[num_survivors:]],
                  "best_genome_id": best_genome.genome_id, "best_score": best_score})

            log(f"\n  Selection: top {num_survivors} survive")
            for i, idx in enumerate(ranked[:num_survivors]):
                marker = " *" if population[idx].genome_id == best_genome.genome_id else ""
                log(f"    {i+1}. [{population[idx].genome_id}] avg={agg_scores[idx]:.2f}{marker}")

            save_checkpoint(self.run_dir, gen, population, best_genome, best_score, scenarios, self.rubric)
            all_avgs = [v for v in agg_scores.values() if v > 0]
            pop_avg = sum(all_avgs) / len(all_avgs) if all_avgs else 0.0
            emit({"type": "generation_complete", "generation": gen,
                  "best_score": best_score, "avg_score": round(pop_avg, 3),
                  "diversity": diversity})

            if gen < self.num_generations - 1:
                population = await self._evolve(survivors, gen + 1)
                for g in population:
                    g.save(os.path.join(self.run_dir, f"gen_{gen + 1}"))
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
            "best_genome_id": best_genome.genome_id, "best_score": best_score,
            "best_prompt": best_genome.to_prompt(), "goal": self.goal, "rubric": self.rubric,
        }, "results.json")

        emit({"type": "evolution_complete", "best_genome_id": best_genome.genome_id,
              "best_score": best_score, "best_prompt": best_genome.to_prompt(),
              "rubric": self.rubric, "run_dir": self.run_dir})
        return best_genome

    # ── Scenario generation ──────────────────────────────────────────────

    async def _generate_scenarios(self) -> list[dict]:
        prompt = SCENARIOS_GENERATION_PROMPT.format(
            goal=self.goal, num_scenarios=self.num_scenarios)

        for attempt in range(SCENARIO_GEN_RETRIES):
            result = await complete_json(self.client, self.model, prompt, temperature=0.8)

            # Handle both array and wrapped object
            if isinstance(result, list):
                scenarios = result
            elif isinstance(result, dict) and "scenarios" in result:
                scenarios = result["scenarios"]
            else:
                scenarios = [result]

            # Validate each
            all_valid = True
            for i, s in enumerate(scenarios):
                errors = validate_scenario(s)
                if errors:
                    logger.warning(f"Scenario {i} invalid (attempt {attempt+1}): {errors}")
                    all_valid = False

            if all_valid and len(scenarios) >= 1:
                return scenarios[:self.num_scenarios]

            prompt = SCENARIOS_GENERATION_PROMPT.format(
                goal=self.goal, num_scenarios=self.num_scenarios) + (
                f"\n\nYour previous attempt had errors. Return a valid JSON array.")

        raise ValueError("Failed to generate valid scenarios after retries")

    async def _generate_rubric(self, scenario_descriptions: str) -> str:
        prompt = RUBRIC_GENERATION_PROMPT.format(
            goal=self.goal, scenario_descriptions=scenario_descriptions)
        return await complete(self.client, self.model, prompt, temperature=0.5)

    # ── Population generation ────────────────────────────────────────────

    async def _generate_initial_population(self, scenario_descriptions: str) -> list[AgentGenome]:
        async def gen_one(hint):
            prompt = SEED_GENOME_PROMPT.format(
                goal=self.goal, rubric=self.rubric,
                scenario_descriptions=scenario_descriptions, diversity_hint=hint)
            data = await complete_json(self.client, self.model, prompt, temperature=1.0)
            return AgentGenome(
                role=data.get("role", ""), goals=data.get("goals", ""),
                strategy=data.get("strategy", ""), tactics=data.get("tactics", ""),
                style=data.get("style", ""), constraints=data.get("constraints", ""),
                generation=0)

        first = await gen_one("This is the first agent. Be creative and practical.")
        pop = [first]
        hints = [f"Be DIFFERENT from: {first.strategy[:80]}. Try approach #{i+1}."
                 for i in range(1, self.population_size)]
        results = await asyncio.gather(*[gen_one(h) for h in hints], return_exceptions=True)
        for r in results:
            if isinstance(r, Exception):
                logger.warning(f"Seed gen failed: {r}")
                pop.append(await gen_one("Use a unique, practical approach."))
            else:
                pop.append(r)
        return pop

    # ── Evolution ────────────────────────────────────────────────────────

    async def _evolve(self, survivors, generation):
        next_gen = list(survivors)
        tasks = []
        operations = []  # track what each task does
        for _ in range(self.population_size - len(next_gen)):
            if len(survivors) >= 2 and random.random() < 0.3:
                a, b = random.sample(survivors, 2)
                tasks.append(self.mutator.crossover(a, b, generation))
                operations.append(("crossover", a.genome_id, b.genome_id))
            else:
                parent = random.choice(survivors)
                tasks.append(self.mutator.mutate(parent, generation))
                operations.append(("mutate", parent.genome_id, None))

        children = await asyncio.gather(*tasks, return_exceptions=True)
        for i, child in enumerate(children):
            op, parent1, parent2 = operations[i]
            if isinstance(child, Exception):
                f = random.choice(survivors)
                child = AgentGenome(**f.sections(), generation=generation,
                                    parent_ids=[f.genome_id])
                emit({"type": "breed", "operation": "clone",
                      "child": child.genome_id[:8], "parent": f.genome_id[:8]})
            else:
                if op == "crossover":
                    emit({"type": "breed", "operation": "crossover",
                          "child": child.genome_id[:8],
                          "parent_a": parent1[:8], "parent_b": parent2[:8]})
                else:
                    emit({"type": "breed", "operation": "mutate",
                          "child": child.genome_id[:8], "parent": parent1[:8]})
            next_gen.append(child)
        return next_gen[:self.population_size]

    async def _inject_diversity(self, population, generation, scenario_descriptions):
        n = max(1, self.population_size // 4)
        tasks = [complete_json(self.client, self.model, SEED_GENOME_PROMPT.format(
            goal=self.goal, rubric=self.rubric,
            scenario_descriptions=scenario_descriptions,
            diversity_hint="Population converged. Generate a RADICALLY different approach."),
            temperature=1.2) for _ in range(n)]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        fresh = []
        for r in results:
            if isinstance(r, Exception):
                continue
            fresh.append(AgentGenome(
                role=r.get("role", ""), goals=r.get("goals", ""),
                strategy=r.get("strategy", ""), tactics=r.get("tactics", ""),
                style=r.get("style", ""), constraints=r.get("constraints", ""),
                generation=generation))
        return population[:len(population) - len(fresh)] + fresh

    # ── Scenario building ────────────────────────────────────────────────

    def _build_generation_scenario(self, base_scenario, population, generation, scenario_idx):
        scenario = dict(base_scenario)
        scenario["negotiators"] = [
            {"username": f"agent_{g.genome_id}", "name": f"Agent {g.genome_id}",
             "bio": f"Evolved agent, gen {generation}", "persona": g.to_prompt()}
            for g in population
        ]
        gen_dir = os.path.join(self.run_dir, f"gen_{generation}")
        os.makedirs(gen_dir, exist_ok=True)
        path = os.path.join(gen_dir, f"scenario_{scenario_idx}.json")
        with open(path, "w") as f:
            json.dump(scenario, f, indent=2)
        return path

    def _save_json(self, data, filename):
        path = os.path.join(self.run_dir, filename)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f, indent=2)


# ── CLI ──────────────────────────────────────────────────────────────────────

def main():
    import argparse
    import resource
    # Increase file descriptor limit for parallel OASIS instances
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    resource.setrlimit(resource.RLIMIT_NOFILE, (min(hard, 4096), hard))

    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S")

    parser = argparse.ArgumentParser(description="Agent Kitchen: Evolve the perfect AI agent")
    parser.add_argument("goal")
    parser.add_argument("--rubric", default=None)
    parser.add_argument("--population", type=int, default=8)
    parser.add_argument("--generations", type=int, default=5)
    parser.add_argument("--scenarios", type=int, default=NUM_SCENARIOS)
    parser.add_argument("--survival-rate", type=float, default=0.4)
    parser.add_argument("--model", default=None)
    parser.add_argument("--resume", default=None)
    args = parser.parse_args()

    rubric = None
    if args.rubric:
        with open(args.rubric) as f:
            rubric = f.read()

    o = Orchestrator(goal=args.goal, rubric=rubric, population_size=args.population,
                     num_generations=args.generations, num_scenarios=args.scenarios,
                     survival_rate=args.survival_rate, model=args.model, run_dir=args.resume)
    asyncio.run(o.run())


if __name__ == "__main__":
    main()
