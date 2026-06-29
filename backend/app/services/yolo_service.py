from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import numpy as np

try:
    from ultralytics import YOLO  # type: ignore
except Exception:  # pragma: no cover - optional until runtime install succeeds
    YOLO = None  # type: ignore

try:
    from dotenv import dotenv_values
except Exception:  # pragma: no cover - optional until runtime install succeeds
    dotenv_values = None

from backend.app.core.config import MODELS_DIR, PROJECT_ROOT
from backend.app.core.utils import load_local_env_values, ENV_FILE_NAMES
from backend.app.schemas.detection import BBox, Detection


DEFAULT_WEIGHTS = MODELS_DIR / "yolov8n.pt"
MODEL_SEARCH_DIRS = (
    PROJECT_ROOT / "model",
    MODELS_DIR,
)
DEFAULT_MODEL_CANDIDATE_FILENAMES = (
    "yolov8s.pt",
    "yolo26s.pt",
    "yolov8n.pt",
    "best.pt",
)
MODEL_ENV_KEYS = (
    "YOLO_WEIGHTS_PATH",
    "DETECTION_MODEL_PATH",
    "MODEL_WEIGHTS_PATH",
)
MODEL_PRIORITY_ENV_KEY = "YOLO_MODEL_PRIORITY"
ENV_FILE_NAMES = (".env", ".env.local")

CATEGORY_GROUPS = {
    "person": ["person", "human", "pedestrian", "man", "woman", "worker", "head", "person-like"],
    "vehicle": ["bicycle", "car", "motorcycle", "bus", "truck", "auto", "vehicle", "van"],
    "animal": ["bird", "cat", "dog", "horse", "sheep", "cow", "animal", "pet"],
}


def _normalize_candidate_path(candidate: Path | str) -> Path:
    path = Path(candidate)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def _load_local_model_env_values() -> Dict[str, str]:
    values, _ = load_local_env_values()
    return values


def _model_env_value(key: str) -> str:
    env_value = os.getenv(key, "").strip()
    if env_value:
        return env_value
    return _load_local_model_env_values().get(key, "").strip()


def _model_candidate_filenames() -> tuple[str, ...]:
    raw = _model_env_value(MODEL_PRIORITY_ENV_KEY)
    if not raw:
        return DEFAULT_MODEL_CANDIDATE_FILENAMES

    parsed = tuple(item.strip() for item in raw.split(",") if item.strip())
    return parsed or DEFAULT_MODEL_CANDIDATE_FILENAMES


def iter_weight_candidates(explicit_path: Optional[Path] = None) -> List[Path]:
    candidates: List[Path] = []
    seen = set()
    candidate_filenames = _model_candidate_filenames()

    raw_candidates: List[Path | str] = []
    if explicit_path is not None:
        raw_candidates.append(explicit_path)
    else:
        for env_key in MODEL_ENV_KEYS:
            raw_value = _model_env_value(env_key)
            if raw_value:
                raw_candidates.append(raw_value)
        for directory in MODEL_SEARCH_DIRS:
            for file_name in candidate_filenames:
                raw_candidates.append(directory / file_name)
        raw_candidates.append(DEFAULT_WEIGHTS)

    for raw_candidate in raw_candidates:
        normalized = _normalize_candidate_path(raw_candidate)
        key = str(normalized).lower()
        if key in seen:
            continue
        seen.add(key)
        candidates.append(normalized)

    return candidates


def resolve_default_weights_path(explicit_path: Optional[Path] = None) -> Path:
    candidates = iter_weight_candidates(explicit_path)
    existing = next((candidate for candidate in candidates if candidate.exists()), None)
    return existing or candidates[0]

def _map_to_canonical_category(raw_name: str) -> str:
    raw_lower = str(raw_name).strip().lower()
    for canonical, expected_raws in CATEGORY_GROUPS.items():
        if raw_lower in expected_raws:
            return canonical
    return raw_lower
PRIMARY_PREVIEW_SELECTION = ("person",)
EXPANDED_PREVIEW_SELECTION = ("person", "vehicle", "animal")


def expand_category_selection(
    items: Optional[Iterable[str]],
    *,
    fallback_groups: Optional[Iterable[str]] = None,
) -> List[str]:
    expanded: List[str] = []
    seen = set()

    for raw in items or []:
        key = str(raw or "").strip().lower()
        if not key:
            continue

        values = CATEGORY_GROUPS.get(key, [key])
        for value in values:
            normalized = str(value).strip().lower()
            if normalized and normalized not in seen:
                seen.add(normalized)
                expanded.append(normalized)

    if expanded:
        return expanded

    fallback: List[str] = []
    for group in (fallback_groups or EXPANDED_PREVIEW_SELECTION):
        fallback.extend(CATEGORY_GROUPS[group])
    return fallback


def preview_group_selection(mode: str) -> tuple[str, ...]:
    return PRIMARY_PREVIEW_SELECTION if str(mode or "").strip().lower() == "person" else EXPANDED_PREVIEW_SELECTION


class YoloService:
    def __init__(self, weights_path: Optional[Path] = None):
        self.explicit_weights_path = Path(weights_path).resolve() if weights_path is not None else None
        self.weights_path = resolve_default_weights_path(self.explicit_weights_path)
        self.candidate_paths = iter_weight_candidates(self.explicit_weights_path)
        self.model = None
        self.last_load_error = ""

    def load(self):
        if YOLO is None:
            raise RuntimeError("ultralytics is not available. Please install project dependencies first.")
        if self.model is None:
            load_errors: List[str] = []
            for candidate in self.candidate_paths:
                if not candidate.exists():
                    load_errors.append(f"{candidate.name}: missing")
                    continue
                compatible, reason = self._candidate_runtime_compatible(candidate)
                if not compatible:
                    load_errors.append(f"{candidate.name}: {reason}")
                    continue
                try:
                    self.model = YOLO(str(candidate))
                    self.weights_path = candidate
                    self.last_load_error = ""
                    break
                except Exception as exc:  # pragma: no cover - depends on local runtime
                    load_errors.append(f"{candidate.name}: {type(exc).__name__}: {exc}")

            if self.model is None:
                self.last_load_error = " | ".join(load_errors[-3:])
                raise RuntimeError(
                    "No usable YOLO weights could be loaded. "
                    f"Tried: {', '.join(str(path) for path in self.candidate_paths)}. "
                    f"Errors: {self.last_load_error}"
                )
        return self.model

    @staticmethod
    def _candidate_runtime_compatible(candidate: Path) -> tuple[bool, str]:
        name = candidate.name.lower()
        if "yolo26" not in name:
            return True, ""

        try:
            from ultralytics.nn.modules import block as block_module  # type: ignore
        except Exception as exc:  # pragma: no cover - depends on local runtime
            return False, f"cannot inspect ultralytics runtime: {type(exc).__name__}: {exc}"

        if not hasattr(block_module, "C3k2"):
            return False, "requires a newer ultralytics runtime (missing C3k2 block support)"
        return True, ""

    def runtime_status(self) -> dict:
        configured_candidate = next((path for path in self.candidate_paths if path.exists()), self.weights_path)
        resolved_runtime_candidate = next(
            (path for path in self.candidate_paths if path.exists() and self._candidate_runtime_compatible(path)[0]),
            configured_candidate,
        )
        candidate_notes = []
        for path in self.candidate_paths:
            exists = path.exists()
            compatible, reason = self._candidate_runtime_compatible(path) if exists else (False, "missing")
            candidate_notes.append(
                {
                    "path": str(path),
                    "exists": exists,
                    "compatible": compatible if exists else False,
                    "reason": "" if compatible and exists else reason,
                }
            )
        reported_active_path = str(self.weights_path if self.model is not None else resolved_runtime_candidate)
        return {
            "configured_path": str(configured_candidate),
            "active_path": reported_active_path,
            "active_name": Path(reported_active_path).name,
            "loaded": self.model is not None,
            "last_error": self.last_load_error,
            "candidates": [str(path) for path in self.candidate_paths if path.exists()],
            "candidate_notes": candidate_notes,
            "priority": list(_model_candidate_filenames()),
        }

    def class_name_to_id_map(self) -> dict[str, int]:
        model = self.load()
        names = model.names.items() if hasattr(model.names, "items") else enumerate(model.names)
        return {str(name).strip().lower(): int(idx) for idx, name in names}

    def resolve_class_ids(self, class_names: Optional[Iterable[str]]) -> List[int]:
        mapping = self.class_name_to_id_map()
        resolved: List[int] = []
        seen = set()
        for name in expand_category_selection(class_names, fallback_groups=EXPANDED_PREVIEW_SELECTION):
            class_id = mapping.get(name)
            if class_id is None or class_id in seen:
                continue
            seen.add(class_id)
            resolved.append(class_id)
        return resolved

    def supported_preview_categories(self, mode: str = "all") -> List[str]:
        mapping = self.class_name_to_id_map()
        groups = preview_group_selection(mode)
        return [name for name in expand_category_selection(groups, fallback_groups=groups) if name in mapping]

    def default_preview_class_ids(self, mode: str = "all") -> List[int]:
        groups = preview_group_selection(mode)
        return self.resolve_class_ids(groups)

    def detect(
        self,
        source: str,
        classes: Optional[Iterable[int]] = None,
        *,
        conf: float = 0.25,
        imgsz: int = 640,
    ) -> List[Detection]:
        model = self.load()
        results = model.predict(
            source,
            stream=False,
            classes=list(classes) if classes else None,
            conf=conf,
            imgsz=imgsz,
            verbose=False,
        )
        return self._parse_results(results, camera_id="unknown")

    def detect_frame(
        self,
        frame: np.ndarray,
        *,
        camera_id: str,
        classes: Optional[Iterable[int]] = None,
        conf: float = 0.25,
        imgsz: int = 640,
    ) -> List[Detection]:
        model = self.load()
        results = model.predict(
            frame,
            stream=False,
            classes=list(classes) if classes else None,
            conf=conf,
            imgsz=imgsz,
            verbose=False,
        )
        return self._parse_results(results, camera_id=camera_id)

    def _parse_results(self, results, *, camera_id: str) -> List[Detection]:
        model = self.load()
        names = dict(model.names.items()) if hasattr(model.names, "items") else dict(enumerate(model.names))
        detections: List[Detection] = []

        for result in results:
            boxes = getattr(result, "boxes", None)
            if boxes is None or len(boxes) == 0:
                continue

            ids = boxes.id.tolist() if boxes.id is not None else [None] * len(boxes)
            xyxy_list = boxes.xyxy.tolist()
            conf_list = boxes.conf.tolist()
            cls_list = boxes.cls.tolist()

            for xyxy, conf, cls_id, track_id in zip(xyxy_list, conf_list, cls_list, ids):
                x1, y1, x2, y2 = [float(value) for value in xyxy]
                
                raw_category = str(names.get(int(cls_id), "object")).strip().lower()
                canonical_category = _map_to_canonical_category(raw_category)

                detections.append(
                    Detection(
                        camera_id=camera_id,
                        category=canonical_category,
                        display_category=raw_category,
                        confidence=float(conf),
                        bbox=BBox(x1=x1, y1=y1, x2=x2, y2=y2),
                        track_id=None if track_id is None else int(track_id),
                    )
                )

        return detections


yolo_service = YoloService()
