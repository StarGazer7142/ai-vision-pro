from pathlib import Path
import sys
import tempfile
import unittest

import yaml
from fastapi import HTTPException

sys.path.append(str(Path(__file__).resolve().parents[2]))

from backend.app.api import routes
from backend.app.core import config as app_config
from backend.app.services.storage_service import StorageService


class AdminProtectedRoutesTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp_db = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.tmp_db.close()
        self.db_path = Path(self.tmp_db.name)
        self.service = StorageService(db_path=self.db_path)

        self.tmp_rules = tempfile.NamedTemporaryFile(delete=False, suffix=".yaml")
        self.tmp_rules.close()
        self.rules_path = Path(self.tmp_rules.name)
        self._write_rules_config()

        self.original_storage_service = routes.storage_service
        self.original_engine_rule_path = routes.engine.rule_path
        self.original_engine_reload_rules = routes.engine.reload_rules
        self.original_default_rule_path = app_config.DEFAULT_RULE_PATH

        routes.storage_service = self.service
        routes.engine.rule_path = self.rules_path
        routes.engine.reload_rules = lambda: {"ok": True, "revision": 999}
        app_config.DEFAULT_RULE_PATH = self.rules_path

        self.legacy_region_update_endpoint = next(
            route.endpoint
            for route in routes.router.routes
            if getattr(route, "path", "") == "/config/update_region"
        )

    def tearDown(self):
        routes.storage_service = self.original_storage_service
        routes.engine.rule_path = self.original_engine_rule_path
        routes.engine.reload_rules = self.original_engine_reload_rules
        app_config.DEFAULT_RULE_PATH = self.original_default_rule_path

        if self.rules_path.exists():
            self.rules_path.unlink()
        if self.db_path.exists():
            self.db_path.unlink()

    def _write_rules_config(self):
        config = {
            "scenes": [],
            "rules": [],
            "cameras": [
                {
                    "id": "cam_test",
                    "name": "Test Camera",
                    "stream": "camera://0",
                    "rois": [
                        {
                            "id": "line1",
                            "label": "Fence Line",
                            "line": [[0.1, 0.5], [0.9, 0.5]],
                            "polygon": [],
                            "path_points": [],
                            "path_width": 0.08,
                            "draw_mode": "line",
                        }
                    ],
                    "dwell_zones": [
                        {
                            "id": "zone1",
                            "label": "Yard",
                            "polygon": [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]],
                        }
                    ],
                }
            ],
        }
        with self.rules_path.open("w", encoding="utf-8") as f:
            yaml.safe_dump(config, f, allow_unicode=True, sort_keys=False)

    def _read_rules_config(self):
        with self.rules_path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f)

    def _admin_token(self) -> str:
        admin = self.service.verify_user("admin", "123456")
        self.assertIsNotNone(admin)
        session = self.service.create_session(user_id=admin["id"])
        return session["token"]

    def test_log_endpoints_require_admin_session(self):
        with self.assertRaises(HTTPException) as ctx:
            routes.get_operation_logs(authorization=None)
        self.assertEqual(ctx.exception.status_code, 401)

        with self.assertRaises(HTTPException) as ctx:
            routes.get_system_logs(authorization=None)
        self.assertEqual(ctx.exception.status_code, 401)

        token = self._admin_token()
        logs = routes.get_system_logs(authorization=f"Bearer {token}", tail=5)
        self.assertIn("app", logs)
        self.assertIn("error", logs)

    def test_region_update_and_clear_require_admin_session_and_record_audit_logs(self):
        payload = routes.ZoneUpdateRequest(
            region_type="rois",
            points=[[0.2, 0.25], [0.8, 0.25]],
            drawing_mode="line",
            path_width=0.08,
        )

        with self.assertRaises(HTTPException) as ctx:
            routes.update_camera_region("cam_test", "line1", payload, authorization=None)
        self.assertEqual(ctx.exception.status_code, 401)

        with self.assertRaises(HTTPException) as ctx:
            routes.clear_camera_region("cam_test", "line1", region_type="rois", authorization=None)
        self.assertEqual(ctx.exception.status_code, 401)

        token = self._admin_token()
        update_result = routes.update_camera_region(
            "cam_test",
            "line1",
            payload,
            authorization=f"Bearer {token}",
        )
        self.assertTrue(update_result["ok"])

        config_after_update = self._read_rules_config()
        roi = config_after_update["cameras"][0]["rois"][0]
        self.assertEqual(roi["line"], [[0.2, 0.25], [0.8, 0.25]])
        self.assertEqual(roi["draw_mode"], "line")

        clear_result = routes.clear_camera_region(
            "cam_test",
            "line1",
            region_type="rois",
            authorization=f"Bearer {token}",
        )
        self.assertTrue(clear_result["ok"])

        config_after_clear = self._read_rules_config()
        roi_after_clear = config_after_clear["cameras"][0]["rois"][0]
        self.assertEqual(roi_after_clear["line"], [])
        self.assertEqual(roi_after_clear["polygon"], [])
        self.assertEqual(roi_after_clear["path_points"], [])

        operation_logs = self.service.list_operation_logs(module="regions", limit=10)
        self.assertEqual([item["action"] for item in operation_logs[:2]], ["clear", "update"])
        self.assertEqual(operation_logs[0]["detail"]["camera_id"], "cam_test")
        self.assertEqual(operation_logs[0]["detail"]["region_id"], "line1")

    def test_legacy_region_update_route_requires_admin_session(self):
        payload = routes.RegionUpdate(
            camera_id="cam_test",
            region_type="dwell_zones",
            region_id="zone1",
            points=[[0.15, 0.15], [0.85, 0.15], [0.85, 0.85], [0.15, 0.85]],
        )

        with self.assertRaises(HTTPException) as ctx:
            self.legacy_region_update_endpoint(payload, authorization=None)
        self.assertEqual(ctx.exception.status_code, 401)

        token = self._admin_token()
        result = self.legacy_region_update_endpoint(payload, authorization=f"Bearer {token}")
        self.assertTrue(result["ok"])

        config_after_update = self._read_rules_config()
        zone = config_after_update["cameras"][0]["dwell_zones"][0]
        self.assertEqual(zone["polygon"], payload.points)

        operation_logs = self.service.list_operation_logs(module="regions", limit=10)
        self.assertEqual(operation_logs[0]["action"], "update")
        self.assertEqual(operation_logs[0]["detail"]["source"], "config/update_region")


if __name__ == "__main__":
    unittest.main()
