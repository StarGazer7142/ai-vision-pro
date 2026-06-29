from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Optional, Sequence

from backend.app.core.config import DEFAULT_DB_PATH

logger = logging.getLogger("ai-platform.storage")


SESSION_HOURS_DEFAULT = 12
VALID_ROLES = {"super_admin", "admin", "operator", "viewer"}
VALID_ALERT_STATUSES = {"new", "acknowledged", "processing", "resolved", "false_positive"}


class StorageService:
    """SQLite runtime storage for alerts, signal snapshots, ingest stats, admins, and sessions."""

    def __init__(self, db_path: Path = DEFAULT_DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._user_columns: set[str] = set()
        self._session_columns: set[str] = set()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=5, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _utcnow() -> str:
        return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()

    @staticmethod
    def _user_table_sql(table_name: str = "users") -> str:
        return f"""
        CREATE TABLE {table_name} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            display_name TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'admin',
            status TEXT NOT NULL DEFAULT 'active',
            note TEXT,
            password_hash TEXT,
            password_salt TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """

    @staticmethod
    def _hash_password(password: str, salt: Optional[str] = None) -> tuple[str, str]:
        raw_password = (password or "").strip()
        if len(raw_password) < 6:
            raise ValueError("Password must be at least 6 characters")
        resolved_salt = salt or secrets.token_hex(16)
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            raw_password.encode("utf-8"),
            resolved_salt.encode("utf-8"),
            120000,
        ).hex()
        return digest, resolved_salt

    def _verify_password(self, password: str, password_hash: str, password_salt: str) -> bool:
        if not password_hash or not password_salt:
            return False
        expected_hash, _ = self._hash_password(password, password_salt)
        return secrets.compare_digest(expected_hash, password_hash)

    @staticmethod
    def _serialize_user(row: sqlite3.Row) -> dict:
        return {
            "id": row["id"],
            "username": row["username"],
            "display_name": row["display_name"],
            "role": row["role"] or "admin",
            "status": row["status"],
            "note": row["note"] or "",
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _normalize_role(role: str) -> str:
        clean_role = str(role or "viewer").strip().lower()
        if clean_role not in VALID_ROLES:
            raise ValueError(f"Unsupported role: {role}")
        return clean_role

    @staticmethod
    def _normalize_alert_status(status: str) -> str:
        clean_status = str(status or "new").strip().lower()
        if clean_status not in VALID_ALERT_STATUSES:
            raise ValueError(f"Unsupported alert status: {status}")
        return clean_status

    @staticmethod
    def _serialize_operation_log(row: sqlite3.Row) -> dict:
        detail = {}
        try:
            detail = json.loads(row["detail_json"] or "{}")
        except json.JSONDecodeError:
            detail = {}
        return {
            "id": row["id"],
            "module": row["module"],
            "action": row["action"],
            "operator": row["operator"],
            "target": row["target"],
            "detail": detail,
            "created_at": row["created_at"],
        }

    def _table_columns(self, conn: sqlite3.Connection, table_name: str) -> set[str]:
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        return {str(row["name"]) for row in rows}

    def _ensure_user_indexes(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_users_role ON users(role);
            CREATE INDEX IF NOT EXISTS idx_users_status ON users(status);
            CREATE INDEX IF NOT EXISTS idx_users_updated_at ON users(updated_at);
            """
        )

    def _legacy_row_password_material(
        self,
        row: sqlite3.Row,
        columns: set[str],
    ) -> tuple[str, str]:
        password_hash = str(row["password_hash"] or "").strip() if "password_hash" in columns else ""
        password_salt = str(row["password_salt"] or "").strip() if "password_salt" in columns else ""
        if password_hash and password_salt:
            return password_hash, password_salt

        legacy_password = str(row["password"] or "").strip() if "password" in columns else ""
        if not legacy_password:
            legacy_password = "123456"
        return self._hash_password(legacy_password)

    @staticmethod
    def _user_integrity_error_message(exc: sqlite3.IntegrityError) -> str:
        detail = str(exc or "").strip()
        lowered = detail.lower()
        if "unique constraint failed" in lowered and "users.username" in lowered:
            return "管理员账号已存在"
        if "not null constraint failed" in lowered and "users.password" in lowered:
            return "管理员账号数据表仍是旧结构，请先完成用户表迁移"
        return f"管理员账号写入失败：{detail or '未知约束冲突'}"

    def _init_db(self) -> None:
        with self._lock:
            with self._connection() as conn:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS alerts (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        scene_ids TEXT,
                        rule_id TEXT NOT NULL,
                        camera_id TEXT,
                        track_id INTEGER,
                        category TEXT,
                        confidence REAL,
                        message TEXT,
                        severity TEXT,
                        created_at TEXT NOT NULL
                    );

                    CREATE INDEX IF NOT EXISTS idx_alerts_timestamp ON alerts(timestamp);
                    CREATE INDEX IF NOT EXISTS idx_alerts_scene_ids ON alerts(scene_ids);
                    CREATE INDEX IF NOT EXISTS idx_alerts_rule_id ON alerts(rule_id);

                    CREATE TABLE IF NOT EXISTS signal_snapshots (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        scene_id TEXT NOT NULL,
                        scene_name TEXT,
                        payload_json TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    );

                    CREATE INDEX IF NOT EXISTS idx_signal_scene_time ON signal_snapshots(scene_id, timestamp);

                    CREATE TABLE IF NOT EXISTS ingest_frames (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        camera_id TEXT,
                        frame_id TEXT,
                        detection_count INTEGER,
                        alert_count INTEGER,
                        created_at TEXT NOT NULL
                    );

                    CREATE INDEX IF NOT EXISTS idx_ingest_time ON ingest_frames(timestamp);

                    CREATE TABLE IF NOT EXISTS users (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        username TEXT NOT NULL UNIQUE,
                        display_name TEXT NOT NULL,
                        role TEXT NOT NULL DEFAULT 'admin',
                        status TEXT NOT NULL DEFAULT 'active',
                        note TEXT,
                        password_hash TEXT,
                        password_salt TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );

                    CREATE INDEX IF NOT EXISTS idx_users_role ON users(role);
                    CREATE INDEX IF NOT EXISTS idx_users_status ON users(status);
                    CREATE INDEX IF NOT EXISTS idx_users_updated_at ON users(updated_at);

                    CREATE TABLE IF NOT EXISTS auth_sessions (
                        token TEXT PRIMARY KEY,
                        user_id INTEGER NOT NULL,
                        created_at TEXT NOT NULL,
                        expires_at TEXT NOT NULL,
                        last_seen_at TEXT NOT NULL,
                        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                    );

                    CREATE INDEX IF NOT EXISTS idx_auth_sessions_user_id ON auth_sessions(user_id);
                    CREATE INDEX IF NOT EXISTS idx_auth_sessions_expires_at ON auth_sessions(expires_at);

                    CREATE TABLE IF NOT EXISTS operation_logs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        module TEXT NOT NULL,
                        action TEXT NOT NULL,
                        operator TEXT,
                        target TEXT,
                        detail_json TEXT,
                        created_at TEXT NOT NULL
                    );

                    CREATE INDEX IF NOT EXISTS idx_operation_logs_module ON operation_logs(module);
                    CREATE INDEX IF NOT EXISTS idx_operation_logs_created_at ON operation_logs(created_at);

                    CREATE TABLE IF NOT EXISTS video_analyses (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        event_timestamp TEXT NOT NULL,
                        scene_id TEXT,
                        rule_id TEXT,
                        camera_id TEXT NOT NULL,
                        source_video_path TEXT,
                        clip_path TEXT,
                        clip_before_seconds INTEGER NOT NULL DEFAULT 2,
                        clip_after_seconds INTEGER NOT NULL DEFAULT 3,
                        provider TEXT,
                        model TEXT,
                        summary TEXT,
                        risk_assessment TEXT,
                        analysis_json TEXT,
                        error TEXT,
                        analysis_available INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        UNIQUE(event_timestamp, camera_id, rule_id, scene_id)
                    );

                    CREATE INDEX IF NOT EXISTS idx_video_analyses_event ON video_analyses(event_timestamp, camera_id);
                    CREATE INDEX IF NOT EXISTS idx_video_analyses_scene ON video_analyses(scene_id, created_at);

                    CREATE TABLE IF NOT EXISTS alert_workflows (
                        alert_id INTEGER PRIMARY KEY,
                        status TEXT NOT NULL DEFAULT 'new',
                        assignee TEXT,
                        note TEXT,
                        false_positive INTEGER NOT NULL DEFAULT 0,
                        handled_by TEXT,
                        handled_at TEXT,
                        updated_at TEXT NOT NULL,
                        FOREIGN KEY(alert_id) REFERENCES alerts(id) ON DELETE CASCADE
                    );

                    CREATE INDEX IF NOT EXISTS idx_alert_workflows_status ON alert_workflows(status);
                    CREATE INDEX IF NOT EXISTS idx_alert_workflows_updated_at ON alert_workflows(updated_at);

                    CREATE TABLE IF NOT EXISTS system_settings (
                        key TEXT PRIMARY KEY,
                        value_json TEXT NOT NULL,
                        updated_by TEXT,
                        updated_at TEXT NOT NULL
                    );
                    """
                )

                self._migrate_user_schema(conn)
                self._session_columns = self._table_columns(conn, "auth_sessions")
                self._cleanup_expired_sessions(conn)
                self._ensure_default_admin(conn)

    def _migrate_user_schema(self, conn: sqlite3.Connection) -> None:
        columns = self._table_columns(conn, "users")
        if "password" in columns:
            now = self._utcnow()
            rows = conn.execute("SELECT * FROM users ORDER BY id ASC").fetchall()

            conn.execute("DROP TABLE IF EXISTS users__migrated")
            conn.execute(self._user_table_sql("users__migrated"))

            for row in rows:
                password_hash, password_salt = self._legacy_row_password_material(row, columns)
                username = str(row["username"] or "").strip()
                display_name = str(row["display_name"] or username or "admin").strip() if "display_name" in columns else username
                status = "disabled" if str(row["status"] or "").strip().lower() == "disabled" else "active"
                note = str(row["note"] or "").strip() if "note" in columns else ""
                created_at = str(row["created_at"] or "").strip() if "created_at" in columns else ""
                updated_at = str(row["updated_at"] or "").strip() if "updated_at" in columns else ""
                if not created_at:
                    created_at = now
                if not updated_at:
                    updated_at = created_at

                conn.execute(
                    """
                    INSERT INTO users__migrated (
                        id, username, display_name, role, status, note,
                        password_hash, password_salt, created_at, updated_at
                    ) VALUES (?, ?, ?, 'admin', ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        int(row["id"]),
                        username,
                        display_name,
                        status,
                        note,
                        password_hash,
                        password_salt,
                        created_at,
                        updated_at,
                    ),
                )

            conn.execute("DROP TABLE users")
            conn.execute("ALTER TABLE users__migrated RENAME TO users")
            columns = self._table_columns(conn, "users")

        add_specs = {
            "display_name": "TEXT",
            "role": "TEXT NOT NULL DEFAULT 'admin'",
            "status": "TEXT NOT NULL DEFAULT 'active'",
            "note": "TEXT",
            "password_hash": "TEXT",
            "password_salt": "TEXT",
            "created_at": "TEXT",
            "updated_at": "TEXT",
        }

        for column_name, spec in add_specs.items():
            if column_name not in columns:
                conn.execute(f"ALTER TABLE users ADD COLUMN {column_name} {spec}")

        columns = self._table_columns(conn, "users")
        now = self._utcnow()

        if "display_name" in columns:
            conn.execute("UPDATE users SET display_name = COALESCE(NULLIF(display_name, ''), username)")
        if "role" in columns:
            conn.execute("UPDATE users SET role = 'admin' WHERE role IS NULL OR TRIM(role) = ''")
        if "status" in columns:
            conn.execute("UPDATE users SET status = 'active' WHERE status IS NULL OR TRIM(status) = ''")
        if "note" in columns:
            conn.execute("UPDATE users SET note = '' WHERE note IS NULL")
        if "created_at" in columns:
            conn.execute("UPDATE users SET created_at = ? WHERE created_at IS NULL OR TRIM(created_at) = ''", (now,))
        if "updated_at" in columns:
            conn.execute("UPDATE users SET updated_at = COALESCE(NULLIF(updated_at, ''), created_at, ?)", (now,))

        self._ensure_user_indexes(conn)
        self._user_columns = self._table_columns(conn, "users")

    def _cleanup_expired_sessions(self, conn: sqlite3.Connection) -> None:
        if not self._session_columns:
            return
        conn.execute("DELETE FROM auth_sessions WHERE expires_at <= ?", (self._utcnow(),))

    def _ensure_default_admin(self, conn: sqlite3.Connection) -> None:
        row = conn.execute("SELECT * FROM users WHERE username = 'admin' LIMIT 1").fetchone()
        now = self._utcnow()
        if row is None:
            initial_password = os.getenv("ADMIN_PASSWORD", "123456").strip() or "123456"
            password_hash, password_salt = self._hash_password(initial_password)
            conn.execute(
                """
                INSERT INTO users (
                    username, display_name, role, status, note,
                    password_hash, password_salt, created_at, updated_at
                ) VALUES (?, ?, 'admin', 'active', ?, ?, ?, ?, ?)
                """,
                (
                    "admin",
                    "超级管理员",
                    "系统硬核内置账号",
                    password_hash,
                    password_salt,
                    now,
                    now,
                ),
            )
            logger.info("已创建管理员账号 admin，密码: %s（建议尽快修改）", initial_password)
            print(f"\n{'='*60}")
            print(f"  已创建管理员账号: admin / {initial_password}")
            print(f"  建议登录后尽快修改密码")
            print(f"{'='*60}\n")
            return

        updates: list[str] = []
        params: list[object] = []
        display_name = str(row["display_name"] or "")
        note = str(row["note"] or "")
        if not display_name or "ç" in display_name or "è" in display_name or "å" in display_name:
            updates.append("display_name = ?")
            params.append("超级管理员")
        if not note or "ç" in note or "è" in note or "å" in note:
            updates.append("note = ?")
            params.append("系统内置超级管理员账号")
        if row["status"] != "active":
            updates.append("status = ?")
            params.append("active")
        if (row["role"] or "").strip().lower() != "admin":
            updates.append("role = ?")
            params.append("admin")
        if not row["password_hash"] or not row["password_salt"]:
            fallback_password = secrets.token_urlsafe(8)
            password_hash, password_salt = self._hash_password(fallback_password)
            updates.extend(["password_hash = ?", "password_salt = ?"])
            params.extend([password_hash, password_salt])
            logger.warning("admin 账号密码数据缺失，已生成新密码: %s，请登录后修改！", fallback_password)
            print(f"\n  admin 密码已重置: {fallback_password}\n")
        if updates:
            updates.append("updated_at = ?")
            params.append(now)
            params.append(int(row["id"]))
            conn.execute(f"UPDATE users SET {', '.join(updates)} WHERE id = ?", params)

    def _get_user_by_username(self, conn: sqlite3.Connection, username: str) -> Optional[sqlite3.Row]:
        return conn.execute(
            "SELECT * FROM users WHERE username = ? LIMIT 1",
            ((username or "").strip(),),
        ).fetchone()

    def _get_user_by_id(self, conn: sqlite3.Connection, user_id: int) -> Optional[sqlite3.Row]:
        return conn.execute(
            "SELECT * FROM users WHERE id = ? LIMIT 1",
            (int(user_id),),
        ).fetchone()

    def verify_user(self, username: str, password: str) -> Optional[dict]:
        clean_username = (username or "").strip()
        clean_password = (password or "").strip()
        if not clean_username or not clean_password:
            return None

        with self._lock:
            with self._connection() as conn:
                self._cleanup_expired_sessions(conn)
                row = conn.execute(
                    "SELECT * FROM users WHERE username = ? AND status = 'active' LIMIT 1",
                    (clean_username,),
                ).fetchone()

        if row is None:
            return None
        if not self._verify_password(clean_password, row["password_hash"] or "", row["password_salt"] or ""):
            return None
        return self._serialize_user(row)

    def list_admins(
        self,
        *,
        keyword: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 200,
    ) -> list[dict]:
        query = "SELECT * FROM users WHERE role = 'admin'"
        params: list[object] = []

        if keyword:
            like_value = f"%{keyword.strip()}%"
            query += " AND (username LIKE ? OR display_name LIKE ? OR note LIKE ?)"
            params.extend([like_value, like_value, like_value])
        if status:
            query += " AND status = ?"
            params.append("disabled" if str(status).strip().lower() == "disabled" else "active")

        query += " ORDER BY updated_at DESC, id DESC LIMIT ?"
        params.append(max(1, min(limit, 1000)))

        with self._lock:
            with self._connection() as conn:
                rows = conn.execute(query, params).fetchall()

        return [self._serialize_user(row) for row in rows]

    def list_users(
        self,
        *,
        keyword: Optional[str] = None,
        role: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 200,
    ) -> list[dict]:
        query = "SELECT * FROM users WHERE 1=1"
        params: list[object] = []

        if keyword:
            like_value = f"%{keyword.strip()}%"
            query += " AND (username LIKE ? OR display_name LIKE ? OR note LIKE ?)"
            params.extend([like_value, like_value, like_value])
        if role:
            query += " AND role = ?"
            params.append(self._normalize_role(role))
        if status:
            query += " AND status = ?"
            params.append("disabled" if str(status).strip().lower() == "disabled" else "active")

        query += " ORDER BY updated_at DESC, id DESC LIMIT ?"
        params.append(max(1, min(limit, 1000)))

        with self._lock:
            with self._connection() as conn:
                rows = conn.execute(query, params).fetchall()

        return [self._serialize_user(row) for row in rows]

    def get_all_users(self) -> list[dict]:
        return self.list_users(limit=1000)

    def count_users(self) -> int:
        with self._lock:
            with self._connection() as conn:
                row = conn.execute("SELECT COUNT(*) AS total FROM users").fetchone()
        return int(row["total"] if row else 0)

    def count_active_admins(self) -> int:
        with self._lock:
            with self._connection() as conn:
                row = conn.execute(
                    "SELECT COUNT(*) AS total FROM users WHERE role = 'admin' AND status = 'active'"
                ).fetchone()
        return int(row["total"] if row else 0)

    def create_admin(
        self,
        *,
        username: str,
        display_name: str,
        password: str,
        note: str = "",
        status: str = "active",
    ) -> dict:
        clean_username = (username or "").strip()
        clean_display_name = (display_name or "").strip()
        clean_note = (note or "").strip()
        clean_status = "disabled" if str(status).strip().lower() == "disabled" else "active"
        if not clean_username:
            raise ValueError("Username is required")
        if not clean_display_name:
            raise ValueError("Display name is required")

        password_hash, password_salt = self._hash_password(password)
        now = self._utcnow()

        try:
            with self._lock:
                with self._connection() as conn:
                    cursor = conn.execute(
                        """
                        INSERT INTO users (
                            username, display_name, role, status, note,
                            password_hash, password_salt, created_at, updated_at
                        ) VALUES (?, ?, 'admin', ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            clean_username,
                            clean_display_name,
                            clean_status,
                            clean_note,
                            password_hash,
                            password_salt,
                            now,
                            now,
                        ),
                    )
                    row = conn.execute("SELECT * FROM users WHERE id = ?", (cursor.lastrowid,)).fetchone()
        except sqlite3.IntegrityError as exc:
            raise ValueError(self._user_integrity_error_message(exc)) from exc

        return self._serialize_user(row)

    def create_user(
        self,
        *,
        username: str,
        display_name: str,
        role: str,
        password: str,
        note: str = "",
        status: str = "active",
    ) -> dict:
        clean_username = (username or "").strip()
        clean_display_name = (display_name or "").strip()
        clean_note = (note or "").strip()
        clean_status = "disabled" if str(status).strip().lower() == "disabled" else "active"
        clean_role = self._normalize_role(role)
        if not clean_username:
            raise ValueError("Username is required")
        if not clean_display_name:
            raise ValueError("Display name is required")

        password_hash, password_salt = self._hash_password(password)
        now = self._utcnow()

        try:
            with self._lock:
                with self._connection() as conn:
                    cursor = conn.execute(
                        """
                        INSERT INTO users (
                            username, display_name, role, status, note,
                            password_hash, password_salt, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            clean_username,
                            clean_display_name,
                            clean_role,
                            clean_status,
                            clean_note,
                            password_hash,
                            password_salt,
                            now,
                            now,
                        ),
                    )
                    row = conn.execute("SELECT * FROM users WHERE id = ?", (cursor.lastrowid,)).fetchone()
        except sqlite3.IntegrityError as exc:
            raise ValueError(self._user_integrity_error_message(exc)) from exc

        return self._serialize_user(row)

    def add_user(self, username: str, password: str, role: str) -> bool:
        _ = role
        try:
            self.create_admin(
                username=username,
                display_name=(username or "").strip() or "系统管理员",
                password=password,
                note="控制台创建的管理员账号",
            )
            return True
        except Exception:
            return False

    def update_admin(
        self,
        *,
        user_id: int,
        username: str,
        display_name: str,
        note: str = "",
    ) -> dict:
        clean_username = (username or "").strip()
        clean_display_name = (display_name or "").strip()
        clean_note = (note or "").strip()
        if not clean_username:
            raise ValueError("Username is required")
        if not clean_display_name:
            raise ValueError("Display name is required")

        try:
            with self._lock:
                with self._connection() as conn:
                    cursor = conn.execute(
                        """
                        UPDATE users
                        SET username = ?, display_name = ?, role = 'admin', note = ?, updated_at = ?
                        WHERE id = ? AND role = 'admin'
                        """,
                        (
                            clean_username,
                            clean_display_name,
                            clean_note,
                            self._utcnow(),
                            int(user_id),
                        ),
                    )
                    if cursor.rowcount <= 0:
                        raise LookupError("Admin not found")
                    row = conn.execute("SELECT * FROM users WHERE id = ?", (int(user_id),)).fetchone()
        except sqlite3.IntegrityError as exc:
            raise ValueError("Username already exists") from exc

        return self._serialize_user(row)

    def update_user(
        self,
        *,
        user_id: int,
        username: str,
        display_name: str,
        role: str,
        note: str = "",
    ) -> dict:
        clean_username = (username or "").strip()
        clean_display_name = (display_name or "").strip()
        clean_note = (note or "").strip()
        clean_role = self._normalize_role(role)
        if not clean_username:
            raise ValueError("Username is required")
        if not clean_display_name:
            raise ValueError("Display name is required")

        try:
            with self._lock:
                with self._connection() as conn:
                    cursor = conn.execute(
                        """
                        UPDATE users
                        SET username = ?, display_name = ?, role = ?, note = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (
                            clean_username,
                            clean_display_name,
                            clean_role,
                            clean_note,
                            self._utcnow(),
                            int(user_id),
                        ),
                    )
                    if cursor.rowcount <= 0:
                        raise LookupError("User not found")
                    row = conn.execute("SELECT * FROM users WHERE id = ?", (int(user_id),)).fetchone()
        except sqlite3.IntegrityError as exc:
            raise ValueError("Username already exists") from exc

        return self._serialize_user(row)

    def set_user_status(self, *, user_id: int, status: str) -> dict:
        clean_status = "disabled" if str(status).strip().lower() == "disabled" else "active"
        with self._lock:
            with self._connection() as conn:
                row = self._get_user_by_id(conn, int(user_id))
                if row is None:
                    raise LookupError("Admin not found")
                if row["status"] == "active" and clean_status == "disabled":
                    active_admins = conn.execute(
                        "SELECT COUNT(*) AS total FROM users WHERE role = 'admin' AND status = 'active'"
                    ).fetchone()
                    if int(active_admins["total"]) <= 1:
                        raise ValueError("At least one active admin account must remain")

                cursor = conn.execute(
                    "UPDATE users SET status = ?, updated_at = ? WHERE id = ?",
                    (clean_status, self._utcnow(), int(user_id)),
                )
                if cursor.rowcount <= 0:
                    raise LookupError("Admin not found")
                row = conn.execute("SELECT * FROM users WHERE id = ?", (int(user_id),)).fetchone()
        return self._serialize_user(row)

    def reset_user_password(self, *, user_id: int, password: str) -> dict:
        password_hash, password_salt = self._hash_password(password)
        with self._lock:
            with self._connection() as conn:
                cursor = conn.execute(
                    """
                    UPDATE users
                    SET password_hash = ?, password_salt = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (password_hash, password_salt, self._utcnow(), int(user_id)),
                )
                if cursor.rowcount <= 0:
                    raise LookupError("Admin not found")
                row = conn.execute("SELECT * FROM users WHERE id = ?", (int(user_id),)).fetchone()
        return self._serialize_user(row)

    def delete_admin(self, user_id: int) -> dict:
        with self._lock:
            with self._connection() as conn:
                row = self._get_user_by_id(conn, int(user_id))
                if row is None:
                    raise LookupError("Admin not found")
                if row["username"] == "admin":
                    raise ValueError("Built-in admin account cannot be deleted")
                if row["status"] == "active":
                    active_admins = conn.execute(
                        "SELECT COUNT(*) AS total FROM users WHERE role = 'admin' AND status = 'active'"
                    ).fetchone()
                    if int(active_admins["total"]) <= 1:
                        raise ValueError("At least one active admin account must remain")

                conn.execute("DELETE FROM auth_sessions WHERE user_id = ?", (int(user_id),))
                cursor = conn.execute("DELETE FROM users WHERE id = ? AND role = 'admin'", (int(user_id),))
                if cursor.rowcount <= 0:
                    raise LookupError("Admin not found")
        return self._serialize_user(row)

    def delete_user(self, user_id: int) -> None:
        with self._lock:
            with self._connection() as conn:
                row = self._get_user_by_id(conn, int(user_id))
                if row is None:
                    raise LookupError("User not found")
                if row["username"] == "admin":
                    raise ValueError("Built-in admin account cannot be deleted")
                if row["role"] in {"admin", "super_admin"} and row["status"] == "active":
                    active_admins = conn.execute(
                        "SELECT COUNT(*) AS total FROM users WHERE role IN ('admin', 'super_admin') AND status = 'active'"
                    ).fetchone()
                    if int(active_admins["total"]) <= 1:
                        raise ValueError("At least one active admin account must remain")

                conn.execute("DELETE FROM auth_sessions WHERE user_id = ?", (int(user_id),))
                cursor = conn.execute("DELETE FROM users WHERE id = ?", (int(user_id),))
                if cursor.rowcount <= 0:
                    raise LookupError("User not found")
        return self._serialize_user(row)

    def create_session(self, *, user_id: int, hours: int = SESSION_HOURS_DEFAULT) -> dict:
        created_at = self._utcnow()
        expires_at = (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=max(1, int(hours)))).isoformat()
        token = secrets.token_urlsafe(32)

        with self._lock:
            with self._connection() as conn:
                row = self._get_user_by_id(conn, int(user_id))
                if row is None:
                    raise LookupError("Admin not found")
                conn.execute(
                    """
                    INSERT INTO auth_sessions (token, user_id, created_at, expires_at, last_seen_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (token, int(user_id), created_at, expires_at, created_at),
                )

        return {"token": token, "expires_at": expires_at}

    def get_session_user(self, token: str) -> Optional[dict]:
        clean_token = (token or "").strip()
        if not clean_token:
            return None

        with self._lock:
            with self._connection() as conn:
                self._cleanup_expired_sessions(conn)
                row = conn.execute(
                    """
                    SELECT u.*, s.expires_at
                    FROM auth_sessions AS s
                    JOIN users AS u ON u.id = s.user_id
                    WHERE s.token = ?
                      AND u.status = 'active'
                    LIMIT 1
                    """,
                    (clean_token,),
                ).fetchone()
                if row is None:
                    return None
                conn.execute(
                    "UPDATE auth_sessions SET last_seen_at = ? WHERE token = ?",
                    (self._utcnow(), clean_token),
                )

        user = self._serialize_user(row)
        user["expires_at"] = row["expires_at"]
        return user

    def revoke_session(self, token: str) -> None:
        clean_token = (token or "").strip()
        if not clean_token:
            return
        with self._lock:
            with self._connection() as conn:
                conn.execute("DELETE FROM auth_sessions WHERE token = ?", (clean_token,))

    def revoke_user_sessions(self, user_id: int) -> None:
        with self._lock:
            with self._connection() as conn:
                conn.execute("DELETE FROM auth_sessions WHERE user_id = ?", (int(user_id),))

    def insert_alert(self, alert: dict, scene_ids: Sequence[str]) -> None:
        with self._lock:
            with self._connection() as conn:
                conn.execute(
                    """
                    INSERT INTO alerts (
                        timestamp, scene_ids, rule_id, camera_id, track_id, category,
                        confidence, message, severity, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(alert.get("timestamp")),
                        json.dumps(list(scene_ids), ensure_ascii=False),
                        str(alert.get("rule_id", "")),
                        alert.get("camera_id"),
                        alert.get("track_id"),
                        alert.get("category"),
                        float(alert.get("confidence", 0.0)),
                        alert.get("message"),
                        alert.get("severity"),
                        self._utcnow(),
                    ),
                )

    def clear_alerts(self) -> dict:
        """清空所有告警记录，返回被删除的行数。"""
        with self._lock:
            with self._connection() as conn:
                cursor = conn.execute("SELECT COUNT(*) FROM alerts")
                count_before = cursor.fetchone()[0]
                conn.execute("DELETE FROM alerts")
                conn.execute("DELETE FROM alert_workflows")
        return {"deleted_alerts": count_before}

    def insert_signal_snapshot(self, snapshot: dict) -> None:
        with self._lock:
            with self._connection() as conn:
                conn.execute(
                    """
                    INSERT INTO signal_snapshots (timestamp, scene_id, scene_name, payload_json, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        str(snapshot.get("timestamp")),
                        str(snapshot.get("scene_id", "")),
                        str(snapshot.get("scene_name", "")),
                        json.dumps(snapshot, ensure_ascii=False),
                        self._utcnow(),
                    ),
                )

    def insert_ingest_frame(
        self,
        *,
        timestamp: str,
        camera_id: str,
        frame_id: str,
        detection_count: int,
        alert_count: int,
    ) -> None:
        with self._lock:
            with self._connection() as conn:
                conn.execute(
                    """
                    INSERT INTO ingest_frames (
                        timestamp, camera_id, frame_id, detection_count, alert_count, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        timestamp,
                        camera_id,
                        frame_id,
                        int(detection_count),
                        int(alert_count),
                        self._utcnow(),
                    ),
                )

    def record_operation(
        self,
        *,
        module: str,
        action: str,
        operator: str = "system",
        target: str = "",
        detail: Optional[dict[str, Any]] = None,
    ) -> None:
        with self._lock:
            with self._connection() as conn:
                conn.execute(
                    """
                    INSERT INTO operation_logs (module, action, operator, target, detail_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(module or "general"),
                        str(action or "unknown"),
                        str(operator or "system"),
                        str(target or ""),
                        json.dumps(detail or {}, ensure_ascii=False),
                        self._utcnow(),
                    ),
                )

    @staticmethod
    def _serialize_video_analysis_row(row: sqlite3.Row) -> dict:
        payload = {}
        try:
            payload = json.loads(row["analysis_json"] or "{}")
        except json.JSONDecodeError:
            payload = {}
        return {
            "id": row["id"],
            "event_timestamp": row["event_timestamp"],
            "scene_id": row["scene_id"] or "",
            "rule_id": row["rule_id"] or "",
            "camera_id": row["camera_id"] or "",
            "source_video_path": row["source_video_path"] or "",
            "clip_path": row["clip_path"] or "",
            "clip_before_seconds": int(row["clip_before_seconds"] or 0),
            "clip_after_seconds": int(row["clip_after_seconds"] or 0),
            "provider": row["provider"] or "",
            "model": row["model"] or "",
            "summary": row["summary"] or "",
            "risk_assessment": row["risk_assessment"] or "",
            "analysis": payload,
            "error": row["error"] or "",
            "analysis_available": bool(row["analysis_available"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def upsert_video_analysis(
        self,
        *,
        event_timestamp: str,
        scene_id: str,
        rule_id: str,
        camera_id: str,
        source_video_path: str = "",
        clip_path: str = "",
        clip_before_seconds: int = 2,
        clip_after_seconds: int = 3,
        provider: str = "",
        model: str = "",
        summary: str = "",
        risk_assessment: str = "",
        analysis: Optional[dict] = None,
        error: str = "",
        analysis_available: bool = False,
    ) -> dict:
        now = self._utcnow()
        analysis_json = json.dumps(analysis or {}, ensure_ascii=False)
        with self._lock:
            with self._connection() as conn:
                conn.execute(
                    """
                    INSERT INTO video_analyses (
                        event_timestamp, scene_id, rule_id, camera_id,
                        source_video_path, clip_path, clip_before_seconds, clip_after_seconds,
                        provider, model, summary, risk_assessment, analysis_json,
                        error, analysis_available, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(event_timestamp, camera_id, rule_id, scene_id)
                    DO UPDATE SET
                        source_video_path = excluded.source_video_path,
                        clip_path = excluded.clip_path,
                        clip_before_seconds = excluded.clip_before_seconds,
                        clip_after_seconds = excluded.clip_after_seconds,
                        provider = excluded.provider,
                        model = excluded.model,
                        summary = excluded.summary,
                        risk_assessment = excluded.risk_assessment,
                        analysis_json = excluded.analysis_json,
                        error = excluded.error,
                        analysis_available = excluded.analysis_available,
                        updated_at = excluded.updated_at
                    """,
                    (
                        event_timestamp,
                        scene_id,
                        rule_id,
                        camera_id,
                        source_video_path,
                        clip_path,
                        int(clip_before_seconds),
                        int(clip_after_seconds),
                        provider,
                        model,
                        summary,
                        risk_assessment,
                        analysis_json,
                        error,
                        1 if analysis_available else 0,
                        now,
                        now,
                    ),
                )
                row = conn.execute(
                    """
                    SELECT *
                    FROM video_analyses
                    WHERE SUBSTR(event_timestamp, 1, 19) = ? AND camera_id = ? AND rule_id = ? AND scene_id = ?
                    LIMIT 1
                    """,
                    (str(event_timestamp or "")[:19], camera_id, rule_id, scene_id),
                ).fetchone()
        return self._serialize_video_analysis_row(row) if row else {}

    def get_video_analysis(
        self,
        *,
        event_timestamp: str,
        scene_id: str,
        rule_id: str,
        camera_id: str,
    ) -> Optional[dict]:
        with self._lock:
            with self._connection() as conn:
                row = conn.execute(
                    """
                    SELECT *
                    FROM video_analyses
                    WHERE SUBSTR(event_timestamp, 1, 19) = ? AND camera_id = ? AND rule_id = ? AND scene_id = ?
                    LIMIT 1
                    """,
                    (str(event_timestamp or "")[:19], camera_id, rule_id, scene_id),
                ).fetchone()
        if row is None:
            return None
        return self._serialize_video_analysis_row(row)

    def get_alert_history(
        self,
        *,
        scene_id: Optional[str] = None,
        limit: int = 200,
        rule_id: Optional[str] = None,
        camera_id: Optional[str] = None,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
    ) -> list[dict]:
        query = "SELECT * FROM alerts WHERE 1=1"
        params: list[object] = []

        if scene_id:
            query += " AND scene_ids LIKE ?"
            params.append(f'%"{scene_id}"%')
        if rule_id:
            query += " AND rule_id = ?"
            params.append(rule_id)
        if camera_id:
            query += " AND camera_id = ?"
            params.append(camera_id)
        if start_time:
            query += " AND timestamp >= ?"
            params.append(start_time)
        if end_time:
            query += " AND timestamp <= ?"
            params.append(end_time)

        query += " ORDER BY id DESC LIMIT ?"
        params.append(max(1, min(limit, 10000)))

        with self._lock:
            with self._connection() as conn:
                rows = conn.execute(query, params).fetchall()

                # 批量获取所有告警的 workflow（一次查询）
                alert_ids = [int(row["id"]) for row in rows]
                workflow_map: dict[int, dict] = {}
                if alert_ids:
                    placeholders = ",".join("?" * len(alert_ids))
                    wf_rows = conn.execute(
                        f"SELECT * FROM alert_workflows WHERE alert_id IN ({placeholders})",
                        alert_ids,
                    ).fetchall()
                    for wf in wf_rows:
                        workflow_map[int(wf["alert_id"])] = {
                            "alert_id": int(wf["alert_id"]),
                            "status": wf["status"] or "new",
                            "assignee": wf["assignee"] or "",
                            "note": wf["note"] or "",
                            "false_positive": bool(wf["false_positive"]),
                            "handled_by": wf["handled_by"] or "",
                            "handled_at": wf["handled_at"] or "",
                            "updated_at": wf["updated_at"] or "",
                        }

                # 批量获取所有告警的 video_analysis（一次查询，用 IN 子句按 camera+rule+scene 分组）
                analysis_map: dict[str, dict] = {}
                if rows:
                    # 收集所有唯一的 (camera_id, rule_id, scene_id) 组合
                    query_keys = set()
                    for row in rows:
                        try:
                            sids = json.loads(row["scene_ids"] or "[]")
                        except json.JSONDecodeError:
                            sids = []
                        sid = str(sids[0]) if sids else ""
                        query_keys.add((row["camera_id"], row["rule_id"], sid))

                    for cam, rule, sid in query_keys:
                        a_row = conn.execute(
                            """
                            SELECT * FROM video_analyses
                            WHERE camera_id = ? AND rule_id = ? AND scene_id = ?
                            ORDER BY id DESC LIMIT 1
                            """,
                            (cam, rule, sid),
                        ).fetchone()
                        if a_row is not None:
                            analysis_map[f"{cam}:{rule}:{sid}"] = self._serialize_video_analysis_row(a_row)

                result = []
                for row in rows:
                    scene_ids = []
                    try:
                        scene_ids = json.loads(row["scene_ids"] or "[]")
                    except json.JSONDecodeError:
                        scene_ids = []

                    resolved_scene_id = str(scene_ids[0]) if scene_ids else ""
                    analysis_key = f"{row['camera_id']}:{row['rule_id']}:{resolved_scene_id}"
                    analysis = analysis_map.get(analysis_key)

                    result.append(
                        {
                            "id": row["id"],
                            "timestamp": row["timestamp"],
                            "scene_ids": scene_ids,
                            "rule_id": row["rule_id"],
                            "camera_id": row["camera_id"],
                            "track_id": row["track_id"],
                            "category": row["category"],
                            "confidence": row["confidence"],
                            "message": row["message"],
                            "severity": row["severity"],
                            "workflow": workflow_map.get(int(row["id"]), {
                                "alert_id": int(row["id"]),
                                "status": "new",
                                "assignee": "",
                                "note": "",
                                "false_positive": False,
                                "handled_by": "",
                                "handled_at": "",
                                "updated_at": "",
                            }),
                            "video_analysis": analysis,
                            "video_analysis_available": bool(analysis and analysis.get("analysis_available")),
                            "video_analysis_summary": (analysis or {}).get("summary", ""),
                        }
                    )
        return result

    def _get_alert_workflow_for_row(self, conn: sqlite3.Connection, alert_id: int) -> dict:
        row = conn.execute("SELECT * FROM alert_workflows WHERE alert_id = ? LIMIT 1", (int(alert_id),)).fetchone()
        if row is None:
            return {
                "alert_id": int(alert_id),
                "status": "new",
                "assignee": "",
                "note": "",
                "false_positive": False,
                "handled_by": "",
                "handled_at": "",
                "updated_at": "",
            }
        return {
            "alert_id": int(row["alert_id"]),
            "status": row["status"] or "new",
            "assignee": row["assignee"] or "",
            "note": row["note"] or "",
            "false_positive": bool(row["false_positive"]),
            "handled_by": row["handled_by"] or "",
            "handled_at": row["handled_at"] or "",
            "updated_at": row["updated_at"] or "",
        }

    def update_alert_workflow(
        self,
        *,
        alert_id: int,
        status: str,
        assignee: str = "",
        note: str = "",
        handled_by: str = "",
    ) -> dict:
        clean_status = self._normalize_alert_status(status)
        clean_assignee = str(assignee or "").strip()
        clean_note = str(note or "").strip()
        clean_handler = str(handled_by or "").strip()
        now = self._utcnow()
        handled_at = now if clean_status in {"resolved", "false_positive"} else ""
        false_positive = 1 if clean_status == "false_positive" else 0

        with self._lock:
            with self._connection() as conn:
                alert = conn.execute("SELECT id FROM alerts WHERE id = ? LIMIT 1", (int(alert_id),)).fetchone()
                if alert is None:
                    raise LookupError("Alert not found")
                conn.execute(
                    """
                    INSERT INTO alert_workflows (
                        alert_id, status, assignee, note, false_positive,
                        handled_by, handled_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(alert_id)
                    DO UPDATE SET
                        status = excluded.status,
                        assignee = excluded.assignee,
                        note = excluded.note,
                        false_positive = excluded.false_positive,
                        handled_by = excluded.handled_by,
                        handled_at = excluded.handled_at,
                        updated_at = excluded.updated_at
                    """,
                    (
                        int(alert_id),
                        clean_status,
                        clean_assignee,
                        clean_note,
                        false_positive,
                        clean_handler,
                        handled_at,
                        now,
                    ),
                )
                workflow = self._get_alert_workflow_for_row(conn, int(alert_id))
        return workflow

    def get_system_settings(self) -> dict:
        defaults = {
            "retention_days": 30,
            "replay_retention_days": 30,
            "default_dwell_seconds": 5,
            "notification_channels": [],
            "model_profile": "balanced",
            "allow_debug_tools": False,
            "auto_reconnect_streams": True,
        }
        with self._lock:
            with self._connection() as conn:
                rows = conn.execute("SELECT key, value_json FROM system_settings").fetchall()
        settings = dict(defaults)
        for row in rows:
            try:
                settings[row["key"]] = json.loads(row["value_json"])
            except json.JSONDecodeError:
                continue
        return settings

    def update_system_settings(self, settings: dict, *, updated_by: str = "system") -> dict:
        current = self.get_system_settings()
        allowed_keys = set(current.keys())
        merged = dict(current)
        for key, value in (settings or {}).items():
            if key in allowed_keys:
                merged[key] = value

        now = self._utcnow()
        with self._lock:
            with self._connection() as conn:
                for key, value in merged.items():
                    conn.execute(
                        """
                        INSERT INTO system_settings (key, value_json, updated_by, updated_at)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(key)
                        DO UPDATE SET value_json = excluded.value_json,
                                      updated_by = excluded.updated_by,
                                      updated_at = excluded.updated_at
                        """,
                        (key, json.dumps(value, ensure_ascii=False), updated_by, now),
                    )
        return merged

    def get_signal_history(self, scene_id: str, limit: int = 200) -> list[dict]:
        with self._lock:
            with self._connection() as conn:
                rows = conn.execute(
                    """
                    SELECT payload_json
                    FROM signal_snapshots
                    WHERE scene_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (scene_id, max(1, min(limit, 5000))),
                ).fetchall()

        data: list[dict] = []
        for row in rows:
            try:
                data.append(json.loads(row["payload_json"]))
            except json.JSONDecodeError:
                continue
        return data

    def get_latest_ingest_stats(self, limit: int = 50) -> list[dict]:
        with self._lock:
            with self._connection() as conn:
                rows = conn.execute(
                    """
                    SELECT timestamp, camera_id, frame_id, detection_count, alert_count
                    FROM ingest_frames
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (max(1, min(limit, 1000)),),
                ).fetchall()

        return [dict(row) for row in rows]

    def list_operation_logs(
        self,
        *,
        module: Optional[str] = None,
        keyword: Optional[str] = None,
        limit: int = 200,
    ) -> list[dict]:
        query = "SELECT * FROM operation_logs WHERE 1=1"
        params: list[object] = []

        if module:
            query += " AND module = ?"
            params.append(module)
        if keyword:
            like_value = f"%{keyword.strip()}%"
            query += " AND (action LIKE ? OR operator LIKE ? OR target LIKE ? OR detail_json LIKE ?)"
            params.extend([like_value, like_value, like_value, like_value])

        query += " ORDER BY id DESC LIMIT ?"
        params.append(max(1, min(limit, 1000)))

        with self._lock:
            with self._connection() as conn:
                rows = conn.execute(query, params).fetchall()

        return [self._serialize_operation_log(row) for row in rows]


storage_service = StorageService()
