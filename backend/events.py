"""JSONL event emitter for real-time TUI streaming.

Emits structured events to stdout. One JSON object per line.
All human-readable output goes to stderr via logging.

The DB poller watches group_messages during a simulation and emits
new messages as they're committed by OASIS agents.
"""

import asyncio
import json
import sqlite3
import sys


def emit(event: dict):
    """Write a JSON event to stdout, one per line."""
    sys.stdout.write(json.dumps(event) + "\n")
    sys.stdout.flush()


async def poll_messages(db_path: str, agents_spec: list, stop_event: asyncio.Event,
                        generation: int = 0, scenario_id: int = 0):
    """Poll the DB for new messages and emit them in real time.

    Runs concurrently with the simulation. Checks for new group_messages
    every 500ms and emits each as a JSONL event.

    Args:
        db_path: Path to the OASIS SQLite database.
        agents_spec: List of (agent_id, profile_dict, is_negotiator, source_index).
        stop_event: Set this to stop polling.
        generation: Current generation number for event metadata.
    """
    # Build agent lookup
    agent_info = {}
    for agent_id, profile, is_neg, src_idx in agents_spec:
        agent_info[agent_id] = {
            "name": profile["name"],
            "role": "negotiator" if is_neg else "counterparty",
            "genome_id": profile.get("username", f"agent_{agent_id}"),
        }

    # Build group lookup (populated as groups appear)
    group_names = {}

    last_message_id = 0

    while not stop_event.is_set():
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row

            # Refresh group names
            for row in conn.execute("SELECT group_id, name FROM chat_group").fetchall():
                group_names[row["group_id"]] = row["name"]

            # Fetch new messages
            rows = conn.execute(
                "SELECT message_id, group_id, sender_id, content, sent_at "
                "FROM group_messages WHERE message_id > ? ORDER BY message_id",
                (last_message_id,),
            ).fetchall()

            conn.close()

            for row in rows:
                last_message_id = row["message_id"]
                sender = agent_info.get(row["sender_id"], {})
                emit({
                    "type": "message",
                    "generation": generation,
                    "scenario_id": scenario_id,
                    "message_id": row["message_id"],
                    "group": group_names.get(row["group_id"], f"group_{row['group_id']}"),
                    "group_id": row["group_id"],
                    "sender": sender.get("name", f"Agent {row['sender_id']}"),
                    "role": sender.get("role", "unknown"),
                    "content": row["content"],
                    "timestamp": row["sent_at"],
                })

        except sqlite3.OperationalError:
            # DB might be locked momentarily, that's fine
            pass

        await asyncio.sleep(0.5)
