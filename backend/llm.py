"""Shared LLM client utilities.

Handles OpenAI-compatible APIs that may return SSE streams instead of
standard responses (e.g., TAMU chat-api).
"""

import json
import logging
import os

from openai import AsyncOpenAI
from dotenv import load_dotenv

load_dotenv(override=True)

logger = logging.getLogger(__name__)

MAX_RETRIES = 3


def create_client() -> AsyncOpenAI:
    """Create an AsyncOpenAI client using environment config."""
    base_url = os.getenv("OPENAI_API_BASE_URL") or os.getenv("OPENAI_BASE_URL")
    if base_url:
        return AsyncOpenAI(base_url=base_url)
    return AsyncOpenAI()


def get_model() -> str:
    """Get the default model from environment."""
    return os.getenv("MODEL", "gpt-4o")


def parse_response(response) -> str:
    """Extract text content from an LLM response.

    Handles both standard OpenAI responses and raw SSE streams
    returned by TAMU-style APIs.
    """
    if not isinstance(response, str):
        return response.choices[0].message.content.strip()

    content_parts = []
    for line in response.split("\n"):
        line = line.strip()
        if line.startswith("data: ") and line != "data: [DONE]":
            try:
                chunk = json.loads(line[6:])
                delta = chunk.get("choices", [{}])[0].get("delta", {})
                if "content" in delta and delta["content"]:
                    content_parts.append(delta["content"])
            except json.JSONDecodeError:
                continue
    return "".join(content_parts).strip()


def parse_json(raw: str) -> dict:
    """Parse JSON from LLM output, handling markdown code blocks."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        if "```" in raw:
            json_str = raw.split("```")[1]
            if json_str.startswith("json"):
                json_str = json_str[4:]
            return json.loads(json_str.strip())
        raise


async def complete(
    client: AsyncOpenAI,
    model: str,
    prompt: str,
    temperature: float = 0.7,
) -> str:
    """Make a chat completion call and return the text content.

    Retries on transient failures.
    """
    for attempt in range(MAX_RETRIES):
        try:
            response = await client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature,
            )
            return parse_response(response)
        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                logger.warning(f"LLM call failed (attempt {attempt + 1}): {e}")
                continue
            raise


async def complete_json(
    client: AsyncOpenAI,
    model: str,
    prompt: str,
    temperature: float = 0.7,
) -> dict:
    """Make a chat completion call and parse JSON from the response."""
    raw = await complete(client, model, prompt, temperature)
    return parse_json(raw)
