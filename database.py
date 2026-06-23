import sqlite3
import os
from datetime import datetime
from typing import Optional, List, Dict


DB_PATH = os.environ.get("DB_PATH", "tasks.db")


class Database:
    def __init__(self):
        self._init_db()

    def _conn(self):
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id    INTEGER PRIMARY KEY,
                    name       TEXT NOT NULL,
                    username   TEXT,
                    created_at TEXT DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS tasks (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    title         TEXT NOT NULL,
                    assignee_ids  TEXT NOT NULL,
                    assignee_name TEXT NOT NULL,
                    deadline      TEXT,
                    done          INTEGER DEFAULT 0,
                    done_at       TEXT,
                    reminded      INTEGER DEFAULT 0,
                    archived      INTEGER DEFAULT 0,
                    created_at    TEXT DEFAULT (datetime('now'))
                );
            """)
            # Миграция: добавить новые колонки если их нет (для существующих БД)
            try:
                conn.execute("ALTER TABLE tasks ADD COLUMN assignee_ids TEXT")
            except Exception:
                pass
            try:
                conn.execute("ALTER TABLE tasks ADD COLUMN archived INTEGER DEFAULT 0")
            except Exception:
                pass

    # ── Users ──────────────────────────────────────────────────────────────

    def register_user(self, user_id: int, name: str, username: Optional[str]):
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO users (user_id, name, username)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET name=excluded.name, username=excluded.username
            """, (user_id, name, username))

    def get_user(self, user_id: int) -> Optional[Dict]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
            return dict(row) if row else None

    def get_team_members(self) -> List[Dict]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM users ORDER BY name").fetchall()
            return [dict(r) for r in rows]

    # ── Tasks ──────────────────────────────────────────────────────────────

    def create_task(self, title: str, assignee_ids: List[int],
                    assignee_name: str, deadline: Optional[str]) -> int:
        ids_str = ",".join(str(i) for i in assignee_ids)
        with self._conn() as conn:
            cur = conn.execute("""
                INSERT INTO tasks (title, assignee_ids, assignee_name, deadline)
                VALUES (?, ?, ?, ?)
            """, (title, ids_str, assignee_name, deadline))
            return cur.lastrowid

    def get_task(self, task_id: int) -> Optional[Dict]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            return dict(row) if row else None

    def get_all_tasks(self, done: bool = False) -> List[Dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE done=? AND archived=0 ORDER BY deadline ASC, created_at ASC",
                (1 if done else 0,)
            ).fetchall()
            return [dict(r) for r in rows]

    def get_user_tasks(self, user_id: int, done: bool = False) -> List[Dict]:
        """Задачи где user_id есть среди ответственных."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE done=? AND archived=0 ORDER BY deadline ASC",
                (1 if done else 0,)
            ).fetchall()
            result = []
            for r in rows:
                d = dict(r)
                ids = [int(x) for x in (d.get("assignee_ids") or "").split(",") if x.strip()]
                if user_id in ids:
                    result.append(d)
            return result

    def mark_task_done(self, task_id: int):
        with self._conn() as conn:
            conn.execute(
                "UPDATE tasks SET done=1, done_at=? WHERE id=?",
                (datetime.utcnow().isoformat(), task_id)
            )

    def delete_task(self, task_id: int):
        with self._conn() as conn:
            conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))

    def update_deadline(self, task_id: int, deadline: Optional[str]):
        with self._conn() as conn:
            conn.execute(
                "UPDATE tasks SET deadline=?, reminded=0 WHERE id=?",
                (deadline, task_id)
            )

    def archive_done_tasks(self):
        """Архивировать выполненные задачи (после вечернего отчёта)."""
        with self._conn() as conn:
            conn.execute(
                "UPDATE tasks SET archived=1 WHERE done=1 AND archived=0"
            )

    def get_tasks_due_soon(self, within_seconds: int = 3660) -> List[Dict]:
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT * FROM tasks
                WHERE done=0 AND archived=0
                  AND deadline IS NOT NULL
                  AND reminded=0
                  AND datetime(deadline) <= datetime('now', ? || ' seconds')
                  AND datetime(deadline) > datetime('now')
            """, (str(within_seconds),)).fetchall()
            return [dict(r) for r in rows]

    def mark_reminded(self, task_id: int):
        with self._conn() as conn:
            conn.execute("UPDATE tasks SET reminded=1 WHERE id=?", (task_id,))

    def get_users_with_active_tasks(self) -> List[int]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT assignee_ids FROM tasks WHERE done=0 AND archived=0"
            ).fetchall()
            ids = set()
            for r in rows:
                for x in (r[0] or "").split(","):
                    x = x.strip()
                    if x:
                        ids.add(int(x))
            return list(ids)

    def get_archive(self, limit: int = 50) -> List[Dict]:
        """Архив выполненных задач, свежие сначала."""
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT * FROM tasks
                WHERE archived=1
                ORDER BY done_at DESC
                LIMIT ?
            """, (limit,)).fetchall()
            return [dict(r) for r in rows]
