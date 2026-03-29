# Agent Kitchen

**Replace prompt engineering with evolution.**

Agent Kitchen evolves the best AI agent for any social interaction by running natural selection inside [OASIS](https://github.com/camel-ai/oasis) social simulations. Describe the agent you want — "best negotiator", "best teacher", "best marketer" — and the system breeds a population of competing agents across diverse scenarios until the fittest prompt emerges.

---

## How It Works

```
You: "best negotiator"
         |
         v
  +-----------------+
  | Scenario Gen    |  AI creates diverse test scenarios:
  |                 |  salary negotiation, vendor contract,
  |                 |  used car haggling, public outreach...
  +-----------------+
         |
         v
  +-----------------+
  | Population Seed |  4 agents with different strategies
  |                 |  are generated as starting genomes
  +-----------------+
         |
    +----+----+  <-- repeat for N generations
    |         |
    v         |
  +-----------------+
  | Simulate        |  Every agent runs through every scenario
  |                 |  inside OASIS social simulations — live
  |                 |  conversations, posts, comments, follows
  +-----------------+
    |
    v
  +-----------------+
  | Score           |  LLM judge evaluates each agent's
  |                 |  transcripts against a rubric
  +-----------------+
    |
    v
  +-----------------+
  | Select          |  Top performers survive.
  |                 |  Weak agents are eliminated.
  +-----------------+
    |
    v
  +-----------------+
  | Breed           |  Survivors are mutated and crossed over
  |                 |  to produce the next generation
  +-----------------+
    |         |
    +---------+
         |
         v
  +-----------------+
  | Result          |  The best-performing agent's prompt
  |                 |  is the output — evolved, not written
  +-----------------+
```

---

## Why OASIS

[OASIS](https://github.com/camel-ai/oasis) is a social media simulator from CAMEL-AI that can realistically model up to one million users on platforms like Reddit and Twitter. Agent Kitchen leverages OASIS as the **fitness testing environment** — the arena where agents prove themselves.

OASIS gives us capabilities no simple chat API can:

| OASIS Feature | How Agent Kitchen Uses It |
|---|---|
| **Group Messaging** | Private 1-on-1 negotiations, interviews, mediation sessions |
| **Posts & Comments** | Public-facing scenarios — marketing, debate, customer outreach |
| **Social Graph** (follow/mute) | Measures whether an agent builds influence, not just talks well |
| **Recommendation System** | Tests if an agent's content surfaces naturally in feeds |
| **Multi-Agent Environments** | Panel interviews, group mediations, multi-party negotiations |
| **Turn-Based & Simultaneous** | Private chats use turn-taking; social feeds let everyone act at once |

Each scenario automatically picks the right OASIS primitives:
- **Group mode** — private chat rooms for negotiations, interviews, tutoring
- **Social mode** — public posts/comments/likes for marketing, debate, outreach
- **Mixed mode** — both private and public interaction in one scenario


---

## The Genome

Each agent is represented as a structured genome with six evolvable sections:

| Section | Biological Analogy | What It Controls |
|---|---|---|
| **Role** | Body plan | Foundational identity — evolves slowly |
| **Goals** | Drive function | What the agent optimizes for |
| **Strategy** | Behavioral phenotype | High-level approach |
| **Tactics** | Specific adaptations | Concrete techniques |
| **Style** | Signaling | Communication tone and personality |
| **Constraints** | Immune system | Hard boundaries it won't cross |

Evolution operators mirror real genetics:
- **Point mutation** (50%) — small tweak to one section
- **Rewrite** (20%) — larger rewrite of one section
- **Insertion** (10%) — add a new element to a section
- **Deletion** (10%) — remove a redundant element
- **Crossover** (10%) — combine sections from two parents

Section volatility mirrors biological evolution rates — tactics and style evolve fast, role and constraints evolve slowly.

---

## The TUI

Agent Kitchen ships as a terminal application with a real-time dashboard built with [Bubble Tea](https://github.com/charmbracelet/bubbletea) and [Lip Gloss](https://github.com/charmbracelet/lipgloss).

### Input Screen
- Describe your goal in natural language
- Optionally define a custom scoring rubric with weighted criteria
- Set generations and scenario count

### Grid View
- Each cell is a different test scenario running in parallel
- Live-streaming conversations and social activity as agents interact
- Sidebar shows current evolution phase, progress, and activity log
- Expand any cell to watch individual agent interactions

### Detail View
- Tabs for each evolved agent being tested in that scenario
- Feed tab for social media activity (posts, comments, follows)
- Generation markers show which interactions belong to which evolution cycle

### Results View
- Fitness chart showing best and average scores over generations
- Generation timeline with diversity indicators
- Final leaderboard with per-scenario score breakdowns
- Full evolved agent prompt with section highlighting
- Evaluation rubric used for scoring
- One-key export to markdown

---

## Architecture

```
frontend/                 Go + Bubble Tea TUI
  input.go               Goal, rubric, and settings input
  result.go              Grid, detail, and results views
  backend.go             Subprocess manager + JSONL event routing

backend/                  Python + OASIS
  orchestrator.py         Evolution loop — scenario gen, population, selection, breeding
  main.py                 OASIS simulation runner — mode-aware (group/social/mixed)
  chat_agent.py           Custom SocialAgent subclasses for focused prompts
  genome.py               AgentGenome dataclass with 6 evolvable sections
  mutator.py              Mutation operators modeled on biological genetics
  topology.py             Flexible group topology builder (pairwise/rooms/custom)
  events.py               JSONL event streaming + DB activity polling
  llm.py                  Shared LLM client with SSE parsing and retry logic
  evaluator/evaluate.py   LLM judge — scores transcripts against rubric
```

Communication: Python emits JSONL events to stdout. Go reads them via a pipe and routes to the TUI in real time. All simulation data is persisted in SQLite (one DB per scenario per generation).

---

## Quick Start

### Prerequisites
- Python 3.11+
- Go 1.21+
- An OpenAI-compatible API key

### Setup
```bash
# Clone
git clone https://github.com/karmette/agent-kitchen.git
cd agent-kitchen

# Backend
python -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt

# Configure API
cp backend/.env.example backend/.env
# Edit backend/.env with your API key

# Frontend
cd frontend
go build -o agent-kitchen

# Run
./agent-kitchen
```

---



*AI slop implies the existence of AI peak*
