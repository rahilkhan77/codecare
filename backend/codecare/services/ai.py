from __future__ import annotations

import json
import re
from typing import Protocol

import httpx

from codecare.config import get_settings
from codecare.security import redact

JSON_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


class AIProvider(Protocol):
    name: str

    async def complete(self, system: str, user: str) -> str: ...


class HeuristicProvider:
    name = "heuristic"

    async def complete(self, system: str, user: str) -> str:
        payload = {
            "provider": self.name,
            "note": "No AI API key is configured. This is not model analysis and no patch was generated.",
            "system_task": system[:200],
            "input_excerpt": redact(user)[:1500],
        }
        return json.dumps(payload)


class GroqProvider:
    name = "groq"

    def __init__(self, api_key: str, model: str):
        self.api_key = api_key
        self.model = model

    async def complete(self, system: str, user: str) -> str:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    "temperature": 0.1,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                },
            )
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"]


class OpenAICompatibleProvider:
    name = "openai"

    def __init__(self, api_key: str, base_url: str, model: str):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model

    async def complete(self, system: str, user: str) -> str:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    "temperature": 0.1,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                },
            )
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"]


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str):
        self.api_key = api_key

    async def complete(self, system: str, user: str) -> str:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-3-haiku-20240307",
                    "max_tokens": 2000,
                    "system": system,
                    "messages": [{"role": "user", "content": user}],
                },
            )
            response.raise_for_status()
            return response.json()["content"][0]["text"]


def get_provider() -> AIProvider:
    settings = get_settings()
    preferred = settings.ai_provider.lower()
    if preferred in {"groq", "auto"} and settings.groq_api_key:
        return GroqProvider(settings.groq_api_key, settings.groq_model)
    if preferred in {"openai", "auto"} and settings.openai_api_key:
        return OpenAICompatibleProvider(settings.openai_api_key, settings.openai_base_url, settings.openai_model)
    if preferred in {"anthropic", "auto"} and settings.anthropic_api_key:
        return AnthropicProvider(settings.anthropic_api_key)
    return HeuristicProvider()


def is_heuristic(provider: AIProvider | None = None) -> bool:
    return isinstance(provider or get_provider(), HeuristicProvider)


async def complete_with_retry(provider: AIProvider, system: str, user: str, attempts: int = 3) -> str:
    last_error: Exception | None = None
    for _ in range(attempts):
        try:
            return await provider.complete(system, user)
        except Exception as exc:  # noqa: BLE001 — provider errors are retried then surfaced
            last_error = exc
    raise RuntimeError(f"AI provider '{provider.name}' failed after retries: {last_error}")


def parse_json_object(raw: str) -> dict | None:
    text = raw.strip()
    match = JSON_FENCE.search(text)
    if match:
        text = match.group(1)
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                data = json.loads(text[start : end + 1])
                return data if isinstance(data, dict) else None
            except json.JSONDecodeError:
                return None
        return None


DIAGNOSIS_SYSTEM = """You are CodeCare, an AI software maintenance engineer.
Analyze the issue and repository evidence. Return JSON with keys:
root_cause, impact, confidence (low|medium|high), recommended_tests, caution.
Never claim the diagnosis is verified. Never include secrets."""

FIX_SYSTEM = """You are CodeCare generating a proposed patch.
Return JSON: {"summary": "...", "files": [{"path": "...", "content": "full file content after change"}]}.
Follow existing style. Change as little as possible. If you cannot produce a safe patch, return {"summary":"insufficient context","files":[]}.
Never include secrets. The patch is a proposal only and is not verified."""


async def diagnose(issue_title: str, evidence: str, context: str) -> tuple[str, str, bool]:
    provider = get_provider()
    raw = await complete_with_retry(
        provider,
        DIAGNOSIS_SYSTEM,
        f"Issue: {issue_title}\nEvidence:\n{redact(evidence)}\nContext:\n{redact(context)[:8000]}",
    )
    parsed = parse_json_object(raw)
    if parsed:
        raw = json.dumps(parsed)
    return provider.name, redact(raw), not isinstance(provider, HeuristicProvider)


async def propose_fix(issue_title: str, evidence: str, files: dict[str, str]) -> tuple[str, str, dict[str, str], bool]:
    provider = get_provider()
    if isinstance(provider, HeuristicProvider):
        note = "No AI provider is configured, so CodeCare did not generate a patch. Configure GROQ_API_KEY, OPENAI_API_KEY, or ANTHROPIC_API_KEY."
        return provider.name, note, {}, False
    listing = "\n\n".join(f"FILE {path}\n{content}" for path, content in files.items())
    raw = await complete_with_retry(
        provider,
        FIX_SYSTEM,
        f"Issue: {issue_title}\nEvidence:\n{redact(evidence)}\n\n{redact(listing)[:12000]}",
    )
    files_out: dict[str, str] = {}
    parsed = parse_json_object(raw)
    summary = raw
    if parsed:
        summary = parsed.get("summary") or raw
        for item in parsed.get("files") or []:
            path = (item.get("path") or "").replace("\\", "/").lstrip("/")
            if path and ".." not in path.split("/") and item.get("content"):
                files_out[path] = item["content"]
    return provider.name, redact(str(summary)), files_out, True
