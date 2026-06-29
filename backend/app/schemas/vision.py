from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from backend.app.schemas.detection import BBox, Detection


class VisionRuleEvent(BaseModel):
    rule_id: str = Field(..., description="Rule id mapped to config/rules.yaml")
    active: bool = Field(default=True, description="Whether the event is currently active")
    count: int = Field(default=1, ge=0, description="Active target count for the rule")
    message: str = Field(default="", description="User-facing alert message")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    category: str = Field(default="person")
    track_id: Optional[int] = Field(default=None, description="Single active track id when available")
    track_ids: List[int] = Field(default_factory=list, description="Optional list of active track ids")
    bbox: Optional[BBox] = Field(default=None, description="Optional primary bbox for overlay rendering")


@dataclass
class VisionFrameAnalysis:
    backend_key: str
    pipeline: str
    detections: List[Detection] = field(default_factory=list)
    overlay_detections: List[Detection] = field(default_factory=list)
    direct_events: List[VisionRuleEvent] = field(default_factory=list)
    summary: str = ""
    error: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
