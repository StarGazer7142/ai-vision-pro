from __future__ import annotations

import base64
import json
import mimetypes
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

from backend.app.core.config import PROJECT_ROOT
from backend.app.core.utils import load_local_env_values, read_config_value

try:
    from dotenv import dotenv_values
except Exception:  # pragma: no cover - optional dependency fallback
    dotenv_values = None
DEFAULT_SYSTEM_PROMPT = (
    "You are MiMo, an AI assistant developed by Xiaomi. "
    "Analyze the provided security monitoring video clip using only visible evidence."
)
DEFAULT_ANALYSIS_PROMPT = (
    "请分析这段安防监控短视频，并严格返回 JSON，不要输出任何额外文字。"
    "summary 必须先写画面理解，包含人数、衣着颜色或外观、所在位置、正在做什么；"
    "如果画面看不清衣着，请明确写“衣着细节不清”。"
    "summary 最后再写规则判定。"
    "JSON schema: "
    "{\"summary\":\"一句话总结\","
    "\"observations\":[\"观察1\",\"观察2\"],"
    "\"events\":[{\"time_offset_sec\":0,\"title\":\"事件标题\",\"description\":\"事件描述\",\"severity\":\"low|medium|high\",\"confidence\":0.0}],"
    "\"risk_assessment\":\"风险判断\","
    "\"recommended_actions\":[\"建议1\",\"建议2\"]}"
)

NEGATIVE_DWELL_PHRASES = (
    "未滞留",
    "未发现滞留",
    "没有滞留",
    "无滞留",
    "疑似误报",
    "误触发",
    "误报",
)

RULE_ONLY_SUMMARY_PREFIXES = (
    "规则引擎已按",
    "规则判定：",
    "规则判定:",
)

VISUAL_DETAIL_KEYWORDS = (
    "人",
    "名",
    "位",
    "穿",
    "衣",
    "白色",
    "黑色",
    "蓝色",
    "红色",
    "黄色",
    "灰色",
    "深色",
    "浅色",
    "上衣",
    "裤",
    "站",
    "走",
    "行走",
    "停留",
    "作业",
    "会议",
    "区域",
    "通道",
    "仓库",
    "围栏",
)


def _extract_message_text(message_content: Any) -> str:
    if isinstance(message_content, str):
        return message_content.strip()
    if isinstance(message_content, list):
        parts: List[str] = []
        for item in message_content:
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())
        return "\n".join(parts).strip()
    return ""


def _safe_json_parse(text: str) -> Optional[dict]:
    text = str(text or "").strip()
    if not text:
        return None
    try:
        payload = json.loads(text)
        return payload if isinstance(payload, dict) else None
    except Exception:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        payload = json.loads(text[start : end + 1])
        return payload if isinstance(payload, dict) else None
    except Exception:
        return None


class MimoVideoClient:
    def __init__(self):
        self.local_env_values, self.loaded_env_files = load_local_env_values()
        self.api_key, self.key_source = read_config_value(
            self.local_env_values,
            ["MIMO_API_KEY", "VIDEO_UNDERSTANDING_API_KEY"],
            default="",
        )
        self.base_url, _ = read_config_value(
            self.local_env_values,
            ["MIMO_BASE_URL", "VIDEO_UNDERSTANDING_API_URL"],
            default="https://api.xiaomimimo.com/v1",
        )
        self.base_url = self.base_url.rstrip("/")
        self.model, _ = read_config_value(
            self.local_env_values,
            ["MIMO_MODEL", "VIDEO_UNDERSTANDING_MODEL"],
            default="mimo-v2.5",
        )

        timeout_text, _ = read_config_value(
            self.local_env_values,
            ["MIMO_TIMEOUT_SECONDS"],
            default="45",
        )
        fps_text, _ = read_config_value(
            self.local_env_values,
            ["MIMO_VIDEO_FPS"],
            default="2",
        )
        max_tokens_text, _ = read_config_value(
            self.local_env_values,
            ["MIMO_MAX_COMPLETION_TOKENS"],
            default="1024",
        )
        media_resolution, _ = read_config_value(
            self.local_env_values,
            ["MIMO_VIDEO_MEDIA_RESOLUTION"],
            default="default",
        )
        use_base64_text, _ = read_config_value(
            self.local_env_values,
            ["MIMO_VIDEO_USE_BASE64"],
            default="1",
        )

        try:
            self.timeout_seconds = max(5.0, float(timeout_text or "45"))
        except Exception:
            self.timeout_seconds = 45.0
        try:
            self.fps = max(1, int(fps_text or "2"))
        except Exception:
            self.fps = 2
        try:
            self.max_completion_tokens = max(128, int(max_tokens_text or "1024"))
        except Exception:
            self.max_completion_tokens = 1024

        self.media_resolution = str(media_resolution or "default").strip() or "default"
        self.use_base64_for_local_files = use_base64_text.strip().lower() not in {"0", "false", "off", "no"}
        self.last_error: str = ""

    def reload(self) -> None:
        """Re-read .env / .env.local so runtime changes to MIMO_API_KEY take effect
        without restarting the backend process."""
        self.local_env_values, self.loaded_env_files = load_local_env_values()
        self.api_key, self.key_source = read_config_value(
            self.local_env_values,
            ["MIMO_API_KEY", "VIDEO_UNDERSTANDING_API_KEY"],
            default="",
        )
        self.last_error = "" if self.is_enabled else self.last_error

    @property
    def is_enabled(self) -> bool:
        return bool(self.api_key)

    def status(self) -> dict:
        return {
            "enabled": self.is_enabled,
            "has_api_key": bool(self.api_key),
            "key_source": self.key_source,
            "base_url": self.base_url,
            "model": self.model,
            "timeout_seconds": self.timeout_seconds,
            "fps": self.fps,
            "media_resolution": self.media_resolution,
            "use_base64_for_local_files": self.use_base64_for_local_files,
            "last_error": self.last_error,
        }

    def _video_message_part(self, video_source: str) -> Dict[str, Any]:
        return {
            "type": "video_url",
            "video_url": {
                "url": video_source,
            },
            "fps": self.fps,
            "media_resolution": self.media_resolution,
        }

    def _guess_video_mime_type(self, path: Path) -> str:
        guessed, _ = mimetypes.guess_type(str(path))
        if guessed:
            return guessed
        return "video/mp4"

    def _encode_local_video_as_data_url(self, path: Path) -> Tuple[str, str]:
        mime_type = self._guess_video_mime_type(path)
        encoded = base64.b64encode(path.read_bytes()).decode("utf-8")
        return f"data:{mime_type};base64,{encoded}", mime_type

    def _resolve_video_source(self, video_path_or_url: str) -> tuple[str, dict]:
        raw = str(video_path_or_url or "").strip()
        if not raw:
            raise ValueError("video_path_or_url is empty")

        if raw.startswith("http://") or raw.startswith("https://") or raw.startswith("data:"):
            return raw, {"source_kind": "remote_url" if raw.startswith("http") else "data_url"}

        path = Path(raw)
        if not path.is_absolute():
            path = (PROJECT_ROOT / path).resolve()
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(f"Video file not found: {path}")

        if self.use_base64_for_local_files:
            source, mime_type = self._encode_local_video_as_data_url(path)
            return source, {
                "source_kind": "local_base64",
                "mime_type": mime_type,
                "path": str(path),
                "size_bytes": path.stat().st_size,
            }

        raise ValueError(
            "Local video requires base64 mode or an externally reachable URL. "
            "Set MIMO_VIDEO_USE_BASE64=1 or provide an http(s) video URL."
        )

    def analyze_video(
        self,
        *,
        video_path_or_url: str,
        prompt: str = DEFAULT_ANALYSIS_PROMPT,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
    ) -> dict:
        if not self.is_enabled:
            self.last_error = "mimo_api_key_missing"
            return {
                "ok": False,
                "error": self.last_error,
                "analysis_available": False,
            }

        try:
            video_source, source_meta = self._resolve_video_source(video_path_or_url)
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return {
                "ok": False,
                "error": self.last_error,
                "analysis_available": False,
            }

        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": system_prompt,
                },
                {
                    "role": "user",
                    "content": [
                        self._video_message_part(video_source),
                        {
                            "type": "text",
                            "text": prompt,
                        },
                    ],
                },
            ],
            "max_completion_tokens": self.max_completion_tokens,
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        url = self.base_url if self.base_url.endswith("/chat/completions") else f"{self.base_url}/chat/completions"

        try:
            response = requests.post(url, headers=headers, json=payload, timeout=self.timeout_seconds)
            if response.status_code >= 400:
                body = response.text[:320].replace("\n", " ").strip()
                self.last_error = f"http_{response.status_code}: {body}"
                return {
                    "ok": False,
                    "error": self.last_error,
                    "analysis_available": False,
                    "request": {
                        "model": self.model,
                        "source_meta": source_meta,
                    },
                }

            response_body = response.json()
            choices = response_body.get("choices") or []
            if not choices:
                self.last_error = "empty_choices"
                return {
                    "ok": False,
                    "error": self.last_error,
                    "analysis_available": False,
                }

            message = choices[0].get("message") or {}
            # 优先读 content，为空时读取 reasoning_content（mimo-v2.5 推理模型会在该字段输出）
            raw_text = _extract_message_text(message.get("content"))
            if not raw_text:
                raw_text = _extract_message_text(message.get("reasoning_content"))
            parsed = _safe_json_parse(raw_text)
            self.last_error = ""

            result = {
                "ok": True,
                "analysis_available": True,
                "model": response_body.get("model") or self.model,
                "source_meta": source_meta,
                "raw_text": raw_text,
                "parsed": parsed,
                "usage": response_body.get("usage") or {},
            }
            if parsed:
                result.update(
                    {
                        "summary": str(parsed.get("summary") or "").strip(),
                        "observations": parsed.get("observations") or [],
                        "events": parsed.get("events") or [],
                        "risk_assessment": str(parsed.get("risk_assessment") or "").strip(),
                        "recommended_actions": parsed.get("recommended_actions") or [],
                    }
                )
            return result
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return {
                "ok": False,
                "error": self.last_error,
                "analysis_available": False,
                "request": {
                    "model": self.model,
                    "source_meta": source_meta,
                },
            }

    def analyze_security_event_clip(
        self,
        *,
        video_path_or_url: str,
        camera_id: str = "",
        scene_id: str = "",
        rule_id: str = "",
        alert_message: str = "",
        rule_context: dict | None = None,
    ) -> dict:
        context_lines = [
            "请按安防事件分析的口径理解这段视频。",
            "先做正常的视频理解：说明几个人、在什么位置、正在做什么、是否有明显异常。",
            "然后再单独说明规则判定：是否达到滞留/越界等规则条件、阈值是多少、风险如何。",
        ]
        rule_context = rule_context or {}
        if camera_id:
            context_lines.append(f"camera_id={camera_id}")
        if scene_id:
            context_lines.append(f"scene_id={scene_id}")
        if rule_id:
            context_lines.append(f"rule_id={rule_id}")
        if rule_context.get("rule_label"):
            context_lines.append(f"规则名称={rule_context.get('rule_label')}")
        if rule_context.get("rule_type"):
            context_lines.append(f"规则类型={rule_context.get('rule_type')}")
        if rule_context.get("zone_label"):
            context_lines.append(f"关联区域={rule_context.get('zone_label')}")
        if rule_context.get("threshold_seconds"):
            context_lines.append(f"规则阈值={int(rule_context['threshold_seconds'])}秒")
        if alert_message:
            context_lines.append(f"已有告警提示={alert_message}")
        if str(rule_context.get("rule_type") or "").strip().lower() == "dwell":
            threshold = int(rule_context.get("threshold_seconds") or 1)
            label = str(rule_context.get("rule_label") or "人员滞留")
            context_lines.extend(
                [
                    "规则判定用于补充视频理解，不要用规则结论替代画面事实描述。",
                    f"本事件已经由规则引擎按“{label}”触发。",
                    f"同一目标在滞留区域连续停留达到或超过 {threshold} 秒，即应表述为“有人滞留”。",
                    "除非视频片段完全不足以覆盖该阈值，否则不要写“未滞留、误触发、误报”等相反结论。",
                    "summary 请优先写画面事实，例如“几名人员在某区域行走/停留/作业”，末尾再补一句规则判定。",
                ]
            )

        prompt = DEFAULT_ANALYSIS_PROMPT + "\n" + "\n".join(context_lines)
        result = self.analyze_video(
            video_path_or_url=video_path_or_url,
            prompt=prompt,
            system_prompt=DEFAULT_SYSTEM_PROMPT,
        )
        if self._needs_visual_detail_retry(result):
            visual_result = self.analyze_video(
                video_path_or_url=video_path_or_url,
                prompt=self._visual_detail_prompt(rule_context),
                system_prompt=DEFAULT_SYSTEM_PROMPT,
            )
            result = self._merge_visual_detail_result(result, visual_result)
        return self._align_analysis_with_rule_context(result, rule_context)

    def _visual_detail_prompt(self, rule_context: dict) -> str:
        lines = [
            DEFAULT_ANALYSIS_PROMPT,
            "这一次请只做画面理解，不要只复述规则。",
            "summary 用中文写成一句自然描述，格式必须接近：",
            "画面理解：视频中可见X名人员，穿着/外观为……，位于……，正在……。",
            "必须尽量描述人数、衣着颜色或外观、位置、动作。看不清就写“衣着细节不清”，不要省略。",
        ]
        if rule_context.get("rule_label"):
            lines.append(f"关联规则={rule_context.get('rule_label')}，但规则只能放在风险判断里，不能替代画面理解。")
        return "\n".join(lines)

    def _is_rule_only_text(self, text: str) -> bool:
        normalized = str(text or "").strip()
        if not normalized:
            return True
        return any(normalized.startswith(prefix) for prefix in RULE_ONLY_SUMMARY_PREFIXES)

    def _has_visual_detail(self, text: str) -> bool:
        normalized = str(text or "").strip()
        if not normalized:
            return False
        if self._is_rule_only_text(normalized):
            return False
        return sum(1 for keyword in VISUAL_DETAIL_KEYWORDS if keyword in normalized) >= 3

    def _needs_visual_detail_retry(self, result: dict) -> bool:
        if not result.get("analysis_available"):
            return False
        summary = str(result.get("summary") or "").strip()
        observations = " ".join(str(item) for item in result.get("observations") or [])
        events = " ".join(
            f"{event.get('title', '')} {event.get('description', '')}"
            for event in result.get("events") or []
            if isinstance(event, dict)
        )
        return not self._has_visual_detail(f"{summary} {observations} {events}")

    def _merge_visual_detail_result(self, base: dict, visual: dict) -> dict:
        if not visual.get("analysis_available"):
            return base

        visual_summary = str(visual.get("summary") or "").strip()
        if self._has_visual_detail(visual_summary):
            base["summary"] = visual_summary

        visual_observations = visual.get("observations")
        if isinstance(visual_observations, list) and visual_observations:
            base["observations"] = visual_observations

        visual_events = visual.get("events")
        if isinstance(visual_events, list) and visual_events:
            base["events"] = visual_events

        parsed = base.get("parsed")
        if isinstance(parsed, dict):
            parsed["summary"] = base.get("summary", "")
            if isinstance(base.get("observations"), list):
                parsed["observations"] = base["observations"]
            if isinstance(base.get("events"), list):
                parsed["events"] = base["events"]
        return base

    def _align_analysis_with_rule_context(self, result: dict, rule_context: dict) -> dict:
        if not result.get("analysis_available"):
            return result
        if str(rule_context.get("rule_type") or "").strip().lower() != "dwell":
            return result

        threshold = int(rule_context.get("threshold_seconds") or 1)
        label = str(rule_context.get("rule_label") or "人员滞留").strip()
        zone_label = str(rule_context.get("zone_label") or "滞留区域").strip()
        rule_sentence = f"规则判定：{zone_label}内目标停留达到或超过 {threshold} 秒，判定为{label}。"

        summary = str(result.get("summary") or "").strip()
        risk = str(result.get("risk_assessment") or "").strip()
        combined = f"{summary}\n{risk}"
        has_negative_dwell = any(phrase in combined for phrase in NEGATIVE_DWELL_PHRASES)
        if not summary:
            result["summary"] = f"画面理解：未返回足够的可见画面细节，需重新分析确认人数、衣着和动作。{rule_sentence}"
        elif has_negative_dwell:
            result["summary"] = f"{summary} {rule_sentence}"
        elif not self._is_rule_only_text(summary) and "滞留" not in summary and "停留" not in summary:
            result["summary"] = f"{summary} {rule_sentence}"
        elif self._is_rule_only_text(summary):
            result["summary"] = f"画面理解：未返回足够的可见画面细节，需重新分析确认人数、衣着和动作。{rule_sentence}"

        if result.get("summary") != summary:
            parsed = result.get("parsed")
            if isinstance(parsed, dict):
                parsed["summary"] = result["summary"]

        events = result.get("events")
        if not isinstance(events, list):
            events = []
        has_dwell_event = any(
            "滞留" in str(event.get("title") or event.get("description") or "")
            for event in events
            if isinstance(event, dict)
        )
        if not has_dwell_event:
            events.append(
                {
                    "time_offset_sec": 0,
                    "title": "规则判定",
                    "description": f"{zone_label}内目标停留达到或超过规则阈值 {threshold} 秒，按规则判定为滞留事件。",
                    "severity": str(rule_context.get("severity") or "medium") or "medium",
                    "confidence": 0.9,
                },
            )
            result["events"] = events
            parsed = result.get("parsed")
            if isinstance(parsed, dict):
                parsed["events"] = events

        if not risk:
            risk_text = f"{rule_sentence}需结合现场确认人员身份、停留原因和处置结果。"
            result["risk_assessment"] = risk_text
            parsed = result.get("parsed")
            if isinstance(parsed, dict):
                parsed["risk_assessment"] = risk_text
        elif has_negative_dwell:
            risk_text = f"{risk} {rule_sentence}需结合现场确认人员身份、停留原因和处置结果。"
            result["risk_assessment"] = risk_text
            parsed = result.get("parsed")
            if isinstance(parsed, dict):
                parsed["risk_assessment"] = risk_text
        return result


mimo_video_client = MimoVideoClient()
