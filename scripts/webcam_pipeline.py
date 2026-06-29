"""
Webcam recognition + optional sample collection + backend ingest.

Run example:
    python scripts/webcam_pipeline.py --camera-id cam_fence
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import cv2
import requests
from ultralytics import YOLO

from backend.app.services.yolo_service import (
    EXPANDED_PREVIEW_SELECTION,
    PRIMARY_PREVIEW_SELECTION,
    expand_category_selection,
    resolve_default_weights_path,
)


DEFAULT_MODEL_PATH = resolve_default_weights_path()
DEFAULT_CLASS_SELECTION = ",".join(PRIMARY_PREVIEW_SELECTION)
EXPANDED_CLASS_SELECTION = ",".join(EXPANDED_PREVIEW_SELECTION)


def resolve_video_source(source: str, camera_index: int) -> Any:
    text = (source or "").strip()
    if not text:
        return int(camera_index)

    if text.startswith("camera://"):
        return int(text.split("://", 1)[1])

    if text.isdigit():
        return int(text)

    candidate = Path(text)
    if candidate.exists():
        return str(candidate.resolve())

    project_candidate = PROJECT_ROOT / text
    if project_candidate.exists():
        return str(project_candidate.resolve())

    return text


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Webcam recognition pipeline.")
    parser.add_argument("--camera-index", type=int, default=0, help="Camera index in OpenCV.")
    parser.add_argument(
        "--source",
        type=str,
        default="",
        help="Optional video source: local file / RTSP / camera://0. Empty means use --camera-index.",
    )
    parser.add_argument("--camera-id", type=str, default="cam_fence", help="Camera ID for backend rules.")
    parser.add_argument("--backend-url", type=str, default="http://127.0.0.1:8000", help="FastAPI base URL.")
    parser.add_argument(
        "--weights",
        type=str,
        default=str(DEFAULT_MODEL_PATH),
        help="YOLO weights path. Defaults to the first available of models/yolov8s.pt, models/yolo26s.pt, models/yolov8n.pt, models/best.pt.",
    )
    parser.add_argument("--conf", type=float, default=0.28, help="Confidence threshold.")
    parser.add_argument("--imgsz", type=int, default=640, help="Inference image size.")
    parser.add_argument(
        "--classes",
        type=str,
        default=DEFAULT_CLASS_SELECTION,
        help="Target classes. Supports names or groups: person, vehicle, animal.",
    )
    parser.add_argument(
        "--send-every-n",
        type=int,
        default=1,
        help="Send one frame payload to backend every N frames.",
    )
    parser.add_argument(
        "--sample-interval-sec",
        type=float,
        default=0.0,
        help="Save pseudo-labeled sample every X seconds, 0 disables.",
    )
    parser.add_argument(
        "--sample-dir",
        type=str,
        default=str(PROJECT_ROOT / "data" / "collected" / "webcam"),
        help="Where collected images/labels are saved.",
    )
    parser.add_argument("--max-seconds", type=float, default=0.0, help="Max runtime, 0 means no limit.")
    parser.add_argument(
        "--loop-file",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Loop playback for local video source when reaching EOF (default: enabled).",
    )
    parser.add_argument("--no-view", action="store_true", help="Disable OpenCV window.")
    parser.add_argument("--no-track", action="store_true", help="Disable YOLO track API.")
    parser.add_argument(
        "--record",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Enable local replay recording files (default: enabled).",
    )
    parser.add_argument(
        "--record-dir",
        type=str,
        default=str(PROJECT_ROOT / "data" / "replay"),
        help="Local root path for replay files.",
    )
    parser.add_argument(
        "--segment-seconds",
        type=int,
        default=300,
        help="Length of each replay segment file in seconds.",
    )
    parser.add_argument(
        "--record-fps",
        type=float,
        default=12.0,
        help="FPS used for local replay recording.",
    )
    return parser.parse_args()


def parse_class_selection(text: str) -> List[str]:
    items = [item.strip() for item in str(text or "").split(",") if item.strip()]
    return expand_category_selection(items, fallback_groups=PRIMARY_PREVIEW_SELECTION)


def resolve_class_ids(model: YOLO, class_names: List[str]) -> List[int]:
    names = model.names.items() if hasattr(model.names, "items") else enumerate(model.names)
    name_map = {str(name).strip().lower(): int(idx) for idx, name in names}
    resolved: List[int] = []
    seen = set()
    for class_name in class_names:
        class_id = name_map.get(str(class_name).strip().lower())
        if class_id is None or class_id in seen:
            continue
        seen.add(class_id)
        resolved.append(class_id)
    return resolved


def category_color(category: str) -> Tuple[int, int, int]:
    color_map = {
        "person": (0, 230, 255),
        "bicycle": (120, 220, 120),
        "car": (255, 185, 60),
        "motorcycle": (255, 150, 90),
        "bus": (120, 180, 255),
        "truck": (90, 110, 255),
        "bird": (210, 180, 255),
        "cat": (255, 140, 210),
        "dog": (255, 120, 180),
        "horse": (210, 170, 120),
        "sheep": (215, 215, 215),
        "cow": (170, 215, 255),
    }
    return color_map.get(str(category or "").strip().lower(), (110, 210, 255))


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def normalize_bbox(x1: float, y1: float, x2: float, y2: float, width: int, height: int) -> Tuple[float, float, float, float]:
    return (
        clamp01(x1 / width),
        clamp01(y1 / height),
        clamp01(x2 / width),
        clamp01(y2 / height),
    )


def extract_target_detections(
    result: Any,
    width: int,
    height: int,
    camera_id: str,
    class_name_lookup: Dict[int, str],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    payload_dets: List[Dict[str, Any]] = []
    draw_dets: List[Dict[str, Any]] = []
    boxes = getattr(result, "boxes", None)
    if boxes is None or len(boxes) == 0:
        return payload_dets, draw_dets

    ids = boxes.id.tolist() if boxes.id is not None else [None] * len(boxes)
    xyxy_list = boxes.xyxy.tolist()
    conf_list = boxes.conf.tolist()
    cls_list = boxes.cls.tolist()

    for idx, (xyxy, conf, cls_id) in enumerate(zip(xyxy_list, conf_list, cls_list)):
        class_id = int(cls_id)
        class_name = class_name_lookup.get(class_id, "object")
        x1, y1, x2, y2 = [float(v) for v in xyxy]
        nx1, ny1, nx2, ny2 = normalize_bbox(x1, y1, x2, y2, width, height)
        track_id = None if ids[idx] is None else int(ids[idx])

        payload_dets.append(
            {
                "camera_id": camera_id,
                "category": class_name,
                "confidence": float(conf),
                "bbox": {"x1": nx1, "y1": ny1, "x2": nx2, "y2": ny2},
                "track_id": track_id,
                "class_id": class_id,
            }
        )
        draw_dets.append(
            {
                "x1": int(x1),
                "y1": int(y1),
                "x2": int(x2),
                "y2": int(y2),
                "category": class_name,
                "confidence": float(conf),
                "track_id": track_id,
            }
        )

    return payload_dets, draw_dets


def yolo_label_line(det: Dict[str, Any]) -> str:
    class_id = int(det.get("class_id", 0))
    x1 = det["bbox"]["x1"]
    y1 = det["bbox"]["y1"]
    x2 = det["bbox"]["x2"]
    y2 = det["bbox"]["y2"]
    cx = (x1 + x2) / 2
    cy = (y1 + y2) / 2
    w = x2 - x1
    h = y2 - y1
    return f"{class_id} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}"


def save_sample(frame: Any, detections: List[Dict[str, Any]], sample_dir: Path) -> Tuple[Path, Path]:
    images_dir = sample_dir / "images"
    labels_dir = sample_dir / "labels"
    images_dir.mkdir(parents=True, exist_ok=True)
    labels_dir.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    image_path = images_dir / f"{stamp}.jpg"
    label_path = labels_dir / f"{stamp}.txt"
    cv2.imwrite(str(image_path), frame)

    with label_path.open("w", encoding="utf-8") as f:
        for det in detections:
            f.write(yolo_label_line(det) + "\n")

    return image_path, label_path


class SegmentRecorder:
    """Write rolling MP4 segments for replay review."""

    def __init__(self, *, root_dir: Path, camera_id: str, segment_seconds: int, fps: float):
        self.root_dir = Path(root_dir)
        self.camera_id = camera_id
        self.segment_seconds = max(10, int(segment_seconds))
        self.fps = max(1.0, float(fps))
        self.writer: Optional[cv2.VideoWriter] = None
        self.segment_started_at: Optional[float] = None
        self.current_file: Optional[Path] = None

    def _build_segment_path(self, ts: datetime) -> Path:
        date_dir = ts.strftime("%Y-%m-%d")
        hour_dir = ts.strftime("%H")
        file_name = f"{ts.strftime('%Y%m%d_%H%M%S')}.mp4"
        target_dir = self.root_dir / self.camera_id / date_dir / hour_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        return target_dir / file_name

    def _open_new_writer(self, frame: Any, now_ts: float) -> None:
        if self.writer is not None:
            self.writer.release()
            self.writer = None

        now_dt = datetime.utcnow()
        output_path = self._build_segment_path(now_dt)
        height, width = frame.shape[:2]
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(str(output_path), fourcc, self.fps, (int(width), int(height)))
        if not writer.isOpened():
            return

        self.writer = writer
        self.segment_started_at = now_ts
        self.current_file = output_path
        print(f"[record] segment opened: {output_path}")

    def write(self, frame: Any, now_ts: float) -> None:
        need_rotate = self.writer is None or self.segment_started_at is None
        if not need_rotate:
            need_rotate = (now_ts - self.segment_started_at) >= self.segment_seconds

        if need_rotate:
            self._open_new_writer(frame, now_ts)

        if self.writer is not None:
            self.writer.write(frame)

    def close(self) -> None:
        if self.writer is not None:
            self.writer.release()
            self.writer = None
            if self.current_file is not None:
                print(f"[record] segment closed: {self.current_file}")


def post_frame(payload: Dict[str, Any], backend_url: str) -> Optional[Dict[str, Any]]:
    try:
        resp = requests.post(
            f"{backend_url.rstrip('/')}/ingest/detections",
            data=json.dumps(payload),
            headers={"Content-Type": "application/json"},
            timeout=1.5,
        )
        if resp.ok:
            return resp.json()
    except requests.RequestException:
        return None
    return None


def summarize_categories(detections: List[Dict[str, Any]]) -> str:
    if not detections:
        return "none"

    counts: Dict[str, int] = {}
    for det in detections:
        category = str(det.get("category") or "object")
        counts[category] = counts.get(category, 0) + 1

    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return ", ".join(f"{name}:{count}" for name, count in ordered[:4])


def draw_overlay(frame: Any, draw_dets: List[Dict[str, Any]], status_text: str) -> Any:
    for det in draw_dets:
        x1, y1, x2, y2 = det["x1"], det["y1"], det["x2"], det["y2"]
        color = category_color(det.get("category", "object"))
        thickness = 2 if det.get("category") == "person" else 1
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, thickness)
        label = f"{det.get('category', 'object')} {det['confidence']:.2f}"
        if det.get("track_id") is not None:
            label = f"{label} #{det['track_id']}"
        cv2.putText(frame, label, (x1, max(y1 - 8, 20)), cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2)
    cv2.putText(frame, status_text, (14, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (50, 255, 50), 2)
    return frame


def main() -> None:
    args = parse_args()

    weights_path = Path(args.weights)
    model_source = str(weights_path if weights_path.exists() else "yolov8n.pt")
    model = YOLO(model_source)
    selected_class_names = parse_class_selection(args.classes)
    selected_class_ids = resolve_class_ids(model, selected_class_names)
    names = model.names.items() if hasattr(model.names, "items") else enumerate(model.names)
    class_name_lookup = {int(idx): str(name).strip().lower() for idx, name in names}
    requested_classes = list(selected_class_names)
    if not selected_class_ids:
        selected_class_names = expand_category_selection(
            [item.strip() for item in EXPANDED_CLASS_SELECTION.split(",") if item.strip()],
            fallback_groups=EXPANDED_PREVIEW_SELECTION,
        )
        selected_class_ids = resolve_class_ids(model, selected_class_names)
        print(f"[warn] no valid classes matched for: {', '.join(requested_classes) or '(empty)'}")
        print(f"[warn] fallback classes: {', '.join(selected_class_names)}")

    source = resolve_video_source(args.source, args.camera_index)
    is_file_source = isinstance(source, str) and Path(source).exists()
    cap = cv2.VideoCapture(source)
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open source: {source}")

    sample_dir = Path(args.sample_dir)
    recorder: Optional[SegmentRecorder] = None
    if args.record:
        recorder = SegmentRecorder(
            root_dir=Path(args.record_dir),
            camera_id=args.camera_id,
            segment_seconds=args.segment_seconds,
            fps=args.record_fps,
        )

    start_time = time.time()
    last_sample_ts = 0.0
    frame_idx = 0
    total_sent = 0
    total_alerts = 0

    print("Video pipeline started. Press 'q' to quit, 's' to save one sample.")
    print(f"camera_id={args.camera_id}, backend={args.backend_url}, weights={model_source}, source={source}")
    print(f"classes={', '.join(selected_class_names)}")
    if recorder is not None:
        print(f"recording_dir={Path(args.record_dir)} segment_seconds={args.segment_seconds} fps={args.record_fps}")

    while True:
        now = time.time()
        if args.max_seconds > 0 and (now - start_time) >= args.max_seconds:
            break

        ok, frame = cap.read()
        if not ok or frame is None:
            if is_file_source and args.loop_file:
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                time.sleep(0.01)
                continue
            if is_file_source and not args.loop_file:
                print("Reached end of file source, stopping.")
                break
            time.sleep(0.05)
            continue

        frame_idx += 1
        height, width = frame.shape[:2]

        if args.no_track:
            result = model.predict(
                frame,
                classes=selected_class_ids or None,
                conf=args.conf,
                imgsz=args.imgsz,
                verbose=False,
            )[0]
        else:
            result = model.track(
                frame,
                classes=selected_class_ids or None,
                conf=args.conf,
                imgsz=args.imgsz,
                persist=True,
                verbose=False,
            )[0]

        payload_dets, draw_dets = extract_target_detections(
            result,
            width,
            height,
            args.camera_id,
            class_name_lookup,
        )
        if recorder is not None:
            recorder.write(frame, now)

        frame_payload = {
            "frame_id": f"{args.camera_id}_{frame_idx}",
            "camera_id": args.camera_id,
            "timestamp": datetime.utcnow().isoformat(),
            "detections": payload_dets,
        }

        if frame_idx % max(1, args.send_every_n) == 0:
            backend_resp = post_frame(frame_payload, args.backend_url)
            total_sent += 1
            if backend_resp is not None:
                total_alerts += int(backend_resp.get("alerts_generated", 0))

        if args.sample_interval_sec > 0 and (now - last_sample_ts) >= args.sample_interval_sec:
            save_sample(frame, payload_dets, sample_dir)
            last_sample_ts = now

        status = f"det={len(payload_dets)} [{summarize_categories(payload_dets)}] sent={total_sent} alerts={total_alerts}"
        if not args.no_view:
            view = draw_overlay(frame.copy(), draw_dets, status)
            cv2.imshow("webcam-recognition", view)
            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                break
            if key == ord("s"):
                img_path, txt_path = save_sample(frame, payload_dets, sample_dir)
                print(f"Saved sample: {img_path} | {txt_path}")

    cap.release()
    cv2.destroyAllWindows()
    if recorder is not None:
        recorder.close()
    print(f"Stopped. total_frames={frame_idx}, sent={total_sent}, alerts={total_alerts}")


if __name__ == "__main__":
    main()
