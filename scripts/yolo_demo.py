"""
Offline YOLO demo for quick image/video verification.

Example:
    python scripts/yolo_demo.py --source data/sample.jpg
"""

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.services.yolo_service import PRIMARY_PREVIEW_SELECTION, YoloService


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=str, required=True, help="Image/video/RTSP/HTTP source")
    parser.add_argument("--weights", type=str, default=None, help="Optional weights path")
    parser.add_argument(
        "--classes",
        type=str,
        default=",".join(PRIMARY_PREVIEW_SELECTION),
        help="Target classes. Supports names or groups: person, vehicle, animal.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    service = YoloService(Path(args.weights) if args.weights else None)
    class_names = [item.strip() for item in args.classes.split(",") if item.strip()]
    class_ids = service.resolve_class_ids(class_names)
    detections = service.detect(args.source, classes=class_ids or None)
    print(f"detection done, total={len(detections)}")
    for det in detections[:10]:
        print(det.json())


if __name__ == "__main__":
    main()
