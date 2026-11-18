"""Minimal client for the local OpenAI-compatible LLM endpoint.

Points at Nexus's Ollama backend by default (http://127.0.0.1:11434/v1), which
needs no auth. The Nexus gateway itself (http://localhost:3000/v1 with a
LAG_API_KEY) speaks the same protocol and can be swapped in via LLM_BASE_URL
once its LLM Chat route is enabled again.

This is only ever called while pre-rendering variants. The exhibit loop never
touches it, so a cold GPU or a down gateway cannot interrupt a show.
"""

from __future__ import annotations

import json
import re

import httpx

# Ollama cold-loads 26-44 GB across two cards on the first request, which can
# take a couple of minutes. Nexus itself allows 600s; match that.
DEFAULT_TIMEOUT = 600.0

# Qwen3.6 is a reasoning model: it spends most of its budget in a `reasoning`
# field before writing a single character of `content`. Observed runs here spent
# 20k+ characters thinking, and a 4k cap returned an EMPTY content with
# finish_reason "length" -- silently, since `content` is simply "". Budget
# generously for the thinking, not just for the answer.
DEFAULT_MAX_TOKENS = 32000

# How long Ollama should keep the model resident between our requests.
# Nexus itself sends keep_alive=-1, which pins ~30 GB in VRAM forever; on a
# shared research box that is rude, so we do NOT do that. But the default on
# this host evicts the model within about a minute, and a generation run makes
# up to seven requests -- each one then paying a full 32 GB cold load, turning
# a three-minute job into twenty. A bounded window is the middle ground: the
# model stays up for the run, then releases itself.
DEFAULT_KEEP_ALIVE = "5m"

# Reasoning models wrap their scratchpad in <think>...</think>. Strip it before
# looking for JSON.
_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_CODE_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.MULTILINE)


class LLMError(RuntimeError):
    pass


def chat(
    messages: list[dict[str, str]],
    *,
    base_url: str,
    model: str,
    api_key: str = "ollama",
    temperature: float = 0.8,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    timeout: float = DEFAULT_TIMEOUT,
    keep_alive: str = DEFAULT_KEEP_ALIVE,
) -> str:
    """One non-streaming completion. Returns the assistant message content."""
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
        # Ignored by an OpenAI-compatible server that does not know it; Ollama
        # reads it and holds the model for that long instead of evicting it
        # between our requests.
        "keep_alive": keep_alive,
    }
    try:
        response = httpx.post(
            f"{base_url.rstrip('/')}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=timeout,
        )
    except httpx.HTTPError as exc:
        raise LLMError(f"could not reach {base_url}: {exc}") from exc

    if response.status_code >= 400:
        raise LLMError(f"HTTP {response.status_code} from {base_url}: {response.text[:400]}")

    try:
        data = response.json()
        choice = data["choices"][0]
        content = choice["message"].get("content") or ""
    except (KeyError, IndexError, ValueError) as exc:
        raise LLMError(f"unexpected response shape: {response.text[:400]}") from exc

    if not content.strip():
        # A reasoning model that hit the cap mid-thought answers with an empty
        # content and everything it was thinking in `reasoning`. Say so plainly
        # rather than letting the caller puzzle over an empty reply.
        reason = choice.get("finish_reason")
        thought = len(choice["message"].get("reasoning") or "")
        raise LLMError(
            f"empty content (finish_reason={reason}, {thought} chars of reasoning, "
            f"max_tokens={max_tokens}). Raise max_tokens if this is 'length'."
        )

    return content


def strip_reasoning(text: str) -> str:
    """Remove <think> blocks and code fences a reasoning model may emit."""
    return _CODE_FENCE.sub("", _THINK_BLOCK.sub("", text)).strip()


def extract_json_object(text: str) -> dict:
    """Pull the first balanced JSON object out of a model reply.

    Models drift: they add a preamble, wrap the JSON in a fence, or think out
    loud first. Rather than trusting the reply to be clean, scan for the first
    `{` and walk forward to its matching `}` (ignoring braces inside strings).
    """
    cleaned = strip_reasoning(text)

    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except ValueError:
        pass

    start = cleaned.find("{")
    if start == -1:
        raise LLMError(f"no JSON object in reply: {cleaned[:300]}")

    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(cleaned)):
        char = cleaned[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                candidate = cleaned[start : index + 1]
                try:
                    return json.loads(candidate)
                except ValueError as exc:
                    raise LLMError(f"malformed JSON object: {candidate[:300]}") from exc

    raise LLMError(f"unterminated JSON object in reply: {cleaned[:300]}")
