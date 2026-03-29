"""JSONL event emitter for real-time TUI streaming.

Emits structured events to stdout. One JSON object per line.
All human-readable output goes to stderr via logging.

The DB poller watches group_messages, posts, and comments during a
simulation and emits new activity as it's committed by OASIS agents.
"""

import asyncio
import json
import sqlite3
import os
import sys
import threading

# Protect stdout writes from concurrent async tasks interleaving lines
_stdout_lock = threading.Lock()

# Direct file descriptor to real stdout — bypasses sys.stdout redirection
# so OASIS's stray prints to sys.stdout (redirected to stderr) don't
# corrupt our JSONL stream
_real_stdout_fd = None


def init_emit():
    """Call once at startup to capture the real stdout fd before redirection."""
    global _real_stdout_fd
    _real_stdout_fd = os.fdopen(os.dup(sys.stdout.fileno()), "w")


def emit(event: dict):
    """Write a JSON event to the real stdout, one per line."""
    line = json.dumps(event) + "\n"
    out = _real_stdout_fd or sys.stdout
    try:
        with _stdout_lock:
            out.write(line)
            out.flush()
    except BrokenPipeError:
        pass


async def poll_activity(db_path: str, agents_spec: list, stop_event: asyncio.Event,
                        generation: int = 0, scenario_id: int = 0, mode: str = "group"):
    """Poll the DB for new activity and emit events in real time.

    Supports three modes:
      - group: polls group_messages only
      - social: polls posts, comments, follows
      - mixed: polls everything
    """
    # Build agent lookup
    agent_info = {}
    for agent_id, profile, is_neg, src_idx in agents_spec:
        agent_info[agent_id] = {
            "name": profile["name"],
            "role": "negotiator" if is_neg else "counterparty",
        }

    # Build user_id -> agent_id mapping (populated lazily)
    user_to_agent = {}

    last_message_id = 0
    last_post_id = 0
    last_comment_id = 0
    last_follow_id = 0

    poll_groups = mode in ("group", "mixed")
    poll_social = mode in ("social", "mixed")

    while not stop_event.is_set():
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row

            # Refresh user_id -> agent_id mapping
            for row in conn.execute("SELECT user_id, agent_id FROM user").fetchall():
                user_to_agent[row["user_id"]] = row["agent_id"]

            def resolve(user_id):
                aid = user_to_agent.get(user_id, user_id)
                info = agent_info.get(aid, {"name": f"User {user_id}", "role": "unknown"})
                return info["name"], info["role"]

            # ── Group messages ──
            if poll_groups:
                rows = conn.execute(
                    "SELECT message_id, group_id, sender_id, content, sent_at "
                    "FROM group_messages WHERE message_id > ? ORDER BY message_id",
                    (last_message_id,),
                ).fetchall()

                for row in rows:
                    last_message_id = row["message_id"]
                    name, role = resolve(row["sender_id"])
                    emit({
                        "type": "message",
                        "generation": generation,
                        "scenario_id": scenario_id,
                        "message_id": row["message_id"],
                        "group_id": row["group_id"],
                        "sender": name,
                        "role": role,
                        "content": row["content"],
                    })

            # ── Posts ──
            if poll_social:
                rows = conn.execute(
                    "SELECT post_id, user_id, content, created_at, "
                    "num_likes, num_dislikes "
                    "FROM post WHERE post_id > ? ORDER BY post_id",
                    (last_post_id,),
                ).fetchall()

                for row in rows:
                    last_post_id = row["post_id"]
                    name, role = resolve(row["user_id"])
                    emit({
                        "type": "post",
                        "generation": generation,
                        "scenario_id": scenario_id,
                        "post_id": row["post_id"],
                        "sender": name,
                        "role": role,
                        "content": row["content"] or "",
                        "likes": row["num_likes"],
                        "dislikes": row["num_dislikes"],
                    })

                # ── Comments ──
                rows = conn.execute(
                    "SELECT comment_id, post_id, user_id, content, created_at "
                    "FROM comment WHERE comment_id > ? ORDER BY comment_id",
                    (last_comment_id,),
                ).fetchall()

                for row in rows:
                    last_comment_id = row["comment_id"]
                    name, role = resolve(row["user_id"])
                    emit({
                        "type": "comment",
                        "generation": generation,
                        "scenario_id": scenario_id,
                        "comment_id": row["comment_id"],
                        "post_id": row["post_id"],
                        "sender": name,
                        "role": role,
                        "content": row["content"] or "",
                    })

                # ── Follows ──
                rows = conn.execute(
                    "SELECT follow_id, follower_id, followee_id "
                    "FROM follow WHERE follow_id > ? ORDER BY follow_id",
                    (last_follow_id,),
                ).fetchall()

                for row in rows:
                    last_follow_id = row["follow_id"]
                    follower_name, _ = resolve(row["follower_id"])
                    followee_name, _ = resolve(row["followee_id"])
                    emit({
                        "type": "follow",
                        "generation": generation,
                        "scenario_id": scenario_id,
                        "follower": follower_name,
                        "followee": followee_name,
                    })

            conn.close()

        except sqlite3.OperationalError:
            pass

        await asyncio.sleep(0.5)


# Backward-compatible alias
async def poll_messages(db_path, agents_spec, stop_event, generation=0, scenario_id=0):
    await poll_activity(db_path, agents_spec, stop_event, generation, scenario_id, mode="group")
