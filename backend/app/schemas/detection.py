from datetime import datetime, timezone
from typing import List, Optional

from pydantic import BaseModel, Field


class BBox(BaseModel):
    x1: float = Field(..., description="左上角 x")
    y1: float = Field(..., description="左上角 y")
    x2: float = Field(..., description="右下角 x")
    y2: float = Field(..., description="右下角 y")


class Detection(BaseModel):
    camera_id: str
    category: str = Field("person", description="用于规则判断的标准类别名")
    display_category: Optional[str] = Field(None, description="用于前端展示的原始类别名")
    confidence: float = Field(0.0, ge=0, le=1)
    bbox: BBox
    track_id: Optional[int] = Field(None, description="跟踪 ID，若为空则由后端分配")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None))


class DetectionFrame(BaseModel):
    frame_id: str
    camera_id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    width: Optional[int] = Field(default=None, description="原始帧宽度（像素）")
    height: Optional[int] = Field(default=None, description="原始帧高度（像素）")
    detections: List[Detection] = Field(default_factory=list)
