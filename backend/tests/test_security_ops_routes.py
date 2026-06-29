from pathlib import Path
import sys
import tempfile
import unittest

from fastapi import HTTPException

sys.path.append(str(Path(__file__).resolve().parents[2]))

from backend.app.api import routes
from backend.app.services.storage_service import StorageService


class SecurityOpsRoutesTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.tmp.close()
        self.db_path = Path(self.tmp.name)
        self.service = StorageService(db_path=self.db_path)
        self.original_storage_service = routes.storage_service
        self.original_debug_tokens = dict(routes.DEBUG_TOKENS)
        self.original_login_failures = dict(routes.LOGIN_FAILURES)
        routes.storage_service = self.service
        routes.DEBUG_TOKENS.clear()
        routes.LOGIN_FAILURES.clear()

    def tearDown(self):
        routes.storage_service = self.original_storage_service
        routes.DEBUG_TOKENS.clear()
        routes.DEBUG_TOKENS.update(self.original_debug_tokens)
        routes.LOGIN_FAILURES.clear()
        routes.LOGIN_FAILURES.update(self.original_login_failures)
        if self.db_path.exists():
            self.db_path.unlink()

    def _admin_token(self) -> str:
        admin = self.service.verify_user("admin", "123456")
        self.assertIsNotNone(admin)
        return self.service.create_session(user_id=admin["id"])["token"]

    def test_debug_login_is_disabled_by_default_and_can_be_enabled_by_setting(self):
        payload = routes.DebugLoginRequest(username="debug", password="123456")
        with self.assertRaises(HTTPException) as ctx:
            routes.debug_login(payload)
        self.assertEqual(ctx.exception.status_code, 403)

        self.service.update_system_settings({"allow_debug_tools": True}, updated_by="test")
        response = routes.debug_login(payload)
        self.assertTrue(response["token"])

    def test_ops_health_requires_session(self):
        with self.assertRaises(HTTPException) as ctx:
            routes.ops_health(authorization=None)
        self.assertEqual(ctx.exception.status_code, 401)

        response = routes.ops_health(authorization=f"Bearer {self._admin_token()}")
        self.assertEqual(response["data"]["status"], "ok")
        self.assertIn("disk", response["data"])


if __name__ == "__main__":
    unittest.main()
