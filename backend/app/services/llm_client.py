from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional

import requests
from backend.app.core.config import PROJECT_ROOT
from backend.app.core.utils import load_local_env_values, ENV_FILE_NAMES

try:
    from dotenv import dotenv_values
except Exception:  # pragma: no cover - fallback when dependency is missing
    dotenv_values = None


VALID_INTENTS = {"summary", "runtime", "alerts", "replay"}


def _extract_content(message_content: Any) -> str:
    if isinstance(message_content, str):
        return message_content.strip()

    if isinstance(message_content, list):
        parts: List[str] = []
        for part in message_content:
            if isinstance(part, dict):
                text = part.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts).strip()

    return ""


def _safe_json_parse(text: str) -> Optional[dict]:
    if not text:
        return None
    try:
        payload = json.loads(text)
        return payload if isinstance(payload, dict) else None
    except Exception:
        pass

    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        return None
    try:
        payload = json.loads(match.group(0))
        return payload if isinstance(payload, dict) else None
    except Exception:
        return None


def _truncate_payload_for_model(payload: dict) -> dict:
    compact = dict(payload)
    alerts = compact.get("alerts")
    if isinstance(alerts, dict):
        copied = dict(alerts)
        items = copied.get("items")
        if isinstance(items, list):
            copied["items"] = items[:5]
        compact["alerts"] = copied
    return compact


class AgentLLMClient:
    def __init__(self):
        self.local_env_values, self.loaded_env_files = load_local_env_values()
        self.local_env_enabled = bool(self.loaded_env_files)

        self.api_key, self.key_source = self._read_config_value(
            ["API_KEY", "DEEPSEEK_API_KEY", "AGENT_API_KEY"],
            default="",
        )
        raw_enable, _ = self._read_config_value(["AGENT_ENABLE_LLM"], default="")
        raw_enable = raw_enable.lower()
        if raw_enable in {"0", "false", "off", "no"}:
            self.enable_llm = False
        elif raw_enable in {"1", "true", "on", "yes"}:
            self.enable_llm = True
        else:
            # Auto-enable when key exists and flag is not explicitly configured.
            self.enable_llm = bool(self.api_key)

        self.base_url, _ = self._read_config_value(
            ["BASE_URL", "AGENT_BASE_URL"],
            default="https://api.deepseek.com/v1",
        )
        self.base_url = self.base_url.rstrip("/")

        self.model, _ = self._read_config_value(
            ["MODEL_NAME", "AGENT_MODEL"],
            default="deepseek-chat",
        )

        timeout_text, _ = self._read_config_value(
            ["TIMEOUT_SECONDS", "AGENT_TIMEOUT_SECONDS"],
            default="8",
        )
        try:
            self.timeout_seconds = float(timeout_text or "8")
        except Exception:
            self.timeout_seconds = 8.0

        json_mode_text, _ = self._read_config_value(["AGENT_USE_JSON_RESPONSE_FORMAT"], default="1")
        self.use_json_response_format = json_mode_text.strip().lower() not in {"0", "false", "off", "no"}
        self.last_error: str = ""
        self.last_generation_used_llm: bool = False

    @property
    def is_enabled(self) -> bool:
        return self.enable_llm and bool(self.api_key)

    def _read_config_value(self, keys: List[str], default: str = "") -> tuple[str, str]:
        for key in keys:
            value = (self.local_env_values.get(key) or "").strip()
            if value:
                return value, f"local_env:{key}"
        for key in keys:
            value = os.getenv(key, "").strip()
            if value:
                return value, key
        return default, "default"

    def _chat_completion(
        self,
        *,
        messages: List[dict],
        temperature: float,
        max_tokens: int,
        json_mode: bool = False,
    ) -> Optional[str]:
        if not self.is_enabled:
            self.last_error = "llm_disabled_or_missing_key"
            return None

        url = self.base_url if self.base_url.endswith("/chat/completions") else f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode and self.use_json_response_format:
            payload["response_format"] = {"type": "json_object"}

        try:
            response = requests.post(url, headers=headers, json=payload, timeout=self.timeout_seconds)
            if response.status_code >= 400:
                body = response.text[:280].replace("\n", " ").strip()
                self.last_error = f"http_{response.status_code}: {body}"
                return None
            body = response.json()
            choices = body.get("choices") or []
            if not choices:
                self.last_error = "empty_choices"
                return None
            message = choices[0].get("message") or {}
            self.last_error = ""
            return _extract_content(message.get("content"))
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return None

    def classify_intent(self, *, query: str, local_intent: str) -> Optional[str]:
        if not self.is_enabled:
            return None

        messages = [
            {
                "role": "system",
                "content": (
                    "Classify monitoring assistant intent.\n"
                    "Return JSON only: {\"intent\":\"summary|runtime|alerts|replay\"}.\n"
                    "Do not return any extra text."
                ),
            },
            {
                "role": "user",
                "content": f"query={query}\nlocal_intent_hint={local_intent}",
            },
        ]
        text = self._chat_completion(messages=messages, temperature=0.0, max_tokens=80, json_mode=True)
        data = _safe_json_parse(text or "")
        if not data:
            return None

        intent = str(data.get("intent") or "").strip().lower()
        return intent if intent in VALID_INTENTS else None

    def generate_answer(
        self,
        *,
        query: str,
        intent: str,
        payload: dict,
        tools_used: List[str],
        default_answer: str,
    ) -> str:
        self.last_generation_used_llm = False
        if not self.is_enabled:
            return default_answer

        compact_payload = _truncate_payload_for_model(payload)
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a monitoring copilot assistant.\n"
                    "Use only provided data. Do not hallucinate.\n"
                    "Reply in Chinese, concise and action-oriented."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "query": query,
                        "intent": intent,
                        "tools_used": tools_used,
                        "data": compact_payload,
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        text = self._chat_completion(messages=messages, temperature=0.2, max_tokens=420, json_mode=False)
        if text:
            self.last_generation_used_llm = True
            return text.strip()
        return default_answer
