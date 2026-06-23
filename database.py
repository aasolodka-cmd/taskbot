import os
from datetime import datetime
from typing import Optional, List, Dict
import psycopg2
from psycopg2.extras import RealDictCursor

DATABASE_URL = os.environ["DATABASE_URL"]

def _conn():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def init_db():
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id    BIGINT PRIMARY KEY,
                    name       TEXT NOT NULL,
                    username   TEXT,
                    created_at TIMESTAMP DEFAULT NOW()
                );

                CREATE TABLE IF NOT EXISTS tasks (
                    id            SERIAL PRIMARY KEY,
                    title         TEXT NOT NULL,
                    assignee_ids  TEXT NOT NULL DEFAULT '',
                    assignee_name TEXT NOT NULL DEFAULT '',
                    deadline      TEXT,
                    done          BOOLEAN DEFAULT FALSE,
                    done_at       TEXT,
                    reminded      BOOLEAN DEFAULT FALSE,
                    archived      BOOLEAN DEFAULT FALSE,
                    created_at    TIMESTAMP DEFAULT NOW()
                );

                CREATE TABLE IF NOT EXISTS calls (
                    id            SERIAL PRIMARY KEY,
                    title         TEXT NOT NULL,
                    assignee_ids  TEXT NOT NULL DEFAULT '',
                    assignee_name TEXT NOT NULL DEFAULT '',
                    scheduled_at  TEXT NOT NULL,
                    reminded_1h   BOOLEAN DEFAULT FALSE,
                    reminded_5m   BOOLEAN DEFAULT FALSE,
                    done          BOOLEAN DEFAULT FALSE,
                    created_at    TIMESTAMP DEFAULT NOW()
                );
            """)
        conn.commit()

class Database:
    def __init__(self):
        init_db()

    # ── Users ──────────────────────────────────────────────────────────────

    def register_user(self, user_id: int, name: str, username: Optional[str]):
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO users (user_id, name, username)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (user_id) DO UPDATE SET name=EXCLUDED.name, username=EXCLUDED.username
                """, (user_id, name, username))
            conn.commit()

    def get_user(self, user_id: int) -> Optional[Dict]:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM users WHERE user_id=%s", (user_id,))
                row = cur.fetchone()
                return dict(row) if row else None

    def get_team_members(self) -> List[Dict]:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM users ORDER BY name")
                return [dict(r) for r in cur.fetchall()]

    # ── Tasks ──────────────────────────────────────────────────────────────

    def create_task(self, title: str, assignee_ids: List[int],
                    assignee_name: str, deadline: Optional[str]) -> int:
        ids_str = ",".join(str(i) for i in assignee_ids)
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO tasks (title, assignee_ids, assignee_name, deadline)
                    VALUES (%s, %s, %s, %s) RETURNING id
                """, (title, ids_str, assignee_name, deadline))
                task_id = cur.fetchone()["id"]
            conn.commit()
            return task_id

    def get_task(self, task_id: int) -> Optional[Dict]:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM tasks WHERE id=%s", (task_id,))
                row = cur.fetchone()
                return dict(row) if row else None

    def get_all_tasks(self, done: bool = False) -> List[Dict]:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT * FROM tasks WHERE done=%s AND archived=FALSE
                    ORDER BY deadline ASC NULLS LAST, created_at ASC
                """, (done,))
                return [dict(r) for r in cur.fetchall()]

    def get_user_tasks(self, user_id: int, done: bool = False) -> List[Dict]:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT * FROM tasks WHERE done=%s AND archived=FALSE
                    ORDER BY deadline ASC NULLS LAST
                """, (done,))
                result = []
                for r in cur.fetchall():
                    d = dict(r)
                    ids = [int(x) for x in (d.get("assignee_ids") or "").split(",") if x.strip()]
                    if user_id in ids:
                        result.append(d)
                return result

    def mark_task_done(self, task_id: int):
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE tasks SET done=TRUE, done_at=%s WHERE id=%s",
                    (datetime.utcnow().isoformat(), task_id)
                )
            conn.commit()

    def delete_task(self, task_id: int):
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM tasks WHERE id=%s", (task_id,))
            conn.commit()

    def update_deadline(self, task_id: int, deadline: Optional[str]):
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE tasks SET deadline=%s, reminded=FALSE WHERE id=%s",
                    (deadline, task_id)
                )
            conn.commit()

    def archive_done_tasks(self):
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE tasks SET archived=TRUE WHERE done=TRUE AND archived=FALSE")
            conn.commit()

    def get_archive(self, limit: int = 50) -> List[Dict]:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT * FROM tasks WHERE archived=TRUE
                    ORDER BY done_at DESC NULLS LAST LIMIT %s
                """, (limit,))
                return [dict(r) for r in cur.fetchall()]

    def get_tasks_due_soon(self, within_seconds: int = 3660) -> List[Dict]:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT * FROM tasks
                    WHERE done=FALSE AND archived=FALSE
                      AND deadline IS NOT NULL
                      AND reminded=FALSE
                      AND deadline::timestamp <= NOW() + (%s || ' seconds')::interval
                      AND deadline::timestamp > NOW()
                """, (str(within_seconds),))
                return [dict(r) for r in cur.fetchall()]

    def mark_reminded(self, task_id: int):
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE tasks SET reminded=TRUE WHERE id=%s", (task_id,))
            conn.commit()

    def get_users_with_active_tasks(self) -> List[int]:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT assignee_ids FROM tasks WHERE done=FALSE AND archived=FALSE")
                ids = set()
                for r in cur.fetchall():
                    for x in (r["assignee_ids"] or "").split(","):
                        x = x.strip()
                        if x:
                            ids.add(int(x))
                return list(ids)

    # ── Calls ──────────────────────────────────────────────────────────────

    def create_call(self, title: str, assignee_ids: List[int],
                    assignee_name: str, scheduled_at: str) -> int:
        ids_str = ",".join(str(i) for i in assignee_ids)
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO calls (title, assignee_ids, assignee_name, scheduled_at)
                    VALUES (%s, %s, %s, %s) RETURNING id
                """, (title, ids_str, assignee_name, scheduled_at))
                call_id = cur.fetchone()["id"]
            conn.commit()
            return call_id

    def get_call(self, call_id: int) -> Optional[Dict]:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM calls WHERE id=%s", (call_id,))
                row = cur.fetchone()
                return dict(row) if row else None

    def get_active_calls(self) -> List[Dict]:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT * FROM calls WHERE done=FALSE
                    ORDER BY scheduled_at ASC
                """)
                return [dict(r) for r in cur.fetchall()]

    def delete_call(self, call_id: int):
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM calls WHERE id=%s", (call_id,))
            conn.commit()

    def get_calls_due_soon(self, from_sec: int, to_sec: int) -> List[Dict]:
        """Созвоны у которых scheduled_at через from_sec..to_sec секунд."""
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT * FROM calls
                    WHERE done=FALSE
                      AND scheduled_at::timestamp > NOW() + (%s || ' seconds')::interval
                      AND scheduled_at::timestamp <= NOW() + (%s || ' seconds')::interval
                """, (str(from_sec), str(to_sec)))
                return [dict(r) for r in cur.fetchall()]

    def mark_call_reminded_1h(self, call_id: int):
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE calls SET reminded_1h=TRUE WHERE id=%s", (call_id,))
            conn.commit()

    def mark_call_reminded_5m(self, call_id: int):
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE calls SET reminded_5m=TRUE WHERE id=%s", (call_id,))
            conn.commit()

    def mark_call_done(self, call_id: int):
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE calls SET done=TRUE WHERE id=%s", (call_id,))
            conn.commit()
