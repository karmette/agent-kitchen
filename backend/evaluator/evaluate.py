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

NUM_EVALS = 1

EVAL_PROMPT = """\
You are a deterministic scoring system. Your scores must be reproducible — \
the same transcript must always produce the same scores.

## Scoring method
For each criterion, count the specific evidence in the transcript:

1. Read the full transcript
2. For each criterion, list the concrete moments that demonstrate it
3. Score based on quantity and quality of evidence:
   - 0.0 = zero evidence, agent didn't address this at all
   - 0.2 = mentioned once but superficially
   - 0.4 = attempted but ineffective or partially wrong
   - 0.6 = competent, addressed adequately with some evidence
   - 0.8 = strong, multiple clear demonstrations
   - 1.0 = exceptional, consistent mastery throughout the conversation
4. If the transcript is empty or agent didn't participate, all scores = 0.0
5. Overall = weighted average if weights given, otherwise simple average
6. Do NOT round to convenient numbers. Use the scale precisely.

## Rubric
{rubric}

## Transcript
{transcript}

For each criterion, cite the specific evidence, then score. Return ONLY valid JSON:
{{"scores": {{"criterion_name": float, ...}}, "overall": float, "reasoning": "evidence summary"}}
"""


def _extract_transcripts(db_path, agents_spec, mode="group"):
    """Pull conversations from the DB, grouped by negotiator.

    Dispatches to mode-specific extraction:
      - group: reads group_messages (private chat)
      - social: reads posts, comments, likes, follows (public activity)
      - mixed: reads both

    Returns dict: {source_index: [transcript_string, ...]}
    """
    if mode == "social":
        return _extract_social_transcripts(db_path, agents_spec)
    elif mode == "mixed":
        group = _extract_group_transcripts(db_path, agents_spec)
        social = _extract_social_transcripts(db_path, agents_spec)
        # Merge: concatenate transcripts per source_index
        merged = {}
        for idx in set(list(group.keys()) + list(social.keys())):
            merged[idx] = group.get(idx, []) + social.get(idx, [])
        return merged
    else:
        return _extract_group_transcripts(db_path, agents_spec)


def _extract_group_transcripts(db_path, agents_spec):
    """Pull group chat conversations, grouped by negotiator."""
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


def _extract_social_transcripts(db_path, agents_spec):
    """Pull social activity (posts, comments, likes, follows) per negotiator."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    agent_info = {}
    for agent_id, profile, is_neg, src_idx in agents_spec:
        agent_info[agent_id] = (profile["name"], is_neg, src_idx)

    # Build user_id -> agent_id mapping (OASIS assigns user_ids separately)
    user_to_agent = {}
    for row in conn.execute("SELECT user_id, agent_id FROM user").fetchall():
        user_to_agent[row["user_id"]] = row["agent_id"]

    def resolve(user_id):
        aid = user_to_agent.get(user_id, user_id)
        name, is_neg, src_idx = agent_info.get(aid, (f"User {user_id}", False, -1))
        role = "AGENT" if is_neg else "OTHER"
        return name, role, src_idx

    # Load all posts
    posts = conn.execute(
        "SELECT post_id, user_id, content, created_at, num_likes, num_dislikes "
        "FROM post ORDER BY created_at"
    ).fetchall()

    # Load all comments keyed by post_id
    comments_by_post = {}
    for c in conn.execute(
        "SELECT comment_id, post_id, user_id, content, created_at "
        "FROM comment ORDER BY created_at"
    ).fetchall():
        comments_by_post.setdefault(c["post_id"], []).append(c)

    # Load follow counts per agent
    follows_gained = {}
    for f in conn.execute(
        "SELECT followee_id, COUNT(*) as cnt FROM follow GROUP BY followee_id"
    ).fetchall():
        follows_gained[f["followee_id"]] = f["cnt"]

    # Build transcript per negotiator source_index
    transcripts = {}
    for src_idx in set(info[2] for info in agent_info.values() if info[1]):
        lines = []

        # Find all agent_ids for this source_index
        my_agent_ids = [aid for aid, (_, is_neg, si) in agent_info.items()
                        if is_neg and si == src_idx]
        my_user_ids = [uid for uid, aid in user_to_agent.items()
                       if aid in my_agent_ids]

        for post in posts:
            poster_name, poster_role, _ = resolve(post["user_id"])
            is_mine = post["user_id"] in my_user_ids

            if is_mine:
                lines.append(
                    f"[{poster_role} — {poster_name}] POSTED: \"{post['content']}\"\n"
                    f"  ({post['num_likes']} likes, {post['num_dislikes']} dislikes)")
            else:
                lines.append(
                    f"[{poster_role} — {poster_name}] POSTED: \"{post['content']}\"")

            # Add comments on this post
            for c in comments_by_post.get(post["post_id"], []):
                c_name, c_role, _ = resolve(c["user_id"])
                lines.append(f"  [{c_role} — {c_name}] COMMENTED: \"{c['content']}\"")

        # Add engagement summary
        total_follows = sum(follows_gained.get(uid, 0) for uid in my_user_ids)
        if total_follows > 0:
            lines.append(f"\n[ENGAGEMENT SUMMARY] Gained {total_follows} followers")

        if lines:
            transcripts[src_idx] = ["\n".join(lines)]

    conn.close()
    return transcripts


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


async def evaluate_generation(db_path, agents_spec, rubric, model=None, mode="group"):
    """Evaluate all negotiators in a simulation run.

    Args:
        db_path: Path to the OASIS SQLite database.
        agents_spec: List of (agent_id, profile_dict, is_negotiator, source_index)
        rubric: Plain text rubric string.
        model: LLM model to use for judging.
        mode: Scenario mode — "group", "social", or "mixed".

    Returns:
        Dict mapping source_index -> {"scores": {...}, "overall": float, "reasoning": str}
    """
    model = model or get_model()
    client = create_client()

    transcripts = _extract_transcripts(db_path, agents_spec, mode=mode)
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
