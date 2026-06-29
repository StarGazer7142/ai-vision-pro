from pathlib import Path
import sys
import tempfile
import unittest

import yaml

sys.path.append(str(Path(__file__).resolve().parents[2]))

from backend.app.services.vision_backend_service import VisionBackendManager


class VisionBackendManagerTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".yaml")
        self.tmp.close()
        self.path = Path(self.tmp.name)
        with self.path.open("w", encoding="utf-8") as f:
            yaml.safe_dump(
                {
                    "default_backend": "yolo",
                    "active_backend": "yolo",
                    "scene_overrides": {},
                    "camera_overrides": {},
                    "backends": {
                        "yolo": {"label": "方案一：YOLO目标检测"},
                        "video_understanding": {
                            "label": "方案二：视频理解模型",
                            "provider_mode": "mock_local",
                            "sample_stride": 8,
                        },
                    },
                },
                f,
                allow_unicode=True,
                sort_keys=False,
            )

    def tearDown(self):
        if self.path.exists():
            self.path.unlink()

    def test_activate_backend_updates_config_file(self):
        manager = VisionBackendManager(config_path=self.path)
        self.assertEqual(manager.status()["active_backend"], "yolo")

        result = manager.activate("video_understanding")
        self.assertEqual(result["active_backend"], "video_understanding")
        self.assertEqual(result["available_backends"]["video_understanding"]["provider_mode"], "mock_local")

        with self.path.open("r", encoding="utf-8") as f:
            stored = yaml.safe_load(f)
        self.assertEqual(stored["active_backend"], "video_understanding")

    def test_invalid_backend_raises(self):
        manager = VisionBackendManager(config_path=self.path)
        with self.assertRaises(ValueError):
            manager.activate("unknown")

    def test_resolve_precedence_camera_over_scene_over_default(self):
        with self.path.open("w", encoding="utf-8") as f:
            yaml.safe_dump(
                {
                    "default_backend": "yolo",
                    "active_backend": "yolo",
                    "scene_overrides": {"warehouse_dock": "video_understanding"},
                    "camera_overrides": {"cam_dock": "yolo"},
                    "backends": {
                        "yolo": {"label": "方案一：YOLO目标检测"},
                        "video_understanding": {"label": "方案二：视频理解模型", "provider_mode": "mock_local"},
                    },
                },
                f,
                allow_unicode=True,
                sort_keys=False,
            )

        manager = VisionBackendManager(config_path=self.path)
        self.assertEqual(manager.resolve_backend_key(scene_id="warehouse_dock"), "video_understanding")
        self.assertEqual(manager.resolve_backend_key(scene_id="warehouse_dock", camera_id="cam_dock"), "yolo")
        self.assertEqual(manager.resolve_backend_key(camera_id="cam_unknown"), "yolo")

    def test_update_config_persists_scope_and_video_understanding_fields(self):
        manager = VisionBackendManager(config_path=self.path)
        result = manager.update_config(
            default_backend="video_understanding",
            scene_overrides={"campus_fence": "yolo"},
            camera_overrides={"cam_warehouse": "video_understanding"},
            video_understanding={
                "provider_mode": "api",
                "api_url": "https://example.com/video",
                "model": "doubao-video-understanding",
                "timeout_seconds": 18,
                "sample_stride": 6,
            },
        )

        self.assertEqual(result["default_backend"], "video_understanding")
        self.assertEqual(result["scene_overrides"]["campus_fence"], "yolo")
        self.assertEqual(result["camera_overrides"]["cam_warehouse"], "video_understanding")
        self.assertEqual(result["available_backends"]["video_understanding"]["provider_mode"], "api")

        with self.path.open("r", encoding="utf-8") as f:
            stored = yaml.safe_load(f)
        self.assertEqual(stored["default_backend"], "video_understanding")
        self.assertEqual(stored["scene_overrides"]["campus_fence"], "yolo")
        self.assertEqual(stored["camera_overrides"]["cam_warehouse"], "video_understanding")
        self.assertEqual(stored["backends"]["video_understanding"]["api_url"], "https://example.com/video")


if __name__ == "__main__":
    unittest.main()
