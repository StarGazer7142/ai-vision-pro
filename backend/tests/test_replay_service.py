from pathlib import Path
import sys
import unittest

sys.path.append(str(Path(__file__).resolve().parents[2]))

from backend.app.services.replay_service import replay_service


class ReplayServiceTestCase(unittest.TestCase):
    def test_acceptance_demo_video_is_no_longer_special_fallback(self):
        origin = replay_service._source_origin(Path("D:/Project/data/acceptance_demo/demo_cam_fence_xxx.mp4"))
        quality, warning = replay_service._source_quality(origin)
        self.assertEqual(origin, "external")
        self.assertEqual(quality, "medium")
        self.assertIn("外部视频源", warning)

    def test_relative_demo_path_is_treated_as_external_source(self):
        origin = replay_service._source_origin(Path("data/acceptance_demo/demo_cam_fence_xxx.mp4"))
        self.assertEqual(origin, "external")

        play_offset, alignment = replay_service._normalize_play_offset(47662.79, 19.96, origin)
        self.assertEqual(play_offset, 0.0)
        self.assertEqual(alignment, "fallback_time_mismatch")


if __name__ == "__main__":
    unittest.main()
