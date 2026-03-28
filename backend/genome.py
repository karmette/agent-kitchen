"""AgentGenome: the unit of evolution.

A genome is a structured prompt string with distinct sections that can be
independently mutated. The sections map to biological evolution:

- Role:        body plan — foundational identity, evolves slowly
- Goals:       drive function — what the agent optimizes for
- Strategy:    behavioral phenotype — high-level approach
- Tactics:     specific adaptations — concrete techniques
- Style:       signaling — how the agent communicates
- Constraints: immune system — hard boundaries it won't cross
"""

import json
import os
import uuid
from dataclasses import dataclass, field

SECTIONS = ["role", "goals", "strategy", "tactics", "style", "constraints"]

GENOME_TEMPLATE = """\
## Role
{role}

## Goals
{goals}

## Strategy
{strategy}

## Tactics
{tactics}

## Style
{style}

## Constraints
{constraints}\
"""


@dataclass
class AgentGenome:
    role: str
    goals: str
    strategy: str
    tactics: str
    style: str
    constraints: str
    genome_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    generation: int = 0
    parent_ids: list[str] = field(default_factory=list)

    def to_prompt(self) -> str:
        """Render the full prompt string for use as an OASIS persona."""
        return GENOME_TEMPLATE.format(
            role=self.role,
            goals=self.goals,
            strategy=self.strategy,
            tactics=self.tactics,
            style=self.style,
            constraints=self.constraints,
        )

    def sections(self) -> dict[str, str]:
        """Return all evolvable sections as a dict."""
        return {s: getattr(self, s) for s in SECTIONS}

    def to_dict(self) -> dict:
        return {
            "genome_id": self.genome_id,
            "generation": self.generation,
            "parent_ids": self.parent_ids,
            **self.sections(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AgentGenome":
        return cls(
            role=data["role"],
            goals=data["goals"],
            strategy=data["strategy"],
            tactics=data["tactics"],
            style=data["style"],
            constraints=data["constraints"],
            genome_id=data.get("genome_id", uuid.uuid4().hex[:12]),
            generation=data.get("generation", 0),
            parent_ids=data.get("parent_ids", []),
        )

    def save(self, directory: str) -> str:
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, f"{self.genome_id}.json")
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)
        return path

    @classmethod
    def load(cls, path: str) -> "AgentGenome":
        with open(path) as f:
            return cls.from_dict(json.load(f))
