import os
import pg8000.native
from datetime import datetime
from typing import Optional, List, Dict
from urllib.parse import urlparse

def _conn():
    url = urlparse(os.environ["DATABASE_URL"])
    return pg8000.native.Connection(
        host=url.hostname,
        port=url.port or 5432,
        database=url.path.lstrip("/"),
        user=url.username,
        password=url.password,
        ssl_context=True
    )

def init_db():
    con = _conn()
    con.run("""
        CREATE TABLE IF NOT EXISTS users (
            user_id    BIGINT PRIMARY KEY,
            name       TEXT NOT NULL,
            username   TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    con.run("""
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
        )
    """)
    con.run("""
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
        )
    """)
    con.close()

def _rows_to_dicts(rows, columns):
    return [dict(zip(columns, row)) for row in rows]

class Database:
    def __init__(self):
        init_db()

    # ── Users ──────────────────────────────────────────────────────────────

    def register_user(self, user_id: int, name: str, username: Optional[str]):
        con = _conn()
        con.run("""
            INSERT INTO users (user_id, name, username) VALUES (:uid, :name, :un)
            ON CONFLICT (user_id) DO UPDATE SET name=EXCLUDED.name, username=EXCLUDED.username
        """, uid=user_id, name=name, un=username)
        con.close()

    def get_user(self, user_id: int) -> Optional[Dict]:
        con = _conn()
        rows = con.run("SELECT user_id,name,username FROM users WHERE user_id=:uid", uid=user_id)
        con.close()
        if not rows:
            return None
        return {"user_id": rows[0][0], "name": rows[0][1], "username": rows[0][2]}

    def get_team_members(self) -> List[Dict]:
        con = _conn()
        rows = con.run("SELECT user_id,name,username FROM users ORDER BY name")
        con.close()
        return [{"user_id": r[0], "name": r[1], "username": r[2]} for r in rows]

    # ── Tasks ──────────────────────────────────────────────────────────────

    def create_task(self, title, assignee_ids, assignee_name, deadline) -> int:
        ids_str = ",".join(str(i) for i in assignee_ids)
        con = _conn()
        rows = con.run("""
            INSERT INTO tasks (title,assignee_ids,assignee_name,deadline)
            VALUES (:t,:ids,:an,:dl) RETURNING id
        """, t=title, ids=ids_str, an=assignee_name, dl=deadline)
        con.close()
        return rows[0][0]

    def get_task(self, task_id) -> Optional[Dict]:
        con = _conn()
        rows = con.run("""
            SELECT id,title,assignee_ids,assignee_name,deadline,done,done_at,reminded,archived
            FROM tasks WHERE id=:id
        """, id=task_id)
        con.close()
        if not rows:
            return None
        cols = ["id","title","assignee_ids","assignee_name","deadline","done","done_at","reminded","archived"]
        return dict(zip(cols, rows[0]))

    def _task_rows(self, rows):
        cols = ["id","title","assignee_ids","assignee_name","deadline","done","done_at","reminded","archived"]
        return [dict(zip(cols, r)) for r in rows]

    def get_all_tasks(self, done=False) -> List[Dict]:
        con = _conn()
        rows = con.run("""
            SELECT id,title,assignee_ids,assignee_name,deadline,done,done_at,reminded,archived
            FROM tasks WHERE done=:done AND archived=FALSE
            ORDER BY deadline ASC NULLS LAST, created_at ASC
        """, done=done)
        con.close()
        return self._task_rows(rows)

    def get_user_tasks(self, user_id, done=False) -> List[Dict]:
        all_tasks = self.get_all_tasks(done=done)
        result = []
        for t in all_tasks:
            ids = [int(x) for x in (t.get("assignee_ids") or "").split(",") if x.strip()]
            if user_id in ids:
                result.append(t)
        return result

    def mark_task_done(self, task_id):
        con = _conn()
        con.run("UPDATE tasks SET done=TRUE, done_at=:dt WHERE id=:id",
                dt=datetime.utcnow().isoformat(), id=task_id)
        con.close()

    def delete_task(self, task_id):
        con = _conn()
        con.run("DELETE FROM tasks WHERE id=:id", id=task_id)
        con.close()

    def update_deadline(self, task_id, deadline):
        con = _conn()
        con.run("UPDATE tasks SET deadline=:dl, reminded=FALSE WHERE id=:id", dl=deadline, id=task_id)
        con.close()

    def archive_done_tasks(self):
        con = _conn()
        con.run("UPDATE tasks SET archived=TRUE WHERE done=TRUE AND archived=FALSE")
        con.close()

    def get_archive(self, limit=50) -> List[Dict]:
        con = _conn()
        rows = con.run("""
            SELECT id,title,assignee_ids,assignee_name,deadline,done,done_at,reminded,archived
            FROM tasks WHERE archived=TRUE ORDER BY done_at DESC NULLS LAST LIMIT :lim
        """, lim=limit)
        con.close()
        return self._task_rows(rows)

    def get_tasks_due_soon(self, within_seconds=3660) -> List[Dict]:
        con = _conn()
        rows = con.run("""
            SELECT id,title,assignee_ids,assignee_name,deadline,done,done_at,reminded,archived
            FROM tasks
            WHERE done=FALSE AND archived=FALSE AND deadline IS NOT NULL AND reminded=FALSE
              AND deadline::timestamptz <= NOW() + (:sec || ' seconds')::interval
              AND deadline::timestamptz > NOW()
        """, sec=str(within_seconds))
        con.close()
        return self._task_rows(rows)

    def mark_reminded(self, task_id):
        con = _conn()
        con.run("UPDATE tasks SET reminded=TRUE WHERE id=:id", id=task_id)
        con.close()

    def get_users_with_active_tasks(self) -> List[int]:
        con = _conn()
        rows = con.run("SELECT assignee_ids FROM tasks WHERE done=FALSE AND archived=FALSE")
        con.close()
        ids = set()
        for r in rows:
            for x in (r[0] or "").split(","):
                x = x.strip()
                if x:
                    ids.add(int(x))
        return list(ids)

    # ── Calls ──────────────────────────────────────────────────────────────

    def create_call(self, title, assignee_ids, assignee_name, scheduled_at) -> int:
        ids_str = ",".join(str(i) for i in assignee_ids)
        con = _conn()
        rows = con.run("""
            INSERT INTO calls (title,assignee_ids,assignee_name,scheduled_at)
            VALUES (:t,:ids,:an,:sa) RETURNING id
        """, t=title, ids=ids_str, an=assignee_name, sa=scheduled_at)
        con.close()
        return rows[0][0]

    def _call_rows(self, rows):
        cols = ["id","title","assignee_ids","assignee_name","scheduled_at","reminded_1h","reminded_5m","done"]
        return [dict(zip(cols, r)) for r in rows]

    def get_call(self, call_id) -> Optional[Dict]:
        con = _conn()
        rows = con.run("""
            SELECT id,title,assignee_ids,assignee_name,scheduled_at,reminded_1h,reminded_5m,done
            FROM calls WHERE id=:id
        """, id=call_id)
        con.close()
        return self._call_rows(rows)[0] if rows else None

    def get_active_calls(self) -> List[Dict]:
        con = _conn()
        rows = con.run("""
            SELECT id,title,assignee_ids,assignee_name,scheduled_at,reminded_1h,reminded_5m,done
            FROM calls WHERE done=FALSE ORDER BY scheduled_at ASC
        """)
        con.close()
        return self._call_rows(rows)

    def delete_call(self, call_id):
        con = _conn()
        con.run("DELETE FROM calls WHERE id=:id", id=call_id)
        con.close()

    def get_calls_due_soon(self, from_sec, to_sec) -> List[Dict]:
        con = _conn()
        rows = con.run("""
            SELECT id,title,assignee_ids,assignee_name,scheduled_at,reminded_1h,reminded_5m,done
            FROM calls WHERE done=FALSE
              AND scheduled_at::timestamptz > NOW() + (:f || ' seconds')::interval
              AND scheduled_at::timestamptz <= NOW() + (:t || ' seconds')::interval
        """, f=str(from_sec), t=str(to_sec))
        con.close()
        return self._call_rows(rows)

    def mark_call_reminded_1h(self, call_id):
        con = _conn()
        con.run("UPDATE calls SET reminded_1h=TRUE WHERE id=:id", id=call_id)
        con.close()

    def mark_call_reminded_5m(self, call_id):
        con = _conn()
        con.run("UPDATE calls SET reminded_5m=TRUE WHERE id=:id", id=call_id)
        con.close()

    def mark_call_done(self, call_id):
        con = _conn()
        con.run("UPDATE calls SET done=TRUE WHERE id=:id", id=call_id)
        con.close()
