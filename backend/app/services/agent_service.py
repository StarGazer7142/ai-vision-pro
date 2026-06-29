from __future__ import annotations

from typing import Optional

from backend.app.services.agent_orchestrator import orchestrator


def status() -> dict:
    return orchestrator.status()


def chat(
    *,
    query: str,
    scene_id: Optional[str] = None,
    camera_id: Optional[str] = None,
    limit: int = 20,
) -> dict:
    return orchestrator.chat(
        query=query,
        scene_id=scene_id,
        camera_id=camera_id,
        limit=limit,
    )
