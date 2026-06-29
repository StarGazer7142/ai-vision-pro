from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

from backend.app.services.agent_policy import filter_allowed_tools
from backend.app.services.agent_tools import TOOL_REGISTRY
from backend.app.services.llm_client import AgentLLMClient


RUNTIME_KEYWORDS = [
    "runtime",
    "backend",
    "health",
    "status",
    "tracker",
    "运行",
    "状态",
    "健康",
    "后端",
    "方案一",
    "方案二",
    "yolo",
    "视频理解",
    "vlm",
    "值守",
    "值班",
    "巡检",
]
ALERT_KEYWORDS = [
    "alert",
    "alerts",
    "event",
    "history",
    "告警",
    "报警",
    "事件",
]
REPLAY_KEYWORDS = [
    "replay",
    "download",
    "video",
    "clip",
    "回放",
    "录像",
    "监控",
    "视频",
    "定位建议",
]
SUMMARY_KEYWORDS = [
    "summary",
    "overview",
    "总览",
    "总结",
]
VIDEO_ANALYSIS_KEYWORDS = [
    "what happened",
    "describe video",
    "analyze video",
    "视频里",
    "画面里",
    "发生了什么",
    "短视频",
    "截取",
    "解读",
    "分析视频",
]

INTENT_TOOL_PLAN = {
    "runtime": ["get_runtime_status"],
    "alerts": ["get_alert_summary"],
    "replay": ["get_alert_summary", "get_replay_hint", "analyze_replay_video"],
    "summary": ["get_runtime_status", "get_alert_summary", "get_replay_hint"],
}


def _contains_any(text: str, keywords: List[str]) -> bool:
    return any(keyword in text for keyword in keywords)


def detect_intent_local(query: str) -> str:
    q = (query or "").strip().lower()
    if not q:
        return "summary"

    if _contains_any(q, VIDEO_ANALYSIS_KEYWORDS):
        return "replay"
    if _contains_any(q, REPLAY_KEYWORDS):
        return "replay"
    if _contains_any(q, ALERT_KEYWORDS):
        return "alerts"
    if _contains_any(q, RUNTIME_KEYWORDS):
        return "runtime"
    if _contains_any(q, SUMMARY_KEYWORDS):
        return "summary"
    return "summary"


def _runtime_line(runtime: dict) -> str:
    engine_status = runtime.get("engine", {})
    tracker_status = runtime.get("tracker", {})
    detector_status = runtime.get("detector", {})
    backend_label = detector_status.get("backend_label") or detector_status.get("backend_key") or "unknown"
    pipeline = detector_status.get("pipeline") or "unknown"
    requested_camera = runtime.get("requested_camera_id") or "未指定摄像头"
    return (
        "运行状态："
        f"当前方案={backend_label}，"
        f"处理链路={pipeline}，"
        f"目标摄像头={requested_camera}，"
        f"累计处理帧={engine_status.get('processed_frames', 0)}，"
        f"累计告警={engine_status.get('total_generated_alerts', 0)}，"
        f"活跃轨迹={tracker_status.get('active_tracks', 0)}。"
    )


def _alerts_line(alerts: dict) -> str:
    return (
        "告警概况："
        f"最近告警数={alerts.get('total', 0)}，"
        f"按等级统计={alerts.get('by_severity', {})}。"
    )


def _latest_alert_lines(alerts: dict, max_items: int = 3) -> List[str]:
    latest = (alerts.get("items") or [])[:max_items]
    if not latest:
        return ["当前没有最近告警。"]

    lines = ["最近告警："]
    for item in latest:
        rule_label = item.get("rule_label") or item.get("rule_display") or item.get("rule_id")
        lines.append(
            f"- {item.get('timestamp')} | {item.get('camera_id')} | "
            f"{rule_label} | {item.get('message')}"
        )
    return lines


def _video_analysis_lines(video_analysis: dict) -> List[str]:
    if not video_analysis:
        return []
    message = str(video_analysis.get("message") or "").strip()
    if not video_analysis.get("available"):
        return [message] if message else []

    lines: List[str] = []
    clip_path = str(video_analysis.get("clip_path") or "").strip()
    if clip_path:
        lines.append(f"已截取事件短视频：{clip_path}")

    analysis = video_analysis.get("analysis") or {}
    summary = str(analysis.get("summary") or message).strip()
    if summary:
        lines.append(f"视频解读：{summary}")

    events = analysis.get("events") or []
    if isinstance(events, list) and events:
        lines.append("视频事件：")
        for event in events[:3]:
            if not isinstance(event, dict):
                continue
            title = str(event.get("title") or "事件").strip()
            desc = str(event.get("description") or "").strip()
            offset = event.get("time_offset_sec")
            prefix = f"- t+{offset}s {title}" if offset is not None else f"- {title}"
            lines.append(f"{prefix}：{desc}" if desc else prefix)

    risk = str(analysis.get("risk_assessment") or "").strip()
    if risk:
        lines.append(f"风险判断：{risk}")
    return lines


def _compose_default_answer(intent: str, payload: dict) -> str:
    answer_lines: List[str] = []

    if intent == "runtime":
        runtime = payload.get("runtime") or {}
        answer_lines.append(_runtime_line(runtime))
        answer_lines.append("如果需要，我可以继续总结最新告警，或者直接定位并解读对应的异常视频片段。")
        return "\n".join(answer_lines)

    if intent == "alerts":
        alerts = payload.get("alerts") or {}
        answer_lines.append(_alerts_line(alerts))
        answer_lines.extend(_latest_alert_lines(alerts))
        return "\n".join(answer_lines)

    if intent == "replay":
        alerts = payload.get("alerts") or {}
        replay = payload.get("replay") or {}
        video_analysis = payload.get("video_analysis") or {}
        answer_lines.append(_alerts_line(alerts))
        if replay.get("available"):
            answer_lines.append(
                f"已为你定位到最近一次告警回放：{replay.get('display_timestamp') or replay.get('timestamp')}"
            )
            if replay.get("event_message"):
                answer_lines.append(f"告警内容：{replay.get('event_message')}")
            if replay.get("replay_page_url"):
                answer_lines.append(f"回放链接：{replay.get('replay_page_url')}")
            answer_lines.extend(_video_analysis_lines(video_analysis))
        else:
            answer_lines.append(str(replay.get("message") or "暂无可用回放。"))
        return "\n".join(answer_lines)

    runtime = payload.get("runtime") or {}
    alerts = payload.get("alerts") or {}
    replay = payload.get("replay") or {}
    video_analysis = payload.get("video_analysis") or {}
    answer_lines.append(_runtime_line(runtime))
    answer_lines.append(_alerts_line(alerts))
    if replay.get("available"):
        answer_lines.append(
            f"最近可用回放时间：{replay.get('display_timestamp') or replay.get('timestamp')}。"
        )
        if replay.get("replay_page_url"):
            answer_lines.append(f"回放链接：{replay.get('replay_page_url')}")
        answer_lines.extend(_video_analysis_lines(video_analysis))
    else:
        answer_lines.append(str(replay.get("message") or "暂无可用回放。"))
    return "\n".join(answer_lines)


class AgentOrchestrator:
    def __init__(self, llm_client: Optional[AgentLLMClient] = None):
        self.llm_client = llm_client or AgentLLMClient()

    def status(self) -> dict:
        masked_tail = ""
        if self.llm_client.api_key:
            masked_tail = self.llm_client.api_key[-4:]
        return {
            "enable_flag": bool(self.llm_client.enable_llm),
            "llm_enabled": bool(self.llm_client.is_enabled),
            "has_api_key": bool(self.llm_client.api_key),
            "key_source": self.llm_client.key_source,
            "key_tail": masked_tail,
            "local_env_enabled": bool(self.llm_client.local_env_enabled),
            "local_env_files": self.llm_client.loaded_env_files,
            "base_url": self.llm_client.base_url,
            "model": self.llm_client.model,
            "last_error": self.llm_client.last_error,
        }

    def _resolve_intent(self, query: str) -> tuple[str, str]:
        local_intent = detect_intent_local(query)
        llm_intent = self.llm_client.classify_intent(query=query, local_intent=local_intent)
        if llm_intent:
            return llm_intent, "llm_classify"
        return local_intent, "local_rule"

    def _should_include_video_analysis(self, *, query: str, intent: str) -> bool:
        if intent == "replay":
            return True
        return _contains_any((query or "").strip().lower(), VIDEO_ANALYSIS_KEYWORDS)

    def _execute_tools(
        self,
        *,
        query: str,
        intent: str,
        scene_id: Optional[str],
        camera_id: Optional[str],
        limit: int,
    ) -> tuple[dict, List[str]]:
        planned_tools = list(INTENT_TOOL_PLAN.get(intent, INTENT_TOOL_PLAN["summary"]))
        if self._should_include_video_analysis(query=query, intent=intent) and "analyze_replay_video" not in planned_tools:
            planned_tools.append("analyze_replay_video")
        tools = filter_allowed_tools(planned_tools)

        payload: Dict[str, dict] = {}
        latest_alert = None
        tools_used: List[str] = []

        for tool_name in tools:
            tool_fn = TOOL_REGISTRY.get(tool_name)
            if not tool_fn:
                continue

            params = {
                "scene_id": scene_id,
                "camera_id": camera_id,
                "limit": limit,
                "latest_alert": latest_alert,
            }
            result = tool_fn(params)

            if tool_name == "get_runtime_status":
                payload["runtime"] = result
            elif tool_name == "get_alert_summary":
                payload["alerts"] = result
                items = result.get("items") or []
                latest_alert = items[0] if items else None
            elif tool_name == "get_replay_hint":
                payload["replay"] = result
            elif tool_name == "analyze_replay_video":
                payload["video_analysis"] = result

            tools_used.append(tool_name)

        return payload, tools_used

    def chat(
        self,
        *,
        query: str,
        scene_id: Optional[str] = None,
        camera_id: Optional[str] = None,
        limit: int = 20,
    ) -> dict:
        started = time.perf_counter()
        normalized_limit = max(1, min(int(limit), 200))
        intent, intent_source = self._resolve_intent(query)
        payload, tools_used = self._execute_tools(
            query=query,
            intent=intent,
            scene_id=scene_id,
            camera_id=camera_id,
            limit=normalized_limit,
        )

        default_answer = _compose_default_answer(intent, payload)
        answer = self.llm_client.generate_answer(
            query=query,
            intent=intent,
            payload=payload,
            tools_used=tools_used,
            default_answer=default_answer,
        )
        llm_used = bool(self.llm_client.last_generation_used_llm)
        if llm_used:
            mode = "hybrid_llm"
        elif self.llm_client.is_enabled:
            mode = "local_fallback_answer"
        else:
            mode = "local_fallback"

        return {
            "answer": answer,
            "intent": intent,
            "data": payload,
            "tools_used": tools_used,
            "intent_source": intent_source,
            "agent_mode": mode,
            "llm_used": llm_used,
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
            "generated_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
        }


orchestrator = AgentOrchestrator()
