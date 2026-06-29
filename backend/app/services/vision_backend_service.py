from __future__ import annotations

import base64
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import cv2
import numpy as np
import requests

from backend.app.core.config import DEFAULT_VISION_BACKEND_PATH, load_vision_backend
from backend.app.schemas.detection import BBox, Detection
from backend.app.schemas.vision import VisionFrameAnalysis, VisionRuleEvent
from backend.app.services.mimo_video_client import mimo_video_client
from backend.app.services.yolo_service import yolo_service


def _env_or_config(env_key: str, configured_value: str = "") -> str:
    raw = os.getenv(env_key, "").strip()
    if raw:
        return raw
    return str(configured_value or "").strip()


def _event_bbox_to_detection(
    event: VisionRuleEvent,
    *,
    camera_id: str,
    frame_shape: tuple[int, int, int],
) -> Optional[Detection]:
    if not event.bbox:
        return None

    height, width = frame_shape[:2]
    x1 = float(event.bbox.x1)
    y1 = float(event.bbox.y1)
    x2 = float(event.bbox.x2)
    y2 = float(event.bbox.y2)
    if max(abs(x1), abs(y1), abs(x2), abs(y2)) <= 1.1:
        x1 *= width
        x2 *= width
        y1 *= height
        y2 *= height

    return Detection(
        camera_id=camera_id,
        category=event.category,
        display_category=event.category,
        confidence=event.confidence,
        bbox=BBox(x1=x1, y1=y1, x2=x2, y2=y2),
        track_id=event.track_id,
    )


class YoloDetectionBackend:
    key = "yolo"
    label = "方案一：YOLO目标检测"
    pipeline = "object_detection"

    def runtime_status(self) -> dict:
        status = yolo_service.runtime_status()
        status.update(
            {
                "backend_key": self.key,
                "backend_label": self.label,
                "pipeline": self.pipeline,
                "supports_direct_events": False,
                "supports_overlay_boxes": True,
            }
        )
        return status

    def analyze_frame(
        self,
        frame: np.ndarray,
        *,
        camera_id: str,
        preview_mode: str,
        conf: float,
        imgsz: int,
    ) -> VisionFrameAnalysis:
        class_ids = yolo_service.default_preview_class_ids(preview_mode)
        detections = yolo_service.detect_frame(
            frame,
            camera_id=camera_id,
            classes=class_ids or None,
            conf=conf,
            imgsz=imgsz,
        )
        return VisionFrameAnalysis(
            backend_key=self.key,
            pipeline=self.pipeline,
            detections=detections,
            overlay_detections=detections,
            summary=f"YOLO detections={len(detections)}",
        )


class VideoUnderstandingBackend:
    key = "video_understanding"
    label = "方案二：视频理解模型"
    pipeline = "semantic_video_understanding"

    def __init__(self, settings: Optional[dict] = None):
        self.settings = settings or {}

    def refresh(self, settings: dict) -> None:
        self.settings = settings or {}

    @property
    def provider_mode(self) -> str:
        configured = str(self.settings.get("provider_mode") or "mock_local").strip().lower()
        return _env_or_config("VIDEO_UNDERSTANDING_PROVIDER_MODE", configured) or "mock_local"

    @property
    def api_url(self) -> str:
        return _env_or_config("VIDEO_UNDERSTANDING_API_URL", str(self.settings.get("api_url") or ""))

    @property
    def api_key(self) -> str:
        return _env_or_config("VIDEO_UNDERSTANDING_API_KEY", str(self.settings.get("api_key") or ""))

    @property
    def model(self) -> str:
        return _env_or_config("VIDEO_UNDERSTANDING_MODEL", str(self.settings.get("model") or ""))

    @property
    def timeout_seconds(self) -> float:
        text = _env_or_config("VIDEO_UNDERSTANDING_TIMEOUT_SECONDS", str(self.settings.get("timeout_seconds") or "12"))
        try:
            return float(text or "12")
        except Exception:
            return 12.0

    @property
    def sample_stride(self) -> int:
        text = _env_or_config("VIDEO_UNDERSTANDING_SAMPLE_STRIDE", str(self.settings.get("sample_stride") or "12"))
        try:
            return max(1, int(text or "12"))
        except Exception:
            return 12

    def runtime_status(self) -> dict:
        mimo_status = mimo_video_client.status()
        return {
            "backend_key": self.key,
            "backend_label": self.label,
            "pipeline": self.pipeline,
            "provider_mode": self.provider_mode,
            "api_url": self.api_url,
            "api_configured": bool(self.api_url),
            "has_api_key": bool(self.api_key),
            "model": self.model,
            "timeout_seconds": self.timeout_seconds,
            "sample_stride": self.sample_stride,
            "supports_direct_events": True,
            "supports_overlay_boxes": True,
            "supports_clip_video_analysis": True,
            "mimo_video_ready": bool(mimo_status.get("enabled")),
            "mimo_video_model": mimo_status.get("model", ""),
        }

    def _mock_local_analysis(
        self,
        frame: np.ndarray,
        *,
        camera_id: str,
        preview_mode: str,
        conf: float,
        imgsz: int,
    ) -> VisionFrameAnalysis:
        # 商业改造中保留 mock 模式，便于在真实视频理解模型尚未接通前完成前后端联调和验收演示。
        detections = yolo_service.detect_frame(
            frame,
            camera_id=camera_id,
            classes=yolo_service.default_preview_class_ids(preview_mode) or None,
            conf=conf,
            imgsz=imgsz,
        )
        return VisionFrameAnalysis(
            backend_key=self.key,
            pipeline=self.pipeline,
            detections=detections,
            overlay_detections=detections,
            summary="video_understanding mock_local bridged by local detector",
            metadata={"provider_mode": "mock_local"},
        )

    def _encode_frame(self, frame: np.ndarray) -> str:
        ok, jpg = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 86])
        if not ok:
            raise RuntimeError("Failed to encode frame for video understanding backend")
        return base64.b64encode(jpg.tobytes()).decode("utf-8")

    def _api_analysis(
        self,
        frame: np.ndarray,
        *,
        camera_id: str,
        preview_mode: str,
        conf: float,
        imgsz: int,
    ) -> VisionFrameAnalysis:
        if not self.api_url:
            return VisionFrameAnalysis(
                backend_key=self.key,
                pipeline=self.pipeline,
                error="video_understanding_api_url_missing",
            )

        from backend.app.services.rules_engine import engine

        rule_hints = []
        for rule in engine.get_rules_by_camera(camera_id):
            rule_hints.append(
                {
                    "rule_id": rule.get("id"),
                    "type": rule.get("type"),
                    "description": rule.get("desc") or "",
                    "label": rule.get("alert_label") or rule.get("signal_cn") or rule.get("id"),
                }
            )

        payload = {
            "camera_id": camera_id,
            "preview_mode": preview_mode,
            "model": self.model,
            "timestamp": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
            "contract_version": "vision_rule_events/v1",
            "frame": {
                "mime_type": "image/jpeg",
                "image_base64": self._encode_frame(frame),
            },
            "rule_hints": rule_hints,
            "detector_hint": {
                "conf": conf,
                "imgsz": imgsz,
            },
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        try:
            response = requests.post(
                self.api_url,
                headers=headers,
                json=payload,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            body = response.json()
        except Exception as exc:
            return VisionFrameAnalysis(
                backend_key=self.key,
                pipeline=self.pipeline,
                error=f"{type(exc).__name__}: {exc}",
            )

        events: List[VisionRuleEvent] = []
        for raw_event in body.get("events") or []:
            try:
                events.append(VisionRuleEvent.parse_obj(raw_event))
            except Exception:
                continue

        overlay_detections: List[Detection] = []
        for event in events:
            det = _event_bbox_to_detection(event, camera_id=camera_id, frame_shape=frame.shape)
            if det is not None:
                overlay_detections.append(det)

        return VisionFrameAnalysis(
            backend_key=self.key,
            pipeline=self.pipeline,
            direct_events=events,
            overlay_detections=overlay_detections,
            summary=str(body.get("summary") or ""),
            metadata={"provider_mode": "api"},
        )

    def _mimo_video_preview_analysis(
        self,
        frame: np.ndarray,
        *,
        camera_id: str,
        preview_mode: str,
        conf: float,
        imgsz: int,
    ) -> VisionFrameAnalysis:
        # MiMo works on short video clips rather than single live frames.
        # For the live preview pipeline we keep local detection for overlays,
        # and expose MiMo through replay clip / agent analysis.
        analysis = self._mock_local_analysis(
            frame,
            camera_id=camera_id,
            preview_mode=preview_mode,
            conf=conf,
            imgsz=imgsz,
        )
        analysis.summary = "mimo_video uses local detector for live preview; semantic video understanding is available on replay clips"
        analysis.metadata.update(
            {
                "provider_mode": "mimo_video",
                "live_preview_mode": "local_detector_fallback",
                "clip_video_analysis_ready": bool(mimo_video_client.is_enabled),
                "clip_video_model": mimo_video_client.model,
            }
        )
        return analysis

    def analyze_frame(
        self,
        frame: np.ndarray,
        *,
        camera_id: str,
        preview_mode: str,
        conf: float,
        imgsz: int,
    ) -> VisionFrameAnalysis:
        if self.provider_mode == "api":
            return self._api_analysis(
                frame,
                camera_id=camera_id,
                preview_mode=preview_mode,
                conf=conf,
                imgsz=imgsz,
            )
        if self.provider_mode == "mimo_video":
            return self._mimo_video_preview_analysis(
                frame,
                camera_id=camera_id,
                preview_mode=preview_mode,
                conf=conf,
                imgsz=imgsz,
            )
        return self._mock_local_analysis(
            frame,
            camera_id=camera_id,
            preview_mode=preview_mode,
            conf=conf,
            imgsz=imgsz,
        )


class VisionBackendManager:
    def __init__(self, config_path: Path = DEFAULT_VISION_BACKEND_PATH):
        self.config_path = Path(config_path)
        self.backends: Dict[str, Any] = {
            "yolo": YoloDetectionBackend(),
            "video_understanding": VideoUnderstandingBackend(),
        }
        self.backend_config: dict = {}
        self.active_backend_key = "yolo"
        self.refresh()

    @property
    def backend_keys(self) -> List[str]:
        return list(self.backends.keys())

    def _sanitize_backend_key(self, value: Optional[str], *, allow_inherit: bool = False) -> Optional[str]:
        text = str(value or "").strip().lower()
        if not text:
            return None if allow_inherit else "yolo"
        if text not in self.backends:
            raise ValueError(f"Unsupported backend: {value}")
        return text

    def _sanitize_overrides(self, raw: Optional[dict]) -> Dict[str, str]:
        overrides: Dict[str, str] = {}
        for key, value in (raw or {}).items():
            normalized = self._sanitize_backend_key(value, allow_inherit=True)
            if normalized:
                overrides[str(key)] = normalized
        return overrides

    def refresh(self) -> dict:
        self.backend_config = load_vision_backend(self.config_path)
        active_from_config = (
            str(
                self.backend_config.get("default_backend")
                or self.backend_config.get("active_backend")
                or "yolo"
            )
            .strip()
            .lower()
            or "yolo"
        )
        env_override = os.getenv("VISION_BACKEND_MODE", "").strip().lower()
        active_backend_key = env_override or active_from_config
        self.active_backend_key = self._sanitize_backend_key(active_backend_key) or "yolo"

        self.backend_config["default_backend"] = self.active_backend_key
        self.backend_config["active_backend"] = self.active_backend_key
        self.backend_config["scene_overrides"] = self._sanitize_overrides(self.backend_config.get("scene_overrides"))
        self.backend_config["camera_overrides"] = self._sanitize_overrides(self.backend_config.get("camera_overrides"))

        backend_sections = self.backend_config.get("backends") or {}
        video_understanding = self.backends.get("video_understanding")
        if hasattr(video_understanding, "refresh"):
            video_understanding.refresh(backend_sections.get("video_understanding") or {})
        return self.status()

    def resolve_backend_key(
        self,
        *,
        scene_id: Optional[str] = None,
        camera_id: Optional[str] = None,
    ) -> str:
        camera_overrides = self.backend_config.get("camera_overrides") or {}
        scene_overrides = self.backend_config.get("scene_overrides") or {}
        if camera_id and camera_overrides.get(camera_id):
            return camera_overrides[camera_id]
        if scene_id and scene_overrides.get(scene_id):
            return scene_overrides[scene_id]
        return self.active_backend_key

    def _scope_status(
        self,
        *,
        scene_id: Optional[str] = None,
        camera_id: Optional[str] = None,
    ) -> dict:
        backend_key = self.resolve_backend_key(scene_id=scene_id, camera_id=camera_id)
        runtime = self.backends[backend_key].runtime_status()
        return {
            "backend_key": backend_key,
            "backend_label": runtime.get("backend_label", backend_key),
            "pipeline": runtime.get("pipeline", ""),
        }

    def status(self, topology: Optional[dict] = None) -> dict:
        available = {}
        for key, backend in self.backends.items():
            available[key] = backend.runtime_status()
        active = available.get(self.active_backend_key, {})
        scene_rows = []
        camera_rows = []
        topology = topology or {}
        for scene in topology.get("scenes") or []:
            resolved = self._scope_status(scene_id=scene.get("id"))
            scene_rows.append(
                {
                    "id": scene.get("id"),
                    "name": scene.get("name") or scene.get("id"),
                    "camera_ids": list(scene.get("cameras") or []),
                    "override_backend": (self.backend_config.get("scene_overrides") or {}).get(scene.get("id"), ""),
                    "effective_backend": resolved["backend_key"],
                    "effective_label": resolved["backend_label"],
                    "effective_pipeline": resolved["pipeline"],
                }
            )
        for camera in topology.get("cameras") or []:
            related_scene_ids = [
                scene.get("id")
                for scene in topology.get("scenes") or []
                if camera.get("id") in (scene.get("cameras") or [])
            ]
            primary_scene_id = related_scene_ids[0] if related_scene_ids else None
            resolved = self._scope_status(scene_id=primary_scene_id, camera_id=camera.get("id"))
            camera_rows.append(
                {
                    "id": camera.get("id"),
                    "name": camera.get("name") or camera.get("id"),
                    "scene_ids": related_scene_ids,
                    "override_backend": (self.backend_config.get("camera_overrides") or {}).get(camera.get("id"), ""),
                    "effective_backend": resolved["backend_key"],
                    "effective_label": resolved["backend_label"],
                    "effective_pipeline": resolved["pipeline"],
                }
            )
        return {
            "active_backend": self.active_backend_key,
            "active_backend_label": active.get("backend_label", self.active_backend_key),
            "active_pipeline": active.get("pipeline", ""),
            "default_backend": self.active_backend_key,
            "env_force_backend": os.getenv("VISION_BACKEND_MODE", "").strip().lower(),
            "scene_overrides": dict(self.backend_config.get("scene_overrides") or {}),
            "camera_overrides": dict(self.backend_config.get("camera_overrides") or {}),
            "available_backends": available,
            "backend_keys": self.backend_keys,
            "scenes": scene_rows,
            "cameras": camera_rows,
            "config_path": str(self.config_path),
        }

    def activate(self, backend_key: str) -> dict:
        normalized = str(backend_key or "").strip().lower()
        if normalized not in self.backends:
            raise ValueError(f"Unsupported backend: {backend_key}")

        config = load_vision_backend(self.config_path)
        config["default_backend"] = normalized
        config["active_backend"] = normalized
        with self.config_path.open("w", encoding="utf-8") as f:
            import yaml

            yaml.safe_dump(config, f, allow_unicode=True, sort_keys=False)
        return self.refresh()

    def update_config(
        self,
        *,
        default_backend: Optional[str] = None,
        scene_overrides: Optional[dict] = None,
        camera_overrides: Optional[dict] = None,
        video_understanding: Optional[dict] = None,
    ) -> dict:
        config = load_vision_backend(self.config_path)
        if default_backend is not None:
            normalized_default = self._sanitize_backend_key(default_backend)
            config["default_backend"] = normalized_default
            config["active_backend"] = normalized_default
        config["scene_overrides"] = self._sanitize_overrides(
            scene_overrides if scene_overrides is not None else config.get("scene_overrides")
        )
        config["camera_overrides"] = self._sanitize_overrides(
            camera_overrides if camera_overrides is not None else config.get("camera_overrides")
        )

        backends = config.setdefault("backends", {})
        vu = dict(backends.get("video_understanding") or {})
        if video_understanding:
            for key in ("provider_mode", "api_url", "model", "timeout_seconds", "sample_stride", "preview_boxes"):
                if key in video_understanding:
                    vu[key] = video_understanding[key]
        backends["video_understanding"] = vu

        with self.config_path.open("w", encoding="utf-8") as f:
            import yaml

            yaml.safe_dump(config, f, allow_unicode=True, sort_keys=False)

        # Re-read .env so that any MIMO_API_KEY added by the operator is picked up
        # without requiring a backend restart.
        mimo_video_client.reload()
        return self.refresh()

    def analyze_frame(
        self,
        frame: np.ndarray,
        *,
        camera_id: str,
        scene_id: Optional[str] = None,
        preview_mode: str,
        conf: float,
        imgsz: int,
    ) -> VisionFrameAnalysis:
        backend_key = self.resolve_backend_key(scene_id=scene_id, camera_id=camera_id)
        backend = self.backends[backend_key]
        return backend.analyze_frame(
            frame,
            camera_id=camera_id,
            preview_mode=preview_mode,
            conf=conf,
            imgsz=imgsz,
        )

    def active_runtime_status(
        self,
        *,
        scene_id: Optional[str] = None,
        camera_id: Optional[str] = None,
    ) -> dict:
        backend_key = self.resolve_backend_key(scene_id=scene_id, camera_id=camera_id)
        return self.backends[backend_key].runtime_status()


vision_backend_service = VisionBackendManager()
