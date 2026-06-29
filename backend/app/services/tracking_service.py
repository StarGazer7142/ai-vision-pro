from __future__ import annotations

import itertools
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from backend.app.core.config import load_tracker
from backend.app.schemas.detection import BBox, Detection


@dataclass
class _TrackState:
    track_id: int
    camera_id: str
    category: str
    bbox: BBox
    last_seen: datetime
    missed: int = 0


class IoUTracker:
    def __init__(self):
        cfg = load_tracker()
        
        # 🔴 核心修改 1：大幅降低匹配门槛 (IoU从 0.45 降到 0.1)
        # 只要前后两帧的框稍微沾到一点边，甚至有轻微位移，都死死咬住是同一个人！
        self.match_thresh = float(cfg.get("match_thresh", 0.1)) 
        
        # 🔴 核心修改 2：巨幅增加记忆容量 (允许丢失 60 帧)
        # 哪怕这个人被柱子挡住了，或者 YOLO 瞎了没认出来，只要在 60 帧以内他再次出现，ID 绝对不换！
        self.track_buffer = int(cfg.get("track_buffer", 60)) 
        self.frame_rate = int(cfg.get("frame_rate", 25))
        
        # 🔴 核心修改 3：强制记忆时间延长至 5 秒以上
        self.max_age_seconds = float(cfg.get("max_age_seconds", 5.0))

        self._track_counter = itertools.count(1)
        self._tracks: Dict[Tuple[str, str], Dict[int, _TrackState]] = {}

    @staticmethod
    def _iou(box_a: BBox, box_b: BBox) -> float:
        ax1, ay1, ax2, ay2 = box_a.x1, box_a.y1, box_a.x2, box_a.y2
        bx1, by1, bx2, by2 = box_b.x1, box_b.y1, box_b.x2, box_b.y2

        inter_x1 = max(ax1, bx1)
        inter_y1 = max(ay1, by1)
        inter_x2 = min(ax2, bx2)
        inter_y2 = min(ay2, by2)

        inter_w = max(0.0, inter_x2 - inter_x1)
        inter_h = max(0.0, inter_y2 - inter_y1)
        inter_area = inter_w * inter_h
        if inter_area <= 0:
            return 0.0

        area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
        area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
        union = area_a + area_b - inter_area
        if union <= 1e-9:
            return 0.0
        return inter_area / union

    def _cleanup(self, now: datetime) -> None:
        for key in list(self._tracks.keys()):
            track_map = self._tracks[key]
            stale = []
            for track_id, state in track_map.items():
                age = (now - state.last_seen).total_seconds()
                if age > self.max_age_seconds or state.missed > self.track_buffer:
                    stale.append(track_id)
            for track_id in stale:
                track_map.pop(track_id, None)
            if not track_map:
                self._tracks.pop(key, None)

    def _register_external_track(self, det: Detection, now: datetime) -> None:
        key = (det.camera_id, det.category)
        if key not in self._tracks:
            self._tracks[key] = {}

        track_id = int(det.track_id)
        self._tracks[key][track_id] = _TrackState(
            track_id=track_id,
            camera_id=det.camera_id,
            category=det.category,
            bbox=det.bbox,
            last_seen=now,
            missed=0,
        )

    def _next_track_id(self) -> int:
        used_ids = {
            state.track_id
            for track_map in self._tracks.values()
            for state in track_map.values()
        }
        candidate = next(self._track_counter)
        max_attempts = len(used_ids) + 100
        attempts = 0
        while candidate in used_ids and attempts < max_attempts:
            candidate = next(self._track_counter)
            attempts += 1
        if attempts >= max_attempts:
            # 强制清理最老的轨迹，释放ID
            self._cleanup_oldest_tracks()
        return candidate

    def _cleanup_oldest_tracks(self) -> None:
        """清理最老的50%轨迹以释放ID"""
        all_tracks = []
        for cam_cat, track_map in self._tracks.items():
            for tid, state in track_map.items():
                all_tracks.append((cam_cat, tid, state.last_seen))
        all_tracks.sort(key=lambda x: x[2])
        cutoff = len(all_tracks) // 2
        for cam_cat, tid, _ in all_tracks[:cutoff]:
            self._tracks[cam_cat].pop(tid, None)

    def reset(self) -> None:
        """
        选择性重置：仅清除超过24小时未更新的追踪轨迹
        """
        from datetime import timedelta
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        cutoff = now - timedelta(hours=24)
        stale_ids = [tid for tid, track in self._tracks.items()
                     if track.get("last_seen", now) < cutoff]
        for tid in stale_ids:
            self._tracks.pop(tid, None)

    def assign_tracks(self, detections: List[Detection], now: Optional[datetime] = None) -> List[Detection]:
        now = now or datetime.now(timezone.utc).replace(tzinfo=None)
        if not detections:
            self._cleanup(now)
            return detections

        # 1) keep externally provided track ids and register them in memory.
        unresolved: List[Detection] = []
        for det in detections:
            if det.track_id is not None:
                self._register_external_track(det, now)
            else:
                unresolved.append(det)

        # 2) assign missing track ids via IoU matching grouped by camera/category.
        grouped: Dict[Tuple[str, str], List[Detection]] = {}
        for det in unresolved:
            grouped.setdefault((det.camera_id, det.category), []).append(det)

        for key, missing_dets in grouped.items():
            track_map = self._tracks.setdefault(key, {})
            track_ids = list(track_map.keys())

            # mark tracks as missed before current matching.
            for track in track_map.values():
                track.missed += 1

            candidates: List[Tuple[float, int, int]] = []
            for det_idx, det in enumerate(missing_dets):
                for tr_idx, trk_id in enumerate(track_ids):
                    iou = self._iou(det.bbox, track_map[trk_id].bbox)
                    if iou >= self.match_thresh:
                        candidates.append((iou, det_idx, tr_idx))

            used_det = set()
            used_track_idx = set()
            for iou, det_idx, tr_idx in sorted(candidates, key=lambda item: item[0], reverse=True):
                if det_idx in used_det or tr_idx in used_track_idx:
                    continue
                used_det.add(det_idx)
                used_track_idx.add(tr_idx)

                det = missing_dets[det_idx]
                trk_id = track_ids[tr_idx]
                det.track_id = trk_id
                state = track_map[trk_id]
                state.bbox = det.bbox
                state.last_seen = now
                state.missed = 0

            for det_idx, det in enumerate(missing_dets):
                if det_idx in used_det:
                    continue
                new_track_id = self._next_track_id()
                det.track_id = new_track_id
                track_map[new_track_id] = _TrackState(
                    track_id=new_track_id,
                    camera_id=det.camera_id,
                    category=det.category,
                    bbox=det.bbox,
                    last_seen=now,
                    missed=0,
                )

        self._cleanup(now)
        return detections

    def runtime_state(self) -> dict:
        camera_summary: Dict[str, int] = {}
        total_tracks = 0
        for (camera_id, _category), track_map in self._tracks.items():
            count = len(track_map)
            total_tracks += count
            camera_summary[camera_id] = camera_summary.get(camera_id, 0) + count

        return {
            "tracker": "iou_greedy_tracker",
            "match_thresh": self.match_thresh,
            "track_buffer": self.track_buffer,
            "frame_rate": self.frame_rate,
            "max_age_seconds": self.max_age_seconds,
            "active_tracks": total_tracks,
            "active_tracks_by_camera": camera_summary,
        }


_tracker = IoUTracker()


def assign_tracks(detections: List[Detection], now: Optional[datetime] = None) -> List[Detection]:
    return _tracker.assign_tracks(detections, now=now)


def tracker_runtime_state() -> dict:
    return _tracker.runtime_state()

def reset() -> None:
    """
    [新增] 提供给 main.py 定时任务调用的重置接口
    """
    _tracker.reset()