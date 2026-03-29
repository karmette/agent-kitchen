"""Custom SocialAgent subclass for focused group chat conversations.

OASIS's default `perform_action_by_llm` sends a confusing "social media"
prompt that includes posts, followers, and "pick one you want to perform
action" instructions. This is designed for Reddit/Twitter simulation,
not structured group chat.

ChatSocialAgent overrides that method to:
1. Only show group messages (no posts/followers noise)
2. Always send a message (no wasted listen/do-nothing turns)
3. Frame the interaction as a conversation, not social media
4. Tell the LLM the exact group_id so it doesn't guess
"""

import sqlite3
import logging
import sys

from camel.messages import BaseMessage

from oasis.social_agent.agent import SocialAgent

if "sphinx" not in sys.modules:
    agent_log = logging.getLogger(name="social.agent")


class ChatSocialAgent(SocialAgent):
    """SocialAgent that focuses purely on group chat conversation.

    Set `self.db_path` after construction (before simulation starts).
    """

    db_path: str = None

    async def perform_action_by_llm(self):
        """Override: build a conversation-focused prompt instead of
        OASIS's default social-media prompt."""

        agent_id = self.social_agent_id
        db_path = self.db_path
        if not db_path:
            agent_log.error(f"Agent {agent_id}: no db_path set")
            return await super().perform_action_by_llm()

        # Read conversation history and find our group
        conversation_lines = []
        group_id = None
        my_name = None

        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row

            # Get our name
            me = conn.execute(
                "SELECT name FROM user WHERE agent_id = ?", (agent_id,)
            ).fetchone()
            my_name = me["name"] if me else f"Agent {agent_id}"

            # Find which group this agent is in
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

        # Build the prompt
        if conversation_lines:
            chat_history = "\n".join(conversation_lines)
            prompt = (
                f"Here is the conversation so far:\n\n"
                f"{chat_history}\n\n"
                f"It's your turn to speak as {my_name}. "
                f"Reply to what the other person just said. "
                f"Do NOT repeat anything you've already said. "
                f"Advance the conversation.\n\n"
                f"Call send_to_group with group_id={group_id} and your message."
            )
        else:
            prompt = (
                f"You are {my_name}. The conversation is just starting. "
                f"Open with a natural first message — introduce yourself "
                f"and get the discussion going.\n\n"
                f"Call send_to_group with group_id={group_id} and your message."
            )

        user_msg = BaseMessage.make_user_message(
            role_name="User", content=prompt)

        try:
            agent_log.info(
                f"Agent {agent_id} chat prompt: {prompt[:300]}...")
            response = await self.astep(user_msg)

            if response.info.get("tool_calls"):
                for tool_call in response.info["tool_calls"]:
                    agent_log.info(
                        f"Agent {agent_id} action: {tool_call.tool_name} "
                        f"args: {tool_call.args}")
                return response

            # If the LLM responded with text instead of a tool call,
            # force it into send_to_group
            text = response.msg.content if response.msg else ""
            if text and group_id is not None:
                # Strip wrapping quotes the LLM sometimes adds
                text = text.strip().strip('"').strip("'")
                agent_log.info(
                    f"Agent {agent_id} text fallback -> send_to_group({group_id})")
                await self.env.action.send_to_group(group_id, text)

            return response

        except Exception as e:
            agent_log.error(f"Agent {agent_id} error: {e}")
            return e
