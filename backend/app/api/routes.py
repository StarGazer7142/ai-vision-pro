from __future__ import annotations

import asyncio
import json
import math
import os
import secrets
import shutil
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import yaml
import cv2
from fastapi import APIRouter, BackgroundTasks, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from backend.app.core.config import DATA_DIR, PROJECT_ROOT, load_rules
from backend.app.core.utils import safe_segment as _safe_segment
from backend.app.core.utils import build_replay_dir as _build_replay_dir_core
from backend.app.core.utils import get_replay_candidate_dirs as _get_replay_candidate_dirs_core
from backend.app.core.logging import APP_LOG_PATH, ERROR_LOG_PATH
from backend.app.schemas.detection import DetectionFrame
from backend.app.services import tracking_service
from backend.app.services.agent_service import chat as agent_chat
from backend.app.services.agent_service import status as agent_status
from backend.app.services.maintenance_service import cleanup_runtime, collect_health, create_backup
from backend.app.services.replay_service import DEFAULT_CLIP_AFTER_SECONDS, DEFAULT_CLIP_BEFORE_SECONDS, replay_service
from backend.app.services.rules_engine import engine
from backend.app.services.storage_service import storage_service
from backend.app.services.stream_service import mjpeg_stream
from backend.app.services.vision_backend_service import vision_backend_service

OFFLINE_ANALYSIS_RESULTS = {}
_yaml_config_lock = threading.Lock()
OFFLINE_RESULTS_TTL_SECONDS = 3600  # 1小时后自动清理


def _store_offline_result(task_id: str, data: dict) -> None:
    """存储离线分析结果，同时清理过期条目"""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    # 清理过期条目
    expired = [k for k, v in OFFLINE_ANALYSIS_RESULTS.items()
               if (now - v.get("_created_at", now)).total_seconds() > OFFLINE_RESULTS_TTL_SECONDS]
    for k in expired:
        OFFLINE_ANALYSIS_RESULTS.pop(k, None)
    # 写入新结果
    data["_created_at"] = now
    OFFLINE_ANALYSIS_RESULTS[task_id] = data


def process_offline_video_task(task_id: str, video_path: Path, camera_id: str, stay_limit: int):
    try:
        from scripts.loitering import process_loitering_video
        from backend.app.services.yolo_service import yolo_service

        camera_config = engine.get_camera(camera_id)
        if not camera_config:
            _store_offline_result(task_id, {"status": "error", "msg": f"找不到摄像头: {camera_id}"})
            return

        output_filename = f"processed_{video_path.name}"
        output_path = video_path.parent / output_filename
        yolo_service.load()
        model_path = yolo_service.weights_path

        process_loitering_video(
            input_video_path=str(video_path),
            output_video_path=str(output_path),
            model_path=str(model_path),
            stay_limit=stay_limit,
        )

        _store_offline_result(task_id, {
            "status": "completed",
            "video_url": f"/download/{output_filename}",
            "events": [{"message": "视频已处理完毕，请下载查看附带检测框的视频。"}],
        })
    except Exception as e:
        _store_offline_result(task_id, {"status": "error", "msg": str(e)})


router = APIRouter()

DEBUG_USERNAME = os.getenv("DEBUG_USERNAME", "")
DEBUG_PASSWORD = os.getenv("DEBUG_PASSWORD", "")
DEBUG_TOKEN_HOURS = int(os.getenv("DEBUG_TOKEN_HOURS", "12"))
DEBUG_TOKENS: dict[str, datetime] = {}
LOGIN_FAILURES: dict[str, dict] = {}
LOGIN_FAILURE_LIMIT = int(os.getenv("LOGIN_FAILURE_LIMIT", "5"))
LOGIN_LOCK_MINUTES = int(os.getenv("LOGIN_LOCK_MINUTES", "15"))
REPLAY_ROOT = Path(os.getenv("REPLAY_ROOT", str(DATA_DIR / "replay")))
REPLAY_LAYOUT = os.getenv("REPLAY_LAYOUT", "{camera_id}/{date}/{hour}")
UPLOAD_VIDEO_ROOT = Path(os.getenv("UPLOAD_VIDEO_ROOT", str(DATA_DIR / "uploads" / "videos")))
ALLOWED_VIDEO_SUFFIX = {".mp4", ".avi", ".mov", ".mkv", ".webm"}
MAX_UPLOAD_SIZE_MB = int(os.getenv("MAX_UPLOAD_SIZE_MB", "500"))
MAX_UPLOAD_SIZE_BYTES = MAX_UPLOAD_SIZE_MB * 1024 * 1024


def _check_upload_size(file: UploadFile) -> None:
    """检查上传文件大小是否超限"""
    content_length = file.size
    if content_length is not None and content_length > MAX_UPLOAD_SIZE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"文件大小 {content_length / 1024 / 1024:.1f}MB 超过限制 {MAX_UPLOAD_SIZE_MB}MB",
        )
ALLOWED_STREAM_SCHEMES = {"http", "https", "rtsp", "rtmp", "udp", "tcp"}
LOG_FILES = {
    "app": APP_LOG_PATH,
    "error": ERROR_LOG_PATH,
}


class DebugLoginRequest(BaseModel):
    username: str
    password: str


class UpdateRegionRequest(BaseModel):
    camera_id: str
    region_type: str = Field(..., description="rois 或 dwell_zones")
    region_id: str
    points: list[list[float]] = Field(..., description="[[x1,y1], [x2,y2]...] 归一化坐标")

class DebugSimulateRequest(BaseModel):
    rule_id: str = Field(..., description="Rule ID from config/rules.yaml")
    count: int = Field(1, ge=0, le=200)
    message: str = Field("[调试] 手动注入事件")

# --- 1. 定义前端传过来的数据格式模型 ---
class LoginRequest(BaseModel):
    username: str
    password: str


class AdminRegisterRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=60)
    display_name: str = Field(..., min_length=1, max_length=80)
    password: str = Field(..., min_length=6, max_length=120)
    note: str = Field(default="", max_length=500)

class AgentChatRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000, description="Natural language query")
    scene_id: Optional[str] = Field(default=None, description="Optional scene id")
    camera_id: Optional[str] = Field(default=None, description="Optional camera id")
    limit: int = Field(default=20, ge=1, le=200)


class AgentChatResponse(BaseModel):
    answer: str
    intent: str
    data: dict
    tools_used: list[str]
    intent_source: str
    agent_mode: Optional[str] = None
    llm_used: bool
    elapsed_ms: int
    generated_at: str


class AgentStatusResponse(BaseModel):
    enable_flag: bool
    llm_enabled: bool
    has_api_key: bool
    key_source: str
    key_tail: str
    local_env_enabled: bool
    local_env_files: list[str]
    base_url: str
    model: str
    last_error: str


class UserCreateRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=60)
    display_name: str = Field(..., min_length=1, max_length=80)
    role: str = Field(default="operator", pattern="^(super_admin|admin|operator|viewer)$")
    password: str = Field(..., min_length=6, max_length=120)
    note: str = Field(default="", max_length=500)
    status: str = Field(default="active", pattern="^(active|disabled)$")


class UserUpdateRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=60)
    display_name: str = Field(..., min_length=1, max_length=80)
    role: str = Field(default="operator", pattern="^(super_admin|admin|operator|viewer)$")
    note: str = Field(default="", max_length=500)


class UserStatusRequest(BaseModel):
    status: str = Field(..., pattern="^(active|disabled)$")


class UserPasswordResetRequest(BaseModel):
    password: str = Field(..., min_length=6, max_length=120)


class DeviceUpsertRequest(BaseModel):
    id: str = Field(..., min_length=1, max_length=80)
    name: str = Field(..., min_length=1, max_length=120)
    group: str = Field(default="默认分组", max_length=120)
    stream: str = Field(default="camera://0", max_length=1000)
    status: str = Field(default="active", pattern="^(active|disabled)$")
    scene_id: Optional[str] = Field(default=None, max_length=100)


class DeviceUpdateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    group: str = Field(default="默认分组", max_length=120)
    stream: str = Field(default="camera://0", max_length=1000)
    status: str = Field(default="active", pattern="^(active|disabled)$")
    scene_id: Optional[str] = Field(default=None, max_length=100)


class AlertWorkflowRequest(BaseModel):
    status: str = Field(..., pattern="^(new|acknowledged|processing|resolved|false_positive)$")
    assignee: str = Field(default="", max_length=120)
    note: str = Field(default="", max_length=1000)


class SystemSettingsRequest(BaseModel):
    retention_days: int = Field(default=30, ge=1, le=3650)
    replay_retention_days: int = Field(default=30, ge=1, le=3650)
    default_dwell_seconds: int = Field(default=5, ge=1, le=3600)
    notification_channels: list[str] = Field(default_factory=list)
    model_profile: str = Field(default="balanced", pattern="^(fast|balanced|accurate)$")
    allow_debug_tools: bool = False
    auto_reconnect_streams: bool = True


class DashboardOverviewResponse(BaseModel):
    summary: dict
    scenes: list[dict]
    recent_alerts: list[dict]
    agent: dict
    users: dict

class RegionUpdate(BaseModel):
    camera_id: str
    region_type: str  # "rois" 或 "dwell_zones"
    region_id: str
    # 归一化坐标序列，例如 [[0.1, 0.2], [0.3, 0.4], ...]
    points: list[list[float]]

class LogFilesResponse(BaseModel):
    files: list[dict]


class LogFileContentResponse(BaseModel):
    file: str
    path: str
    lines: list[str]


class SystemLogsResponse(BaseModel):
    app: list[str]
    error: list[str]


class BackupRequest(BaseModel):
    include_videos: bool = False


class CleanupRequest(BaseModel):
    dry_run: bool = True
    retention_days: Optional[int] = Field(default=None, ge=1, le=3650)
    replay_retention_days: Optional[int] = Field(default=None, ge=1, le=3650)
    backup_retention_days: int = Field(default=90, ge=1, le=3650)


class VisionBackendActivateRequest(BaseModel):
    backend: str = Field(..., min_length=1, max_length=80, description="yolo 或 video_understanding")


class VideoUnderstandingSettingsRequest(BaseModel):
    provider_mode: str = Field(default="mock_local", min_length=1, max_length=80)
    api_url: str = Field(default="", max_length=500)
    model: str = Field(default="", max_length=200)
    timeout_seconds: float = Field(default=12, ge=1, le=120)
    sample_stride: int = Field(default=12, ge=1, le=120)


class VisionBackendConfigRequest(BaseModel):
    default_backend: str = Field(..., min_length=1, max_length=80)
    scene_overrides: dict[str, str] = Field(default_factory=dict)
    camera_overrides: dict[str, str] = Field(default_factory=dict)
    video_understanding: VideoUnderstandingSettingsRequest = Field(default_factory=VideoUnderstandingSettingsRequest)


def _parse_event_timestamp(ts: str) -> datetime:
    text = (ts or "").strip()
    if not text:
        raise ValueError("timestamp is empty")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return datetime.fromisoformat(text)


def _uses_legacy_temp_replay_clip(item: Optional[dict]) -> bool:
    if not item:
        return False
    clip_path = str(item.get("clip_path") or "").replace("\\", "/").lower()
    return "/temp/ai_video_replay/" in clip_path or clip_path.endswith("/ai_video_replay")


def _uses_rule_only_video_analysis(item: Optional[dict]) -> bool:
    if not item:
        return False
    summary = str(item.get("summary") or "").strip()
    if (
        summary.startswith("规则引擎已按")
        or summary.startswith("规则判定：")
        or summary.startswith("规则判定:")
        or "未返回足够的可见画面细节" in summary
    ):
        return True
    analysis = item.get("analysis") or {}
    if isinstance(analysis, dict):
        raw_summary = str(analysis.get("summary") or "").strip()
        return (
            raw_summary.startswith("规则引擎已按")
            or raw_summary.startswith("规则判定：")
            or raw_summary.startswith("规则判定:")
            or "未返回足够的可见画面细节" in raw_summary
        )
    return False




def _is_inside_path(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _relative_stream_value(path: Path) -> str:
    try:
        relative = path.resolve().relative_to(PROJECT_ROOT.resolve())
        return str(relative).replace("\\", "/")
    except ValueError:
        return str(path.resolve())


def _validate_network_stream_url(stream_url: str) -> str:
    value = str(stream_url or "").strip()
    if not value:
        raise HTTPException(status_code=400, detail="Missing stream_url")

    parsed = urlparse(value)
    scheme = parsed.scheme.lower()
    if scheme not in ALLOWED_STREAM_SCHEMES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported stream scheme: {parsed.scheme or '(empty)'}. allowed={sorted(ALLOWED_STREAM_SCHEMES)}",
        )
    if scheme in {"http", "https", "rtsp", "rtmp"} and not parsed.netloc:
        raise HTTPException(status_code=400, detail="stream_url must include host and port when needed")
    return value


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _normalize_path_points(points: list[list[float]]) -> list[list[float]]:
    normalized: list[list[float]] = []
    for item in points or []:
        if not isinstance(item, list) or len(item) < 2:
            continue
        x = _clamp01(item[0])
        y = _clamp01(item[1])
        if normalized:
            prev_x, prev_y = normalized[-1]
            if math.hypot(x - prev_x, y - prev_y) < 0.0025:
                continue
        normalized.append([round(x, 4), round(y, 4)])
    return normalized


def _build_path_corridor_polygon(points: list[list[float]], width: float) -> list[list[float]]:
    path = _normalize_path_points(points)
    if len(path) < 2:
        return []

    half_width = max(0.01, min(float(width), 0.30)) / 2.0

    def _tangent_at(index: int) -> tuple[float, float]:
        if index <= 0:
            dx = path[1][0] - path[0][0]
            dy = path[1][1] - path[0][1]
        elif index >= len(path) - 1:
            dx = path[-1][0] - path[-2][0]
            dy = path[-1][1] - path[-2][1]
        else:
            dx = path[index + 1][0] - path[index - 1][0]
            dy = path[index + 1][1] - path[index - 1][1]
        norm = math.hypot(dx, dy)
        if norm <= 1e-6:
            return 1.0, 0.0
        return dx / norm, dy / norm

    left_points: list[list[float]] = []
    right_points: list[list[float]] = []
    for idx, (x, y) in enumerate(path):
        tx, ty = _tangent_at(idx)
        nx, ny = -ty, tx
        left_points.append([round(_clamp01(x + nx * half_width), 4), round(_clamp01(y + ny * half_width), 4)])
        right_points.append([round(_clamp01(x - nx * half_width), 4), round(_clamp01(y - ny * half_width), 4)])

    polygon = left_points + list(reversed(right_points))
    return polygon if len(polygon) >= 4 else []


def _update_camera_stream(camera_id: str, stream_value: str) -> dict:
    rules_path = engine.rule_path
    config = load_rules(rules_path)
    cameras = config.get("cameras", [])
    target = next((camera for camera in cameras if camera.get("id") == camera_id), None)
    if target is None:
        raise HTTPException(status_code=404, detail=f"Camera not found in config: {camera_id}")

    target["stream"] = stream_value
    with Path(rules_path).open("w", encoding="utf-8") as f:
        yaml.safe_dump(config, f, allow_unicode=True, sort_keys=False)

    return engine.reload_rules()


def _default_restore_stream(target_camera: dict) -> str:
    current_stream = str(target_camera.get("stream") or "").strip()
    if current_stream.startswith("camera://") or current_stream.isdigit():
        return current_stream or "camera://0"
    return "camera://0"


def _remember_original_stream(target_camera: dict) -> None:
    if "original_stream" in target_camera:
        return
    target_camera["original_stream"] = _default_restore_stream(target_camera)


def _normalize_history_ts(ts: str) -> str:
    dt = _parse_event_timestamp(ts)
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.isoformat()


def _build_replay_dir(
    *,
    camera_id: str,
    event_time: datetime,
    scene_id: str = "",
    rule_id: str = "",
    layout: str,
) -> Path:
    return _build_replay_dir_core(
        camera_id=camera_id, event_time=event_time,
        scene_id=scene_id, rule_id=rule_id,
        layout=layout, replay_root=REPLAY_ROOT,
    )


def _get_replay_candidate_dirs(
    *,
    camera_id: str,
    event_time: datetime,
    scene_id: str = "",
    rule_id: str = "",
) -> list[Path]:
    return _get_replay_candidate_dirs_core(
        camera_id=camera_id, event_time=event_time,
        scene_id=scene_id, rule_id=rule_id,
        replay_root=REPLAY_ROOT, replay_layout=REPLAY_LAYOUT,
    )


def _serialize_file(path: Path) -> dict:
    stat = path.stat()
    return {
        "name": path.name,
        "size": stat.st_size,
        "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
    }


def _serialize_detection(det) -> dict:
    return {
        "camera_id": det.camera_id,
        "category": det.category,
        "display_category": det.display_category or det.category,
        "confidence": float(det.confidence),
        "track_id": det.track_id,
        "bbox": {
            "x1": float(det.bbox.x1),
            "y1": float(det.bbox.y1),
            "x2": float(det.bbox.x2),
            "y2": float(det.bbox.y2),
        },
    }


def _cleanup_expired_tokens() -> None:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    expired = [token for token, expires_at in DEBUG_TOKENS.items() if expires_at < now]
    for token in expired:
        DEBUG_TOKENS.pop(token, None)


def _env_flag(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _debug_tools_enabled() -> bool:
    if _env_flag("ALLOW_DEBUG_TOOLS"):
        return True
    try:
        return bool(storage_service.get_system_settings().get("allow_debug_tools"))
    except Exception:
        return False


def _require_debug_tools_enabled() -> None:
    if not _debug_tools_enabled():
        raise HTTPException(status_code=403, detail="Debug 接口已关闭，请使用正式登录和设备管理页面。")
    if not DEBUG_USERNAME or not DEBUG_PASSWORD:
        raise HTTPException(status_code=403, detail="调试凭据未配置，请设置环境变量 DEBUG_USERNAME 和 DEBUG_PASSWORD")


def _verify_debug_token(authorization: Optional[str]) -> str:
    _require_debug_tools_enabled()
    _cleanup_expired_tokens()
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Authorization must be Bearer token")

    token = authorization.split(" ", 1)[1].strip()
    expires_at = DEBUG_TOKENS.get(token)
    if not expires_at:
        raise HTTPException(status_code=401, detail="Invalid debug token")
    if expires_at < datetime.now(timezone.utc).replace(tzinfo=None):
        DEBUG_TOKENS.pop(token, None)
        raise HTTPException(status_code=401, detail="Debug token expired")
    return token


def _extract_bearer_token(authorization: Optional[str], *, required: bool = True) -> str:
    if not authorization:
        if required:
            raise HTTPException(status_code=401, detail="Missing Authorization header")
        return ""
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Authorization must be Bearer token")
    return authorization.split(" ", 1)[1].strip()


def _require_admin_session(authorization: Optional[str]) -> tuple[str, dict]:
    token = _extract_bearer_token(authorization, required=True)
    user = storage_service.get_session_user(token)
    if not user:
        raise HTTPException(status_code=401, detail="Admin session is invalid or expired")
    return token, user


def _require_roles(authorization: Optional[str], allowed_roles: set[str]) -> tuple[str, dict]:
    token, user = _require_admin_session(authorization)
    role = str(user.get("role") or "").strip().lower()
    if role not in allowed_roles:
        raise HTTPException(status_code=403, detail="当前账号没有执行该操作的权限")
    return token, user


def _operator_name(user: dict) -> str:
    return str(user.get("username") or "system")


def _check_login_lock(username: str) -> None:
    entry = LOGIN_FAILURES.get(username)
    if not entry:
        return
    locked_until = entry.get("locked_until")
    if isinstance(locked_until, datetime) and locked_until > datetime.now(timezone.utc).replace(tzinfo=None):
        raise HTTPException(
            status_code=423,
            detail=f"账号登录失败次数过多，已锁定至 {locked_until.isoformat()}",
        )
    if isinstance(locked_until, datetime) and locked_until <= datetime.now(timezone.utc).replace(tzinfo=None):
        LOGIN_FAILURES.pop(username, None)


def _record_login_success(username: str) -> None:
    LOGIN_FAILURES.pop(username, None)


def _record_login_failure(username: str) -> None:
    entry = LOGIN_FAILURES.setdefault(username, {"count": 0, "locked_until": None})
    entry["count"] = int(entry.get("count") or 0) + 1
    if entry["count"] >= max(1, LOGIN_FAILURE_LIMIT):
        entry["locked_until"] = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=max(1, LOGIN_LOCK_MINUTES))


def _create_admin_from_payload(payload: AdminRegisterRequest) -> dict:
    try:
        return storage_service.create_admin(
            username=payload.username,
            display_name=payload.display_name,
            password=payload.password,
            note=payload.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _authenticate_console_user(payload: LoginRequest) -> dict:
    username = (payload.username or "").strip()
    password = (payload.password or "").strip()
    if not username or not password:
        raise HTTPException(status_code=400, detail="请输入账号和密码")

    _check_login_lock(username)
    user = storage_service.verify_user(username, password)
    if user:
        _record_login_success(username)
        return user

    _record_login_failure(username)
    raise HTTPException(status_code=401, detail="用户名或密码不正确，或账号已被停用")


def _read_log_lines(path: Path, tail: int = 200) -> list[str]:
    if not path.exists() or not path.is_file():
        return []
    try:
        content = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        return content[-max(1, min(tail, 2000)):]
    except Exception:
        return []


def _dashboard_overview() -> dict:
    scenes = engine.get_scenes()
    scene_signals = engine.get_scene_signals() or []
    rules_config = engine.rules_config
    cameras = rules_config.get("cameras", [])
    recent_alerts = engine.get_alert_history(limit=8)
    runtime = engine.get_runtime_status()
    tracker = tracking_service.tracker_runtime_state()
    agent = agent_status()
    user_count = storage_service.count_users()

    return {
        "summary": {
            "scene_count": len(scenes),
            "camera_count": len(cameras),
            "rule_count": len(rules_config.get("rules", [])),
            "active_tracks": tracker.get("active_tracks", 0),
            "processed_frames": runtime.get("processed_frames", 0),
            "recent_alert_count": len(recent_alerts),
            "user_count": user_count,
        },
        "scenes": [
            {
                "id": scene.get("id"),
                "name": scene.get("name"),
                "description": scene.get("description", ""),
                "camera_count": len(scene.get("cameras", [])),
                "rule_count": len(scene.get("rule_ids", [])),
                "entry_page": scene.get("entry_page", ""),
                "signals": next((item for item in scene_signals if item.get("scene_id") == scene.get("id")), None),
            }
            for scene in scenes
        ],
        "recent_alerts": recent_alerts,
        "agent": agent,
        "users": {
            "total": user_count,
        },
    }


def _record_region_operation(
    *,
    operator: dict,
    action: str,
    camera_id: str,
    region_id: str,
    region_type: str,
    detail: Optional[dict] = None,
) -> None:
    payload = {
        "camera_id": camera_id,
        "region_id": region_id,
        "region_type": region_type,
    }
    if detail:
        payload.update(detail)

    storage_service.record_operation(
        module="regions",
        action=action,
        operator=operator["username"],
        target=f"{camera_id}:{region_type}:{region_id}",
        detail=payload,
    )


def _scene_ids_for_camera(camera_id: str, config: Optional[dict] = None) -> list[str]:
    rules_config = config or engine.rules_config
    return [
        str(scene.get("id"))
        for scene in rules_config.get("scenes", [])
        if camera_id in (scene.get("cameras") or [])
    ]


def _serialize_device(camera: dict, config: Optional[dict] = None) -> dict:
    stream = str(camera.get("stream") or "").strip()
    status = str(camera.get("status") or "active").strip().lower()
    scene_ids = _scene_ids_for_camera(str(camera.get("id") or ""), config)
    if status == "disabled":
        online_status = "disabled"
    elif stream:
        online_status = "configured"
    else:
        online_status = "missing_stream"
    return {
        "id": camera.get("id"),
        "name": camera.get("name") or camera.get("id"),
        "group": camera.get("group") or "默认分组",
        "stream": stream,
        "status": "disabled" if status == "disabled" else "active",
        "scene_ids": scene_ids,
        "online_status": online_status,
        "rois": camera.get("rois") or [],
        "dwell_zones": camera.get("dwell_zones") or [],
        "original_stream": camera.get("original_stream") or "",
    }


def _load_rules_for_write() -> tuple[Path, dict]:
    rules_path = Path(engine.rule_path)
    return rules_path, load_rules(rules_path)


def _persist_rules_config(rules_path: Path, config: dict) -> dict:
    with Path(rules_path).open("w", encoding="utf-8") as f:
        yaml.safe_dump(config, f, allow_unicode=True, sort_keys=False)
    return engine.reload_rules()


def _ensure_device_stream_allowed(stream: str) -> str:
    value = str(stream or "").strip()
    if not value:
        raise HTTPException(status_code=400, detail="stream 不能为空")
    parsed = urlparse(value)
    if parsed.scheme.lower() in ALLOWED_STREAM_SCHEMES:
        return _validate_network_stream_url(value)
    if value.startswith("camera://") or value.isdigit():
        return value
    if any(value.lower().endswith(suffix) for suffix in ALLOWED_VIDEO_SUFFIX):
        return value
    raise HTTPException(status_code=400, detail="stream 必须是本地摄像头、项目视频路径或受支持的网络实时流")


@router.get("/health")
def health():
    return {
        "status": "ok",
        "time": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
        "revision": engine.config_revision,
    }


@router.get("/dashboard/overview", response_model=DashboardOverviewResponse)
def dashboard_overview():
    return _dashboard_overview()


@router.get("/alerts")
def get_alerts(
    scene_id: Optional[str] = Query(default=None, description="Optional scene id for filtering"),
    limit: int = Query(default=50, ge=1, le=500),
):
    return {"data": engine.get_alerts(scene_id=scene_id, limit=limit)}

@router.get("/config/full")
def get_full_config(authorization: Optional[str] = Header(default=None)):
    """获取完整的规则配置，用于前端绘图初始化"""
    _verify_debug_token(authorization)
    return engine.rules_config

@router.get("/config/cameras")
def get_camera_configs():
    """获取所有摄像头及其当前的防区配置，含所属场景 ID"""
    config = engine.rules_config
    scenes = config.get("scenes", [])
    camera_list = config.get("cameras", [])
    scene_ids_by_camera: dict[str, list[str]] = {}
    for scene in scenes:
        for cam_id in (scene.get("cameras") or []):
            scene_ids_by_camera.setdefault(cam_id, []).append(scene["id"])
    result = []
    for cam in camera_list:
        item = dict(cam)
        item["scene_ids"] = scene_ids_by_camera.get(cam.get("id"), [])
        result.append(item)
    return result


@router.get("/devices")
def list_devices(authorization: Optional[str] = Header(default=None)):
    _require_roles(authorization, {"super_admin", "admin", "operator", "viewer"})
    config = engine.rules_config
    return {"data": [_serialize_device(camera, config) for camera in config.get("cameras", [])]}


@router.post("/devices")
def create_device(payload: DeviceUpsertRequest, authorization: Optional[str] = Header(default=None)):
    _, operator = _require_roles(authorization, {"super_admin", "admin"})
    stream = _ensure_device_stream_allowed(payload.stream)
    with _yaml_config_lock:
        rules_path, config = _load_rules_for_write()
        cameras = config.setdefault("cameras", [])
        if any(str(camera.get("id")) == payload.id for camera in cameras):
            raise HTTPException(status_code=400, detail=f"设备 ID 已存在: {payload.id}")

        camera = {
            "id": payload.id,
            "name": payload.name,
            "group": payload.group or "默认分组",
            "status": payload.status,
            "stream": stream,
            "rois": [],
            "dwell_zones": [],
            "original_stream": stream,
        }
        cameras.append(camera)

        if payload.scene_id:
            scene = next((item for item in config.get("scenes", []) if item.get("id") == payload.scene_id), None)
            if scene is None:
                raise HTTPException(status_code=404, detail=f"场景不存在: {payload.scene_id}")
            scene_cameras = scene.setdefault("cameras", [])
            if payload.id not in scene_cameras:
                scene_cameras.append(payload.id)

        reload_result = _persist_rules_config(rules_path, config)
    storage_service.record_operation(
        module="devices",
        action="create",
        operator=_operator_name(operator),
        target=payload.id,
        detail={"device": _serialize_device(camera, config), "reload": reload_result},
    )
    return {"ok": True, "data": _serialize_device(camera, config), "reload": reload_result}


@router.put("/devices/{camera_id}")
def update_device(camera_id: str, payload: DeviceUpdateRequest, authorization: Optional[str] = Header(default=None)):
    _, operator = _require_roles(authorization, {"super_admin", "admin"})
    stream = _ensure_device_stream_allowed(payload.stream)
    with _yaml_config_lock:
        rules_path, config = _load_rules_for_write()
        cameras = config.setdefault("cameras", [])
        camera = next((item for item in cameras if item.get("id") == camera_id), None)
        if camera is None:
            raise HTTPException(status_code=404, detail=f"设备不存在: {camera_id}")

        camera["name"] = payload.name
        camera["group"] = payload.group or "默认分组"
        camera["status"] = payload.status
        camera["stream"] = stream
        camera.setdefault("original_stream", stream)

        for scene in config.get("scenes", []):
            scene_cameras = list(scene.get("cameras") or [])
            if payload.scene_id and scene.get("id") == payload.scene_id:
                if camera_id not in scene_cameras:
                    scene_cameras.append(camera_id)
            else:
                scene_cameras = [item for item in scene_cameras if item != camera_id]
            scene["cameras"] = scene_cameras

        reload_result = _persist_rules_config(rules_path, config)
    storage_service.record_operation(
        module="devices",
        action="update",
        operator=_operator_name(operator),
        target=camera_id,
        detail={"device": _serialize_device(camera, config), "reload": reload_result},
    )
    return {"ok": True, "data": _serialize_device(camera, config), "reload": reload_result}


@router.post("/devices/{camera_id}/status")
def set_device_status(
    camera_id: str,
    payload: UserStatusRequest,
    authorization: Optional[str] = Header(default=None),
):
    _, operator = _require_roles(authorization, {"super_admin", "admin", "operator"})
    with _yaml_config_lock:
        rules_path, config = _load_rules_for_write()
        camera = next((item for item in config.get("cameras", []) if item.get("id") == camera_id), None)
        if camera is None:
            raise HTTPException(status_code=404, detail=f"设备不存在: {camera_id}")
        camera["status"] = payload.status
        reload_result = _persist_rules_config(rules_path, config)
    storage_service.record_operation(
        module="devices",
        action="set_status",
        operator=_operator_name(operator),
        target=camera_id,
        detail={"status": payload.status, "reload": reload_result},
    )
    return {"ok": True, "data": _serialize_device(camera, config), "reload": reload_result}


@router.delete("/devices/{camera_id}")
def delete_device(
    camera_id: str,
    remove_rules: bool = Query(default=False),
    authorization: Optional[str] = Header(default=None),
):
    _, operator = _require_roles(authorization, {"super_admin", "admin"})
    with _yaml_config_lock:
        rules_path, config = _load_rules_for_write()
        cameras = config.setdefault("cameras", [])
        camera = next((item for item in cameras if item.get("id") == camera_id), None)
        if camera is None:
            raise HTTPException(status_code=404, detail=f"设备不存在: {camera_id}")

        rules = config.get("rules", [])
        referenced_rules = [rule.get("id") for rule in rules if rule.get("camera_id") == camera_id]
        if referenced_rules and not remove_rules:
            raise HTTPException(status_code=400, detail=f"设备仍被规则引用，请先删除规则或设置 remove_rules=true: {referenced_rules}")

        config["cameras"] = [item for item in cameras if item.get("id") != camera_id]
        if remove_rules:
            config["rules"] = [rule for rule in rules if rule.get("camera_id") != camera_id]
            for scene in config.get("scenes", []):
                scene_rule_ids = set(scene.get("rule_ids") or [])
                scene["rule_ids"] = [rule_id for rule_id in scene_rule_ids if rule_id not in set(referenced_rules)]
        for scene in config.get("scenes", []):
            scene["cameras"] = [item for item in (scene.get("cameras") or []) if item != camera_id]

        reload_result = _persist_rules_config(rules_path, config)
    storage_service.record_operation(
        module="devices",
        action="delete",
        operator=_operator_name(operator),
        target=camera_id,
        detail={"removed_rules": referenced_rules if remove_rules else [], "reload": reload_result},
    )
    return {"ok": True, "data": _serialize_device(camera, config), "reload": reload_result}

# --- 管理员认证与账号管理接口 ---
@router.post("/auth/login")
def login(payload: LoginRequest):
    user = _authenticate_console_user(payload)
    session = storage_service.create_session(user_id=user["id"])
    # 检测是否为默认密码（内置账号首次登录提示修改）
    is_default = str(user.get("note") or "") == "系统硬核内置账号"
    return {
        "ok": True,
        "user": user,
        "token": session["token"],
        "expires_at": session["expires_at"],
        "force_change_password": is_default,
    }


@router.post("/auth/register")
def register_admin(payload: AdminRegisterRequest, authorization: Optional[str] = Header(default=None)):
    token = _extract_bearer_token(authorization, required=False)
    operator = storage_service.get_session_user(token) if token else None
    if not operator:
        raise HTTPException(
            status_code=403,
            detail="开放注册已关闭，请先使用管理员账号登录，再到管理员账号管理中新增管理员",
        )
    if str(operator.get("role") or "").strip().lower() not in {"super_admin", "admin"}:
        raise HTTPException(status_code=403, detail="当前账号没有新增管理员的权限")

    admin = _create_admin_from_payload(payload)
    storage_service.record_operation(
        module="admins",
        action="create",
        operator=operator["username"],
        target=admin["username"],
        detail={"admin_id": admin["id"], "source": "auth_register"},
    )
    return {
        "ok": True,
        "data": admin,
    }


@router.get("/auth/session")
def auth_session(authorization: Optional[str] = Header(default=None)):
    token = _extract_bearer_token(authorization, required=True)
    user = storage_service.get_session_user(token)
    if not user:
        raise HTTPException(status_code=401, detail="Session expired or invalid")
    return {"ok": True, "user": user}


@router.post("/auth/logout")
def logout(authorization: Optional[str] = Header(default=None)):
    token = _extract_bearer_token(authorization, required=False)
    if token:
        storage_service.revoke_session(token)
    return {"ok": True}


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=6)


@router.post("/auth/change-password")
def change_password(payload: ChangePasswordRequest, authorization: Optional[str] = Header(default=None)):
    token, user = _require_admin_session(authorization)
    if not storage_service.verify_user(user["username"], payload.old_password):
        raise HTTPException(status_code=400, detail="原密码不正确")
    storage_service.reset_user_password(user_id=user["id"], password=payload.new_password)
    storage_service.record_operation(
        module="auth", action="change_password",
        operator=user["username"], target=user["username"], detail={},
    )
    return {"ok": True, "message": "密码修改成功"}


@router.get("/admins")
def list_admins(
    authorization: Optional[str] = Header(default=None),
    keyword: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
):
    _require_roles(authorization, {"super_admin", "admin"})
    return {"data": storage_service.list_admins(keyword=keyword, status=status, limit=limit)}


@router.post("/admins")
def create_admin_account(payload: AdminRegisterRequest, authorization: Optional[str] = Header(default=None)):
    _, operator = _require_roles(authorization, {"super_admin", "admin"})
    admin = _create_admin_from_payload(payload)

    storage_service.record_operation(
        module="admins",
        action="create",
        operator=operator["username"],
        target=admin["username"],
        detail={"admin_id": admin["id"]},
    )
    return {"ok": True, "data": admin}


@router.delete("/admins/{user_id}")
def delete_admin_account(user_id: int, authorization: Optional[str] = Header(default=None)):
    _, operator = _require_roles(authorization, {"super_admin", "admin"})
    if int(operator["id"]) == int(user_id):
        raise HTTPException(status_code=400, detail="当前登录管理员不能删除自己，请先切换账号")

    try:
        admin = storage_service.delete_admin(user_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    storage_service.record_operation(
        module="admins",
        action="delete",
        operator=operator["username"],
        target=admin["username"],
        detail={"admin_id": admin["id"]},
    )
    return {"ok": True, "data": admin}

# --- 4. 真实告警大屏数据接口 ---
@router.get("/alerts/history_data")
def get_real_alerts(limit: int = 50):
    return engine.get_alert_history(limit=limit)

@router.post("/config/update_region")
def update_camera_region(payload: RegionUpdate, authorization: Optional[str] = Header(default=None)):
    """保存前端绘制的坐标并重载引擎"""
    _, operator = _require_admin_session(authorization)

    # 路径定位：这里需要确保 rules.yaml 的路径与加载时一致
    from backend.app.core.config import DEFAULT_RULE_PATH
    
    with open(DEFAULT_RULE_PATH, 'r', encoding='utf-8') as f:
        full_config = yaml.safe_load(f)

    # 准确定位摄像头和防区
    target_cam = next((c for c in full_config.get('cameras', []) if c['id'] == payload.camera_id), None)
    if not target_cam:
        raise HTTPException(status_code=404, detail="摄像头不存在")

    regions = target_cam.get(payload.region_type, [])
    target_region = next((r for r in regions if r['id'] == payload.region_id), None)
    
    if not target_region:
        raise HTTPException(status_code=404, detail="防区 ID 不存在")

    # 核心映射：如果是 ROI 线段，更新 line；如果是 Dwell 多边形，更新 polygon
    if payload.region_type == "rois":
        target_region["line"] = payload.points
    else:
        target_region["polygon"] = payload.points

    # 持久化写回 YAML
    with open(DEFAULT_RULE_PATH, 'w', encoding='utf-8') as f:
        yaml.safe_dump(full_config, f, allow_unicode=True, sort_keys=False)

    # 🔴 关键：调用引擎的热重载，让新坐标立即生效而不丢失当前追踪数据
    _record_region_operation(
        operator=operator,
        action="update",
        camera_id=payload.camera_id,
        region_id=payload.region_id,
        region_type=payload.region_type,
        detail={
            "point_count": len(payload.points),
            "source": "config/update_region",
        },
    )
    return engine.reload_rules()

@router.post("/config/save_region")
def save_camera_region(payload: UpdateRegionRequest, authorization: Optional[str] = Header(default=None)):
    """保存前端绘制的像素级区域坐标到 rules.yaml"""
    _verify_debug_token(authorization)
    
    # 1. 加载当前 YAML 文件
    rules_path = Path(PROJECT_ROOT) / "config" / "rules.yaml" # 根据实际情况微调
    if not rules_path.exists():
        raise HTTPException(status_code=404, detail="rules.yaml not found")
        
    with open(rules_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)

    # 2. 找到对应的摄像头和区域进行修改
    target_cam = next((c for c in config.get('cameras', []) if c['id'] == payload.camera_id), None)
    if not target_cam:
        raise HTTPException(status_code=404, detail="Camera not found")

    regions = target_cam.get(payload.region_type, [])
    target_region = next((r for r in regions if r['id'] == payload.region_id), None)
    
    if not target_region:
        raise HTTPException(status_code=404, detail="Region ID not found")

    # 更新坐标数据
    if payload.region_type == "rois":
        target_region["line"] = payload.points
    else:
        target_region["polygon"] = payload.points

    # 3. 写回文件
    with open(rules_path, 'w', encoding='utf-8') as f:
        yaml.safe_dump(config, f, allow_unicode=True, sort_keys=False)

    # 4. 强制规则引擎重载，立即生效
    engine.reload_rules()
    
    return {"ok": True, "message": f"Region {payload.region_id} updated and engine reloaded"}


@router.get("/alerts/scene/{scene_id}")
def get_alerts_by_scene(scene_id: str, limit: int = Query(default=50, ge=1, le=500)):
    return {"scene_id": scene_id, "data": engine.get_alerts(scene_id=scene_id, limit=limit)}


@router.get("/alerts/history")
def get_alert_history(
    scene_id: Optional[str] = Query(default=None),
    rule_id: Optional[str] = Query(default=None),
    camera_id: Optional[str] = Query(default=None),
    start_time: Optional[str] = Query(default=None),
    end_time: Optional[str] = Query(default=None),
    limit: int = Query(default=2000, ge=1, le=10000),
):
    normalized_start: Optional[str] = None
    normalized_end: Optional[str] = None
    try:
        if start_time:
            normalized_start = _normalize_history_ts(start_time)
        if end_time:
            normalized_end = _normalize_history_ts(end_time)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid history time range: {exc}") from exc

    if normalized_start and normalized_end and normalized_start > normalized_end:
        raise HTTPException(status_code=400, detail="start_time must be earlier than end_time")

    data = engine.get_alert_history(
        scene_id=scene_id,
        rule_id=rule_id,
        camera_id=camera_id,
        start_time=normalized_start,
        end_time=normalized_end,
        limit=limit,
    )
    return {"data": data}


@router.post("/alerts/{alert_id}/workflow")
def update_alert_workflow(
    alert_id: int,
    payload: AlertWorkflowRequest,
    authorization: Optional[str] = Header(default=None),
):
    _, operator = _require_roles(authorization, {"super_admin", "admin", "operator"})
    try:
        workflow = storage_service.update_alert_workflow(
            alert_id=alert_id,
            status=payload.status,
            assignee=payload.assignee,
            note=payload.note,
            handled_by=_operator_name(operator),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    storage_service.record_operation(
        module="alerts",
        action="workflow_update",
        operator=_operator_name(operator),
        target=str(alert_id),
        detail=workflow,
    )
    return {"ok": True, "data": workflow}


@router.post("/ingest/detections")
async def ingest_detections(frame: DetectionFrame):
    """异步处理检测帧：追踪分配、规则评估、DB写入均在后台线程执行"""

    def _process():
        frame.detections = tracking_service.assign_tracks(frame.detections, now=frame.timestamp)
        alerts = engine.evaluate_frame(frame)
        scene_ids = [scene["id"] for scene in engine.get_scenes() if frame.camera_id in scene.get("cameras", [])]
        scene_signals = [engine.get_scene_signals(scene_id) for scene_id in scene_ids]
        engine.record_ingest_frame(
            frame_id=frame.frame_id,
            camera_id=frame.camera_id,
            timestamp=frame.timestamp.isoformat(),
            detection_count=len(frame.detections),
            alert_count=len(alerts),
        )
        return {
            "received": len(frame.detections),
            "alerts_generated": len(alerts),
            "alerts": alerts,
            "scene_signals": [item for item in scene_signals if item],
        }

    return await asyncio.to_thread(_process)


@router.post("/api/acceptance/simulate")
async def acceptance_simulate(
    camera_id: str = Form(..., description="要替换的摄像头ID"),
    file: UploadFile = File(...),
):
    _check_upload_size(file)
    upload_dir = UPLOAD_VIDEO_ROOT / _safe_segment(camera_id)
    upload_dir.mkdir(parents=True, exist_ok=True)

    original_name = Path(file.filename or "").name
    suffix = Path(original_name).suffix.lower()
    if suffix not in ALLOWED_VIDEO_SUFFIX:
        raise HTTPException(status_code=400, detail=f"Unsupported video format: {suffix}")

    stem = _safe_segment(Path(original_name).stem)
    ts = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y%m%d_%H%M%S")
    file_path = upload_dir / f"{ts}_{stem}{suffix}"
    with file_path.open("wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    rules_path = engine.rule_path
    config = load_rules(rules_path)
    target_camera = next((c for c in config.get("cameras", []) if c.get("id") == camera_id), None)

    if not target_camera:
        raise HTTPException(status_code=404, detail="找不到该摄像头配置")

    _remember_original_stream(target_camera)

    target_camera["stream"] = _relative_stream_value(file_path)

    with Path(rules_path).open("w", encoding="utf-8") as f:
        yaml.safe_dump(config, f, allow_unicode=True, sort_keys=False)

    engine.reload_rules()
    return {
        "status": "success",
        "msg": f"视频已上传并绑定到 {camera_id}，打开对应监控画面后会围绕该视频进行识别、告警和回放。",
        "stored_path": str(file_path.resolve()),
        "stream_value": target_camera["stream"],
    }


@router.post("/api/acceptance/restore")
async def acceptance_restore(camera_id: str = Form(...)):
    rules_path = engine.rule_path
    config = load_rules(rules_path)
    target_camera = next((c for c in config.get("cameras", []) if c.get("id") == camera_id), None)

    if not target_camera:
        raise HTTPException(status_code=404, detail="找不到该摄像头配置")

    restore_stream = str(target_camera.get("original_stream") or "").strip()
    if not restore_stream:
        restore_stream = _default_restore_stream(target_camera)

    if str(target_camera.get("stream") or "").strip() == restore_stream:
        return {"status": "info", "msg": "已经是真实摄像头，无需恢复。"}

    target_camera["stream"] = restore_stream
    target_camera["original_stream"] = restore_stream

    with Path(rules_path).open("w", encoding="utf-8") as f:
        yaml.safe_dump(config, f, allow_unicode=True, sort_keys=False)

    engine.reload_rules()
    return {"status": "success", "msg": f"{camera_id} 已恢复为真实摄像头！"}


@router.get("/config/rules")
def show_rules():
    return engine.rules_config


@router.get("/config/scenes")
def show_scenes():
    return {"data": engine.get_scenes()}


@router.post("/config/reload")
def reload_config(authorization: Optional[str] = Header(default=None)):
    _verify_debug_token(authorization)
    return engine.reload_rules()


@router.post("/ops/reset-counts")
def reset_cumulative_counts(authorization: Optional[str] = Header(default=None)):
    """清除所有规则的累计计数（翻越围栏人数、滞留人数等），仅管理员可操作"""
    _, operator = _require_roles(authorization, {"super_admin", "admin"})
    result = engine.reset_cumulative_counts()
    storage_service.record_operation(
        module="ops",
        action="reset_counts",
        operator=operator["username"],
        target="cumulative_counts",
        detail=result,
    )
    return {"ok": True, "data": result}


@router.post("/ops/reset-alerts")
def reset_all_alerts(authorization: Optional[str] = Header(default=None)):
    """清空所有告警记录及关联工作流，仅管理员可操作"""
    _, operator = _require_roles(authorization, {"super_admin", "admin"})
    result = storage_service.clear_alerts()
    storage_service.record_operation(
        module="ops",
        action="reset_alerts",
        operator=operator["username"],
        target="all_alerts",
        detail=result,
    )
    return {"ok": True, "data": result}


@router.get("/signals/scenes")
def get_scene_signals():
    return {"data": engine.get_scene_signals()}


@router.get("/signals/scenes/{scene_id}")
def get_scene_signal(scene_id: str):
    signal = engine.get_scene_signals(scene_id)
    if not signal:
        raise HTTPException(status_code=404, detail=f"Scene not found: {scene_id}")
    return signal


@router.get("/signals/history/{scene_id}")
def get_scene_signal_history(scene_id: str, limit: int = Query(default=200, ge=1, le=2000)):
    return {"scene_id": scene_id, "data": engine.get_scene_signal_history(scene_id, limit=limit)}


@router.get("/signals/output/{scene_id}")
def get_output_signal(scene_id: str, lang: str = Query(default="cn", pattern="^(cn|en)$")):
    signal = engine.get_scene_signals(scene_id)
    if not signal:
        raise HTTPException(status_code=404, detail=f"Scene not found: {scene_id}")
    return signal["signals_cn"] if lang == "cn" else signal["signals"]


@router.get("/runtime/status")
def runtime_status():
    return {
        "engine": engine.get_runtime_status(),
        "tracker": tracking_service.tracker_runtime_state(),
        "detector": vision_backend_service.active_runtime_status(),
        "vision_backend": vision_backend_service.status(),
    }


@router.get("/settings")
def get_system_settings(authorization: Optional[str] = Header(default=None)):
    _require_roles(authorization, {"super_admin", "admin", "operator", "viewer"})
    return {"data": storage_service.get_system_settings()}


@router.post("/settings")
def update_system_settings(payload: SystemSettingsRequest, authorization: Optional[str] = Header(default=None)):
    _, operator = _require_roles(authorization, {"super_admin", "admin"})
    settings = storage_service.update_system_settings(payload.model_dump(), updated_by=_operator_name(operator))
    storage_service.record_operation(
        module="settings",
        action="update",
        operator=_operator_name(operator),
        target="system_settings",
        detail=settings,
    )
    return {"ok": True, "data": settings}


@router.get("/vision/backend/status")
def vision_backend_status(
    scene_id: Optional[str] = Query(default=None),
    camera_id: Optional[str] = Query(default=None),
):
    status = vision_backend_service.status(
        {
            "scenes": engine.get_scenes(),
            "cameras": engine.rules_config.get("cameras", []),
        }
    )
    effective_runtime = vision_backend_service.active_runtime_status(scene_id=scene_id, camera_id=camera_id)
    status["effective_backend"] = effective_runtime.get("backend_key", status.get("active_backend"))
    status["effective_backend_label"] = effective_runtime.get("backend_label", status.get("active_backend_label"))
    status["effective_pipeline"] = effective_runtime.get("pipeline", status.get("active_pipeline"))
    status["requested_scene_id"] = scene_id
    status["requested_camera_id"] = camera_id
    return status


@router.post("/vision/backend/activate")
def activate_vision_backend(
    payload: VisionBackendActivateRequest,
    authorization: Optional[str] = Header(default=None),
):
    _, operator = _require_admin_session(authorization)
    try:
        status = vision_backend_service.activate(payload.backend)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    storage_service.record_operation(
        module="vision_backend",
        action="activate",
        operator=operator["username"],
        target=status.get("active_backend", payload.backend),
        detail={
            "active_backend": status.get("active_backend"),
            "active_pipeline": status.get("active_pipeline"),
        },
    )
    return {"ok": True, "data": status}


@router.post("/vision/backend/config")
def update_vision_backend_config(
    payload: VisionBackendConfigRequest,
    authorization: Optional[str] = Header(default=None),
):
    _, operator = _require_admin_session(authorization)
    try:
        status = vision_backend_service.update_config(
            default_backend=payload.default_backend,
            scene_overrides=payload.scene_overrides,
            camera_overrides=payload.camera_overrides,
            video_understanding=payload.video_understanding.model_dump(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    storage_service.record_operation(
        module="vision_backend",
        action="update_config",
        operator=operator["username"],
        target=status.get("active_backend", payload.default_backend),
        detail={
            "default_backend": payload.default_backend,
            "scene_overrides": payload.scene_overrides,
            "camera_overrides": payload.camera_overrides,
            "provider_mode": payload.video_understanding.provider_mode,
            "api_url": payload.video_understanding.api_url,
            "model": payload.video_understanding.model,
        },
    )
    return {
        "ok": True,
        "data": vision_backend_service.status(
            {
                "scenes": engine.get_scenes(),
                "cameras": engine.rules_config.get("cameras", []),
            }
        ),
    }


@router.get("/runtime/ingest/recent")
def recent_ingest(limit: int = Query(default=50, ge=1, le=1000)):
    return {"data": storage_service.get_latest_ingest_stats(limit=limit)}


@router.post("/agent/chat", response_model=AgentChatResponse)
def agent_chat_endpoint(payload: AgentChatRequest, authorization: Optional[str] = Header(default=None)):
    _require_roles(authorization, {"super_admin", "admin", "operator"})
    return agent_chat(
        query=payload.query,
        scene_id=payload.scene_id,
        camera_id=payload.camera_id,
        limit=payload.limit,
    )


@router.get("/agent/status", response_model=AgentStatusResponse)
def agent_status_endpoint():
    return agent_status()


@router.get("/users")
def list_users(
    authorization: Optional[str] = Header(default=None),
    keyword: Optional[str] = Query(default=None),
    role: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
):
    _require_roles(authorization, {"super_admin", "admin", "operator", "viewer"})
    return {"data": storage_service.list_users(keyword=keyword, role=role, status=status, limit=limit)}


@router.post("/users")
def create_user(payload: UserCreateRequest, authorization: Optional[str] = Header(default=None)):
    _, operator = _require_roles(authorization, {"super_admin", "admin"})
    try:
        user = storage_service.create_user(
            username=payload.username,
            display_name=payload.display_name,
            role=payload.role,
            password=payload.password,
            note=payload.note,
            status=payload.status,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    storage_service.record_operation(
        module="users",
        action="create",
        operator=operator["username"],
        target=user["username"],
        detail={"user_id": user["id"], "role": user["role"], "status": user["status"]},
    )
    return {"ok": True, "data": user}


@router.put("/users/{user_id}")
def update_user(user_id: int, payload: UserUpdateRequest, authorization: Optional[str] = Header(default=None)):
    _, operator = _require_roles(authorization, {"super_admin", "admin"})
    try:
        user = storage_service.update_user(
            user_id=user_id,
            username=payload.username,
            display_name=payload.display_name,
            role=payload.role,
            note=payload.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    storage_service.record_operation(
        module="users",
        action="update",
        operator=operator["username"],
        target=user["username"],
        detail={"user_id": user["id"], "role": user["role"]},
    )
    return {"ok": True, "data": user}


@router.post("/users/{user_id}/status")
def set_user_status(user_id: int, payload: UserStatusRequest, authorization: Optional[str] = Header(default=None)):
    _, operator = _require_roles(authorization, {"super_admin", "admin"})
    try:
        user = storage_service.set_user_status(user_id=user_id, status=payload.status)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    storage_service.record_operation(
        module="users",
        action="set_status",
        operator=operator["username"],
        target=user["username"],
        detail={"user_id": user["id"], "status": user["status"]},
    )
    return {"ok": True, "data": user}


@router.post("/users/{user_id}/reset-password")
def reset_user_password(user_id: int, payload: UserPasswordResetRequest, authorization: Optional[str] = Header(default=None)):
    _, operator = _require_roles(authorization, {"super_admin", "admin"})
    try:
        user = storage_service.reset_user_password(user_id=user_id, password=payload.password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    storage_service.record_operation(
        module="users",
        action="reset_password",
        operator=operator["username"],
        target=user["username"],
        detail={"user_id": user["id"]},
    )
    return {"ok": True, "data": user}


@router.delete("/users/{user_id}")
def delete_user_account(user_id: int, authorization: Optional[str] = Header(default=None)):
    _, operator = _require_roles(authorization, {"super_admin", "admin"})
    if int(operator["id"]) == int(user_id):
        raise HTTPException(status_code=400, detail="当前登录账号不能删除自己，请先切换账号")

    try:
        user = storage_service.delete_user(user_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    storage_service.record_operation(
        module="users",
        action="delete",
        operator=_operator_name(operator),
        target=user["username"],
        detail={"user_id": user["id"], "role": user["role"]},
    )
    return {"ok": True, "data": user}


@router.get("/logs/operations")
def get_operation_logs(
    authorization: Optional[str] = Header(default=None),
    module: Optional[str] = Query(default=None),
    keyword: Optional[str] = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
):
    _require_admin_session(authorization)
    return {"data": storage_service.list_operation_logs(module=module, keyword=keyword, limit=limit)}


@router.get("/logs/system/files", response_model=LogFilesResponse)
def list_log_files(authorization: Optional[str] = Header(default=None)):
    _require_admin_session(authorization)
    return {
        "files": [
            {
                "key": key,
                "path": str(path),
                "exists": path.exists(),
                "size": path.stat().st_size if path.exists() else 0,
                "modified_at": datetime.fromtimestamp(path.stat().st_mtime).isoformat() if path.exists() else "",
            }
            for key, path in LOG_FILES.items()
        ]
    }


@router.get("/logs/system/{log_key}", response_model=LogFileContentResponse)
def read_system_log(
    log_key: str,
    tail: int = Query(default=200, ge=1, le=2000),
    authorization: Optional[str] = Header(default=None),
):
    _require_admin_session(authorization)
    path = LOG_FILES.get(log_key)
    if path is None:
        raise HTTPException(status_code=404, detail=f"Unknown log file: {log_key}")
    return {
        "file": log_key,
        "path": str(path),
        "lines": _read_log_lines(path, tail=tail),
    }


@router.get("/logs/system", response_model=SystemLogsResponse)
def get_system_logs(
    tail: int = Query(default=100, ge=1, le=2000),
    authorization: Optional[str] = Header(default=None),
):
    _require_admin_session(authorization)
    return {
        "app": _read_log_lines(APP_LOG_PATH, tail=tail),
        "error": _read_log_lines(ERROR_LOG_PATH, tail=tail),
    }


@router.get("/ops/health")
def ops_health(authorization: Optional[str] = Header(default=None)):
    _require_roles(authorization, {"super_admin", "admin", "operator", "viewer"})
    return {"data": collect_health()}


@router.post("/ops/backup")
def ops_backup(payload: BackupRequest, authorization: Optional[str] = Header(default=None)):
    _, operator = _require_roles(authorization, {"super_admin", "admin"})
    result = create_backup(include_videos=payload.include_videos)
    storage_service.record_operation(
        module="ops",
        action="backup",
        operator=_operator_name(operator),
        target=result["backup_name"],
        detail=result,
    )
    return {"ok": True, "data": result}


@router.post("/ops/cleanup")
def ops_cleanup(payload: CleanupRequest, authorization: Optional[str] = Header(default=None)):
    _, operator = _require_roles(authorization, {"super_admin", "admin"})
    settings = storage_service.get_system_settings()
    result = cleanup_runtime(
        retention_days=payload.retention_days or int(settings.get("retention_days") or 30),
        replay_retention_days=payload.replay_retention_days or int(settings.get("replay_retention_days") or 30),
        backup_retention_days=payload.backup_retention_days,
        dry_run=payload.dry_run,
    )
    storage_service.record_operation(
        module="ops",
        action="cleanup_preview" if payload.dry_run else "cleanup",
        operator=_operator_name(operator),
        target="runtime_files",
        detail={k: v for k, v in result.items() if k != "items"},
    )
    return {"ok": True, "data": result}


@router.get("/replay/resolve")
def resolve_replay_directory(
    camera_id: str = Query(...),
    timestamp: str = Query(...),
    scene_id: str = Query(default=""),
    rule_id: str = Query(default=""),
):
    try:
        event_time = _parse_event_timestamp(timestamp)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid timestamp: {exc}") from exc

    candidates = _get_replay_candidate_dirs(
        camera_id=camera_id,
        event_time=event_time,
        scene_id=scene_id,
        rule_id=rule_id,
    )
    replay_dir = candidates[0] if candidates else REPLAY_ROOT
    for candidate in candidates:
        if candidate.exists() and candidate.is_dir():
            replay_dir = candidate
            break

    files = []
    if replay_dir.exists() and replay_dir.is_dir():
        files = [_serialize_file(path) for path in sorted(replay_dir.iterdir()) if path.is_file()]

    return {
        "camera_id": camera_id,
        "event_timestamp": event_time.isoformat(),
        "replay_dir": str(replay_dir),
        "replay_dir_exists": replay_dir.exists() and replay_dir.is_dir(),
        "replay_layout": REPLAY_LAYOUT,
        "candidate_dirs": [str(p) for p in candidates],
        "file_count": len(files),
        "files": files[:500],
        "open_uri": replay_dir.resolve().as_uri() if replay_dir.exists() else "",
    }


@router.get("/replay/download")
def download_replay_file(
    camera_id: str = Query(...),
    timestamp: str = Query(...),
    name: str = Query(...),
    scene_id: str = Query(default=""),
    rule_id: str = Query(default=""),
):
    try:
        event_time = _parse_event_timestamp(timestamp)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid timestamp: {exc}") from exc

    if Path(name).name != name:
        raise HTTPException(status_code=400, detail="Invalid replay file name")

    candidates = _get_replay_candidate_dirs(
        camera_id=camera_id,
        event_time=event_time,
        scene_id=scene_id,
        rule_id=rule_id,
    )

    file_path = None
    for replay_dir in candidates:
        candidate_file = replay_dir / name
        if (
            candidate_file.exists()
            and candidate_file.is_file()
            and any(_is_inside_path(candidate_file, allowed_root) for allowed_root in [REPLAY_ROOT, DATA_DIR / "replay_clips"])
        ):
            file_path = candidate_file
            break

    if file_path is None:
        info = replay_service.get_replay_info_for_event(
            camera_id=camera_id,
            event_time=event_time,
            scene_id=scene_id,
            rule_id=rule_id,
        )
        video_path = Path(str(info.get("video_path") or ""))
        if (
            video_path.exists()
            and video_path.is_file()
            and video_path.name == name
            and any(_is_inside_path(video_path, allowed_root) for allowed_root in [REPLAY_ROOT, DATA_DIR / "replay_clips", UPLOAD_VIDEO_ROOT])
        ):
            file_path = video_path

    if file_path is None:
        raise HTTPException(status_code=404, detail="Replay file not found")

    return FileResponse(path=file_path, filename=file_path.name)


@router.get("/replay/info")
def get_replay_info(
    camera_id: str = Query(...),
    timestamp: str = Query(...),
    scene_id: str = Query(default=""),
    rule_id: str = Query(default=""),
):
    try:
        event_time = _parse_event_timestamp(timestamp)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid timestamp: {exc}") from exc

    candidates = _get_replay_candidate_dirs(
        camera_id=camera_id,
        event_time=event_time,
        scene_id=scene_id,
        rule_id=rule_id,
    )
    replay_dir = candidates[0] if candidates else REPLAY_ROOT
    for candidate in candidates:
        if candidate.exists() and candidate.is_dir():
            replay_dir = candidate
            break

    info = replay_service.get_replay_info(
        camera_id=camera_id,
        event_time=event_time,
        replay_dir=replay_dir,
        candidate_dirs=candidates,
    )
    info["candidate_dirs"] = [str(path) for path in candidates]
    info["replay_layout"] = REPLAY_LAYOUT
    info["rule_label"] = engine.get_rule_display_label(rule_id)
    info["video_analysis"] = storage_service.get_video_analysis(
        event_timestamp=event_time.isoformat(),
        scene_id=scene_id,
        rule_id=rule_id,
        camera_id=camera_id,
    )
    return info


@router.get("/replay/detections")
def get_replay_detections(
    camera_id: str = Query(...),
    timestamp: str = Query(...),
    scene_id: str = Query(default=""),
    rule_id: str = Query(default=""),
    offset: float = Query(default=0.0, ge=0.0),
    preview: str = Query(default="person", pattern="^(person|all)$"),
):
    try:
        event_time = _parse_event_timestamp(timestamp)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid timestamp: {exc}") from exc

    info = replay_service.get_replay_info_for_event(
        camera_id=camera_id,
        event_time=event_time,
        scene_id=scene_id,
        rule_id=rule_id,
    )
    video_path_text = str(info.get("video_path") or "").strip()
    if not video_path_text:
        raise HTTPException(status_code=404, detail="未找到可用于叠框的回放视频")

    video_path = Path(video_path_text)
    if not video_path.exists() or not video_path.is_file():
        raise HTTPException(status_code=404, detail="回放视频文件不存在")

    from backend.app.services.yolo_service import yolo_service

    cap = cv2.VideoCapture(str(video_path))
    try:
        if not cap.isOpened():
            raise HTTPException(status_code=400, detail="无法打开回放视频")

        video_duration = float(info.get("video_duration") or 0.0)
        target_offset = max(0.0, float(offset or 0.0))
        if video_duration > 0:
            target_offset = min(target_offset, max(0.0, video_duration - 0.05))

        cap.set(cv2.CAP_PROP_POS_MSEC, target_offset * 1000.0)
        ok, frame = cap.read()
        if not ok or frame is None:
            raise HTTPException(status_code=404, detail="无法读取当前播放帧")

        class_ids = yolo_service.default_preview_class_ids(preview)
        detections = yolo_service.detect_frame(
            frame,
            camera_id=camera_id,
            classes=class_ids or None,
            conf=0.22,
            imgsz=512,
        )
        return {
            "ok": True,
            "camera_id": camera_id,
            "scene_id": scene_id,
            "rule_id": rule_id,
            "rule_label": engine.get_rule_display_label(rule_id),
            "video_name": video_path.name,
            "source_offset": target_offset,
            "frame_width": int(frame.shape[1]),
            "frame_height": int(frame.shape[0]),
            "detections": [_serialize_detection(det) for det in detections],
        }
    finally:
        cap.release()


@router.get("/replay/analyze")
def analyze_replay_clip(
    camera_id: str = Query(...),
    timestamp: str = Query(...),
    scene_id: str = Query(default=""),
    rule_id: str = Query(default=""),
    before: int = Query(default=DEFAULT_CLIP_BEFORE_SECONDS, ge=0, le=300),
    after: int = Query(default=DEFAULT_CLIP_AFTER_SECONDS, ge=0, le=600),
    force: bool = Query(default=False),
):
    from backend.app.services.mimo_video_client import mimo_video_client

    try:
        event_time = _parse_event_timestamp(timestamp)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid timestamp: {exc}") from exc

    # Re-read .env so an API key added while the backend was running is picked up.
    mimo_video_client.reload()

    existing = storage_service.get_video_analysis(
        event_timestamp=event_time.isoformat(),
        scene_id=scene_id,
        rule_id=rule_id,
        camera_id=camera_id,
    )
    if (
        existing
        and existing.get("analysis_available")
        and not force
        and not _uses_legacy_temp_replay_clip(existing)
        and not _uses_rule_only_video_analysis(existing)
    ):
        return {
            "ok": True,
            "data": existing,
            "cached": True,
        }

    info = replay_service.get_replay_info_for_event(
        camera_id=camera_id,
        event_time=event_time,
        scene_id=scene_id,
        rule_id=rule_id,
    )
    if not info.get("video_found"):
        raise HTTPException(status_code=404, detail="未找到可用于分析的回放视频")

    clip_path, _ = replay_service.generate_clip_for_event(
        camera_id=camera_id,
        event_time=event_time,
        scene_id=scene_id,
        rule_id=rule_id,
        before_seconds=before,
        after_seconds=after,
    )
    if not mimo_video_client.is_enabled:
        raise HTTPException(status_code=400, detail="MiMo 视频理解尚未配置，请先填写 MIMO_API_KEY")

    related_alerts = engine.get_alert_history(
        scene_id=scene_id or None,
        rule_id=rule_id or None,
        camera_id=camera_id,
        start_time=event_time.isoformat(),
        end_time=event_time.isoformat(),
        limit=1,
    )
    alert_message = str((related_alerts[0] if related_alerts else {}).get("message") or "")

    analysis_target_path = str(clip_path or info.get("video_path") or "")
    analysis_target_kind = "clip" if clip_path else "source_video"
    if not analysis_target_path:
        raise HTTPException(status_code=404, detail="未找到可用于分析的视频源")

    analysis = mimo_video_client.analyze_security_event_clip(
        video_path_or_url=analysis_target_path,
        camera_id=camera_id,
        scene_id=scene_id,
        rule_id=rule_id,
        alert_message=alert_message,
        rule_context=engine.get_rule_context(rule_id, camera_id),
    )
    stored = storage_service.upsert_video_analysis(
        event_timestamp=event_time.isoformat(),
        scene_id=scene_id,
        rule_id=rule_id,
        camera_id=camera_id,
        source_video_path=str(info.get("video_path") or ""),
        clip_path=str(clip_path or ""),
        clip_before_seconds=before,
        clip_after_seconds=after,
        provider="mimo_video",
        model=str(analysis.get("model") or mimo_video_client.model),
        summary=str(analysis.get("summary") or ""),
        risk_assessment=str(analysis.get("risk_assessment") or ""),
        analysis=analysis,
        error=str(analysis.get("error") or ""),
        analysis_available=bool(analysis.get("analysis_available")),
    )
    return {
        "ok": True,
        "data": stored,
        "cached": False,
        "analysis_target_kind": analysis_target_kind,
        "analysis_target_path": analysis_target_path,
        "clip_generation_available": bool(clip_path),
    }


@router.get("/replay/clip")
def get_replay_clip(
    camera_id: str = Query(...),
    timestamp: str = Query(...),
    scene_id: str = Query(default=""),
    rule_id: str = Query(default=""),
    before: int = Query(default=DEFAULT_CLIP_BEFORE_SECONDS, ge=0, le=300),
    after: int = Query(default=DEFAULT_CLIP_AFTER_SECONDS, ge=0, le=600),
):
    try:
        event_time = _parse_event_timestamp(timestamp)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid timestamp: {exc}") from exc

    candidates = _get_replay_candidate_dirs(
        camera_id=camera_id,
        event_time=event_time,
        scene_id=scene_id,
        rule_id=rule_id,
    )
    replay_dir = candidates[0] if candidates else REPLAY_ROOT
    for candidate in candidates:
        if candidate.exists() and candidate.is_dir():
            replay_dir = candidate
            break

    clip_path = replay_service.generate_clip(
        camera_id=camera_id,
        event_time=event_time,
        replay_dir=replay_dir,
        before_seconds=before,
        after_seconds=after,
        candidate_dirs=candidates,
    )
    if clip_path is None:
        raise HTTPException(status_code=404, detail="无法生成回放片段，可能没有找到对应的录像文件或未安装 ffmpeg")

    file_path = Path(clip_path)
    return FileResponse(path=file_path, filename=f"replay_{camera_id}_{event_time.strftime('%Y%m%d_%H%M%S')}.mp4")


@router.get("/stream/{camera_id}")
def stream_camera(
    camera_id: str,
    preview: str = Query(default="person", pattern="^(person|all)$"),
    token: Optional[str] = Query(default=None, description="Bearer token for auth (img tags cannot send headers)"),
    authorization: Optional[str] = Header(default=None),
):
    auth = authorization or (f"Bearer {token}" if token else None)
    if auth:
        _require_roles(auth, {"super_admin", "admin", "operator", "viewer"})
    return StreamingResponse(
        mjpeg_stream(camera_id, preview_mode=preview),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache"},
    )


@router.post("/debug/login")
def debug_login(payload: DebugLoginRequest):
    _require_debug_tools_enabled()
    _cleanup_expired_tokens()
    if not secrets.compare_digest(payload.username, DEBUG_USERNAME) or not secrets.compare_digest(payload.password, DEBUG_PASSWORD):
        raise HTTPException(status_code=401, detail="Invalid username or password")

    token = secrets.token_urlsafe(24)
    expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=DEBUG_TOKEN_HOURS)
    DEBUG_TOKENS[token] = expires_at
    return {"token": token, "token_type": "Bearer", "expires_at": expires_at.isoformat()}


@router.post("/api/detect_loitering")
async def detect_loitering_api(
    background_tasks: BackgroundTasks,
    camera_id: str = Form("cam_fence", description="选择要应用规则的摄像头配置"),
    file: UploadFile = File(...),
    stay_limit: int = Form(10),
):
    _check_upload_size(file)
    task_id = str(uuid.uuid4())
    upload_dir = Path("data/outputs")
    upload_dir.mkdir(parents=True, exist_ok=True)
    save_path = upload_dir / f"offline_{task_id}_{file.filename}"

    with save_path.open("wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    _store_offline_result(task_id, {"status": "processing"})

    background_tasks.add_task(
        process_offline_video_task,
        task_id=task_id,
        video_path=save_path,
        camera_id=camera_id,
        stay_limit=stay_limit,
    )
    return {"status": "processing", "task_id": task_id, "msg": "视频已进入独立分析队列"}


@router.get("/api/detect_loitering/{task_id}")
async def get_loitering_status(task_id: str):
    result = OFFLINE_ANALYSIS_RESULTS.get(task_id)
    if not result:
        raise HTTPException(status_code=404, detail="任务不存在")
    return result


@router.post("/debug/upload-video")
async def debug_upload_video(
    camera_id: str = Query(..., description="Camera id from config/rules.yaml"),
    file: UploadFile = File(...),
    authorization: Optional[str] = Header(default=None),
):
    _verify_debug_token(authorization)
    _check_upload_size(file)

    camera = engine.get_camera(camera_id)
    if not camera:
        raise HTTPException(status_code=404, detail=f"Camera not found: {camera_id}")

    original_name = Path(file.filename or "").name
    if not original_name:
        raise HTTPException(status_code=400, detail="Missing file name")

    suffix = Path(original_name).suffix.lower()
    if suffix not in ALLOWED_VIDEO_SUFFIX:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported video format: {suffix}. allowed={sorted(ALLOWED_VIDEO_SUFFIX)}",
        )

    UPLOAD_VIDEO_ROOT.mkdir(parents=True, exist_ok=True)
    camera_dir = UPLOAD_VIDEO_ROOT / _safe_segment(camera_id)
    camera_dir.mkdir(parents=True, exist_ok=True)

    stem = _safe_segment(Path(original_name).stem)
    ts = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y%m%d_%H%M%S")
    stored_name = f"{ts}_{stem}{suffix}"
    target_path = camera_dir / stored_name
    with target_path.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    if target_path.stat().st_size == 0:
        target_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    stream_value = _relative_stream_value(target_path)
    rules_path = engine.rule_path
    config = load_rules(rules_path)
    cameras = config.get("cameras", [])
    target = next((camera for camera in cameras if camera.get("id") == camera_id), None)
    if target is None:
        raise HTTPException(status_code=404, detail=f"Camera not found in config: {camera_id}")
    _remember_original_stream(target)
    target["stream"] = stream_value
    with Path(rules_path).open("w", encoding="utf-8") as f:
        yaml.safe_dump(config, f, allow_unicode=True, sort_keys=False)
    reload_result = engine.reload_rules()

    return {
        "ok": True,
        "camera_id": camera_id,
        "uploaded_file": original_name,
        "stored_path": str(target_path.resolve()),
        "stream_value": stream_value,
        "next_step": (
            "Use webcam_pipeline with --source pointing to stream_value and --camera-id "
            f"{camera_id} for full-chain ingest."
        ),
        "reload": reload_result,
    }


@router.post("/debug/bind-video")
def debug_bind_video(
    camera_id: str = Query(..., description="Camera id from config/rules.yaml"),
    video_path: str = Query(..., description="Absolute or project-relative video path"),
    authorization: Optional[str] = Header(default=None),
):
    _verify_debug_token(authorization)

    camera = engine.get_camera(camera_id)
    if not camera:
        raise HTTPException(status_code=404, detail=f"Camera not found: {camera_id}")

    candidate = Path(video_path.strip())
    if not candidate.is_absolute():
        candidate = (PROJECT_ROOT / candidate).resolve()
    if not candidate.exists() or not candidate.is_file():
        raise HTTPException(status_code=404, detail=f"Video file not found: {candidate}")
    if not _is_inside_path(candidate, PROJECT_ROOT):
        raise HTTPException(status_code=403, detail="视频文件必须位于项目目录内")

    stream_value = _relative_stream_value(candidate)
    rules_path = engine.rule_path
    config = load_rules(rules_path)
    cameras = config.get("cameras", [])
    target = next((cam for cam in cameras if cam.get("id") == camera_id), None)
    if target is None:
        raise HTTPException(status_code=404, detail=f"Camera not found in config: {camera_id}")
    _remember_original_stream(target)
    target["stream"] = stream_value
    with Path(rules_path).open("w", encoding="utf-8") as f:
        yaml.safe_dump(config, f, allow_unicode=True, sort_keys=False)
    reload_result = engine.reload_rules()
    return {
        "ok": True,
        "camera_id": camera_id,
        "bound_path": str(candidate),
        "stream_value": stream_value,
        "reload": reload_result,
    }


@router.post("/debug/bind-stream")
def debug_bind_stream(
    camera_id: str = Query(..., description="Camera id from config/rules.yaml"),
    stream_url: str = Query(..., description="LAN camera stream URL, e.g. http://192.168.1.23:8080/video or rtsp://..."),
    authorization: Optional[str] = Header(default=None),
):
    _verify_debug_token(authorization)

    camera = engine.get_camera(camera_id)
    if not camera:
        raise HTTPException(status_code=404, detail=f"Camera not found: {camera_id}")

    stream_value = _validate_network_stream_url(stream_url)
    rules_path = engine.rule_path
    config = load_rules(rules_path)
    cameras = config.get("cameras", [])
    target = next((cam for cam in cameras if cam.get("id") == camera_id), None)
    if target is None:
        raise HTTPException(status_code=404, detail=f"Camera not found in config: {camera_id}")

    _remember_original_stream(target)
    target["stream"] = stream_value
    with Path(rules_path).open("w", encoding="utf-8") as f:
        yaml.safe_dump(config, f, allow_unicode=True, sort_keys=False)
    reload_result = engine.reload_rules()

    return {
        "ok": True,
        "camera_id": camera_id,
        "stream_value": stream_value,
        "message": "局域网实时摄像头地址已绑定，打开对应监控页即可查看实时检测视频。",
        "reload": reload_result,
    }


@router.post("/debug/restore-stream")
def debug_restore_stream(
    camera_id: str = Query(..., description="Camera id from config/rules.yaml"),
    authorization: Optional[str] = Header(default=None),
):
    _verify_debug_token(authorization)

    rules_path = engine.rule_path
    config = load_rules(rules_path)
    cameras = config.get("cameras", [])
    target = next((cam for cam in cameras if cam.get("id") == camera_id), None)
    if target is None:
        raise HTTPException(status_code=404, detail=f"Camera not found in config: {camera_id}")

    stream_value = str(target.get("original_stream") or _default_restore_stream(target))
    target["stream"] = stream_value
    with Path(rules_path).open("w", encoding="utf-8") as f:
        yaml.safe_dump(config, f, allow_unicode=True, sort_keys=False)
    reload_result = engine.reload_rules()

    return {
        "ok": True,
        "camera_id": camera_id,
        "stream_value": stream_value,
        "message": "已恢复该摄像头的原始视频源。",
        "reload": reload_result,
    }


class ZoneUpdateRequest(BaseModel):
    region_type: str = Field(..., description="'rois' or 'dwell_zones'")
    points: list[list[float]] = Field(..., description="Array of coordinates")
    drawing_mode: str = Field(default="default", pattern="^(default|line|trajectory)$")
    path_width: Optional[float] = Field(default=None, ge=0.01, le=0.30)


class DwellThresholdRequest(BaseModel):
    threshold_seconds: int = Field(..., ge=1, le=3600)
    zone_id: Optional[str] = Field(default=None, max_length=120)


@router.post("/api/config/camera/{camera_id}/region/{region_id}")
def update_camera_region(
    camera_id: str,
    region_id: str,
    payload: ZoneUpdateRequest,
    authorization: Optional[str] = Header(default=None),
):
    _, operator = _require_admin_session(authorization)
    rules_path = engine.rule_path
    config = load_rules(rules_path)
    cameras = config.get("cameras", [])
    target = next((camera for camera in cameras if camera.get("id") == camera_id), None)
    if target is None:
        raise HTTPException(status_code=404, detail=f"Camera not found: {camera_id}")

    rt = payload.region_type
    if rt not in ["rois", "dwell_zones"]:
        raise HTTPException(status_code=400, detail="Invalid region type")

    audit_detail = {
        "point_count": len(payload.points),
        "source": "api/config/camera",
    }
    regions = target.get(rt, [])
    region_target = next((r for r in regions if r.get("id") == region_id), None)
    if not region_target:
        raise HTTPException(status_code=404, detail=f"Region not found: {region_id}")

    if rt == "rois":
        drawing_mode = str(payload.drawing_mode or "default").strip().lower()
        if drawing_mode == "default":
            drawing_mode = "trajectory" if len(payload.points) > 2 else "line"

        if drawing_mode == "trajectory":
            path_points = _normalize_path_points(payload.points)
            if len(path_points) < 2:
                raise HTTPException(status_code=400, detail="Trajectory corridor needs at least 2 sampled points")
            path_width = round(float(payload.path_width or region_target.get("path_width") or 0.08), 4)
            region_target["path_points"] = path_points
            region_target["path_width"] = path_width
            region_target["polygon"] = _build_path_corridor_polygon(path_points, path_width)
            region_target["line"] = []
            region_target["draw_mode"] = "trajectory"
            audit_detail["draw_mode"] = "trajectory"
            audit_detail["path_width"] = path_width
        else:
            line_points = _normalize_path_points(payload.points)[:2]
            if len(line_points) < 2:
                raise HTTPException(status_code=400, detail="Boundary line needs 2 points")
            region_target["line"] = line_points
            region_target["polygon"] = []
            region_target["path_points"] = []
            region_target["path_width"] = round(float(payload.path_width or region_target.get("path_width") or 0.08), 4)
            region_target["draw_mode"] = "line"
            audit_detail["draw_mode"] = "line"
            audit_detail["path_width"] = float(region_target["path_width"])
    else:
        region_target["polygon"] = payload.points
        audit_detail["polygon_point_count"] = len(payload.points)

    with Path(rules_path).open("w", encoding="utf-8") as f:
        yaml.safe_dump(config, f, allow_unicode=True, sort_keys=False)

    _record_region_operation(
        operator=operator,
        action="update",
        camera_id=camera_id,
        region_id=region_id,
        region_type=rt,
        detail=audit_detail,
    )
    return engine.reload_rules()


@router.post("/api/config/camera/{camera_id}/dwell-threshold")
def update_camera_dwell_threshold(
    camera_id: str,
    payload: DwellThresholdRequest,
    authorization: Optional[str] = Header(default=None),
):
    _, operator = _require_admin_session(authorization)
    threshold_seconds = int(payload.threshold_seconds)
    zone_id = str(payload.zone_id or "").strip()
    rules_path = engine.rule_path
    config = load_rules(rules_path)
    cameras = config.get("cameras", [])
    target = next((camera for camera in cameras if camera.get("id") == camera_id), None)
    if target is None:
        raise HTTPException(status_code=404, detail=f"Camera not found: {camera_id}")

    dwell_zones = target.get("dwell_zones", [])
    if zone_id:
        target_zones = [zone for zone in dwell_zones if str(zone.get("id") or "") == zone_id]
    else:
        target_zones = list(dwell_zones)
    if not target_zones:
        raise HTTPException(status_code=404, detail="当前摄像头没有可配置的滞留区")

    updated_zone_ids = []
    for zone in target_zones:
        zone["threshold_seconds"] = threshold_seconds
        updated_zone_ids.append(str(zone.get("id") or ""))

    updated_rule_ids = []
    updated_zone_set = set(updated_zone_ids)
    for rule in config.get("rules", []):
        if str(rule.get("camera_id") or "") != camera_id:
            continue
        if str(rule.get("type") or "").strip().lower() != "dwell":
            continue
        rule_zone_id = str(rule.get("zone_id") or "").strip()
        if zone_id and rule_zone_id != zone_id:
            continue
        if not zone_id and rule_zone_id and rule_zone_id not in updated_zone_set:
            continue
        rule["threshold_seconds"] = threshold_seconds
        updated_rule_ids.append(str(rule.get("id") or ""))

    with Path(rules_path).open("w", encoding="utf-8") as f:
        yaml.safe_dump(config, f, allow_unicode=True, sort_keys=False)

    reload_result = engine.reload_rules()
    storage_service.record_operation(
        module="rules",
        action="update_dwell_threshold",
        operator=operator["username"],
        target=f"{camera_id}:{','.join(updated_zone_ids)}",
        detail={
            "camera_id": camera_id,
            "zone_ids": updated_zone_ids,
            "rule_ids": updated_rule_ids,
            "threshold_seconds": threshold_seconds,
            "source": "api/config/camera/dwell-threshold",
        },
    )
    return {
        "ok": True,
        "camera_id": camera_id,
        "zone_ids": updated_zone_ids,
        "rule_ids": updated_rule_ids,
        "threshold_seconds": threshold_seconds,
        "reload": reload_result,
    }


@router.delete("/api/config/camera/{camera_id}/region/{region_id}")
def clear_camera_region(
    camera_id: str,
    region_id: str,
    region_type: str = Query(..., pattern="^(rois|dwell_zones)$"),
    authorization: Optional[str] = Header(default=None),
):
    _, operator = _require_admin_session(authorization)
    rules_path = engine.rule_path
    config = load_rules(rules_path)
    cameras = config.get("cameras", [])
    target = next((camera for camera in cameras if camera.get("id") == camera_id), None)
    if target is None:
        raise HTTPException(status_code=404, detail=f"Camera not found: {camera_id}")

    regions = target.get(region_type, [])
    region_target = next((r for r in regions if r.get("id") == region_id), None)
    if not region_target:
        raise HTTPException(status_code=404, detail=f"Region not found: {region_id}")

    audit_detail = {
        "source": "api/config/camera",
        "previous_draw_mode": str(region_target.get("draw_mode") or ""),
        "line_point_count": len(region_target.get("line") or []),
        "path_point_count": len(region_target.get("path_points") or []),
        "polygon_point_count": len(region_target.get("polygon") or []),
    }
    if region_type == "rois":
        region_target["line"] = []
        region_target["polygon"] = []
        region_target["path_points"] = []
        region_target["draw_mode"] = "line"
    else:
        region_target["polygon"] = []

    with Path(rules_path).open("w", encoding="utf-8") as f:
        yaml.safe_dump(config, f, allow_unicode=True, sort_keys=False)

    engine.reload_rules()
    _record_region_operation(
        operator=operator,
        action="clear",
        camera_id=camera_id,
        region_id=region_id,
        region_type=region_type,
        detail=audit_detail,
    )
    return {
        "ok": True,
        "message": f"Region {region_id} cleared",
        "camera_id": camera_id,
        "region_id": region_id,
        "region_type": region_type,
    }


@router.get("/debug/ping")
def debug_ping(authorization: Optional[str] = Header(default=None)):
    _verify_debug_token(authorization)
    return {
        "ok": True,
        "message": "Debug auth verified",
        "rules": [rule["id"] for rule in engine.rules_config.get("rules", [])],
        "tokens_active": len(DEBUG_TOKENS),
    }


@router.post("/debug/simulate")
def debug_simulate(payload: DebugSimulateRequest, authorization: Optional[str] = Header(default=None)):
    _verify_debug_token(authorization)
    result = engine.inject_debug_signal(
        rule_id=payload.rule_id,
        count=payload.count,
        message=payload.message,
    )
    if not result:
        raise HTTPException(status_code=404, detail=f"Rule not found: {payload.rule_id}")
    return {"ok": True, "result": result}
