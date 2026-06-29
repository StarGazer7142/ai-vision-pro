from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.append(str(Path(__file__).resolve().parents[2]))

from backend.app.services.storage_service import StorageService


class StorageServiceAdminsTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.tmp.close()
        self.db_path = Path(self.tmp.name)
        self.service = StorageService(db_path=self.db_path)

    def tearDown(self):
        if self.db_path.exists():
            self.db_path.unlink()

    def test_default_admin_seed_and_verify(self):
        admins = self.service.list_admins(limit=20)
        usernames = {item["username"] for item in admins}
        self.assertIn("admin", usernames)

        user = self.service.verify_user("admin", "123456")
        self.assertIsNotNone(user)
        self.assertEqual(user["username"], "admin")
        self.assertEqual(user["role"], "admin")

    def test_create_update_and_list_admins(self):
        created = self.service.create_admin(
            username="ops_admin",
            display_name="Ops Admin",
            password="secret123",
            note="night shift owner",
        )
        self.assertEqual(created["username"], "ops_admin")
        self.assertEqual(created["role"], "admin")

        updated = self.service.update_admin(
            user_id=created["id"],
            username="ops_admin",
            display_name="Ops Commander",
            note="night shift",
        )
        self.assertEqual(updated["display_name"], "Ops Commander")

        admins = self.service.list_admins(keyword="Commander")
        self.assertEqual(len(admins), 1)
        self.assertEqual(admins[0]["id"], created["id"])

    def test_session_create_lookup_and_logout(self):
        created = self.service.create_admin(
            username="shift_admin",
            display_name="Shift Admin",
            password="secret123",
        )
        session = self.service.create_session(user_id=created["id"])
        self.assertTrue(session["token"])

        session_user = self.service.get_session_user(session["token"])
        self.assertIsNotNone(session_user)
        self.assertEqual(session_user["username"], "shift_admin")

        self.service.revoke_session(session["token"])
        self.assertIsNone(self.service.get_session_user(session["token"]))

    def test_delete_admin_protection(self):
        with self.assertRaises(ValueError):
            admin = next(item for item in self.service.list_admins(limit=10) if item["username"] == "admin")
            self.service.delete_admin(admin["id"])

        created = self.service.create_admin(
            username="temp_admin",
            display_name="Temp Admin",
            password="secret123",
        )
        deleted = self.service.delete_admin(created["id"])
        self.assertEqual(deleted["username"], "temp_admin")

    def test_operation_logs(self):
        self.service.record_operation(
            module="admins",
            action="create",
            operator="console",
            target="ops_admin",
            detail={"role": "admin"},
        )
        self.service.record_operation(
            module="system",
            action="reload",
            operator="console",
            target="rules",
            detail={"revision": 3},
        )

        logs = self.service.list_operation_logs(module="admins")
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["target"], "ops_admin")
        self.assertEqual(logs[0]["detail"]["role"], "admin")

    def test_video_analysis_is_persisted_and_exposed_in_alert_history(self):
        self.service.insert_alert(
            {
                "timestamp": "2026-05-07T10:00:00",
                "rule_id": "fence_intrusion",
                "camera_id": "cam_fence",
                "track_id": 7,
                "category": "person",
                "confidence": 0.93,
                "message": "人员翻越围栏",
                "severity": "high",
            },
            ["campus_fence"],
        )
        stored = self.service.upsert_video_analysis(
            event_timestamp="2026-05-07T10:00:00",
            scene_id="campus_fence",
            rule_id="fence_intrusion",
            camera_id="cam_fence",
            source_video_path="D:/video/source.mp4",
            clip_path="D:/video/clip.mp4",
            provider="mimo_video",
            model="mimo-v2.5",
            summary="一名人员从围栏外侧攀爬进入园区。",
            risk_assessment="高风险入侵行为",
            analysis={"summary": "一名人员从围栏外侧攀爬进入园区。"},
            analysis_available=True,
        )
        self.assertTrue(stored["analysis_available"])

        history = self.service.get_alert_history(limit=10)
        self.assertEqual(len(history), 1)
        self.assertTrue(history[0]["video_analysis_available"])
        self.assertEqual(history[0]["video_analysis_summary"], "一名人员从围栏外侧攀爬进入园区。")

    def test_legacy_password_schema_is_rebuilt_and_allows_new_admins(self):
        self.service = None
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(
                """
                DROP TABLE IF EXISTS users;
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL,
                    role TEXT NOT NULL,
                    status TEXT NOT NULL,
                    note TEXT,
                    password TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            conn.execute(
                """
                INSERT INTO users (username, display_name, role, status, note, password, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "legacy_admin",
                    "Legacy Admin",
                    "admin",
                    "active",
                    "legacy row",
                    "secret123",
                    "2026-04-20T00:00:00",
                    "2026-04-20T00:00:00",
                ),
            )

        migrated = StorageService(db_path=self.db_path)
        legacy_user = migrated.verify_user("legacy_admin", "secret123")
        self.assertIsNotNone(legacy_user)
        self.assertEqual(legacy_user["username"], "legacy_admin")

        created = migrated.create_admin(
            username="fresh_admin",
            display_name="Fresh Admin",
            password="secret456",
            note="created after migration",
        )
        self.assertEqual(created["username"], "fresh_admin")

        with sqlite3.connect(self.db_path) as conn:
            table_sql = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='users'"
            ).fetchone()[0]
            self.assertNotIn("password TEXT NOT NULL", table_sql)


if __name__ == "__main__":
    unittest.main()
