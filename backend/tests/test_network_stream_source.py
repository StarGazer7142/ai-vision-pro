from pathlib import Path
import sys
import unittest

from fastapi import HTTPException

sys.path.append(str(Path(__file__).resolve().parents[2]))

from backend.app.api.routes import _validate_network_stream_url
from backend.app.services.stream_service import _resolve_source


class NetworkStreamSourceTestCase(unittest.TestCase):
    def test_resolve_source_keeps_lan_http_and_rtsp_urls(self):
        self.assertEqual(
            _resolve_source("http://192.168.1.23:8080/video"),
            "http://192.168.1.23:8080/video",
        )
        self.assertEqual(
            _resolve_source("rtsp://192.168.1.23:8554/live"),
            "rtsp://192.168.1.23:8554/live",
        )

    def test_resolve_source_still_supports_local_camera_and_files(self):
        self.assertEqual(_resolve_source("camera://0"), 0)
        self.assertEqual(_resolve_source("1"), 1)
        resolved = _resolve_source("data/uploads/videos/demo.mp4")
        self.assertTrue(str(resolved).endswith("data\\uploads\\videos\\demo.mp4") or str(resolved).endswith("data/uploads/videos/demo.mp4"))

    def test_validate_network_stream_url_rejects_non_stream_schemes(self):
        with self.assertRaises(HTTPException) as ctx:
            _validate_network_stream_url("file:///C:/secret.mp4")
        self.assertEqual(ctx.exception.status_code, 400)

        with self.assertRaises(HTTPException) as ctx:
            _validate_network_stream_url("192.168.1.23:8080/video")
        self.assertEqual(ctx.exception.status_code, 400)

    def test_validate_network_stream_url_accepts_common_lan_camera_schemes(self):
        self.assertEqual(
            _validate_network_stream_url("http://192.168.1.23:8080/video"),
            "http://192.168.1.23:8080/video",
        )
        self.assertEqual(
            _validate_network_stream_url("rtsp://192.168.1.23:8554/live"),
            "rtsp://192.168.1.23:8554/live",
        )


if __name__ == "__main__":
    unittest.main()
