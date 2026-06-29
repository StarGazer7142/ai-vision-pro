from __future__ import annotations

from typing import Iterable, List


READ_ONLY_TOOLS = {
    "get_runtime_status",
    "get_alert_summary",
    "get_replay_hint",
    "analyze_replay_video",
}


def is_tool_allowed(tool_name: str) -> bool:
    return tool_name in READ_ONLY_TOOLS


def filter_allowed_tools(tool_names: Iterable[str]) -> List[str]:
    return [name for name in tool_names if is_tool_allowed(name)]
