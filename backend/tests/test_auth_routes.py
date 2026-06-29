from pathlib import Path
import sys
import tempfile
import unittest

from fastapi import HTTPException

sys.path.append(str(Path(__file__).resolve().parents[2]))

from backend.app.api import routes
from backend.app.services.storage_service import StorageService


class AuthRegisterRouteTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.tmp.close()
        self.db_path = Path(self.tmp.name)
        self.service = StorageService(db_path=self.db_path)
        self.original_storage_service = routes.storage_service
        routes.storage_service = self.service

    def tearDown(self):
        routes.storage_service = self.original_storage_service
        if self.db_path.exists():
            self.db_path.unlink()

    def _admin_token(self) -> str:
        admin = self.service.verify_user("admin", "123456")
        self.assertIsNotNone(admin)
        session = self.service.create_session(user_id=admin["id"])
        return session["token"]

    def test_register_requires_authenticated_admin_session(self):
        payload = routes.AdminRegisterRequest(
            username="ops_admin",
            display_name="Ops Admin",
            password="secret123",
            note="console created",
        )

        with self.assertRaises(HTTPException) as ctx:
            routes.register_admin(payload, authorization=None)

        self.assertEqual(ctx.exception.status_code, 403)
        self.assertIn("开放注册已关闭", str(ctx.exception.detail))
        admins = self.service.list_admins(limit=20)
        self.assertEqual([item["username"] for item in admins], ["admin"])

    def test_register_allows_authenticated_admin_to_create_account(self):
        payload = routes.AdminRegisterRequest(
            username="ops_admin",
            display_name="Ops Admin",
            password="secret123",
            note="console created",
        )

        response = routes.register_admin(payload, authorization=f"Bearer {self._admin_token()}")

        self.assertTrue(response["ok"])
        self.assertEqual(response["data"]["username"], "ops_admin")
        self.assertEqual(response["data"]["role"], "admin")

        admins = self.service.list_admins(limit=20)
        usernames = {item["username"] for item in admins}
        self.assertEqual(usernames, {"admin", "ops_admin"})


if __name__ == "__main__":
    unittest.main()
