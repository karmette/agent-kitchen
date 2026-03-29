"""Mutation operators for AgentGenome, modeled on biological evolution.

Mutation types mirror real genetics:
- Point mutation:  Small tweak to one section (most common, like SNPs)
- Rewrite:         Larger rewrite of one section (like gene-level mutation)
- Insertion:       Add a new element to a section
- Deletion:        Remove an element from a section
- Crossover:       Combine sections from two parents (sexual reproduction)

Small mutations dominate. Radical changes are rare but occasionally
produce breakthroughs. The weights below reflect that.
"""

import random

from genome import AgentGenome, SECTIONS
from llm import create_client, complete

# How often each mutation type fires (weights, not probabilities)
MUTATION_WEIGHTS = {
    "point":     50,
    "rewrite":   20,
    "insertion": 10,
    "deletion":  10,
    "crossover": 10,
}

# Sections that change easily vs. slowly (mirrors evolutionary rates)
SECTION_VOLATILITY = {
    "role":        1,
    "goals":       2,
    "strategy":    3,
    "tactics":     5,
    "style":       4,
    "constraints": 1,
}

POINT_MUTATION_PROMPT = """\
You are a genetic mutation operator. Make a SMALL, targeted change to the \
following section of an agent's genome. Change a few words, adjust emphasis, \
or slightly shift the approach. Do NOT rewrite the whole thing.

Section name: {section_name}
Current content:
{content}

Return ONLY the mutated section text, nothing else.\
"""

REWRITE_PROMPT = """\
You are a genetic mutation operator. Rewrite the following section of an \
agent's genome. Keep the general intent but take a meaningfully different \
approach. Be creative.

Section name: {section_name}
Current content:
{content}

Full genome for context:
{full_genome}

Return ONLY the rewritten section text, nothing else.\
"""

INSERTION_PROMPT = """\
You are a genetic mutation operator. Add ONE new element (a sentence or \
bullet point) to the following section. It should complement what's already \
there without contradicting it.

Section name: {section_name}
Current content:
{content}

Full genome for context:
{full_genome}

Return ONLY the updated section text with the new element included, nothing else.\
"""

DELETION_PROMPT = """\
You are a genetic mutation operator. Remove ONE element (a sentence, bullet \
point, or clause) from the following section. Pick something that seems \
redundant, overly specific, or potentially counterproductive.

Section name: {section_name}
Current content:
{content}

Return ONLY the updated section text with the element removed, nothing else. \
If the section only has one element, return it unchanged.\
"""


def _pick_section() -> str:
    sections = list(SECTION_VOLATILITY.keys())
    weights = [SECTION_VOLATILITY[s] for s in sections]
    return random.choices(sections, weights=weights, k=1)[0]


def _pick_mutation_type() -> str:
    types = list(MUTATION_WEIGHTS.keys())
    weights = [MUTATION_WEIGHTS[t] for t in types]
    return random.choices(types, weights=weights, k=1)[0]


class Mutator:
    def __init__(self, model: str = None):
        from llm import get_model
        self.model = model or get_model()
        self.client = create_client()

    async def mutate(self, parent: AgentGenome, generation: int) -> AgentGenome:
        """Apply a random mutation to a parent genome and return the child."""
        mutation_type = _pick_mutation_type()
        if mutation_type == "crossover":
            mutation_type = "point"

        section = _pick_section()
        current = getattr(parent, section)

        if mutation_type == "point":
            prompt = POINT_MUTATION_PROMPT.format(section_name=section, content=current)
        elif mutation_type == "rewrite":
            prompt = REWRITE_PROMPT.format(section_name=section, content=current, full_genome=parent.to_prompt())
        elif mutation_type == "insertion":
            prompt = INSERTION_PROMPT.format(section_name=section, content=current, full_genome=parent.to_prompt())
        elif mutation_type == "deletion":
            prompt = DELETION_PROMPT.format(section_name=section, content=current)

        new_content = await complete(self.client, self.model, prompt, temperature=1.0)

        child_sections = parent.sections()
        child_sections[section] = new_content

        return AgentGenome(**child_sections, generation=generation, parent_ids=[parent.genome_id])

    async def crossover(self, parent_a: AgentGenome, parent_b: AgentGenome, generation: int) -> AgentGenome:
        """Sexual reproduction: randomly take each section from one parent."""
        child_sections = {}
        for section in SECTIONS:
            donor = random.choice([parent_a, parent_b])
            child_sections[section] = getattr(donor, section)

        return AgentGenome(**child_sections, generation=generation, parent_ids=[parent_a.genome_id, parent_b.genome_id])
