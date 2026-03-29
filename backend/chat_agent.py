"""Custom SocialAgent subclasses for focused interactions.

OASIS's default perform_action_by_llm sends a "social media" prompt
that's too generic for our use case. These overrides give clean,
focused prompts:

- GroupChatAgent: for private group chat (negotiation, interview, etc.)
- SocialMediaAgent: for public posts/comments (marketing, debate, etc.)
"""

import sqlite3
import logging
import sys

from camel.messages import BaseMessage
from oasis.social_agent.agent import SocialAgent

if "sphinx" not in sys.modules:
    agent_log = logging.getLogger(name="social.agent")


class GroupChatAgent(SocialAgent):
    """SocialAgent with a conversation-focused prompt for group chat."""

    db_path: str = None

    async def perform_action_by_llm(self):
        agent_id = self.social_agent_id
        db_path = self.db_path
        if not db_path:
            return await super().perform_action_by_llm()

        conversation_lines = []
        group_id = None
        my_name = None

        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row

            me = conn.execute(
                "SELECT name FROM user WHERE agent_id = ?", (agent_id,)
            ).fetchone()
            my_name = me["name"] if me else f"Agent {agent_id}"

            groups = conn.execute(
                "SELECT group_id FROM group_members WHERE agent_id = ?",
                (agent_id,),
            ).fetchall()

            for g in groups:
                group_id = g["group_id"]
                messages = conn.execute(
                    "SELECT sender_id, content FROM group_messages "
                    "WHERE group_id = ? ORDER BY sent_at",
                    (group_id,),
                ).fetchall()

                for msg in messages:
                    sender_id = msg["sender_id"]
                    name_row = conn.execute(
                        "SELECT name FROM user WHERE agent_id = ?",
                        (sender_id,),
                    ).fetchone()
                    name = name_row["name"] if name_row else f"Agent {sender_id}"
                    tag = " (you)" if sender_id == agent_id else ""
                    conversation_lines.append(f"{name}{tag}: {msg['content']}")

            conn.close()
        except Exception as e:
            agent_log.error(f"Agent {agent_id} DB read error: {e}")

        if conversation_lines:
            chat_history = "\n".join(conversation_lines)
            prompt = (
                f"Conversation so far:\n\n{chat_history}\n\n"
                f"You are {my_name}. Respond naturally to advance the conversation. "
                f"Do not repeat anything already said.\n\n"
                f"You MUST respond by calling exactly one function:\n"
                f"- send_to_group(group_id={group_id}, message=\"your reply\")\n"
                f"- do_nothing() if the conversation is over\n\n"
                f"Do NOT write a message as text. You MUST use a function call."
            )
        else:
            prompt = (
                f"You are {my_name}. Start the conversation with a natural opening.\n\n"
                f"You MUST respond by calling: send_to_group(group_id={group_id}, message=\"your message\")\n"
                f"Do NOT write a message as text. You MUST use a function call."
            )

        user_msg = BaseMessage.make_user_message(
            role_name="User", content=prompt)

        try:
            response = await self.astep(user_msg)

            if response.info.get("tool_calls"):
                return response

            text = response.msg.content if response.msg else ""
            if text and group_id is not None:
                # LLM didn't use a function call — extract just the message
                text = text.strip().strip('"').strip("'")
                # Discard if it contains prompt echoing
                if "function call" not in text.lower() and "send_to_group" not in text:
                    if len(text) > 10:
                        await self.env.action.send_to_group(group_id, text)

            return response

        except Exception as e:
            agent_log.error(f"Agent {agent_id} error: {e}")
            return e


class SocialMediaAgent(SocialAgent):
    """SocialAgent with a focused prompt for public social media interaction."""

    db_path: str = None

    async def perform_action_by_llm(self):
        agent_id = self.social_agent_id
        db_path = self.db_path
        if not db_path:
            return await super().perform_action_by_llm()

        my_name = None
        posts = []
        my_posts = []

        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row

            # My name
            me = conn.execute(
                "SELECT name FROM user WHERE agent_id = ?", (agent_id,)
            ).fetchone()
            my_name = me["name"] if me else f"Agent {agent_id}"

            # My user_id
            me_user = conn.execute(
                "SELECT user_id FROM user WHERE agent_id = ?", (agent_id,)
            ).fetchone()
            my_user_id = me_user["user_id"] if me_user else -1

            # All posts with author names
            for p in conn.execute(
                "SELECT p.post_id, p.user_id, p.content, p.num_likes, p.num_dislikes, "
                "u.name as author_name "
                "FROM post p JOIN user u ON p.user_id = u.user_id "
                "ORDER BY p.created_at"
            ).fetchall():
                post_info = {
                    "id": p["post_id"],
                    "author": p["author_name"],
                    "content": p["content"] or "",
                    "likes": p["num_likes"],
                    "dislikes": p["num_dislikes"],
                    "is_mine": p["user_id"] == my_user_id,
                    "comments": [],
                }
                # Comments on this post
                for c in conn.execute(
                    "SELECT c.user_id, c.content, u.name as author_name "
                    "FROM comment c JOIN user u ON c.user_id = u.user_id "
                    "WHERE c.post_id = ? ORDER BY c.created_at",
                    (p["post_id"],)
                ).fetchall():
                    post_info["comments"].append({
                        "author": c["author_name"],
                        "content": c["content"] or "",
                        "is_mine": c["user_id"] == my_user_id,
                    })
                posts.append(post_info)
                if post_info["is_mine"]:
                    my_posts.append(post_info)

            conn.close()
        except Exception as e:
            agent_log.error(f"Agent {agent_id} DB read error: {e}")

        # Build a readable feed
        feed_lines = []
        for p in posts:
            mine_tag = " (you)" if p["is_mine"] else ""
            feed_lines.append(
                f"POST by {p['author']}{mine_tag}: {p['content']}"
                f" [{p['likes']} likes, {p['dislikes']} dislikes]"
            )
            for c in p["comments"]:
                c_tag = " (you)" if c["is_mine"] else ""
                feed_lines.append(f"  REPLY by {c['author']}{c_tag}: {c['content']}")

        feed = "\n".join(feed_lines) if feed_lines else "(no posts yet)"

        # Count what I've done
        my_post_count = len(my_posts)
        my_comment_count = sum(
            1 for p in posts for c in p["comments"] if c["is_mine"]
        )

        prompt = (
            f"You are {my_name} on a social media platform.\n\n"
            f"Current feed:\n\n{feed}\n\n"
            f"Your activity so far: {my_post_count} posts, {my_comment_count} comments.\n\n"
            f"You MUST respond by calling exactly one function:\n"
            f"- create_post(content=\"...\") — write a new post\n"
            f"- create_comment(post_id=N, content=\"...\") — reply to a post\n"
            f"- like_post(post_id=N) — like a post\n"
            f"- do_nothing() — if you have nothing to add\n\n"
            f"Be original. Don't repeat what's already been said. "
            f"Do NOT write a message as text. You MUST use a function call."
        )

        user_msg = BaseMessage.make_user_message(
            role_name="User", content=prompt)

        try:
            response = await self.astep(user_msg)

            if response.info.get("tool_calls"):
                return response

            # Fallback: LLM didn't use a function call
            text = response.msg.content if response.msg else ""
            if text:
                text = text.strip().strip('"').strip("'")
                if "function call" not in text.lower() and "create_post" not in text:
                    if len(text) > 10:
                        await self.env.action.create_post(text)

            return response

        except Exception as e:
            agent_log.error(f"Agent {agent_id} error: {e}")
            return e
