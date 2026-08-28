"""Ollama-backed classifier for NewsPilot's three intent classes."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

VALID_INTENTS = frozenset(("list", "qa", "unknown"))


@dataclass(frozen=True)
class IntentPrediction:
    """Normalized output returned by the local intent model."""

    intent: str
    confidence: float | None
    source: str = "ollama"


class OllamaIntentClassifier:
    """Call a local Ollama model and normalize its JSON response."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:11434",
        model: str = "newpilot-intent",
        timeout: float = 5.0,
        threshold: float = 0.75,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.threshold = threshold

    def predict(self, query: str) -> IntentPrediction:
        """Return a safe prediction; transport or parsing errors become unknown."""
        if not query.strip():
            return IntentPrediction("unknown", 0.0, "empty")

        payload = {
            "model": self.model,
            "prompt": self._build_prompt(query),
            "stream": False,
            "format": "json",
            "options": {"temperature": 0, "num_predict": 48},
        }
        try:
            response = httpx.post(
                f"{self.base_url}/api/generate",
                json=payload,
                timeout=self.timeout,
            )
            response.raise_for_status()
            body = response.json()
            return self._parse_response(body.get("response", ""))
        except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
            logger.warning("Ollama intent classification failed: %s", exc)
            return IntentPrediction("unknown", None, "ollama_error")

    def _build_prompt(self, query: str) -> str:
        return (
            "判断下面用户消息的意图，只能选择 list、qa、unknown。\n"
            "list：查询 AI 新闻、最新动态或新闻列表。\n"
            "qa：针对 AI 新闻或新闻内容提问、解释、分析。\n"
            "unknown：与 AI 新闻无关，或无法可靠判断。\n"
            "只输出一个 JSON 对象，不要输出解释："
            '{"intent":"list|qa|unknown","confidence":0.0}\n'
            f"用户消息：{query}"
        )

    def _parse_response(self, content: str) -> IntentPrediction:
        matches = re.findall(r"\{[^{}]*\}", content)
        for candidate in matches:
            try:
                value = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            intent = value.get("intent")
            if intent not in VALID_INTENTS:
                continue
            try:
                confidence = float(value.get("confidence"))
            except (TypeError, ValueError):
                return IntentPrediction("unknown", None, "ollama_invalid")
            if not 0.0 <= confidence <= 1.0:
                return IntentPrediction("unknown", confidence, "ollama_invalid")
            if confidence < self.threshold:
                return IntentPrediction("unknown", confidence, "ollama_low_confidence")
            return IntentPrediction(intent, confidence, "ollama")
        return IntentPrediction("unknown", None, "ollama_invalid")
