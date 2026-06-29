from __future__ import annotations

import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

import cv2

from backend.app.core.config import DATA_DIR
from backend.app.core.utils import safe_segment as _safe_segment_util
from backend.app.core.utils import build_replay_dir as _build_replay_dir_util
from backend.app.core.utils import get_replay_candidate_dirs as _get_candidate_dirs_util


VIDEO_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".flv", ".wmv"}
REPLAY_ROOT = Path(os.getenv("REPLAY_ROOT", str(DATA_DIR / "replay")))
REPLAY_CLIP_ROOT = Path(os.getenv("REPLAY_CLIP_ROOT", str(DATA_DIR / "replay_clips")))
REPLAY_LAYOUT = os.getenv("REPLAY_LAYOUT", "{camera_id}/{date}/{hour}")
DEFAULT_CLIP_BEFORE_SECONDS = 4
DEFAULT_CLIP_AFTER_SECONDS = 4


class ReplayService:
    """Resolve replay videos for an alert timestamp and optionally cut a short clip."""

    @staticmethod
    def ffmpeg_available() -> bool:
        return bool(shutil.which("ffmpeg"))

    @staticmethod
    def _safe_segment(value: str) -> str:
        return _safe_segment_util(value)

    def _build_replay_dir(
        self,
        *,
        camera_id: str,
        event_time: datetime,
        scene_id: str = "",
        rule_id: str = "",
        layout: str,
    ) -> Path:
        return _build_replay_dir_util(
            camera_id=camera_id, event_time=event_time,
            scene_id=scene_id, rule_id=rule_id,
            layout=layout, replay_root=REPLAY_ROOT,
        )

    def get_candidate_dirs(
        self,
        *,
        camera_id: str,
        event_time: datetime,
        scene_id: str = "",
        rule_id: str = "",
    ) -> list[Path]:
        return _get_candidate_dirs_util(
            camera_id=camera_id, event_time=event_time,
            scene_id=scene_id, rule_id=rule_id,
            replay_root=REPLAY_ROOT, replay_layout=REPLAY_LAYOUT,
        )

    def _find_video_files(self, directory: Path) -> list[Path]:
        if not directory.exists() or not directory.is_dir():
            return []
        candidates = []
        for path in directory.iterdir():
            if not path.is_file() or path.suffix.lower() not in VIDEO_EXTENSIONS:
                continue
            try:
                if path.stat().st_size <= 0:
                    continue
            except OSError:
                continue
            candidates.append(path)
        return sorted(candidates)

    def _parse_filename_timestamp(self, path: Path) -> Optional[datetime]:
        stem = path.stem
        patterns = [
            "%Y%m%d_%H%M%S",
            "%Y-%m-%d_%H-%M-%S",
            "%Y%m%d_%H%M",
            "%Y%m%d_%H",
        ]
        for pattern in patterns:
            try:
                return datetime.strptime(stem, pattern)
            except ValueError:
                continue
        return None

    def _find_video_for_timestamp(self, directory: Path, target_time: datetime) -> tuple[Optional[Path], Optional[float]]:
        target_ts = target_time.timestamp()
        best_path: Optional[Path] = None
        best_offset: Optional[float] = None
        best_delta = float("inf")

        for video_path in self._find_video_files(directory):
            file_time = self._parse_filename_timestamp(video_path)
            if file_time is None:
                continue
            offset = target_ts - file_time.timestamp()
            delta = abs(offset)
            if delta < best_delta:
                best_delta = delta
                best_path = video_path
                best_offset = max(0.0, offset)

        if best_path is not None:
            return best_path, best_offset

        for video_path in self._find_video_files(directory):
            try:
                offset = target_ts - video_path.stat().st_mtime
            except OSError:
                continue
            delta = abs(offset)
            if delta < best_delta:
                best_delta = delta
                best_path = video_path
                best_offset = max(0.0, offset)

        return best_path, best_offset

    def _get_video_duration(self, video_path: Path) -> float:
        cap = cv2.VideoCapture(str(video_path))
        try:
            if not cap.isOpened():
                return 0.0
            frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0
            fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
            if fps <= 0:
                return 0.0
            return max(0.0, frame_count / fps)
        finally:
            cap.release()

    def _collect_fallback_videos(self, camera_id: str) -> list[Path]:
        candidates: list[Path] = []
        search_dirs = [
            Path("data") / "uploads" / "videos" / camera_id,
            Path("data") / "replay" / camera_id,
        ]
        for directory in search_dirs:
            if not directory.exists():
                continue
            if directory.is_file() and directory.suffix.lower() in VIDEO_EXTENSIONS:
                candidates.append(directory)
                continue
            for video_path in directory.rglob("*"):
                if video_path.is_file() and video_path.suffix.lower() in VIDEO_EXTENSIONS:
                    candidates.append(video_path)
        return sorted({path.resolve(): path for path in candidates}.values(), key=lambda item: str(item))

    def _pick_best_video(self, videos: list[Path], event_time: datetime) -> tuple[Optional[Path], float]:
        target_ts = event_time.timestamp()
        best_path: Optional[Path] = None
        best_offset = 0.0
        best_delta = float("inf")
        for video_path in videos:
            file_time = self._parse_filename_timestamp(video_path)
            if file_time is not None:
                offset = target_ts - file_time.timestamp()
            else:
                try:
                    offset = target_ts - video_path.stat().st_mtime
                except OSError:
                    continue
            delta = abs(offset)
            if delta < best_delta:
                best_delta = delta
                best_path = video_path
                best_offset = max(0.0, offset)
        return best_path, best_offset

    def _extract_clip(self, source_path: Path, start_time: float, duration: float, output_path: Path) -> bool:
        ffmpeg_path = shutil.which("ffmpeg")
        if not ffmpeg_path:
            return False

        cmd = [
            ffmpeg_path,
            "-ss",
            str(start_time),
            "-i",
            str(source_path),
            "-t",
            str(duration),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-profile:v",
            "baseline",
            "-level",
            "3.0",
            "-c:a",
            "aac",
            "-ac",
            "2",
            "-ar",
            "44100",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            str(output_path),
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, timeout=120, check=False)
        except Exception:
            return False
        return result.returncode == 0 and output_path.exists()

    def _source_origin(self, video_path: Optional[Path]) -> str:
        if video_path is None:
            return "missing"
        path_text = str(video_path).replace("\\", "/").lower()
        if "data/replay/" in path_text:
            return "recorded_replay"
        if "data/uploads/videos/" in path_text:
            return "uploaded_video"
        return "external"

    def _normalize_play_offset(self, play_offset: float, duration: float, source_origin: str) -> tuple[float, str]:
        if duration <= 0:
            return max(0.0, play_offset), "unknown_duration"
        if play_offset <= duration:
            return max(0.0, play_offset), "aligned"
        if source_origin == "recorded_replay":
            return max(0.0, duration - 0.5), "clamped_to_duration"
        return 0.0, "fallback_time_mismatch"

    def _source_quality(self, source_origin: str) -> tuple[str, str]:
        if source_origin == "recorded_replay":
            return "high", "当前命中真实回放录像，适合做事件定位与视频理解。"
        if source_origin == "uploaded_video":
            return "medium", "当前命中上传测试视频，可用于分析，但时间点未必等同于真实事件录像。"
        if source_origin == "legacy_demo_disabled":
            return "low", "当前命中演示兜底视频，时间点和事件内容可能不准确，识别结果仅供演示参考。"
        if source_origin == "external":
            return "medium", "当前命中外部视频源，可分析，但事件定位精度取决于文件命名与时间戳。"
        return "low", "当前没有找到合适的视频源。"

    def get_replay_info(
        self,
        *,
        camera_id: str,
        event_time: datetime,
        replay_dir: Path,
        candidate_dirs: list[Path] | None = None,
    ) -> dict:
        candidate_dirs = candidate_dirs or []
        video_path: Optional[Path] = None
        offset: Optional[float] = None

        search_dirs = [replay_dir, *candidate_dirs]
        seen: set[str] = set()
        for directory in search_dirs:
            key = str(directory.resolve()) if directory.exists() else str(directory)
            if key in seen:
                continue
            seen.add(key)
            picked_path, picked_offset = self._find_video_for_timestamp(directory, event_time)
            if picked_path is not None:
                video_path = picked_path
                offset = picked_offset
                break

        if video_path is None:
            fallback_videos = self._collect_fallback_videos(camera_id)
            video_path, offset = self._pick_best_video(fallback_videos, event_time)

        duration = self._get_video_duration(video_path) if video_path else 0.0
        source_origin = self._source_origin(video_path)
        play_offset, time_alignment = self._normalize_play_offset(float(offset or 0.0), duration, source_origin)
        suggested_start = max(0.0, play_offset - DEFAULT_CLIP_BEFORE_SECONDS)
        default_clip_duration = float(DEFAULT_CLIP_BEFORE_SECONDS + DEFAULT_CLIP_AFTER_SECONDS)
        suggested_duration = default_clip_duration if duration <= 0 else min(default_clip_duration, max(0.0, duration - suggested_start))
        source_quality, source_warning = self._source_quality(source_origin)
        if time_alignment == "fallback_time_mismatch":
            source_warning = f"{source_warning} 当前录像时间与事件时间不匹配，已从视频开头生成回放片段。"
        ffmpeg_ready = self.ffmpeg_available()

        return {
            "camera_id": camera_id,
            "event_time": event_time.isoformat(),
            "display_time": event_time.strftime("%Y-%m-%d %H:%M:%S"),
            "replay_dir": str(replay_dir),
            "replay_dir_exists": replay_dir.exists() and replay_dir.is_dir(),
            "video_found": video_path is not None,
            "video_path": str(video_path) if video_path else None,
            "video_name": video_path.name if video_path else "",
            "video_duration": duration,
            "play_offset": play_offset,
            "suggested_start": suggested_start,
            "suggested_duration": suggested_duration,
            "source_origin": source_origin,
            "source_quality": source_quality,
            "source_warning": source_warning,
            "time_alignment": time_alignment,
            "clip_generation_supported": ffmpeg_ready,
            "clip_generation_message": "ffmpeg 已就绪，可裁剪事件短片段。" if ffmpeg_ready else "未检测到 ffmpeg，无法裁剪片段，但仍可直接分析原始回放视频。",
        }

    def get_replay_info_for_event(
        self,
        *,
        camera_id: str,
        event_time: datetime,
        scene_id: str = "",
        rule_id: str = "",
    ) -> dict:
        candidate_dirs = self.get_candidate_dirs(
            camera_id=camera_id,
            event_time=event_time,
            scene_id=scene_id,
            rule_id=rule_id,
        )
        replay_dir = candidate_dirs[0] if candidate_dirs else REPLAY_ROOT
        for candidate in candidate_dirs:
            if candidate.exists() and candidate.is_dir():
                replay_dir = candidate
                break

        info = self.get_replay_info(
            camera_id=camera_id,
            event_time=event_time,
            replay_dir=replay_dir,
            candidate_dirs=candidate_dirs,
        )
        info["candidate_dirs"] = [str(path) for path in candidate_dirs]
        info["replay_layout"] = REPLAY_LAYOUT
        return info

    def generate_clip(
        self,
        *,
        camera_id: str,
        event_time: datetime,
        replay_dir: Path,
        before_seconds: int = DEFAULT_CLIP_BEFORE_SECONDS,
        after_seconds: int = DEFAULT_CLIP_AFTER_SECONDS,
        candidate_dirs: list[Path] | None = None,
    ) -> Optional[str]:
        replay_info = self.get_replay_info(
            camera_id=camera_id,
            event_time=event_time,
            replay_dir=replay_dir,
            candidate_dirs=candidate_dirs,
        )
        if not replay_info.get("video_found") or not replay_info.get("video_path"):
            return None

        video_path = Path(str(replay_info["video_path"]))
        play_offset = float(replay_info.get("play_offset") or 0.0)
        video_duration = float(replay_info.get("video_duration") or 0.0)
        start_time = max(0.0, play_offset - before_seconds)
        clip_duration = float(before_seconds + after_seconds)
        if video_duration > 0:
            clip_duration = min(clip_duration, max(0.0, video_duration - start_time))
        if clip_duration <= 0:
            return None

        output_dir = REPLAY_CLIP_ROOT / self._safe_segment(camera_id) / event_time.strftime("%Y-%m-%d")
        output_dir.mkdir(parents=True, exist_ok=True)
        output_name = f"replay_{self._safe_segment(camera_id)}_{event_time.strftime('%Y%m%d_%H%M%S')}_{datetime.now().strftime('%H%M%S')}.mp4"
        output_path = output_dir / output_name

        if self._extract_clip(video_path, start_time, clip_duration, output_path):
            return str(output_path)
        return None

    def generate_clip_for_event(
        self,
        *,
        camera_id: str,
        event_time: datetime,
        scene_id: str = "",
        rule_id: str = "",
        before_seconds: int = DEFAULT_CLIP_BEFORE_SECONDS,
        after_seconds: int = DEFAULT_CLIP_AFTER_SECONDS,
    ) -> tuple[Optional[str], dict]:
        info = self.get_replay_info_for_event(
            camera_id=camera_id,
            event_time=event_time,
            scene_id=scene_id,
            rule_id=rule_id,
        )
        replay_dir = Path(str(info.get("replay_dir") or REPLAY_ROOT))
        candidate_dirs = [Path(path) for path in info.get("candidate_dirs") or []]
        clip_path = self.generate_clip(
            camera_id=camera_id,
            event_time=event_time,
            replay_dir=replay_dir,
            before_seconds=before_seconds,
            after_seconds=after_seconds,
            candidate_dirs=candidate_dirs,
        )
        return clip_path, info


replay_service = ReplayService()
