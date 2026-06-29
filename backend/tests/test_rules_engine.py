import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

sys.path.append(str(Path(__file__).resolve().parents[2]))

from backend.app.schemas.detection import DetectionFrame
from backend.app.schemas.vision import VisionRuleEvent
from backend.app.services.rules_engine import RulesEngine


class RulesEngineTestCase(unittest.TestCase):
    def _write_temp_rules(
        self,
        *,
        boundary_confirm_frames: int = 1,
        boundary_direction: str = "any",
        dwell_confirm_frames: int = 1,
        dwell_threshold_seconds: int = 1,
        dwell_cooldown_seconds: int = 1,
        boundary_polygon: list[list[float]] | None = None,
    ) -> Path:
        cfg = {
            "scenes": [
                {
                    "id": "s1",
                    "name": "测试场景",
                    "description": "测试",
                    "cameras": ["cam_test"],
                    "rule_ids": ["fence_intrusion", "fence_dwell"],
                    "entry_page": "module.html?scene=s1",
                }
            ],
            "cameras": [
                {
                    "id": "cam_test",
                    "name": "测试摄像头",
                    "stream": "camera://0",
                    "rois": [
                        {
                            "id": "line1",
                            "type": "boundary_line",
                            "line": [] if boundary_polygon else [[0.1, 0.5], [0.9, 0.5]],
                            "polygon": boundary_polygon or [],
                        }
                    ],
                    "dwell_zones": [
                        {
                            "id": "z1",
                            "label": "区域",
                            "polygon": [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]],
                            "threshold_seconds": dwell_threshold_seconds,
                        }
                    ],
                }
            ],
            "rules": [
                {
                    "id": "fence_intrusion",
                    "type": "boundary",
                    "camera_id": "cam_test",
                    "roi_id": "line1",
                    "severity": "high",
                    "category_filter": ["person"],
                    "confirm_frames": boundary_confirm_frames,
                    "crossing_direction": boundary_direction,
                    "cooldown_seconds": 1,
                    "signal_key": "is_boundary",
                    "count_key": "boundary_count",
                    "signal_cn": "是否越界",
                    "count_cn": "越界人数",
                    "alert_label": "翻越围栏",
                },
                {
                    "id": "fence_dwell",
                    "type": "dwell",
                    "camera_id": "cam_test",
                    "zone_id": "z1",
                    "threshold_seconds": dwell_threshold_seconds,
                    "confirm_frames": dwell_confirm_frames,
                    "severity": "medium",
                    "category_filter": ["person"],
                    "signal_key": "is_dwell",
                    "count_key": "dwell_count",
                    "signal_cn": "是否滞留",
                    "count_cn": "滞留人数",
                    "cooldown_seconds": dwell_cooldown_seconds,
                    "alert_label": "人员滞留",
                }
            ],
            "signal_defaults": [{"key": "是否火灾", "value": 0}],
        }
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".yaml")
        path = Path(tmp.name)
        tmp.close()
        with path.open("w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)
        return path

    def test_dwell_rule_and_scene_signal(self):
        rule_path = self._write_temp_rules()
        engine = RulesEngine(rule_path=rule_path)

        t0 = datetime.now(timezone.utc).replace(tzinfo=None)
        frame1 = DetectionFrame(
            frame_id="f1",
            camera_id="cam_test",
            timestamp=t0,
            detections=[
                {
                    "camera_id": "cam_test",
                    "category": "person",
                    "confidence": 0.9,
                    "bbox": {"x1": 0.2, "y1": 0.2, "x2": 0.3, "y2": 0.4},
                    "track_id": 1,
                    "timestamp": t0.isoformat(),
                }
            ],
        )
        alerts1 = engine.evaluate_frame(frame1)
        self.assertEqual(len(alerts1), 0)

        frame2 = DetectionFrame(
            frame_id="f2",
            camera_id="cam_test",
            timestamp=t0 + timedelta(seconds=2),
            detections=[
                {
                    "camera_id": "cam_test",
                    "category": "person",
                    "confidence": 0.91,
                    "bbox": {"x1": 0.21, "y1": 0.2, "x2": 0.31, "y2": 0.41},
                    "track_id": 1,
                    "timestamp": (t0 + timedelta(seconds=2)).isoformat(),
                }
            ],
        )
        alerts2 = engine.evaluate_frame(frame2)
        self.assertGreaterEqual(len(alerts2), 1)
        self.assertTrue(any(item.get("message") == "人员滞留 2s" for item in alerts2))

        signal = engine.get_scene_signals("s1")
        self.assertEqual(signal["signals_cn"]["是否滞留"], 1)
        self.assertEqual(signal["signals_cn"]["滞留人数"], 1)

    def test_boundary_line_cross_triggers_alert(self):
        rule_path = self._write_temp_rules()
        engine = RulesEngine(rule_path=rule_path)

        t0 = datetime.now(timezone.utc).replace(tzinfo=None)
        frame1 = DetectionFrame(
            frame_id="b1",
            camera_id="cam_test",
            timestamp=t0,
            detections=[
                {
                    "camera_id": "cam_test",
                    "category": "person",
                    "confidence": 0.92,
                    "bbox": {"x1": 0.45, "y1": 0.62, "x2": 0.55, "y2": 0.82},
                    "track_id": 10,
                    "timestamp": t0.isoformat(),
                }
            ],
        )
        alerts1 = engine.evaluate_frame(frame1)
        self.assertEqual(len(alerts1), 0)

        frame2 = DetectionFrame(
            frame_id="b2",
            camera_id="cam_test",
            timestamp=t0 + timedelta(seconds=1),
            detections=[
                {
                    "camera_id": "cam_test",
                    "category": "person",
                    "confidence": 0.93,
                    "bbox": {"x1": 0.45, "y1": 0.18, "x2": 0.55, "y2": 0.38},
                    "track_id": 10,
                    "timestamp": (t0 + timedelta(seconds=1)).isoformat(),
                }
            ],
        )
        alerts2 = engine.evaluate_frame(frame2)
        self.assertTrue(any(item.get("rule_id") == "fence_intrusion" for item in alerts2))
        self.assertTrue(any(item.get("message") == "翻越围栏" for item in alerts2))

    def test_boundary_direction_and_confirm_frames(self):
        rule_path = self._write_temp_rules(
            boundary_confirm_frames=2,
            boundary_direction="neg_to_pos",
            dwell_confirm_frames=99,
        )
        engine = RulesEngine(rule_path=rule_path)

        t0 = datetime.now(timezone.utc).replace(tzinfo=None)
        frame1 = DetectionFrame(
            frame_id="d1",
            camera_id="cam_test",
            timestamp=t0,
            detections=[
                {
                    "camera_id": "cam_test",
                    "category": "person",
                    "confidence": 0.95,
                    "bbox": {"x1": 0.45, "y1": 0.18, "x2": 0.55, "y2": 0.38},
                    "track_id": 20,
                    "timestamp": t0.isoformat(),
                }
            ],
        )
        self.assertEqual(len(engine.evaluate_frame(frame1)), 0)

        frame2 = DetectionFrame(
            frame_id="d2",
            camera_id="cam_test",
            timestamp=t0 + timedelta(seconds=1),
            detections=[
                {
                    "camera_id": "cam_test",
                    "category": "person",
                    "confidence": 0.95,
                    "bbox": {"x1": 0.45, "y1": 0.62, "x2": 0.55, "y2": 0.82},
                    "track_id": 20,
                    "timestamp": (t0 + timedelta(seconds=1)).isoformat(),
                }
            ],
        )
        self.assertEqual(len(engine.evaluate_frame(frame2)), 0)

        frame3 = DetectionFrame(
            frame_id="d3",
            camera_id="cam_test",
            timestamp=t0 + timedelta(seconds=2),
            detections=[
                {
                    "camera_id": "cam_test",
                    "category": "person",
                    "confidence": 0.95,
                    "bbox": {"x1": 0.45, "y1": 0.64, "x2": 0.55, "y2": 0.84},
                    "track_id": 20,
                    "timestamp": (t0 + timedelta(seconds=2)).isoformat(),
                }
            ],
        )
        alerts3 = engine.evaluate_frame(frame3)
        self.assertTrue(any(item.get("rule_id") == "fence_intrusion" for item in alerts3))

    def test_boundary_polygon_only_triggers_on_entry(self):
        rule_path = self._write_temp_rules(
            boundary_confirm_frames=1,
            dwell_confirm_frames=99,
            boundary_polygon=[[0.35, 0.35], [0.65, 0.35], [0.65, 0.65], [0.35, 0.65]],
        )
        engine = RulesEngine(rule_path=rule_path)

        t0 = datetime.now(timezone.utc).replace(tzinfo=None)
        frame1 = DetectionFrame(
            frame_id="p1",
            camera_id="cam_test",
            timestamp=t0,
            detections=[
                {
                    "camera_id": "cam_test",
                    "category": "person",
                    "confidence": 0.95,
                    "bbox": {"x1": 0.10, "y1": 0.10, "x2": 0.20, "y2": 0.20},
                    "track_id": 21,
                    "timestamp": t0.isoformat(),
                }
            ],
        )
        self.assertEqual(len(engine.evaluate_frame(frame1)), 0)

        frame2 = DetectionFrame(
            frame_id="p2",
            camera_id="cam_test",
            timestamp=t0 + timedelta(seconds=1),
            detections=[
                {
                    "camera_id": "cam_test",
                    "category": "person",
                    "confidence": 0.95,
                    "bbox": {"x1": 0.42, "y1": 0.42, "x2": 0.52, "y2": 0.52},
                    "track_id": 21,
                    "timestamp": (t0 + timedelta(seconds=1)).isoformat(),
                }
            ],
        )
        alerts2 = engine.evaluate_frame(frame2)
        self.assertTrue(any(item.get("rule_id") == "fence_intrusion" for item in alerts2))

        frame3 = DetectionFrame(
            frame_id="p3",
            camera_id="cam_test",
            timestamp=t0 + timedelta(seconds=2),
            detections=[
                {
                    "camera_id": "cam_test",
                    "category": "person",
                    "confidence": 0.95,
                    "bbox": {"x1": 0.44, "y1": 0.44, "x2": 0.54, "y2": 0.54},
                    "track_id": 21,
                    "timestamp": (t0 + timedelta(seconds=2)).isoformat(),
                }
            ],
        )
        self.assertEqual(len(engine.evaluate_frame(frame3)), 0)

    def test_boundary_line_triggers_when_bbox_cuts_line_without_center_crossing(self):
        rule_path = self._write_temp_rules(
            boundary_confirm_frames=1,
            dwell_confirm_frames=99,
        )
        engine = RulesEngine(rule_path=rule_path)

        t0 = datetime.now(timezone.utc).replace(tzinfo=None)
        frame1 = DetectionFrame(
            frame_id="cut1",
            camera_id="cam_test",
            timestamp=t0,
            detections=[
                {
                    "camera_id": "cam_test",
                    "category": "person",
                    "confidence": 0.95,
                    "bbox": {"x1": 0.42, "y1": 0.34, "x2": 0.52, "y2": 0.46},
                    "track_id": 60,
                    "timestamp": t0.isoformat(),
                }
            ],
        )
        self.assertEqual(len(engine.evaluate_frame(frame1)), 0)

        frame2 = DetectionFrame(
            frame_id="cut2",
            camera_id="cam_test",
            timestamp=t0 + timedelta(seconds=1),
            detections=[
                {
                    "camera_id": "cam_test",
                    "category": "person",
                    "confidence": 0.95,
                    "bbox": {"x1": 0.42, "y1": 0.44, "x2": 0.52, "y2": 0.56},
                    "track_id": 60,
                    "timestamp": (t0 + timedelta(seconds=1)).isoformat(),
                }
            ],
        )
        alerts2 = engine.evaluate_frame(frame2)
        self.assertTrue(any(item.get("message") == "翻越围栏" for item in alerts2))
        self.assertEqual(engine.get_scene_signals("s1")["signals_cn"]["是否越界"], 1)

    def test_dwell_confirm_frames(self):
        rule_path = self._write_temp_rules(dwell_confirm_frames=2)
        engine = RulesEngine(rule_path=rule_path)

        t0 = datetime.now(timezone.utc).replace(tzinfo=None)
        frame1 = DetectionFrame(
            frame_id="w1",
            camera_id="cam_test",
            timestamp=t0,
            detections=[
                {
                    "camera_id": "cam_test",
                    "category": "person",
                    "confidence": 0.9,
                    "bbox": {"x1": 0.2, "y1": 0.2, "x2": 0.3, "y2": 0.4},
                    "track_id": 30,
                    "timestamp": t0.isoformat(),
                }
            ],
        )
        self.assertEqual(len(engine.evaluate_frame(frame1)), 0)

        frame2 = DetectionFrame(
            frame_id="w2",
            camera_id="cam_test",
            timestamp=t0 + timedelta(seconds=2),
            detections=[
                {
                    "camera_id": "cam_test",
                    "category": "person",
                    "confidence": 0.9,
                    "bbox": {"x1": 0.2, "y1": 0.2, "x2": 0.3, "y2": 0.4},
                    "track_id": 30,
                    "timestamp": (t0 + timedelta(seconds=2)).isoformat(),
                }
            ],
        )
        self.assertEqual(len(engine.evaluate_frame(frame2)), 0)

        frame3 = DetectionFrame(
            frame_id="w3",
            camera_id="cam_test",
            timestamp=t0 + timedelta(seconds=3),
            detections=[
                {
                    "camera_id": "cam_test",
                    "category": "person",
                    "confidence": 0.9,
                    "bbox": {"x1": 0.2, "y1": 0.2, "x2": 0.3, "y2": 0.4},
                    "track_id": 30,
                    "timestamp": (t0 + timedelta(seconds=3)).isoformat(),
                }
            ],
        )
        alerts3 = engine.evaluate_frame(frame3)
        self.assertTrue(any(item.get("rule_id") == "fence_dwell" for item in alerts3))

    def test_dwell_active_track_stays_alarming_after_threshold(self):
        rule_path = self._write_temp_rules(
            dwell_confirm_frames=1,
            dwell_threshold_seconds=1,
            dwell_cooldown_seconds=99,
        )
        engine = RulesEngine(rule_path=rule_path)

        t0 = datetime.now(timezone.utc).replace(tzinfo=None)
        frame1 = DetectionFrame(
            frame_id="a1",
            camera_id="cam_test",
            timestamp=t0,
            detections=[
                {
                    "camera_id": "cam_test",
                    "category": "person",
                    "confidence": 0.9,
                    "bbox": {"x1": 0.2, "y1": 0.2, "x2": 0.3, "y2": 0.4},
                    "track_id": 40,
                    "timestamp": t0.isoformat(),
                }
            ],
        )
        self.assertEqual(len(engine.evaluate_frame(frame1)), 0)

        frame2 = DetectionFrame(
            frame_id="a2",
            camera_id="cam_test",
            timestamp=t0 + timedelta(seconds=2),
            detections=[
                {
                    "camera_id": "cam_test",
                    "category": "person",
                    "confidence": 0.9,
                    "bbox": {"x1": 0.2, "y1": 0.2, "x2": 0.3, "y2": 0.4},
                    "track_id": 40,
                    "timestamp": (t0 + timedelta(seconds=2)).isoformat(),
                }
            ],
        )
        self.assertTrue(any(item.get("rule_id") == "fence_dwell" for item in engine.evaluate_frame(frame2)))
        self.assertIn(40, engine.get_alarming_track_ids("cam_test"))

        frame3 = DetectionFrame(
            frame_id="a3",
            camera_id="cam_test",
            timestamp=t0 + timedelta(seconds=6),
            detections=[
                {
                    "camera_id": "cam_test",
                    "category": "person",
                    "confidence": 0.9,
                    "bbox": {"x1": 0.2, "y1": 0.2, "x2": 0.3, "y2": 0.4},
                    "track_id": 40,
                    "timestamp": (t0 + timedelta(seconds=6)).isoformat(),
                }
            ],
        )
        self.assertEqual(len(engine.evaluate_frame(frame3)), 0)
        self.assertIn(40, engine.get_alarming_track_ids("cam_test"))

    def test_dwell_signal_is_latched_briefly_after_recent_alert(self):
        rule_path = self._write_temp_rules(
            dwell_confirm_frames=1,
            dwell_threshold_seconds=1,
            dwell_cooldown_seconds=5,
        )
        engine = RulesEngine(rule_path=rule_path)

        t0 = datetime.now(timezone.utc).replace(tzinfo=None)
        frame1 = DetectionFrame(
            frame_id="l1",
            camera_id="cam_test",
            timestamp=t0,
            detections=[
                {
                    "camera_id": "cam_test",
                    "category": "person",
                    "confidence": 0.9,
                    "bbox": {"x1": 0.2, "y1": 0.2, "x2": 0.3, "y2": 0.4},
                    "track_id": 50,
                    "timestamp": t0.isoformat(),
                }
            ],
        )
        self.assertEqual(len(engine.evaluate_frame(frame1)), 0)

        frame2 = DetectionFrame(
            frame_id="l2",
            camera_id="cam_test",
            timestamp=t0 + timedelta(seconds=2),
            detections=[
                {
                    "camera_id": "cam_test",
                    "category": "person",
                    "confidence": 0.9,
                    "bbox": {"x1": 0.2, "y1": 0.2, "x2": 0.3, "y2": 0.4},
                    "track_id": 50,
                    "timestamp": (t0 + timedelta(seconds=2)).isoformat(),
                }
            ],
        )
        alerts2 = engine.evaluate_frame(frame2)
        self.assertTrue(any(item.get("rule_id") == "fence_dwell" for item in alerts2))
        self.assertEqual(engine.get_scene_signals("s1")["signals_cn"]["是否滞留"], 1)

        frame3 = DetectionFrame(
            frame_id="l3",
            camera_id="cam_test",
            timestamp=t0 + timedelta(seconds=4),
            detections=[],
        )
        engine.evaluate_frame(frame3)
        self.assertEqual(engine.get_scene_signals("s1")["signals_cn"]["是否滞留"], 1)
        self.assertEqual(engine.get_scene_signals("s1")["signals_cn"]["滞留人数"], 1)

        frame4 = DetectionFrame(
            frame_id="l4",
            camera_id="cam_test",
            timestamp=t0 + timedelta(seconds=8),
            detections=[],
        )
        engine.evaluate_frame(frame4)
        self.assertEqual(engine.get_scene_signals("s1")["signals_cn"]["是否滞留"], 0)

    def test_apply_rule_events_updates_scene_signal(self):
        rule_path = self._write_temp_rules(
            boundary_confirm_frames=1,
            dwell_confirm_frames=1,
            dwell_threshold_seconds=5,
        )
        engine = RulesEngine(rule_path=rule_path)
        now = datetime.now(timezone.utc).replace(tzinfo=None)

        alerts = engine.apply_rule_events(
            camera_id="cam_test",
            timestamp=now,
            events=[
                VisionRuleEvent(
                    rule_id="fence_intrusion",
                    active=True,
                    count=1,
                    message="翻越围栏",
                    confidence=0.95,
                    track_id=501,
                )
            ],
        )

        self.assertTrue(any(item.get("message") == "翻越围栏" for item in alerts))
        signal = engine.get_scene_signals("s1")
        self.assertEqual(signal["signals_cn"]["是否越界"], 1)
        self.assertEqual(signal["signals_cn"]["越界人数"], 1)


if __name__ == "__main__":
    unittest.main()
