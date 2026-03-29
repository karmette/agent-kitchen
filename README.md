<div align="center">
  <img width="2672" height="1522" alt="Screenshot 2026-03-29 at 1 12 40 PM" src="https://github.com/user-attachments/assets/4385729e-3158-49b5-af4c-dd5fba095a87" />
  <h1>Haggle</h1>
</div>

**Replace prompt engineering with evolution.**

Describe the agent you want. A population of competing AI agents is dropped into [OASIS](https://github.com/camel-ai/oasis) social simulations — negotiating deals, posting on feeds, responding to crises. An LLM judge scores them. The weak die. The survivors mutate. The best prompt emerges, battle-tested and never written by a human.

---

## How It Works

> `"best negotiator"` → scenarios generated → agents spawned → simulations run → scored → weakest die → survivors mutate → repeat → **evolved prompt out**

The system auto-generates diverse scenarios (salary negotiation, vendor contract, public outreach on social media). A population of agents with different strategies competes across all of them inside live OASIS simulations. An LLM judge evaluates every interaction against a rubric. Bottom performers are eliminated. Survivors are mutated and crossed over to produce the next generation. After N cycles, the fittest agent's prompt is the output.

---

## Why OASIS

Most agent benchmarks test if an AI can generate good text. That's not enough. [OASIS](https://github.com/camel-ai/oasis) is a social simulation platform from CAMEL-AI that models realistic multi-agent environments at scale. Agent Kitchen uses it as the **fitness arena** — the world where agents prove themselves.

| What OASIS Provides | What It Enables |
|---|---|
| **Group Messaging** | Private negotiations, interviews, mediation |
| **Posts & Comments** | Public-facing marketing, debate, customer outreach |
| **Social Graph** | Measures if an agent builds real influence, not just talks well |
| **Recommendation System** | Tests if content surfaces naturally in feeds |
| **Multi-Agent Environments** | Panel interviews, group mediations, multi-party deals |

A simple LLM wrapper tests what an agent *says*. OASIS tests what an agent *does* — whether it builds influence, handles backlash, negotiates under pressure, and adapts when others push back.

---

## The Genome

Each agent is a structured prompt with six independently evolvable sections:

| Section | Analogy | Role |
|---|---|---|
| **Role** | Body plan | Core identity — evolves slowly |
| **Goals** | Drive | What the agent optimizes for |
| **Strategy** | Phenotype | High-level approach |
| **Tactics** | Adaptations | Concrete techniques |
| **Style** | Signaling | Tone and personality |
| **Constraints** | Immune system | Hard boundaries |

Mutation operators mirror real genetics: point mutations (50%), rewrites (20%), insertions (10%), deletions (10%), and crossover (10%). Tactics and style evolve fast. Role and constraints evolve slowly — just like biology. This means evolution explores the strategy space efficiently instead of randomly rewriting entire prompts.

---

## The TUI

Evolution is a black box if you can't watch it happen. The terminal dashboard (built with [Bubble Tea](https://github.com/charmbracelet/bubbletea) + [Lip Gloss](https://github.com/charmbracelet/lipgloss)) streams everything in real time:

- **Input** — describe your goal, optionally define a custom rubric, set generations and scenarios
- **Grid View** — each cell is a scenario running in parallel with live agent conversations
- **Detail View** — expand any cell, tab between agents to compare how each one handles the same situation
- **Results** — fitness charts, ranked leaderboard with per-scenario breakdowns, the full evolved prompt, and one-key export

The sidebar explains what's happening at every phase: generating scenarios, simulating interactions, scoring agents, natural selection, breeding the next generation.

---

## Architecture

**Frontend:** Go + Bubble Tea TUI. Reads JSONL events from the backend via a pipe and renders them in real time.

**Backend:** Python + OASIS. An orchestrator coordinates the full evolution loop — scenario generation, population seeding, parallel OASIS simulations, LLM evaluation, selection, and breeding. Custom `SocialAgent` subclasses override OASIS's default prompts for focused interactions in both private chat and public feed scenarios. All data persists in SQLite (one DB per scenario per generation).

---

## Quick Start

```bash
git clone https://github.com/karmette/agent-kitchen.git
cd agent-kitchen

# Backend
cd backend && uv sync --no-dev
cp .env.example .env  # add your API key

# Frontend
cd ../frontend && go build -o agent-kitchen
./agent-kitchen
```

Requires Python 3.11+, Go 1.21+, and an OpenAI-compatible API key.

---

*AI slop implies the existence of AI peak*
