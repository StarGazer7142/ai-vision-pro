"""共享工具函数模块 — 消除跨文件重复代码"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List, Tuple

from backend.app.core.config import PROJECT_ROOT

try:
    from dotenv import dotenv_values
except Exception:
    dotenv_values = None


# ===== 1. .env 文件加载 =====

ENV_FILE_NAMES = (".env", ".env.local")


def load_local_env_values() -> Tuple[Dict[str, str], List[str]]:
    """加载项目根目录下的 .env / .env.local 文件，返回 (键值对字典, 已加载文件列表)"""
    if dotenv_values is None:
        return {}, []

    values: Dict[str, str] = {}
    loaded_files: List[str] = []

    for file_name in ENV_FILE_NAMES:
        env_path = PROJECT_ROOT / file_name
        if not env_path.exists():
            continue
        try:
            parsed = dotenv_values(env_path)
        except Exception:
            continue
        for key, raw_value in parsed.items():
            if key is None or raw_value is None:
                continue
            text = str(raw_value).strip()
            if text:
                values[str(key)] = text
        loaded_files.append(str(env_path))

    return values, loaded_files


def read_config_value(
    local_env_values: Dict[str, str],
    keys: List[str],
    default: str = "",
) -> Tuple[str, str]:
    """按优先级从 local_env -> os.getenv 读取配置值，返回 (值, 来源标识)"""
    for key in keys:
        value = (local_env_values.get(key) or "").strip()
        if value:
            return value, f"local_env:{key}"
    for key in keys:
        value = os.getenv(key, "").strip()
        if value:
            return value, f"os_env:{key}"
    return default, "default"


# ===== 2. 路径安全化 =====

def safe_segment(value: str) -> str:
    """将字符串安全化为文件路径段（去除 .. / \\ 等危险字符）"""
    text = str(value or "").strip().replace("\\", "_").replace("/", "_")
    return text.replace("..", "_")


# ===== 3. 回放目录构建 =====

def build_replay_dir(
    *,
    camera_id: str,
    event_time,
    scene_id: str = "",
    rule_id: str = "",
    layout: str,
    replay_root: Path,
) -> Path:
    """根据参数构建回放目录路径"""
    tokens = {
        "camera_id": safe_segment(camera_id),
        "scene_id": safe_segment(scene_id),
        "rule_id": safe_segment(rule_id),
        "date": event_time.strftime("%Y-%m-%d"),
        "date_compact": event_time.strftime("%Y%m%d"),
        "hour": event_time.strftime("%H"),
        "minute": event_time.strftime("%M"),
        "timestamp_compact": event_time.strftime("%Y%m%d_%H%M%S"),
    }
    relative = layout.format(**tokens).strip().lstrip("/\\")
    return (replay_root / relative).resolve()


def get_replay_candidate_dirs(
    *,
    camera_id: str,
    event_time: datetime,
    scene_id: str = "",
    rule_id: str = "",
    replay_root: Path,
    replay_layout: str,
) -> list[Path]:
    """搜索多个可能的回放目录，返回候选列表"""
    default_layouts = [
        replay_layout,
        "{camera_id}/{date}/{hour}",
        "{camera_id}/{date}",
        "{camera_id}",
    ]
    candidates: list[Path] = []
    seen: set[str] = set()
    for layout in default_layouts:
        try:
            path = build_replay_dir(
                camera_id=camera_id,
                event_time=event_time,
                scene_id=scene_id,
                rule_id=rule_id,
                layout=layout,
                replay_root=replay_root,
            )
        except Exception:
            continue
        key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        candidates.append(path)
    return candidates
