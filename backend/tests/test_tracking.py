import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

from backend.app.schemas.detection import Detection
from backend.app.services.tracking_service import IoUTracker


class IoUTrackerTestCase(unittest.TestCase):
    def test_track_id_is_stable_for_close_boxes(self):
        tracker = IoUTracker()
        now = datetime.now(timezone.utc).replace(tzinfo=None)

        frame1 = [
            Detection(
                camera_id="cam_test",
                category="person",
                confidence=0.9,
                bbox={"x1": 0.10, "y1": 0.10, "x2": 0.22, "y2": 0.30},
                timestamp=now,
            )
        ]
        tracked1 = tracker.assign_tracks(frame1, now=now)
        first_id = tracked1[0].track_id

        frame2 = [
            Detection(
                camera_id="cam_test",
                category="person",
                confidence=0.88,
                bbox={"x1": 0.11, "y1": 0.11, "x2": 0.23, "y2": 0.31},
                timestamp=now + timedelta(milliseconds=120),
            )
        ]
        tracked2 = tracker.assign_tracks(frame2, now=now + timedelta(milliseconds=120))
        second_id = tracked2[0].track_id

        self.assertIsNotNone(first_id)
        self.assertEqual(first_id, second_id)

    def test_new_track_created_when_iou_is_low(self):
        tracker = IoUTracker()
        now = datetime.now(timezone.utc).replace(tzinfo=None)

        frame1 = [
            Detection(
                camera_id="cam_test",
                category="person",
                confidence=0.9,
                bbox={"x1": 0.05, "y1": 0.05, "x2": 0.15, "y2": 0.20},
                timestamp=now,
            )
        ]
        id1 = tracker.assign_tracks(frame1, now=now)[0].track_id

        frame2 = [
            Detection(
                camera_id="cam_test",
                category="person",
                confidence=0.9,
                bbox={"x1": 0.70, "y1": 0.60, "x2": 0.85, "y2": 0.90},
                timestamp=now + timedelta(milliseconds=100),
            )
        ]
        id2 = tracker.assign_tracks(frame2, now=now + timedelta(milliseconds=100))[0].track_id

        self.assertNotEqual(id1, id2)


if __name__ == "__main__":
    unittest.main()
