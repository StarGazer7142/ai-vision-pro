from __future__ import annotations

import json
import logging
import math
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from backend.app.core.config import DEFAULT_RULE_PATH, load_rules
from backend.app.schemas.detection import BBox, Detection, DetectionFrame
from backend.app.schemas.vision import VisionRuleEvent
from backend.app.services.storage_service import storage_service


logger = logging.getLogger("ai-platform.rules")


def point_in_polygon(x: float, y: float, polygon: List[Tuple[float, float]]) -> bool:
    """Ray-casting point-in-polygon for normalized coordinates (0-1)."""
    num = len(polygon)
    if num < 3:
        return False
    inside = False
    j = num - 1
    for i in range(num):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        intersect = ((yi > y) != (yj > y)) and (
            x < (xj - xi) * (y - yi) / ((yj - yi) + 1e-9) + xi
        )
        if intersect:
            inside = not inside
        j = i
    return inside


def signed_distance_to_line(
    point: Tuple[float, float],
    line_start: Tuple[float, float],
    line_end: Tuple[float, float],
) -> float:
    px, py = point
    x1, y1 = line_start
    x2, y2 = line_end
    return (x2 - x1) * (py - y1) - (y2 - y1) * (px - x1)


def segment_crossed_line(
    prev_point: Tuple[float, float],
    cur_point: Tuple[float, float],
    line_start: Tuple[float, float],
    line_end: Tuple[float, float],
) -> bool:
    prev_side = signed_distance_to_line(prev_point, line_start, line_end)
    cur_side = signed_distance_to_line(cur_point, line_start, line_end)
    return (prev_side * cur_side) < 0 and abs(prev_side - cur_side) > 1e-6


def _orientation(a: Tuple[float, float], b: Tuple[float, float], c: Tuple[float, float]) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _on_segment(a: Tuple[float, float], b: Tuple[float, float], c: Tuple[float, float]) -> bool:
    return (
        min(a[0], c[0]) - 1e-6 <= b[0] <= max(a[0], c[0]) + 1e-6
        and min(a[1], c[1]) - 1e-6 <= b[1] <= max(a[1], c[1]) + 1e-6
    )


def segments_intersect(
    a1: Tuple[float, float],
    a2: Tuple[float, float],
    b1: Tuple[float, float],
    b2: Tuple[float, float],
) -> bool:
    o1 = _orientation(a1, a2, b1)
    o2 = _orientation(a1, a2, b2)
    o3 = _orientation(b1, b2, a1)
    o4 = _orientation(b1, b2, a2)

    if (o1 * o2) < 0 and (o3 * o4) < 0:
        return True

    if abs(o1) <= 1e-6 and _on_segment(a1, b1, a2):
        return True
    if abs(o2) <= 1e-6 and _on_segment(a1, b2, a2):
        return True
    if abs(o3) <= 1e-6 and _on_segment(b1, a1, b2):
        return True
    if abs(o4) <= 1e-6 and _on_segment(b1, a2, b2):
        return True
    return False


def bbox_intersects_line(
    bbox: Tuple[float, float, float, float],
    line_start: Tuple[float, float],
    line_end: Tuple[float, float],
) -> bool:
    x1, y1, x2, y2 = bbox
    left = min(x1, x2)
    right = max(x1, x2)
    top = min(y1, y2)
    bottom = max(y1, y2)

    if left <= line_start[0] <= right and top <= line_start[1] <= bottom:
        return True
    if left <= line_end[0] <= right and top <= line_end[1] <= bottom:
        return True

    corners = [
        (left, top),
        (right, top),
        (right, bottom),
        (left, bottom),
    ]
    edges = [
        (corners[0], corners[1]),
        (corners[1], corners[2]),
        (corners[2], corners[3]),
        (corners[3], corners[0]),
    ]
    return any(segments_intersect(line_start, line_end, edge_start, edge_end) for edge_start, edge_end in edges)


def expand_bbox(
    bbox: Tuple[float, float, float, float],
    tolerance: float,
) -> Tuple[float, float, float, float]:
    x1, y1, x2, y2 = bbox
    left = max(0.0, min(x1, x2) - tolerance)
    right = min(1.0, max(x1, x2) + tolerance)
    top = max(0.0, min(y1, y2) - tolerance)
    bottom = min(1.0, max(y1, y2) + tolerance)
    return left, top, right, bottom


def normalize_bbox_for_rules(
    bbox: Tuple[float, float, float, float],
    frame_width: float,
    frame_height: float,
) -> Tuple[float, float, float, float]:
    x1, y1, x2, y2 = bbox
    if max(abs(x1), abs(y1), abs(x2), abs(y2)) <= 1.1:
        return (float(x1), float(y1), float(x2), float(y2))
    return (
        float(x1) / max(frame_width, 1.0),
        float(y1) / max(frame_height, 1.0),
        float(x2) / max(frame_width, 1.0),
        float(y2) / max(frame_height, 1.0),
    )


ALARMING_DISPLAY_WINDOW_SECONDS = 3.0


class RulesEngine:

    def reset_states(self) -> None:
        """
        凌晨定时调用的选择性重置：清除过期状态，保留最近24小时的告警
        """
        from datetime import timedelta
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        cutoff = now - timedelta(hours=24)

        # 仅保留最近24小时的告警
        recent_alerts = deque(maxlen=2000)
        for alert in self.alerts:
            ts = alert.get("timestamp") or ""
            try:
                alert_dt = datetime.fromisoformat(ts.replace("Z", "+00:00")).replace(tzinfo=None)
                if alert_dt >= cutoff:
                    recent_alerts.append(alert)
            except Exception:
                recent_alerts.append(alert)
        self.alerts = recent_alerts

        # 清除过期的追踪状态
        stale_keys = [k for k, v in self.track_last_seen_at.items() if v < cutoff]
        for k in stale_keys:
            self.track_last_seen_at.pop(k, None)
            self.track_last_center.pop(k, None)
            self.track_y_history.pop(k, None)
            self.track_aspect_history.pop(k, None)

        # 清除过期的 dwell 计时
        stale_dwell = [k for k, v in self.dwell_first_seen.items() if v < cutoff]
        for k in stale_dwell:
            self.dwell_first_seen.pop(k, None)
            self.dwell_confirm_streak.pop(k, None)

        # 保留近期的边界待确认和规则触发记录
        stale_boundary = [k for k, v in self.boundary_pending.items()
                          if isinstance(v, dict) and v.get("ts", now) < cutoff]
        for k in stale_boundary:
            self.boundary_pending.pop(k, None)

        stale_trigger = [k for k, v in self.rule_last_trigger.items() if v < cutoff]
        for k in stale_trigger:
            self.rule_last_trigger.pop(k, None)

        self.active_alert_tracks_by_camera.clear()

    def reset_cumulative_counts(self) -> dict:
        """手动清除所有规则的累计计数（翻越人数、滞留人数等）"""
        reset_rules = []
        for rule_id, state in self.rule_state.items():
            prev_count = int(state.get("count", 0))
            if prev_count > 0:
                state["count"] = 0
                reset_rules.append(rule_id)
        self.cumulative_triggered_tracks.clear()
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        self._refresh_scene_state(timestamp=now, persist=True, force=True)
        return {
            "reset_rules": reset_rules,
            "reset_at": now.isoformat(),
            "rule_count": len(reset_rules),
        }

    def __init__(self, rule_path: Path = DEFAULT_RULE_PATH):
        self.rule_path = Path(rule_path)

        # runtime buffers
        self.alerts = deque(maxlen=2000)
        self.dwell_first_seen: Dict[Tuple[str, str, int], datetime] = {}
        self.dwell_confirm_streak: Dict[Tuple[str, str, int], int] = {}
        self.boundary_pending: Dict[Tuple[str, str, int], dict] = {}
        self.track_last_center: Dict[Tuple[str, int], Tuple[float, float]] = {}
        self.track_last_bbox: Dict[Tuple[str, int], Tuple[float, float, float, float]] = {}
        self.track_last_seen_at: Dict[Tuple[str, int], datetime] = {}
        self.track_y_history: Dict[Tuple[str, int], deque] = {}
        self.track_aspect_history: Dict[Tuple[str, int], deque] = {}
        self.rule_last_trigger: Dict[Tuple[str, int], datetime] = {}
        self.active_alert_tracks_by_camera: Dict[str, Set[int]] = {}

        # 累计触发过的 track_id，用于翻越人数等"累计计数"信号
        # key: (rule_id, camera_id), value: set of track_ids that have ever triggered
        self.cumulative_triggered_tracks: Dict[Tuple[str, str], Set[int]] = {}

        self.rule_state: Dict[str, dict] = {}
        self.scene_state: Dict[str, dict] = {}
        self.last_frame_by_camera: Dict[str, str] = {}

        self.processed_frames = 0
        self.total_generated_alerts = 0
        self.config_revision = 0

        # config objects (initialized by _load_config)
        self.rules_config: Dict[str, object] = {}
        self.rule_lookup: Dict[str, dict] = {}
        self.scene_lookup: Dict[str, dict] = {}
        self.camera_lookup: Dict[str, dict] = {}
        self.scene_ids_by_rule: Dict[str, List[str]] = {}

        self._load_config(preserve_states=False)
    
    def get_alarming_track_ids(self, camera_id: str) -> Set[int]:
        """
        获取指定摄像头当前正在触发报警的所有 track_id。
        这是一个轻量级函数，供实时流服务查询状态使用。
        """
        alarming_track_ids = set(self.active_alert_tracks_by_camera.get(camera_id, set()))
        
        # 遍历最近产生的报警 (只看最近的 50 条即可，保证性能)
        recent_alerts = list(self.alerts)[-50:]
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        
        for alert in recent_alerts:
            # 只关心当前摄像头的报警
            if alert.get("camera_id") != camera_id:
                continue
                
            # 检查报警是否还在有效期内 (例如，报警产生后的 3 秒内都认为处于报警状态)
            # 避免红框只闪烁一帧就消失
            alert_time = datetime.fromisoformat(alert.get("timestamp"))
            if (now - alert_time).total_seconds() < ALARMING_DISPLAY_WINDOW_SECONDS: 
                track_id = alert.get("track_id")
                if track_id is not None:
                    alarming_track_ids.add(int(track_id))
                    
        return alarming_track_ids


    def _load_config(self, preserve_states: bool) -> None:
        old_rule_state = dict(self.rule_state) if preserve_states else {}
        old_scene_state = dict(self.scene_state) if preserve_states else {}

        self.rules_config = load_rules(self.rule_path)
        self.rule_lookup = {rule["id"]: rule for rule in self.rules_config.get("rules", [])}
        self.scene_lookup = {scene["id"]: scene for scene in self.rules_config.get("scenes", [])}
        self.camera_lookup = {camera["id"]: camera for camera in self.rules_config.get("cameras", [])}

        self.scene_ids_by_rule = {}
        for scene in self.rules_config.get("scenes", []):
            for rule_id in scene.get("rule_ids", []):
                self.scene_ids_by_rule.setdefault(rule_id, []).append(scene["id"])

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        self.rule_state = {}
        for rule in self.rules_config.get("rules", []):
            default_state = {
                "rule_id": rule["id"],
                "camera_id": rule.get("camera_id"),
                "active": 0,
                "count": 0,
                "timestamp": now.isoformat(),
            }
            if preserve_states and rule["id"] in old_rule_state:
                previous = old_rule_state[rule["id"]]
                default_state["active"] = int(previous.get("active", 0))
                default_state["count"] = int(previous.get("count", 0))
                default_state["timestamp"] = str(previous.get("timestamp", now.isoformat()))
            self.rule_state[rule["id"]] = default_state

        self.scene_state = old_scene_state if preserve_states else {}
        self.boundary_pending.clear()
        self.dwell_confirm_streak.clear()
        self.active_alert_tracks_by_camera.clear()
        self.config_revision += 1
        self._refresh_scene_state(timestamp=now, persist=not preserve_states, force=True)

    def reload_rules(self) -> dict:
        self._load_config(preserve_states=True)
        return {
            "ok": True,
            "revision": self.config_revision,
            "rule_count": len(self.rule_lookup),
            "scene_count": len(self.scene_lookup),
            "camera_count": len(self.camera_lookup),
            "loaded_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
        }

    def get_camera(self, camera_id: str) -> Optional[dict]:
        return self.camera_lookup.get(camera_id)

    def get_scene(self, scene_id: str) -> Optional[dict]:
        return self.scene_lookup.get(scene_id)

    def get_scenes(self) -> List[dict]:
        return self.rules_config.get("scenes", [])

    def get_rules_by_camera(self, camera_id: str) -> List[dict]:
        return [r for r in self.rules_config.get("rules", []) if r.get("camera_id") == camera_id]

    def get_rule_states(self) -> List[dict]:
        return list(self.rule_state.values())

    def _track_key(self, det_index: int, track_id: Optional[int]) -> int:
        return int(track_id) if track_id is not None else -(det_index + 1)

    def _should_include_category(self, rule: dict, category: str) -> bool:
        category_filter = rule.get("category_filter")
        if not category_filter:
            return True
        return category in category_filter

    def _should_trigger(self, rule: dict, track_key: int, timestamp: datetime) -> bool:
        cooldown = int(rule.get("cooldown_seconds", 5))
        cache_key = (rule["id"], track_key)
        last_trigger = self.rule_last_trigger.get(cache_key)
        if last_trigger is None:
            self.rule_last_trigger[cache_key] = timestamp
            return True

        if (timestamp - last_trigger) >= timedelta(seconds=cooldown):
            self.rule_last_trigger[cache_key] = timestamp
            return True

        return False

    @staticmethod
    def _signal_hold_seconds(rule: dict) -> int:
        configured = rule.get("signal_hold_seconds")
        if configured is not None:
            return max(0, int(configured))
        cooldown = max(0, int(rule.get("cooldown_seconds", 5)))
        return max(3, cooldown)

    def _latched_active_tracks(self, rule: dict, active_tracks: Set[int], timestamp: datetime) -> Set[int]:
        latched_tracks = set(active_tracks)
        hold_seconds = self._signal_hold_seconds(rule)
        if hold_seconds <= 0:
            return latched_tracks

        for (rule_id, track_key), last_trigger in self.rule_last_trigger.items():
            if rule_id != rule["id"]:
                continue
            if (timestamp - last_trigger).total_seconds() <= hold_seconds:
                latched_tracks.add(track_key)
        return latched_tracks

    @staticmethod
    def _alert_label(rule: dict, fallback_map: Dict[str, str], default: str) -> str:
        explicit_label = str(rule.get("alert_label") or "").strip()
        if explicit_label:
            return explicit_label
        return fallback_map.get(str(rule.get("id") or "").strip(), default)

    @classmethod
    def _rule_display_label(cls, rule: dict | None, rule_id: str = "") -> str:
        rule = rule or {}
        normalized_rule_id = str(rule.get("id") or rule_id or "").strip()
        return cls._alert_label(
            rule,
            {
                "fence_intrusion": "翻越围栏",
                "fence_dwell": "人员滞留",
                "dock_dwell_person": "码头滞留",
                "warehouse_dwell": "仓库滞留",
            },
            normalized_rule_id or "未知规则",
        )

    def get_rule_display_label(self, rule_id: str) -> str:
        normalized_rule_id = str(rule_id or "").strip()
        return self._rule_display_label(self.rule_lookup.get(normalized_rule_id), normalized_rule_id)

    def get_rule_context(self, rule_id: str, camera_id: str = "") -> dict:
        normalized_rule_id = str(rule_id or "").strip()
        rule = self.rule_lookup.get(normalized_rule_id) or {}
        normalized_camera_id = str(camera_id or rule.get("camera_id") or "").strip()
        camera = self.get_camera(normalized_camera_id) or {}
        zone_id = str(rule.get("zone_id") or "").strip()
        dwell_zone = next(
            (zone for zone in camera.get("dwell_zones", []) if str(zone.get("id") or "") == zone_id),
            {},
        )
        threshold = rule.get("threshold_seconds", dwell_zone.get("threshold_seconds"))
        context = {
            "rule_id": normalized_rule_id,
            "rule_label": self._rule_display_label(rule, normalized_rule_id),
            "rule_type": str(rule.get("type") or "").strip(),
            "camera_id": normalized_camera_id,
            "zone_id": zone_id,
            "zone_label": str(dwell_zone.get("label") or zone_id).strip(),
            "severity": str(rule.get("severity") or "").strip(),
        }
        if threshold is not None:
            try:
                context["threshold_seconds"] = max(1, int(threshold))
            except (TypeError, ValueError):
                pass
        return context

    def _enrich_alert_display(self, alert: dict) -> dict:
        rule_id = str(alert.get("rule_id") or "").strip()
        rule = self.rule_lookup.get(rule_id)
        label = self._rule_display_label(rule, rule_id)
        enriched = dict(alert)
        enriched["rule_label"] = label
        enriched["rule_display"] = label
        return enriched

    @classmethod
    def _dwell_alert_message(cls, rule: dict, dwell_time: float) -> str:
        seconds = max(1, int(dwell_time))
        dwell_prefix = cls._alert_label(
            rule,
            {
                "fence_dwell": "人员滞留",
                "dock_dwell_person": "码头滞留",
                "warehouse_dwell": "仓库滞留",
            },
            "区域滞留",
        )
        return f"{dwell_prefix} {seconds}s"

    @classmethod
    def _boundary_alert_message(cls, rule: dict) -> str:
        return cls._alert_label(
            rule,
            {
                "fence_intrusion": "翻越围栏",
            },
            "切线越界",
        )

    @staticmethod
    def _check_vertical_velocity(
        track_key: int,
        camera_id: str,
        current_y: float,
        y_history: Dict[Tuple[str, int], deque],
        min_vertical_speed: float,
        lookback_frames: int = 8,
    ) -> bool:
        """竖直速度门控：翻越时人有明显竖直位移，走路经过时竖直位移很小。
        返回 True 表示通过检查（是翻越行为或无法判断），False 表示应抑制告警。"""
        if min_vertical_speed <= 0:
            return True
        key = (camera_id, track_key)
        hist = y_history.get(key)
        if not hist or len(hist) < 3:
            return True  # 数据不足，放行
        recent = list(hist)[-lookback_frames:]
        if len(recent) < 3:
            return True
        # 计算竖直位移范围：最大y - 最小y
        y_values = [p[0] for p in recent]
        vertical_span = max(y_values) - min(y_values)
        return vertical_span >= min_vertical_speed

    @staticmethod
    def _check_aspect_ratio_change(
        track_key: int,
        camera_id: str,
        current_bbox: Tuple[float, float, float, float],
        aspect_history: Dict[Tuple[str, int], deque],
        max_change: float,
        lookback_frames: int = 8,
    ) -> bool:
        """宽高比变化检查：翻越时身体拉伸/压缩导致宽高比显著变化，走路时比例稳定。
        返回 True 表示通过检查（是翻越行为或无法判断），False 表示应抑制告警。"""
        if max_change <= 0:
            return True
        x1, y1, x2, y2 = current_bbox
        cur_w = max(1e-6, abs(x2 - x1))
        cur_h = max(1e-6, abs(y2 - y1))
        cur_aspect = cur_h / cur_w
        key = (camera_id, track_key)
        hist = aspect_history.get(key)
        if not hist or len(hist) < 3:
            return True  # 数据不足，放行
        recent = list(hist)[-lookback_frames:]
        if len(recent) < 3:
            return True
        # 用最近几帧的平均宽高比作为基准
        avg_aspect = sum(recent) / len(recent)
        if avg_aspect < 1e-6:
            return True
        change_ratio = abs(cur_aspect - avg_aspect) / avg_aspect
        return change_ratio >= max_change

    def evaluate_frame(self, frame: DetectionFrame) -> List[dict]:
        triggered: List[dict] = []
        camera = self.get_camera(frame.camera_id)
        if not camera:
            return triggered
        
        frame_width = getattr(frame, "width", 1920.0)
        frame_height = getattr(frame, "height", 1080.0)

        roi_lookup = {roi["id"]: roi for roi in camera.get("rois", [])}
        dwell_lookup = {zone["id"]: zone for zone in camera.get("dwell_zones", [])}
        camera_rules = self.get_rules_by_camera(frame.camera_id)

        active_tracks_by_rule: Dict[str, Set[int]] = {rule["id"]: set() for rule in camera_rules}
        previous_centers = dict(self.track_last_center)
        previous_bboxes = dict(self.track_last_bbox)
        current_centers: Dict[Tuple[str, int], Tuple[float, float]] = {}
        current_bboxes: Dict[Tuple[str, int], Tuple[float, float, float, float]] = {}
        det_cache: List[Tuple[int, Detection, int, float, float]] = []

        for det_index, det in enumerate(frame.detections):
            track_key = self._track_key(det_index, det.track_id)
            cx = (det.bbox.x1 + det.bbox.x2) / 2
            cy = (det.bbox.y1 + det.bbox.y2) / 2
            if cx > 1.1 or cy > 1.1:
                cx = cx / frame_width
                cy = cy / frame_height
            det_cache.append((det_index, det, track_key, cx, cy))
            current_centers[(frame.camera_id, track_key)] = (cx, cy)
            current_bboxes[(frame.camera_id, track_key)] = normalize_bbox_for_rules(
                (
                    float(det.bbox.x1),
                    float(det.bbox.y1),
                    float(det.bbox.x2),
                    float(det.bbox.y2),
                ),
                frame_width,
                frame_height,
            )
            self.track_last_seen_at[(frame.camera_id, track_key)] = frame.timestamp
            # 追踪竖直位移历史和宽高比历史
            y_key = (frame.camera_id, track_key)
            if y_key not in self.track_y_history:
                self.track_y_history[y_key] = deque(maxlen=30)
            self.track_y_history[y_key].append((cy, frame.timestamp))
            nb = current_bboxes[(frame.camera_id, track_key)]
            nb_w = max(1e-6, abs(nb[2] - nb[0]))
            nb_h = max(1e-6, abs(nb[3] - nb[1]))
            if y_key not in self.track_aspect_history:
                self.track_aspect_history[y_key] = deque(maxlen=30)
            self.track_aspect_history[y_key].append(nb_h / nb_w)

        for rule in camera_rules:
            if rule["type"] == "boundary":
                roi = roi_lookup.get(rule.get("roi_id"))
                if not roi:
                    continue
                polygon = [(float(x), float(y)) for x, y in roi.get("polygon", [])]
                line = roi.get("line", [])
                line_enabled = isinstance(line, list) and len(line) == 2
                confirm_frames = max(1, int(rule.get("confirm_frames", 1)))
                line_touch_tolerance = max(
                    0.0,
                    min(
                        0.20,
                        float(rule.get("line_touch_tolerance", roi.get("path_width", 0.0)) or 0.0),
                    ),
                )
                direction = str(rule.get("crossing_direction", "any")).strip().lower()
                if direction not in {"any", "neg_to_pos", "pos_to_neg"}:
                    direction = "any"

                # trigger_side: 决定人在哪一侧时才计为"活跃/触发"
                #   "any" - 只要 bbox 碰线就算（默认，向后兼容）
                #   "pos" - 仅当人在警戒线正侧（cur_dist > 0）时才计为活跃
                #   "neg" - 仅当人在警戒线负侧（cur_dist < 0）时才计为活跃
                trigger_side = str(rule.get("trigger_side", "any")).strip().lower()
                if trigger_side not in {"any", "pos", "neg"}:
                    trigger_side = "any"

                for det_index, det, track_key, cx, cy in det_cache:
                    if not self._should_include_category(rule, det.category):
                        continue

                    pending_key = (frame.camera_id, rule["id"], track_key)
                    pending = self.boundary_pending.get(pending_key)

                    crossed = False
                    if line_enabled:
                        p1 = (float(line[0][0]), float(line[0][1]))
                        p2 = (float(line[1][0]), float(line[1][1]))
                        prev_center = previous_centers.get((frame.camera_id, track_key))
                        current_bbox = current_bboxes[(frame.camera_id, track_key)]
                        prev_bbox = previous_bboxes.get((frame.camera_id, track_key))
                        cur_dist = signed_distance_to_line((cx, cy), p1, p2)
                        line_check_bbox = expand_bbox(current_bbox, line_touch_tolerance) if line_touch_tolerance > 0 else current_bbox
                        prev_line_check_bbox = expand_bbox(prev_bbox, line_touch_tolerance) if prev_bbox and line_touch_tolerance > 0 else prev_bbox
                        current_intersects = bbox_intersects_line(line_check_bbox, p1, p2)
                        prev_intersects = bbox_intersects_line(prev_line_check_bbox, p1, p2) if prev_line_check_bbox else False

                        just_crossed = False

                        # 优先检查 center-crossing（最可靠的穿越检测）
                        center_crossed = False
                        crossing_direction_val = None
                        if prev_center is not None:
                            prev_dist = signed_distance_to_line(prev_center, p1, p2)
                            center_crossed = segment_crossed_line(prev_center, (cx, cy), p1, p2)
                            if center_crossed:
                                crossing_direction_val = "neg_to_pos" if (prev_dist < 0 < cur_dist) else "pos_to_neg"

                        if center_crossed:
                            direction_ok = direction == "any" or direction == crossing_direction_val
                            side_ok = trigger_side == "any" or (
                                trigger_side == "neg" and cur_dist < 0
                            ) or (
                                trigger_side == "pos" and cur_dist > 0
                            )
                            if direction_ok and side_ok:
                                side = 1 if cur_dist > 0 else -1
                                self.boundary_pending[pending_key] = {"side": side, "frames": 1}
                                pending = self.boundary_pending[pending_key]
                                just_crossed = True
                            else:
                                self.boundary_pending.pop(pending_key, None)
                                pending = None

                        # 仅当 center 未穿越时，才用 bbox intersect 作为补充检测
                        elif current_intersects and direction == "any":
                            if not prev_intersects:
                                side_ok = trigger_side == "any" or (
                                    trigger_side == "neg" and cur_dist < 0
                                ) or (
                                    trigger_side == "pos" and cur_dist > 0
                                )
                                if side_ok:
                                    self.boundary_pending[pending_key] = {"side": 1 if cur_dist > 0 else -1 if cur_dist < 0 else 0, "frames": 1}
                                    pending = self.boundary_pending[pending_key]
                                    just_crossed = True

                        # 首次出现：无前帧，bbox 也没碰线，但中心在危险侧 → 直接判定
                        elif prev_center is None and trigger_side != "any":
                            on_danger = (trigger_side == "neg" and cur_dist < 0) or (
                                trigger_side == "pos" and cur_dist > 0
                            )
                            if on_danger:
                                self.boundary_pending[pending_key] = {"side": 1 if cur_dist > 0 else -1, "frames": confirm_frames}
                                pending = self.boundary_pending[pending_key]
                                just_crossed = True

                        if not just_crossed and pending is not None:
                            side = 1 if cur_dist > 0 else -1 if cur_dist < 0 else 0
                            if current_intersects and direction == "any":
                                pending["frames"] = int(pending.get("frames", 0)) + 1
                            elif side != 0 and int(pending.get("side", 0)) == side:
                                pending["frames"] = int(pending.get("frames", 0)) + 1
                            else:
                                self.boundary_pending.pop(pending_key, None)
                                pending = None

                        # trigger_side: 仅当人在指定侧时才计为活跃
                        side_active = True
                        if trigger_side == "pos" and cur_dist <= 0:
                            side_active = False
                        elif trigger_side == "neg" and cur_dist >= 0:
                            side_active = False
                        if current_intersects and side_active:
                            active_tracks_by_rule[rule["id"]].add(track_key)
                    elif polygon:
                        current_inside = point_in_polygon(cx, cy, polygon)
                        prev_inside = False
                        prev_center = previous_centers.get((frame.camera_id, track_key))
                        if prev_center is not None:
                            prev_inside = point_in_polygon(prev_center[0], prev_center[1], polygon)
                        crossed = current_inside and not prev_inside
                        if crossed:
                            self.boundary_pending[pending_key] = {
                                "side": 1,
                                "frames": int(self.boundary_pending.get(pending_key, {}).get("frames", 0)) + 1,
                            }
                            pending = self.boundary_pending.get(pending_key)
                        elif not current_inside:
                            self.boundary_pending.pop(pending_key, None)
                            pending = None

                    if pending is None or int(pending.get("frames", 0)) < confirm_frames:
                        continue

                    active_tracks_by_rule[rule["id"]].add(track_key)
                    self.boundary_pending.pop(pending_key, None)

                    # --- 翻越行为过滤：竖直速度 + 宽高比变化 ---
                    min_vs = float(rule.get("min_vertical_speed", 0))
                    max_ar = float(rule.get("max_aspect_ratio_change", 0))
                    is_likely_climbing = True
                    if min_vs > 0:
                        is_likely_climbing = is_likely_climbing and self._check_vertical_velocity(
                            track_key, frame.camera_id, cy,
                            self.track_y_history, min_vs,
                        )
                    if max_ar > 0:
                        is_likely_climbing = is_likely_climbing and self._check_aspect_ratio_change(
                            track_key, frame.camera_id,
                            current_bboxes[(frame.camera_id, track_key)],
                            self.track_aspect_history, max_ar,
                        )
                    if not is_likely_climbing:
                        continue

                    if self._should_trigger(rule, track_key, frame.timestamp):
                        triggered.append(
                            self._save_alert(
                                rule=rule,
                                det=det,
                                message=self._boundary_alert_message(rule),
                            )
                        )

            elif rule["type"] == "dwell":
                zone = dwell_lookup.get(rule.get("zone_id"))
                if not zone:
                    continue
                polygon = [(float(x), float(y)) for x, y in zone.get("polygon", [])]
                threshold = int(rule.get("threshold_seconds", zone.get("threshold_seconds", 30)))
                confirm_frames = max(1, int(rule.get("confirm_frames", 1)))
                inside_keys: Set[Tuple[str, str, int]] = set()

                for det_index, det, track_key, cx, cy in det_cache:
                    if not self._should_include_category(rule, det.category):
                        continue

                    if not point_in_polygon(cx, cy, polygon):
                        continue

                    dwell_key = (frame.camera_id, rule["zone_id"], track_key)
                    inside_keys.add(dwell_key)

                    first_seen = self.dwell_first_seen.get(dwell_key)
                    if first_seen is None:
                        self.dwell_first_seen[dwell_key] = frame.timestamp
                        continue

                    dwell_time = (frame.timestamp - first_seen).total_seconds()
                    if dwell_time < threshold:
                        self.dwell_confirm_streak[dwell_key] = 0
                        continue

                    self.dwell_confirm_streak[dwell_key] = self.dwell_confirm_streak.get(dwell_key, 0) + 1
                    if self.dwell_confirm_streak[dwell_key] < confirm_frames:
                        continue

                    active_tracks_by_rule[rule["id"]].add(track_key)
                    if self._should_trigger(rule, track_key, frame.timestamp):
                        triggered.append(
                            self._save_alert(
                                rule=rule,
                                det=det,
                                message=self._dwell_alert_message(rule, dwell_time),
                            )
                        )

                stale_keys = [
                    key
                    for key in list(self.dwell_first_seen.keys())
                    if key[0] == frame.camera_id and key[1] == rule["zone_id"] and key not in inside_keys
                ]
                for stale_key in stale_keys:
                    self.dwell_first_seen.pop(stale_key, None)
                    self.dwell_confirm_streak.pop(stale_key, None)

        display_tracks_by_rule: Dict[str, Set[int]] = {}
        for rule in camera_rules:
            active_tracks = active_tracks_by_rule.get(rule["id"], set())
            display_tracks = self._latched_active_tracks(
                rule,
                active_tracks,
                frame.timestamp,
            )
            display_tracks_by_rule[rule["id"]] = display_tracks
            # count 使用累计触发数（只增不减），记录所有曾被检测到的人数
            # active 使用当前帧实际状态
            cum_key = (rule["id"], frame.camera_id)
            cum_set = self.cumulative_triggered_tracks.setdefault(cum_key, set())
            cum_set.update(active_tracks)
            cum_count = len(cum_set)
            self.rule_state[rule["id"]] = {
                "rule_id": rule["id"],
                "camera_id": frame.camera_id,
                "active": 1 if len(active_tracks) > 0 else 0,
                "count": cum_count,
                "timestamp": frame.timestamp.isoformat(),
            }

        camera_active_tracks: Set[int] = set()
        for track_ids in display_tracks_by_rule.values():
            camera_active_tracks.update(track_ids)
        self.active_alert_tracks_by_camera[frame.camera_id] = camera_active_tracks

        self.track_last_center.update(current_centers)
        self.track_last_bbox.update(current_bboxes)
        stale_track_keys = [
            key
            for key, last_seen in list(self.track_last_seen_at.items())
            if (frame.timestamp - last_seen).total_seconds() > 60
        ]
        for key in stale_track_keys:
            self.track_last_seen_at.pop(key, None)
            self.track_last_center.pop(key, None)
            self.track_last_bbox.pop(key, None)
            self.track_y_history.pop(key, None)
            self.track_aspect_history.pop(key, None)
            stale_camera, stale_track_key = key

            boundary_stale = [
                pending_key
                for pending_key in list(self.boundary_pending.keys())
                if pending_key[0] == stale_camera and pending_key[2] == stale_track_key
            ]
            for pending_key in boundary_stale:
                self.boundary_pending.pop(pending_key, None)

            dwell_stale = [
                dwell_key
                for dwell_key in list(self.dwell_confirm_streak.keys())
                if dwell_key[0] == stale_camera and dwell_key[2] == stale_track_key
            ]
            for dwell_key in dwell_stale:
                self.dwell_confirm_streak.pop(dwell_key, None)

        self.processed_frames += 1
        self.total_generated_alerts += len(triggered)
        self.last_frame_by_camera[frame.camera_id] = frame.timestamp.isoformat()

        self._refresh_scene_state(frame.timestamp, persist=True)
        return triggered

    def _scene_ids_for_rule(self, rule_id: str) -> List[str]:
        return self.scene_ids_by_rule.get(rule_id, [])

    def _alert_display_window_seconds(self, rule_id: str) -> int:
        rule = self.rule_lookup.get(rule_id) or {}
        return max(1, self._signal_hold_seconds(rule))

    def _is_alert_current(self, alert: dict, now: Optional[datetime] = None) -> bool:
        now = now or datetime.now(timezone.utc).replace(tzinfo=None)
        try:
            alert_time = datetime.fromisoformat(str(alert.get("timestamp") or ""))
        except Exception:
            return False
        display_window = self._alert_display_window_seconds(str(alert.get("rule_id") or ""))
        return (now - alert_time).total_seconds() <= display_window

    def apply_rule_events(
        self,
        *,
        camera_id: str,
        timestamp: datetime,
        events: List[VisionRuleEvent],
    ) -> List[dict]:
        camera_rules = self.get_rules_by_camera(camera_id)
        active_tracks_by_rule: Dict[str, Set[int]] = {rule["id"]: set() for rule in camera_rules}
        triggered: List[dict] = []

        for event_index, event in enumerate(events or []):
            rule_id = str(event.rule_id or "").strip()
            rule = self.rule_lookup.get(rule_id)
            if not rule or str(rule.get("camera_id") or "") != camera_id:
                continue
            if not event.active or int(event.count) <= 0:
                continue

            track_ids = list(event.track_ids or [])
            if event.track_id is not None:
                track_ids.insert(0, int(event.track_id))
            if not track_ids:
                base_track = 900000 + event_index * 100
                track_ids = list(range(base_track, base_track + max(1, int(event.count))))

            needed = max(1, int(event.count))
            while len(track_ids) < needed:
                track_ids.append(track_ids[-1] + 1 if track_ids else 900000 + len(track_ids))

            for track_id in track_ids[:needed]:
                active_tracks_by_rule[rule_id].add(int(track_id))
                det_bbox = event.bbox or BBox(x1=0.1, y1=0.1, x2=0.2, y2=0.2)
                det = Detection(
                    camera_id=camera_id,
                    category=event.category or "person",
                    display_category=event.category or "person",
                    confidence=float(event.confidence or 0.99),
                    bbox=det_bbox,
                    track_id=int(track_id),
                    timestamp=timestamp,
                )
                if self._should_trigger(rule, int(track_id), timestamp):
                    message = str(event.message or "").strip() or self._alert_label(rule, {}, rule_id)
                    triggered.append(self._save_alert(rule=rule, det=det, message=message))

        display_tracks_by_rule: Dict[str, Set[int]] = {}
        for rule in camera_rules:
            active_tracks = active_tracks_by_rule.get(rule["id"], set())
            display_tracks = self._latched_active_tracks(
                rule,
                active_tracks,
                timestamp,
            )
            display_tracks_by_rule[rule["id"]] = display_tracks
            cum_key = (rule["id"], camera_id)
            cum_set = self.cumulative_triggered_tracks.setdefault(cum_key, set())
            cum_set.update(active_tracks)
            cum_count = len(cum_set)
            self.rule_state[rule["id"]] = {
                "rule_id": rule["id"],
                "camera_id": camera_id,
                "active": 1 if len(active_tracks) > 0 else 0,
                "count": cum_count,
                "timestamp": timestamp.isoformat(),
            }

        camera_active_tracks: Set[int] = set()
        for track_ids in display_tracks_by_rule.values():
            camera_active_tracks.update(track_ids)
        self.active_alert_tracks_by_camera[camera_id] = camera_active_tracks

        self.last_frame_by_camera[camera_id] = timestamp.isoformat()
        self.processed_frames += 1
        self.total_generated_alerts += len(triggered)
        self._refresh_scene_state(timestamp, persist=True)
        return triggered

    def _save_alert(self, rule: dict, det: Detection, message: str) -> dict:
        alert = {
            "rule_id": rule["id"],
            "rule_label": self._rule_display_label(rule, str(rule.get("id") or "")),
            "rule_display": self._rule_display_label(rule, str(rule.get("id") or "")),
            "camera_id": det.camera_id,
            "track_id": det.track_id,
            "category": det.category,
            "confidence": float(det.confidence),
            "message": message,
            "timestamp": det.timestamp.isoformat(),
            "severity": rule.get("severity", "medium"),
        }
        self.alerts.append(alert)

        scene_ids = self._scene_ids_for_rule(rule["id"])
        storage_service.insert_alert(alert, scene_ids)
        logger.info(
            "alert_triggered %s",
            json.dumps(
                {
                    "scene_ids": scene_ids,
                    "camera_id": alert["camera_id"],
                    "rule_id": alert["rule_id"],
                    "severity": alert["severity"],
                    "track_id": alert["track_id"],
                    "category": alert["category"],
                    "confidence": alert["confidence"],
                    "message": alert["message"],
                    "timestamp": alert["timestamp"],
                },
                ensure_ascii=False,
            ),
        )
        return alert

    def _log_scene_state_change(self, payload: dict) -> None:
        logger.info(
            "scene_signal_changed %s",
            json.dumps(
                {
                    "scene_id": payload.get("scene_id"),
                    "scene_name": payload.get("scene_name"),
                    "timestamp": payload.get("timestamp"),
                    "signals_cn": payload.get("signals_cn", {}),
                },
                ensure_ascii=False,
            ),
        )

    def get_alerts(self, scene_id: Optional[str] = None, limit: int = 50) -> List[dict]:
        alerts = list(self.alerts)

        if scene_id:
            allowed_rules = set(self.get_scene(scene_id).get("rule_ids", [])) if self.get_scene(scene_id) else set()
            alerts = [item for item in alerts if item.get("rule_id") in allowed_rules]

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        alerts = [item for item in alerts if self._is_alert_current(item, now=now)]
        if not alerts:
            return []

        deduped: dict[tuple[str, str, int], dict] = {}
        for item in alerts:
            dedupe_key = (
                str(item.get("rule_id") or ""),
                str(item.get("camera_id") or ""),
                int(item.get("track_id") or 0),
            )
            deduped[dedupe_key] = item

        current_alerts = sorted(
            (self._enrich_alert_display(item) for item in deduped.values()),
            key=lambda item: str(item.get("timestamp") or ""),
            reverse=True,
        )
        return current_alerts[: max(1, min(limit, len(current_alerts)))]

    def get_alert_history(
        self,
        *,
        scene_id: Optional[str] = None,
        limit: int = 200,
        rule_id: Optional[str] = None,
        camera_id: Optional[str] = None,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
    ) -> List[dict]:
        history = storage_service.get_alert_history(
            scene_id=scene_id,
            limit=limit,
            rule_id=rule_id,
            camera_id=camera_id,
            start_time=start_time,
            end_time=end_time,
        )
        return [self._enrich_alert_display(item) for item in history]

    def _split_defaults(self) -> Tuple[dict, dict]:
        en_defaults: Dict[str, int] = {}
        cn_defaults: Dict[str, int] = {}
        for item in self.rules_config.get("signal_defaults", []):
            key = str(item.get("key", "")).strip()
            if not key:
                continue
            value = int(item.get("value", 0))
            if any(ord(ch) > 127 for ch in key):
                cn_defaults[key] = value
            else:
                en_defaults[key] = value
        return en_defaults, cn_defaults

    def _refresh_scene_state(self, timestamp: datetime, *, persist: bool, force: bool = False) -> None:
        en_defaults, cn_defaults = self._split_defaults()

        for scene in self.rules_config.get("scenes", []):
            signals = dict(en_defaults)
            signals_cn = dict(cn_defaults)

            for rule_id in scene.get("rule_ids", []):
                rule = self.rule_lookup.get(rule_id)
                state = self.rule_state.get(rule_id, {"active": 0, "count": 0})
                if not rule:
                    continue

                active = int(state.get("active", 0))
                count = int(state.get("count", 0))

                signal_key = rule.get("signal_key", f"{rule_id}_active")
                count_key = rule.get("count_key", f"{rule_id}_count")
                signals[signal_key] = active
                signals[count_key] = count

                signal_cn = rule.get("signal_cn", signal_key)
                count_cn = rule.get("count_cn", count_key)
                signals_cn[signal_cn] = active
                signals_cn[count_cn] = count

            payload = {
                "scene_id": scene["id"],
                "scene_name": scene.get("name", scene["id"]),
                "timestamp": timestamp.isoformat(),
                "signals": signals,
                "signals_cn": signals_cn,
            }

            previous = self.scene_state.get(scene["id"])
            self.scene_state[scene["id"]] = payload

            values_changed = force or previous is None or previous.get("signals") != signals or previous.get("signals_cn") != signals_cn
            if persist and values_changed:
                storage_service.insert_signal_snapshot(payload)
            if values_changed:
                self._log_scene_state_change(payload)

    def get_scene_signals(self, scene_id: Optional[str] = None):
        if scene_id:
            return self.scene_state.get(scene_id)
        return list(self.scene_state.values())

    def get_scene_signal_history(self, scene_id: str, limit: int = 200) -> List[dict]:
        return storage_service.get_signal_history(scene_id=scene_id, limit=limit)

    def inject_debug_signal(self, rule_id: str, count: int, message: str) -> Optional[dict]:
        rule = self.rule_lookup.get(rule_id)
        if not rule:
            return None

        count = max(0, int(count))
        timestamp = datetime.now(timezone.utc).replace(tzinfo=None)
        self.rule_state[rule_id] = {
            "rule_id": rule_id,
            "camera_id": rule.get("camera_id"),
            "active": 1 if count > 0 else 0,
            "count": count,
            "timestamp": timestamp.isoformat(),
        }

        if count > 0:
            for index in range(count):
                det = Detection(
                    camera_id=str(rule.get("camera_id") or "unknown"),
                    category="person",
                    confidence=0.99,
                    bbox={"x1": 0.10, "y1": 0.10, "x2": 0.20, "y2": 0.20},
                    track_id=9000 + index,
                    timestamp=timestamp,
                )
                self._save_alert(rule=rule, det=det, message=message or "[调试] 手动注入事件")

        self._refresh_scene_state(timestamp, persist=True, force=True)

        return {
            "rule_id": rule_id,
            "active": 1 if count > 0 else 0,
            "count": count,
            "timestamp": timestamp.isoformat(),
        }

    def seed_fake_alerts(self) -> None:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        for idx, rule in enumerate(self.rules_config.get("rules", [])):
            det = Detection(
                camera_id=str(rule.get("camera_id") or "unknown"),
                category="person",
                confidence=0.91,
                bbox={"x1": 0.10, "y1": 0.10, "x2": 0.20, "y2": 0.20},
                track_id=idx + 100,
                timestamp=now - timedelta(seconds=idx * 4),
            )
            self._save_alert(rule=rule, det=det, message=f"[占位告警] {rule['type']} 触发")

    def record_ingest_frame(
        self,
        *,
        frame_id: str,
        camera_id: str,
        timestamp: str,
        detection_count: int,
        alert_count: int,
    ) -> None:
        storage_service.insert_ingest_frame(
            frame_id=frame_id,
            camera_id=camera_id,
            timestamp=timestamp,
            detection_count=detection_count,
            alert_count=alert_count,
        )

    def get_runtime_status(self) -> dict:
        return {
            "config_revision": self.config_revision,
            "rule_count": len(self.rule_lookup),
            "scene_count": len(self.scene_lookup),
            "camera_count": len(self.camera_lookup),
            "in_memory_alerts": len(self.alerts),
            "processed_frames": self.processed_frames,
            "total_generated_alerts": self.total_generated_alerts,
            "pending_boundary_confirmations": len(self.boundary_pending),
            "pending_dwell_confirmations": len([v for v in self.dwell_confirm_streak.values() if v > 0]),
            "last_frame_by_camera": dict(self.last_frame_by_camera),
            "rule_states": self.get_rule_states(),
        }


engine = RulesEngine()
