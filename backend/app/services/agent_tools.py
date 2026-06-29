from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Dict, List, Optional
from urllib.parse import quote

from backend.app.services import tracking_service
from backend.app.services.mimo_video_client import mimo_video_client
from backend.app.services.replay_service import DEFAULT_CLIP_AFTER_SECONDS, DEFAULT_CLIP_BEFORE_SECONDS, replay_service
from backend.app.services.rules_engine import engine
from backend.app.services.storage_service import storage_service
from backend.app.services.vision_backend_service import vision_backend_service


def _counter_dict(items: List[dict], field: str) -> Dict[str, int]:
    counter = Counter(str(item.get(field) or "unknown") for item in items)
    return dict(counter)


def get_runtime_status(params: dict) -> dict:
    scene_id = params.get("scene_id")
    camera_id = params.get("camera_id")
    engine_status = engine.get_runtime_status()
    tracker_status = tracking_service.tracker_runtime_state()
    latest_ingest = storage_service.get_latest_ingest_stats(limit=1)
    return {
        "engine": engine_status,
        "tracker": tracker_status,
        "vision_backend": vision_backend_service.status(),
        "detector": vision_backend_service.active_runtime_status(scene_id=scene_id, camera_id=camera_id),
        "latest_ingest": latest_ingest[0] if latest_ingest else None,
        "mimo_video": mimo_video_client.status(),
        "requested_scene_id": scene_id,
        "requested_camera_id": camera_id,
    }


def get_alert_summary(params: dict) -> dict:
    scene_id = params.get("scene_id")
    camera_id = params.get("camera_id")
    limit = max(1, min(int(params.get("limit", 20)), 200))

    history = engine.get_alert_history(
        scene_id=scene_id,
        camera_id=camera_id,
        limit=limit,
    )
    return {
        "total": len(history),
        "items": history[:limit],
        "by_rule": _counter_dict(history, "rule_id"),
        "by_camera": _counter_dict(history, "camera_id"),
        "by_severity": _counter_dict(history, "severity"),
        "by_category": _counter_dict(history, "category"),
    }


def _build_replay_tip(alert_item: Optional[dict], scene_id: Optional[str]) -> dict:
    if not alert_item:
        return {
            "available": False,
            "message": "当前没有可用于回放定位的最新告警。",
        }

    camera_id = str(alert_item.get("camera_id") or "")
    timestamp = str(alert_item.get("timestamp") or "")
    alert_scene_ids = alert_item.get("scene_ids") or []
    resolved_scene_id = scene_id or (alert_scene_ids[0] if alert_scene_ids else "")
    rule_id = str(alert_item.get("rule_id") or "")
    rule_label = str(alert_item.get("rule_label") or alert_item.get("rule_display") or engine.get_rule_display_label(rule_id))

    if not camera_id or not timestamp:
        return {
            "available": False,
            "message": "最新告警缺少 camera_id 或 timestamp，无法定位回放。",
        }

    display_timestamp = timestamp
    try:
        display_timestamp = datetime.fromisoformat(timestamp).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        display_timestamp = timestamp.replace("T", " ")

    query = (
        f"camera_id={quote(camera_id)}"
        f"&timestamp={quote(timestamp)}"
        f"&scene_id={quote(str(resolved_scene_id))}"
        f"&rule_id={quote(rule_id)}"
    )
    return {
        "available": True,
        "camera_id": camera_id,
        "timestamp": timestamp,
        "display_timestamp": display_timestamp,
        "scene_id": resolved_scene_id,
        "rule_id": rule_id,
        "rule_label": rule_label,
        "resolve_path": f"/replay/resolve?{query}",
        "replay_page_url": f"/replay.html?{query}",
        "open_folder_page_url": f"/replay.html?{query}#folder",
        "event_message": str(alert_item.get("message") or ""),
        "download_hint": "/replay/download?camera_id=...&timestamp=...&name=...",
    }


def get_replay_hint(params: dict) -> dict:
    scene_id = params.get("scene_id")
    latest_alert = params.get("latest_alert")
    if not latest_alert:
        camera_id = params.get("camera_id")
        history = engine.get_alert_history(scene_id=scene_id, camera_id=camera_id, limit=1)
        latest_alert = history[0] if history else None
    return _build_replay_tip(latest_alert, scene_id=scene_id)


def analyze_replay_video(params: dict) -> dict:
    scene_id = params.get("scene_id")
    latest_alert = params.get("latest_alert")
    if not latest_alert:
        camera_id = params.get("camera_id")
        history = engine.get_alert_history(scene_id=scene_id, camera_id=camera_id, limit=1)
        latest_alert = history[0] if history else None

    replay = _build_replay_tip(latest_alert, scene_id=scene_id)
    if not replay.get("available"):
        return {
            "available": False,
            "analysis_available": False,
            "message": replay.get("message") or "No replay video available.",
        }

    timestamp_text = str(replay.get("timestamp") or "").strip()
    camera_id = str(replay.get("camera_id") or "").strip()
    resolved_scene_id = str(replay.get("scene_id") or scene_id or "").strip()
    rule_id = str(replay.get("rule_id") or "").strip()
    event_message = str(replay.get("event_message") or "").strip()
    try:
        event_time = datetime.fromisoformat(timestamp_text)
    except Exception:
        return {
            "available": False,
            "analysis_available": False,
            "message": f"Invalid replay timestamp: {timestamp_text}",
        }

    clip_path, replay_info = replay_service.generate_clip_for_event(
        camera_id=camera_id,
        event_time=event_time,
        scene_id=resolved_scene_id,
        rule_id=rule_id,
        before_seconds=DEFAULT_CLIP_BEFORE_SECONDS,
        after_seconds=DEFAULT_CLIP_AFTER_SECONDS,
    )

    result = {
        "available": replay_info.get("video_found", False),
        "analysis_available": False,
        "camera_id": camera_id,
        "scene_id": resolved_scene_id,
        "rule_id": rule_id,
        "timestamp": timestamp_text,
        "display_timestamp": replay.get("display_timestamp") or replay_info.get("display_time"),
        "event_message": event_message,
        "replay_info": replay_info,
        "clip_path": clip_path,
    }

    if not replay_info.get("video_found"):
        result["message"] = "Replay source video was not found for the latest alert."
        return result

    analysis_target_path = str(clip_path or replay_info.get("video_path") or "").strip()
    analysis_target_kind = "clip" if clip_path else "source_video"
    result["analysis_target_kind"] = analysis_target_kind
    result["analysis_target_path"] = analysis_target_path

    if not analysis_target_path:
        result["message"] = "Replay source video exists, but no usable analysis target was found."
        return result

    result["available"] = True

    if not mimo_video_client.is_enabled:
        if analysis_target_kind == "clip":
            result["message"] = (
                "Replay clip generated successfully, but MiMo video understanding is not configured yet. "
                "Add MIMO_API_KEY later and the Agent will be able to explain the clip."
            )
        else:
            result["message"] = (
                "Replay clip extraction was unavailable, so the Agent will analyze the original replay video once "
                "MiMo video understanding is configured."
            )
        return result

    analysis = mimo_video_client.analyze_security_event_clip(
        video_path_or_url=analysis_target_path,
        camera_id=camera_id,
        scene_id=resolved_scene_id,
        rule_id=rule_id,
        alert_message=event_message,
        rule_context=engine.get_rule_context(rule_id, camera_id),
    )
    result["analysis_available"] = bool(analysis.get("analysis_available"))
    result["analysis"] = analysis

    stored = storage_service.upsert_video_analysis(
        event_timestamp=timestamp_text,
        scene_id=resolved_scene_id,
        rule_id=rule_id,
        camera_id=camera_id,
        source_video_path=str(replay_info.get("video_path") or ""),
        clip_path=str(clip_path or ""),
        clip_before_seconds=DEFAULT_CLIP_BEFORE_SECONDS,
        clip_after_seconds=DEFAULT_CLIP_AFTER_SECONDS,
        provider="mimo_video",
        model=str(analysis.get("model") or mimo_video_client.model),
        summary=str(analysis.get("summary") or ""),
        risk_assessment=str(analysis.get("risk_assessment") or ""),
        analysis=analysis,
        error=str(analysis.get("error") or ""),
        analysis_available=bool(analysis.get("analysis_available")),
    )
    result["stored_analysis"] = stored

    if analysis.get("analysis_available"):
        result["message"] = analysis.get("summary") or "Replay video analysis completed."
        if analysis_target_kind == "source_video":
            result["message"] = f"{result['message']} (analyzed original replay video because clip extraction was unavailable)"
    else:
        result["message"] = analysis.get("error") or "Replay video analysis failed."
    return result


TOOL_REGISTRY = {
    "get_runtime_status": get_runtime_status,
    "get_alert_summary": get_alert_summary,
    "get_replay_hint": get_replay_hint,
    "analyze_replay_video": analyze_replay_video,
}
