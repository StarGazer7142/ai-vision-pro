from pathlib import Path
import sys
import tempfile
import unittest

sys.path.append(str(Path(__file__).resolve().parents[2]))

from backend.app.services.mimo_video_client import MimoVideoClient


class MimoVideoClientTestCase(unittest.TestCase):
    def test_resolve_local_video_to_data_url_when_base64_enabled(self):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as tmp:
            tmp.write(b"fake-video-binary")
            tmp_path = Path(tmp.name)

        try:
            client = MimoVideoClient()
            client.use_base64_for_local_files = True
            source, meta = client._resolve_video_source(str(tmp_path))
            self.assertTrue(source.startswith("data:video/"))
            self.assertEqual(meta.get("source_kind"), "local_base64")
            self.assertEqual(meta.get("path"), str(tmp_path))
        finally:
            if tmp_path.exists():
                tmp_path.unlink()


if __name__ == "__main__":
    unittest.main()
