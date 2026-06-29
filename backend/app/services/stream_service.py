from __future__ import annotations

import os
import platform
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator, List, Union
from urllib.parse import urlparse

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from backend.app.core.config import PROJECT_ROOT
from backend.app.schemas.detection import DetectionFrame
from backend.app.services import tracking_service
from backend.app.services.rules_engine import engine
from backend.app.services.vision_backend_service import vision_backend_service


PREVIEW_CONFIDENCE = float(os.getenv("STREAM_PREVIEW_CONFIDENCE", "0.22"))
PREVIEW_IMGSZ = int(os.getenv("STREAM_PREVIEW_IMGSZ", "512"))
PREVIEW_DETECTION_INTERVAL = max(1, int(os.getenv("STREAM_DETECTION_INTERVAL", "2")))
STREAM_MAX_FPS_DEFAULT = max(1, int(os.getenv("STREAM_MAX_FPS", "18")))
STREAM_JPEG_QUALITY = max(50, min(95, int(os.getenv("STREAM_JPEG_QUALITY", "72"))))
STREAM_DETECTION_MAX_SIDE = max(320, int(os.getenv("STREAM_DETECTION_MAX_SIDE", "960")))
STREAM_READ_FAILURE_REOPEN_AFTER = max(3, int(os.getenv("STREAM_READ_FAILURE_REOPEN_AFTER", "20")))
GUIDE_ALPHA = float(os.getenv("STREAM_GUIDE_ALPHA", "0.26"))
BOX_ALPHA = float(os.getenv("STREAM_BOX_ALPHA", "0.72"))
ALARM_BOX_ALPHA = float(os.getenv("STREAM_ALARM_BOX_ALPHA", "0.90"))

CATEGORY_COLORS = {
    "person": (104, 226, 255),
    "head": (104, 226, 255),
    "person-like": (104, 226, 255),
    "bicycle": (94, 234, 178),
    "car": (255, 176, 76),
    "motorcycle": (255, 122, 76),
    "bus": (255, 214, 92),
    "truck": (255, 96, 96),
    "vehicle": (255, 166, 76),
    "bird": (206, 158, 255),
    "cat": (255, 112, 214),
    "dog": (156, 255, 112),
    "horse": (158, 124, 255),
    "sheep": (232, 232, 232),
    "cow": (112, 196, 255),
    "animal": (188, 132, 255),
    "other": (132, 164, 224),
}

CATEGORY_LABELS = {
    "person": "人员",
    "head": "人员",
    "person-like": "人员",
    "vehicle": "车辆",
    "car": "车辆",
    "truck": "货车",
    "bus": "车辆",
    "motorcycle": "摩托车",
    "bicycle": "自行车",
    "animal": "动物",
}

PREVIEW_MODE_LABELS = {
    "person": "主体筛查",
    "all": "全目标筛查",
}

_font_cache: dict[int, ImageFont.FreeTypeFont] = {}


def _load_pil_font(font_size: int):
    if font_size in _font_cache:
        return _font_cache[font_size]

    _candidates = {
        "Windows": [
            r"C:\Windows\Fonts\msyh.ttc",
            r"C:\Windows\Fonts\simhei.ttf",
            r"C:\Windows\Fonts\simsun.ttc",
            r"C:\Windows\Fonts\simkai.ttf",
        ],
        "Darwin": [
            "/System/Library/Fonts/PingFang.ttc",
            "/System/Library/Fonts/STHeiti Medium.ttc",
            "/Library/Fonts/Arial Unicode.ttf",
        ],
        "Linux": [
            "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
        ],
    }
    system = platform.system()
    for path in _candidates.get(system, _candidates["Linux"]):
        try:
            font = ImageFont.truetype(path, font_size)
            _font_cache[font_size] = font
            return font
        except (IOError, OSError):
            continue
    # 最终回退：直接用字体名，让 PIL 自己查找
    try:
        font = ImageFont.truetype("simhei.ttf", font_size)
        _font_cache[font_size] = font
        return font
    except (IOError, OSError):
        font = ImageFont.load_default()
        _font_cache[font_size] = font
        return font


def _text_size(text: str, font_size: int) -> tuple[int, int]:
    font = _load_pil_font(font_size)
    bbox = ImageDraw.Draw(Image.new("RGB", (1, 1))).textbbox((0, 0), str(text), font=font)
    return max(1, bbox[2] - bbox[0]), max(1, bbox[3] - bbox[1])


def _draw_text_with_pil(frame: np.ndarray, text: str, pos: tuple[int, int], color: tuple[int, int, int], font_size: int = 20) -> np.ndarray:
    cv2_img = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    pil_img = Image.fromarray(cv2_img)
    draw = ImageDraw.Draw(pil_img)
    font = _load_pil_font(font_size)

    draw.text(pos, text, fill=color[::-1], font=font)
    return cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)


def _resolve_source(stream_value: Union[str, int, float, None]) -> Union[int, str]:
    if stream_value is None:
        return 0

    if isinstance(stream_value, (int, float)):
        return int(stream_value)

    value = str(stream_value).strip()
    if value.startswith("camera://"):
        return int(value.split("://", 1)[1])
    if value.isdigit():
        return int(value)

    parsed = urlparse(value)
    if parsed.scheme.lower() in {"http", "https", "rtsp", "rtmp", "udp", "tcp"}:
        return value

    stream_path = Path(value)
    if not stream_path.is_absolute():
        stream_path = PROJECT_ROOT / stream_path
    return str(stream_path)


def _stream_marker(stream_value: Union[str, int, float, None]) -> str:
    return "" if stream_value is None else str(stream_value).strip()


def _open_capture(camera_id: str):
    camera = engine.get_camera(camera_id)
    if not camera:
        return None, None, None, ""
    stream_value = camera.get("stream")
    source = _resolve_source(stream_value)
    return camera, cv2.VideoCapture(source), source, _stream_marker(stream_value)


def _scene_id_for_camera(camera_id: str) -> str:
    for scene in engine.get_scenes():
        if camera_id in (scene.get("cameras") or []):
            return str(scene.get("id") or "")
    return ""


def _fallback_frame(text: str = "stream not available") -> bytes:
    frame = np.zeros((540, 960, 3), dtype=np.uint8)
    frame[:] = (23, 15, 2)
    cv2.rectangle(frame, (1, 1), (958, 538), (54, 38, 15), 2)
    cv2.line(frame, (0, 0), (960, 540), (34, 24, 10), 1)

    raw_text = str(text or "").strip()
    if raw_text.startswith("cannot open source"):
        detail = "当前未接入可用摄像头"
        hint = "请上传视频，或检查真实摄像头连接后刷新"
    elif "unknown camera" in raw_text:
        detail = "未找到摄像头配置"
        hint = raw_text
    elif "waiting" in raw_text.lower():
        detail = "正在建立视频流"
        hint = "请稍候，系统正在等待首帧画面"
    else:
        detail = "视频流暂不可用"
        hint = raw_text or "请上传视频或检查视频源"

    frame = _draw_text_with_pil(frame, "等待视频流", (392, 210), (255, 230, 150), 38)
    frame = _draw_text_with_pil(frame, detail, (382, 270), (226, 232, 240), 24)
    frame = _draw_text_with_pil(frame, hint, (320, 314), (148, 163, 184), 18)
    ok, jpg = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), STREAM_JPEG_QUALITY])
    return jpg.tobytes() if ok else b""


def _color_for_category(category: str) -> tuple[int, int, int]:
    key = str(category or "").strip().lower()
    return CATEGORY_COLORS.get(key, (110, 210, 255))


def _blend_overlay(base: np.ndarray, overlay: np.ndarray, alpha: float) -> None:
    alpha = max(0.0, min(alpha, 1.0))
    cv2.addWeighted(overlay, alpha, base, 1.0 - alpha, 0, base)


def _draw_corner_box(
    frame: np.ndarray,
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    color: tuple[int, int, int],
    *,
    thickness: int,
    alpha: float,
) -> None:
    if x2 <= x1 or y2 <= y1:
        return

    overlay = frame.copy()
    box_w = x2 - x1
    box_h = y2 - y1
    corner = max(10, min(24, int(min(box_w, box_h) * 0.22)))

    cv2.rectangle(overlay, (x1, y1), (x2, y2), color, 1, cv2.LINE_AA)
    cv2.line(overlay, (x1, y1), (x1 + corner, y1), color, thickness, cv2.LINE_AA)
    cv2.line(overlay, (x1, y1), (x1, y1 + corner), color, thickness, cv2.LINE_AA)
    cv2.line(overlay, (x2, y1), (x2 - corner, y1), color, thickness, cv2.LINE_AA)
    cv2.line(overlay, (x2, y1), (x2, y1 + corner), color, thickness, cv2.LINE_AA)
    cv2.line(overlay, (x1, y2), (x1 + corner, y2), color, thickness, cv2.LINE_AA)
    cv2.line(overlay, (x1, y2), (x1, y2 - corner), color, thickness, cv2.LINE_AA)
    cv2.line(overlay, (x2, y2), (x2 - corner, y2), color, thickness, cv2.LINE_AA)
    cv2.line(overlay, (x2, y2), (x2, y2 - corner), color, thickness, cv2.LINE_AA)
    _blend_overlay(frame, overlay, alpha)


def _draw_box_label(frame: np.ndarray, text: str, x: int, y: int, color: tuple[int, int, int]) -> None:
    text_width, text_height = _text_size(text, 13)
    label_x1 = max(8, x)
    label_y2 = max(text_height + 14, y)
    label_x2 = min(frame.shape[1] - 8, label_x1 + text_width + 22)
    label_y1 = max(8, label_y2 - text_height - 14)

    overlay = frame.copy()
    cv2.rectangle(overlay, (label_x1, label_y1), (label_x2, label_y2), (5, 12, 20), -1)
    cv2.rectangle(overlay, (label_x1, label_y1), (label_x2, label_y2), color, 1, cv2.LINE_AA)
    cv2.circle(overlay, (label_x1 + 8, label_y1 + (label_y2 - label_y1) // 2), 3, color, -1, cv2.LINE_AA)
    _blend_overlay(frame, overlay, 0.70)

    rendered = _draw_text_with_pil(frame, text, (label_x1 + 15, label_y1 + 5), (232, 240, 248), 13)
    frame[:, :] = rendered


def _draw_guide_label(frame: np.ndarray, text: str, pos: tuple[int, int], color: tuple[int, int, int]) -> np.ndarray:
    text = str(text or "").strip()
    if not text:
        return frame

    x, y = pos
    x = max(6, x)
    y = max(22, y)
    text_width, text_height = _text_size(text, 13)
    text_width = max(76, text_width + 16)

    overlay = frame.copy()
    cv2.rectangle(overlay, (x - 4, y - text_height - 8), (x + text_width, y + 7), (6, 14, 24), -1)
    cv2.rectangle(overlay, (x - 4, y - text_height - 8), (x + text_width, y + 7), color, 1, cv2.LINE_AA)
    _blend_overlay(frame, overlay, 0.48)
    return _draw_text_with_pil(frame, text, (x + 4, y - text_height - 5), color, 13)


def _draw_detections(frame: np.ndarray, detections: List, preview_mode: str, camera_id: str) -> None:
    alarming_track_ids = engine.get_alarming_track_ids(camera_id)

    for det in detections:
        x1 = max(0, int(det.bbox.x1))
        y1 = max(0, int(det.bbox.y1))
        x2 = min(frame.shape[1] - 1, int(det.bbox.x2))
        y2 = min(frame.shape[0] - 1, int(det.bbox.y2))

        display_category = str(getattr(det, "display_category", "") or det.category or "").strip().lower()
        color = _color_for_category(display_category or det.category)
        is_primary = str(det.category or "").strip().lower() == "person"
        thickness = 2 if preview_mode == "person" or is_primary else 1
        alpha = BOX_ALPHA

        category_label = CATEGORY_LABELS.get(display_category or det.category, display_category or det.category)
        label = f"{category_label} {float(det.confidence) * 100:.0f}%"
        if det.track_id is not None:
            label = f"{label} #{int(det.track_id)}"

        if det.track_id is not None and int(det.track_id) in alarming_track_ids:
            color = (76, 122, 255)
            thickness = max(thickness, 2)
            alpha = ALARM_BOX_ALPHA
            label = f"告警 · {label}"

        _draw_corner_box(frame, x1, y1, x2, y2, color, thickness=thickness, alpha=alpha)
        _draw_box_label(frame, label, x1, max(18, y1 - 6), color)


def _draw_scene_guides(frame: np.ndarray, camera: dict) -> np.ndarray:
    height, width = frame.shape[:2]
    overlay = frame.copy()
    labels: list[tuple[str, tuple[int, int], tuple[int, int, int]]] = []

    for roi in camera.get("rois", []):
        line = roi.get("line")
        if isinstance(line, list) and len(line) == 2:
            p1 = (int(float(line[0][0]) * width), int(float(line[0][1]) * height))
            p2 = (int(float(line[1][0]) * width), int(float(line[1][1]) * height))
            cv2.line(overlay, p1, p2, (96, 210, 242), 1, cv2.LINE_AA)
            labels.append((str(roi.get("label") or roi.get("id") or "boundary"), (p1[0] + 8, max(24, p1[1] - 8)), (96, 210, 242)))

        path_points = roi.get("path_points", [])
        if isinstance(path_points, list) and len(path_points) >= 2:
            centerline = np.array(
                [[int(float(x) * width), int(float(y) * height)] for x, y in path_points],
                dtype=np.int32,
            )
            cv2.polylines(overlay, [centerline], False, (96, 210, 242), 2, cv2.LINE_AA)
            anchor = centerline[0]
            labels.append((str(roi.get("label") or roi.get("id") or "corridor"), (int(anchor[0]) + 8, max(24, int(anchor[1]) - 8)), (96, 210, 242)))

        polygon = roi.get("polygon", [])
        if polygon:
            points = np.array(
                [[int(float(x) * width), int(float(y) * height)] for x, y in polygon],
                dtype=np.int32,
            )
            cv2.polylines(overlay, [points], True, (96, 210, 242), 1, cv2.LINE_AA)

    for zone in camera.get("dwell_zones", []):
        polygon = zone.get("polygon", [])
        if not polygon:
            continue

        points = np.array(
            [[int(float(x) * width), int(float(y) * height)] for x, y in polygon],
            dtype=np.int32,
        )
        cv2.polylines(overlay, [points], True, (118, 216, 156), 1, cv2.LINE_AA)
        anchor = points[0]
        labels.append((str(zone.get("label") or zone.get("id") or "zone"), (int(anchor[0]) + 8, max(24, int(anchor[1]) - 8)), (118, 216, 156)))

    _blend_overlay(frame, overlay, GUIDE_ALPHA)
    for text, pos, color in labels:
        frame = _draw_guide_label(frame, text, pos, color)
    return frame


def _draw_status_banner(
    frame: np.ndarray,
    camera_id: str,
    detections: List,
    preview_mode: str,
    preview_error: str = "",
) -> None:
    mode_label = PREVIEW_MODE_LABELS.get(preview_mode, preview_mode)
    title = "实时识别"
    detail = f"{camera_id} · 目标 {len(detections)} · {mode_label}"
    title_w, title_h = _text_size(title, 15)
    detail_w, detail_h = _text_size(detail, 12)
    card_w = max(218, title_w + detail_w + 64)
    card_h = 52
    x1, y1 = 16, 16
    x2, y2 = min(frame.shape[1] - 16, x1 + card_w), y1 + card_h

    overlay = frame.copy()
    cv2.rectangle(overlay, (x1, y1), (x2, y2), (4, 12, 22), -1)
    cv2.rectangle(overlay, (x1, y1), (x2, y2), (58, 145, 185), 1, cv2.LINE_AA)
    cv2.rectangle(overlay, (x1, y1), (x1 + 4, y2), (74, 222, 255), -1)
    cv2.circle(overlay, (x1 + 18, y1 + 17), 4, (74, 222, 255), -1, cv2.LINE_AA)
    _blend_overlay(frame, overlay, 0.68)

    rendered = _draw_text_with_pil(frame, title, (x1 + 30, y1 + 7), (232, 246, 255), 15)
    frame[:, :] = rendered
    rendered = _draw_text_with_pil(frame, detail, (x1 + 30, y1 + 29), (146, 170, 190), 12)
    frame[:, :] = rendered

    if preview_error:
        badge = "降级显示"
        error_width, error_height = _text_size(badge, 12)
        x2 = frame.shape[1] - 16
        x1 = max(16, x2 - error_width - 22)
        y1 = 12
        y2 = y1 + error_height + 14

        error_overlay = frame.copy()
        cv2.rectangle(error_overlay, (x1, y1), (x2, y2), (22, 28, 38), -1)
        cv2.rectangle(error_overlay, (x1, y1), (x2, y2), (120, 200, 255), 1, cv2.LINE_AA)
        _blend_overlay(frame, error_overlay, 0.50)
        rendered = _draw_text_with_pil(frame, badge, (x1 + 10, y1 + 5), (120, 200, 255), 12)
        frame[:, :] = rendered


def _prepare_detection_frame(frame: np.ndarray) -> tuple[np.ndarray, float]:
    height, width = frame.shape[:2]
    longest_side = max(height, width)
    if longest_side <= STREAM_DETECTION_MAX_SIDE:
        return frame, 1.0

    scale = STREAM_DETECTION_MAX_SIDE / float(longest_side)
    resized_width = max(1, int(width * scale))
    resized_height = max(1, int(height * scale))
    resized_frame = cv2.resize(frame, (resized_width, resized_height), interpolation=cv2.INTER_AREA)
    return resized_frame, scale


def _restore_detection_scale(detections: List, scale: float) -> None:
    if scale >= 0.999:
        return

    inv_scale = 1.0 / max(scale, 1e-6)
    for det in detections:
        det.bbox.x1 *= inv_scale
        det.bbox.y1 *= inv_scale
        det.bbox.x2 *= inv_scale
        det.bbox.y2 *= inv_scale


def _render_preview_frame(
    frame: np.ndarray,
    camera_id: str,
    camera: dict,
    detections: List,
    preview_mode: str,
    preview_error: str = "",
) -> np.ndarray:
    rendered = frame.copy()
    rendered = _draw_scene_guides(rendered, camera)
    _draw_detections(rendered, detections, preview_mode, camera_id)
    _draw_status_banner(rendered, camera_id, detections, preview_mode, preview_error)
    return rendered


def mjpeg_stream(
    camera_id: str,
    max_fps: int = STREAM_MAX_FPS_DEFAULT,
    preview_mode: str = "person",
) -> Generator[bytes, None, None]:
    camera, cap, source, stream_marker = _open_capture(camera_id)
    if not camera or cap is None:
        fallback = _fallback_frame(f"unknown camera: {camera_id}")
        while True:
            yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + fallback + b"\r\n"
            time.sleep(0.5)

    frame_interval = 1.0 / max(1, max_fps)

    preview_class_ids: List[int] = []
    preview_error = ""

    cached_detections: List = []
    frame_index = 0
    read_failures = 0

    try:
        while True:
            started = time.time()

            latest_camera = engine.get_camera(camera_id)
            latest_marker = _stream_marker(latest_camera.get("stream")) if latest_camera else ""
            if latest_camera:
                camera = latest_camera
            if latest_camera and latest_marker != stream_marker:
                cap.release()
                camera, cap, source, stream_marker = _open_capture(camera_id)
                cached_detections = []
                frame_index = 0
                read_failures = 0

            if not camera or cap is None:
                fallback = _fallback_frame(f"unknown camera: {camera_id}")
                yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + fallback + b"\r\n"
                time.sleep(0.5)
                continue

            if not cap.isOpened():
                fallback = _fallback_frame(f"cannot open source: {source}")
                yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + fallback + b"\r\n"
                time.sleep(0.5)
                cap.release()
                camera, cap, source, stream_marker = _open_capture(camera_id)
                read_failures = 0
                continue

            ok, frame = cap.read()
            if not ok or frame is None:
                read_failures += 1
                if read_failures >= STREAM_READ_FAILURE_REOPEN_AFTER:
                    cap.release()
                    camera, cap, source, stream_marker = _open_capture(camera_id)
                    cached_detections = []
                    frame_index = 0
                    read_failures = 0
                    fallback = _fallback_frame("stream disconnected, reconnecting...")
                else:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    fallback = _fallback_frame("waiting for stream frame...")
                yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + fallback + b"\r\n"
                time.sleep(0.2)
                continue

            read_failures = 0
            frame_index += 1
            if frame_index == 1 or frame_index % PREVIEW_DETECTION_INTERVAL == 0:
                try:
                    detection_frame, detect_scale = _prepare_detection_frame(frame)
                    analysis = vision_backend_service.analyze_frame(
                        detection_frame,
                        camera_id=camera_id,
                        scene_id=_scene_id_for_camera(camera_id),
                        preview_mode=preview_mode,
                        conf=PREVIEW_CONFIDENCE,
                        imgsz=PREVIEW_IMGSZ,
                    )
                    cached_detections = list(analysis.overlay_detections or analysis.detections or [])
                    _restore_detection_scale(cached_detections, detect_scale)

                    now = datetime.now(timezone.utc).replace(tzinfo=None)
                    if analysis.direct_events:
                        normalized_events = []
                        for event in analysis.direct_events:
                            cloned = event.copy(deep=True)
                            if cloned.bbox is not None and max(cloned.bbox.x1, cloned.bbox.y1, cloned.bbox.x2, cloned.bbox.y2) <= 1.1:
                                cloned.bbox.x1 *= frame.shape[1]
                                cloned.bbox.x2 *= frame.shape[1]
                                cloned.bbox.y1 *= frame.shape[0]
                                cloned.bbox.y2 *= frame.shape[0]
                            normalized_events.append(cloned)
                        engine.apply_rule_events(
                            camera_id=camera_id,
                            timestamp=now,
                            events=normalized_events,
                        )
                    else:
                        tracked_detections = tracking_service.assign_tracks(list(analysis.detections or []), now=now)
                        cached_detections = tracked_detections if tracked_detections else cached_detections
                        frame_payload = DetectionFrame(
                            camera_id=camera_id,
                            frame_id=f"live_{frame_index}",
                            timestamp=now,
                            width=frame.shape[1],
                            height=frame.shape[0],
                            detections=tracked_detections,
                        )
                        engine.evaluate_frame(frame_payload)

                    preview_error = analysis.error or ""
                except Exception as exc:  # pragma: no cover - depends on local runtime
                    preview_error = str(exc)
                    cached_detections = []

            rendered = _render_preview_frame(frame, camera_id, camera, cached_detections, preview_mode, preview_error)
            ok_encode, jpg = cv2.imencode(".jpg", rendered, [int(cv2.IMWRITE_JPEG_QUALITY), STREAM_JPEG_QUALITY])
            if ok_encode:
                yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpg.tobytes() + b"\r\n"

            elapsed = time.time() - started
            wait_time = frame_interval - elapsed
            if wait_time > 0:
                time.sleep(wait_time)
    finally:
        if cap is not None:
            cap.release()
