"""Provider-neutral LLM interface with Anthropic (official SDK) and OpenAI-compatible adapters.

Neutral message format:
    {"role": "user", "content": str}
    {"role": "assistant", "content": str, "tool_calls": [{"id", "name", "arguments": dict}], "raw": <provider blocks>}
    {"role": "tool", "tool_call_id": str, "content": str}

`raw` carries the provider's exact assistant content (including thinking blocks) so multi-turn and
tool-loop history is replayed append-only and unchanged when the same provider and model are used.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

import anthropic
import httpx

from app.config import Settings, get_settings

# USD per 1M tokens (input, output). Unknown models record cost as None rather than guessing.
PRICES: dict[str, tuple[float, float]] = {
    "claude-fable-5-1": (10.0, 50.0),
    "claude-fable-5": (10.0, 50.0),
    "claude-opus-5-5": (4.0, 20.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-haiku-4-5": (1.0, 5.0),
}
# Models that support the server-side refusal fallback in its "default" form.
_FALLBACK_MODELS = {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}


class AIUnavailable(RuntimeError):
    """No provider configured — callers must surface this state, never substitute canned output."""


class LLMError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class LLMRefusal(LLMError):
    pass


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]


@dataclass
class LLMResult:
    text: str
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    stop_reason: str | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    raw_assistant: Any = None
    data: Any = None  # parsed JSON when a schema was requested

    @property
    def cost_usd(self) -> float | None:
        """Exact list price when known, else None (see `budget_cost_usd` for budgeting)."""
        price = PRICES.get(self.model)
        if not price:
            return None
        return round(self.input_tokens / 1e6 * price[0] + self.output_tokens / 1e6 * price[1], 6)

    @property
    def budget_cost_usd(self) -> tuple[float, bool]:
        """(cost, estimated). Unknown models are charged at the highest known rate so a model without a
        price entry can never bypass the daily budget."""
        if self.cost_usd is not None:
            return self.cost_usd, False
        hi_in, hi_out = max(p[0] for p in PRICES.values()), max(p[1] for p in PRICES.values())
        return round(self.input_tokens / 1e6 * hi_in + self.output_tokens / 1e6 * hi_out, 6), True

    def as_assistant_message(self) -> dict[str, Any]:
        return {
            "role": "assistant",
            "content": self.text,
            "tool_calls": self.tool_calls,
            "raw": self.raw_assistant,
            "provider": self.provider,
            "model": self.model,
        }


class LLMProvider(Protocol):
    name: str
    model: str

    def complete(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        json_schema: dict | None = None,
        tools: list[ToolSpec] | None = None,
        max_tokens: int | None = None,
    ) -> LLMResult: ...


def _parse_json(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMError("The model returned malformed JSON.", retryable=True) from exc


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, settings: Settings, model: str, http_client: Any = None) -> None:
        self.model = model
        self.effort = settings.llm_effort
        self.max_tokens = settings.llm_max_output_tokens
        self.client = anthropic.Anthropic(
            api_key=settings.llm_api_key,
            timeout=settings.llm_timeout_seconds,
            max_retries=2,
            **({"http_client": http_client} if http_client is not None else {}),
        )

    def _messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for m in messages:
            if m["role"] == "user":
                out.append({"role": "user", "content": m["content"]})
            elif m["role"] == "assistant":
                same = m.get("provider") == self.name and m.get("model") == self.model and m.get("raw")
                if same:
                    out.append({"role": "assistant", "content": m["raw"]})
                else:
                    blocks: list[dict[str, Any]] = []
                    if m.get("content"):
                        blocks.append({"type": "text", "text": m["content"]})
                    for tc in m.get("tool_calls") or []:
                        blocks.append(
                            {"type": "tool_use", "id": tc["id"], "name": tc["name"], "input": tc["arguments"]}
                        )
                    out.append({"role": "assistant", "content": blocks or m.get("content", "")})
            elif m["role"] == "tool":
                block = {
                    "type": "tool_result",
                    "tool_use_id": m["tool_call_id"],
                    "content": m["content"],
                    **({"is_error": True} if m.get("is_error") else {}),
                }
                # All results for one assistant turn go back in a single user message.
                if (
                    out
                    and out[-1]["role"] == "user"
                    and isinstance(out[-1]["content"], list)
                    and out[-1]["content"]
                    and out[-1]["content"][0].get("type") == "tool_result"
                ):
                    out[-1]["content"].append(block)
                else:
                    out.append({"role": "user", "content": [block]})
        return out

    def complete(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        json_schema: dict | None = None,
        tools: list[ToolSpec] | None = None,
        max_tokens: int | None = None,
    ) -> LLMResult:
        output_config: dict[str, Any] = {"effort": self.effort}
        if json_schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": json_schema}
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens or self.max_tokens,
            "system": system,
            "messages": self._messages(messages),
            "output_config": output_config,
        }
        if tools:
            kwargs["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.parameters} for t in tools
            ]
            kwargs["tool_choice"] = {"type": "auto"}
        started = time.monotonic()
        try:
            if self.model in _FALLBACK_MODELS:
                resp = self.client.beta.messages.create(
                    betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs
                )
            else:
                resp = self.client.messages.create(**kwargs)
        except anthropic.RateLimitError as exc:
            raise LLMError("The AI provider is rate limiting requests.", retryable=True) from exc
        except anthropic.APIStatusError as exc:
            raise LLMError(f"AI provider error ({exc.status_code}).", retryable=exc.status_code >= 500) from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError("Could not reach the AI provider.", retryable=True) from exc

        latency = int((time.monotonic() - started) * 1000)
        if resp.stop_reason == "refusal":
            raise LLMRefusal("The AI model declined this request.")
        text = "".join(b.text for b in resp.content if b.type == "text")
        tool_calls = [
            {"id": b.id, "name": b.name, "arguments": dict(b.input)} for b in resp.content if b.type == "tool_use"
        ]
        if resp.stop_reason == "max_tokens" and json_schema is not None:
            raise LLMError("The AI response was cut off before completing.", retryable=False)
        result = LLMResult(
            text=text,
            provider=self.name,
            model=resp.model or self.model,
            input_tokens=resp.usage.input_tokens,
            output_tokens=resp.usage.output_tokens,
            latency_ms=latency,
            stop_reason=resp.stop_reason,
            tool_calls=tool_calls,
            raw_assistant=[b.model_dump(exclude_none=True) for b in resp.content],
        )
        if json_schema is not None and not tool_calls:
            result.data = _parse_json(text)
        return result


class OpenAICompatibleProvider:
    """Chat Completions over HTTP — works with OpenAI and any compatible endpoint (via base URL)."""

    name = "openai"

    def __init__(self, settings: Settings, model: str) -> None:
        self.model = model
        self.max_tokens = settings.llm_max_output_tokens
        self.base_url = (settings.llm_base_url or "https://api.openai.com/v1").rstrip("/")
        self.http = httpx.Client(
            timeout=settings.llm_timeout_seconds, headers={"Authorization": f"Bearer {settings.llm_api_key}"}
        )

    def _messages(self, system: str, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = [{"role": "system", "content": system}]
        for m in messages:
            if m["role"] == "assistant":
                msg: dict[str, Any] = {"role": "assistant", "content": m.get("content") or None}
                if m.get("tool_calls"):
                    msg["tool_calls"] = [
                        {
                            "id": tc["id"],
                            "type": "function",
                            "function": {"name": tc["name"], "arguments": json.dumps(tc["arguments"])},
                        }
                        for tc in m["tool_calls"]
                    ]
                out.append(msg)
            elif m["role"] == "tool":
                out.append({"role": "tool", "tool_call_id": m["tool_call_id"], "content": m["content"]})
            else:
                out.append({"role": "user", "content": m["content"]})
        return out

    def complete(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        json_schema: dict | None = None,
        tools: list[ToolSpec] | None = None,
        max_tokens: int | None = None,
    ) -> LLMResult:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": self._messages(system, messages),
            "max_completion_tokens": max_tokens or self.max_tokens,
        }
        if json_schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "result", "schema": json_schema, "strict": True},
            }
        if tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {"name": t.name, "description": t.description, "parameters": t.parameters},
                }
                for t in tools
            ]
        started = time.monotonic()
        data = None
        for attempt in range(3):
            try:
                r = self.http.post(f"{self.base_url}/chat/completions", json=body)
            except httpx.HTTPError as exc:
                if attempt == 2:
                    raise LLMError("Could not reach the AI provider.", retryable=True) from exc
                time.sleep(2**attempt)
                continue
            if r.status_code == 429 or r.status_code >= 500:
                if attempt == 2:
                    raise LLMError(f"AI provider error ({r.status_code}).", retryable=True)
                time.sleep(2**attempt)
                continue
            if r.status_code >= 400:
                raise LLMError(f"AI provider rejected the request ({r.status_code}).")
            data = r.json()
            break
        assert data is not None
        choice = data["choices"][0]
        msg = choice["message"]
        if msg.get("refusal"):
            raise LLMRefusal("The AI model declined this request.")
        tool_calls = []
        for tc in msg.get("tool_calls") or []:
            try:
                args = json.loads(tc["function"]["arguments"] or "{}")
            except json.JSONDecodeError:
                args = {}
            tool_calls.append({"id": tc["id"], "name": tc["function"]["name"], "arguments": args})
        usage = data.get("usage") or {}
        result = LLMResult(
            text=msg.get("content") or "",
            provider=self.name,
            model=data.get("model", self.model),
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            latency_ms=int((time.monotonic() - started) * 1000),
            stop_reason=choice.get("finish_reason"),
            tool_calls=tool_calls,
        )
        if json_schema is not None and not tool_calls:
            result.data = _parse_json(result.text)
        return result


def build_provider(model_override: str | None = None) -> LLMProvider:
    s = get_settings()
    if not s.ai_configured:
        raise AIUnavailable("No AI provider is configured. Set JOBPILOT_LLM_PROVIDER, _MODEL and _API_KEY.")
    model = model_override or s.llm_model
    if s.llm_provider == "anthropic":
        return AnthropicProvider(s, model)
    return OpenAICompatibleProvider(s, model)
