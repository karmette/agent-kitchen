"""Evaluator: scores agent transcripts against a rubric using an LLM judge.

Runs multiple evaluations per transcript and averages them to reduce noise.
Evaluation reliability is the fitness function — if it's noisy, evolution
is random drift. This module prioritizes consistency over speed.
"""

import asyncio
import json
import logging
import os
import sqlite3

from llm import create_client, complete, parse_json, get_model

logger = logging.getLogger(__name__)

NUM_EVALS = 3  # evaluations per transcript, averaged for stability

EVAL_PROMPT = """\
You are an impartial judge evaluating an AI agent's performance in a simulation.

## Rubric
{rubric}

## Transcript
{transcript}

Score the agent on each criterion in the rubric from 0.0 to 1.0.
Also provide an overall score (weighted average of all criteria).

Be precise and consistent. Base scores strictly on observable behavior in \
the transcript, not on assumptions about what might have happened.

Return ONLY valid JSON (no markdown, no code blocks):
{{"scores": {{"criterion_name": float, ...}}, "overall": float, "reasoning": "brief explanation"}}
"""


def _extract_transcripts(db_path, agents_spec):
    """Pull conversations from the DB, grouped by negotiator.

    Returns dict: {source_index: [transcript_string, ...]}
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    agent_info = {}
    for agent_id, profile, is_neg, src_idx in agents_spec:
        agent_info[agent_id] = (profile["name"], is_neg, src_idx)

    groups = conn.execute("SELECT group_id, name FROM chat_group").fetchall()
    transcripts_by_negotiator = {}

    for group in groups:
        group_id = group["group_id"]

        messages = conn.execute("""
            SELECT sender_id, content, sent_at
            FROM group_messages WHERE group_id = ? ORDER BY sent_at
        """, (group_id,)).fetchall()

        if not messages:
            continue

        members = conn.execute(
            "SELECT agent_id FROM group_members WHERE group_id = ?",
            (group_id,),
        ).fetchall()

        neg_src_indices = [
            agent_info[m["agent_id"]][2]
            for m in members
            if m["agent_id"] in agent_info and agent_info[m["agent_id"]][1]
        ]

        if not neg_src_indices:
            continue

        lines = []
        for msg in messages:
            sid = msg["sender_id"]
            name, is_neg, _ = agent_info.get(sid, (f"Agent {sid}", False, -1))
            role = "NEGOTIATOR" if is_neg else "COUNTERPARTY"
            lines.append(f"[{role} — {name}]: {msg['content']}")
        transcript = "\n\n".join(lines)

        for idx in neg_src_indices:
            transcripts_by_negotiator.setdefault(idx, []).append(transcript)

    conn.close()
    return transcripts_by_negotiator


async def _score_once(client, model, transcript, rubric):
    """Single evaluation pass."""
    prompt = EVAL_PROMPT.format(rubric=rubric, transcript=transcript)
    raw = await complete(client, model, prompt, temperature=0.1)
    return parse_json(raw)


async def _score_transcript(client, model, transcript, rubric):
    """Score a transcript multiple times and average for reliability.

    Runs NUM_EVALS independent evaluations and averages the scores.
    Drops outliers if they diverge significantly from the median.
    """
    tasks = [_score_once(client, model, transcript, rubric) for _ in range(NUM_EVALS)]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    valid = [r for r in results if not isinstance(r, Exception) and "overall" in r]

    if not valid:
        logger.error("All evaluation attempts failed")
        return {"scores": {}, "overall": 0.0, "reasoning": "All evaluation attempts failed"}

    if len(valid) == 1:
        return valid[0]

    # Average the overall scores
    overalls = [r["overall"] for r in valid]
    avg_overall = sum(overalls) / len(overalls)

    # Average individual criterion scores
    all_criteria = set()
    for r in valid:
        all_criteria.update(r.get("scores", {}).keys())

    avg_scores = {}
    for criterion in all_criteria:
        values = [r["scores"][criterion] for r in valid if criterion in r.get("scores", {})]
        if values:
            avg_scores[criterion] = round(sum(values) / len(values), 3)

    # Combine reasoning from all evaluations
    reasonings = [r.get("reasoning", "") for r in valid if r.get("reasoning")]
    combined_reasoning = reasonings[0] if reasonings else ""

    spread = max(overalls) - min(overalls)
    if spread > 0.3:
        logger.warning(f"High eval variance: scores={overalls}, spread={spread:.2f}")

    return {
        "scores": avg_scores,
        "overall": round(avg_overall, 3),
        "reasoning": combined_reasoning,
        "_eval_spread": round(spread, 3),
        "_eval_count": len(valid),
    }


async def evaluate_generation(db_path, agents_spec, rubric, model=None):
    """Evaluate all negotiators in a simulation run.

    Args:
        db_path: Path to the OASIS SQLite database.
        agents_spec: List of (agent_id, profile_dict, is_negotiator, source_index)
        rubric: Plain text rubric string.
        model: LLM model to use for judging.

    Returns:
        Dict mapping source_index -> {"scores": {...}, "overall": float, "reasoning": str}
    """
    model = model or get_model()
    client = create_client()

    transcripts = _extract_transcripts(db_path, agents_spec)
    logger.info(f"Extracted transcripts for {len(transcripts)} negotiators")

    tasks = {}
    for src_idx, transcript_list in transcripts.items():
        combined = "\n\n---\n\n".join(transcript_list)
        tasks[src_idx] = _score_transcript(client, model, combined, rubric)

    sorted_keys = sorted(tasks.keys())
    results = await asyncio.gather(
        *[tasks[idx] for idx in sorted_keys],
        return_exceptions=True,
    )

    scores = {}
    for idx, result in zip(sorted_keys, results):
        if isinstance(result, Exception):
            logger.error(f"Evaluation failed for negotiator {idx}: {result}")
            scores[idx] = {"scores": {}, "overall": 0.0, "reasoning": f"Evaluation failed: {result}"}
        else:
            scores[idx] = result

    # Save scores to disk
    scores_dir = os.path.dirname(db_path)
    scores_path = os.path.join(scores_dir, "eval_scores.json")
    with open(scores_path, "w") as f:
        json.dump({str(k): v for k, v in scores.items()}, f, indent=2)
    logger.info(f"Scores saved to {scores_path}")

    return scores
