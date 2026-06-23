import os
import asyncio
import asyncpg
from datetime import datetime
from typing import Optional, List, Dict

DATABASE_URL = os.environ["DATABASE_URL"]

# Глобальный пул соединений
_pool = None

async def get_pool():
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(DATABASE_URL)
    return _pool

async def init_db():
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
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

class Database:
    def __init__(self):
        # Запускаем init в event loop когда он будет готов
        pass

    async def _init(self):
        await init_db()

    async def _pool(self):
        return await get_pool()

    # ── Users ──────────────────────────────────────────────────────────────

    def register_user(self, user_id: int, name: str, username: Optional[str]):
        asyncio.get_event_loop().run_until_complete(self._register_user(user_id, name, username))

    async def _register_user(self, user_id, name, username):
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO users (user_id, name, username) VALUES ($1, $2, $3)
                ON CONFLICT (user_id) DO UPDATE SET name=EXCLUDED.name, username=EXCLUDED.username
            """, user_id, name, username)

    def get_user(self, user_id: int) -> Optional[Dict]:
        return asyncio.get_event_loop().run_until_complete(self._get_user(user_id))

    async def _get_user(self, user_id):
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM users WHERE user_id=$1", user_id)
            return dict(row) if row else None

    def get_team_members(self) -> List[Dict]:
        return asyncio.get_event_loop().run_until_complete(self._get_team_members())

    async def _get_team_members(self):
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM users ORDER BY name")
            return [dict(r) for r in rows]

    # ── Tasks ──────────────────────────────────────────────────────────────

    def create_task(self, title, assignee_ids, assignee_name, deadline) -> int:
        return asyncio.get_event_loop().run_until_complete(
            self._create_task(title, assignee_ids, assignee_name, deadline))

    async def _create_task(self, title, assignee_ids, assignee_name, deadline):
        ids_str = ",".join(str(i) for i in assignee_ids)
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("""
                INSERT INTO tasks (title, assignee_ids, assignee_name, deadline)
                VALUES ($1,$2,$3,$4) RETURNING id
            """, title, ids_str, assignee_name, deadline)
            return row["id"]

    def get_task(self, task_id) -> Optional[Dict]:
        return asyncio.get_event_loop().run_until_complete(self._get_task(task_id))

    async def _get_task(self, task_id):
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM tasks WHERE id=$1", task_id)
            return dict(row) if row else None

    def get_all_tasks(self, done=False) -> List[Dict]:
        return asyncio.get_event_loop().run_until_complete(self._get_all_tasks(done))

    async def _get_all_tasks(self, done):
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT * FROM tasks WHERE done=$1 AND archived=FALSE
                ORDER BY deadline ASC NULLS LAST, created_at ASC
            """, done)
            return [dict(r) for r in rows]

    def get_user_tasks(self, user_id, done=False) -> List[Dict]:
        return asyncio.get_event_loop().run_until_complete(self._get_user_tasks(user_id, done))

    async def _get_user_tasks(self, user_id, done):
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT * FROM tasks WHERE done=$1 AND archived=FALSE
                ORDER BY deadline ASC NULLS LAST
            """, done)
            result = []
            for r in rows:
                d = dict(r)
                ids = [int(x) for x in (d.get("assignee_ids") or "").split(",") if x.strip()]
                if user_id in ids:
                    result.append(d)
            return result

    def mark_task_done(self, task_id):
        asyncio.get_event_loop().run_until_complete(self._mark_task_done(task_id))

    async def _mark_task_done(self, task_id):
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE tasks SET done=TRUE, done_at=$1 WHERE id=$2",
                datetime.utcnow().isoformat(), task_id)

    def delete_task(self, task_id):
        asyncio.get_event_loop().run_until_complete(self._delete_task(task_id))

    async def _delete_task(self, task_id):
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM tasks WHERE id=$1", task_id)

    def update_deadline(self, task_id, deadline):
        asyncio.get_event_loop().run_until_complete(self._update_deadline(task_id, deadline))

    async def _update_deadline(self, task_id, deadline):
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE tasks SET deadline=$1, reminded=FALSE WHERE id=$2", deadline, task_id)

    def archive_done_tasks(self):
        asyncio.get_event_loop().run_until_complete(self._archive_done_tasks())

    async def _archive_done_tasks(self):
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute("UPDATE tasks SET archived=TRUE WHERE done=TRUE AND archived=FALSE")

    def get_archive(self, limit=50) -> List[Dict]:
        return asyncio.get_event_loop().run_until_complete(self._get_archive(limit))

    async def _get_archive(self, limit):
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT * FROM tasks WHERE archived=TRUE
                ORDER BY done_at DESC NULLS LAST LIMIT $1
            """, limit)
            return [dict(r) for r in rows]

    def get_tasks_due_soon(self, within_seconds=3660) -> List[Dict]:
        return asyncio.get_event_loop().run_until_complete(self._get_tasks_due_soon(within_seconds))

    async def _get_tasks_due_soon(self, within_seconds):
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT * FROM tasks
                WHERE done=FALSE AND archived=FALSE
                  AND deadline IS NOT NULL AND reminded=FALSE
                  AND deadline::timestamp <= NOW() + ($1 || ' seconds')::interval
                  AND deadline::timestamp > NOW()
            """, str(within_seconds))
            return [dict(r) for r in rows]

    def mark_reminded(self, task_id):
        asyncio.get_event_loop().run_until_complete(self._mark_reminded(task_id))

    async def _mark_reminded(self, task_id):
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute("UPDATE tasks SET reminded=TRUE WHERE id=$1", task_id)

    def get_users_with_active_tasks(self) -> List[int]:
        return asyncio.get_event_loop().run_until_complete(self._get_users_with_active_tasks())

    async def _get_users_with_active_tasks(self):
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT assignee_ids FROM tasks WHERE done=FALSE AND archived=FALSE")
            ids = set()
            for r in rows:
                for x in (r["assignee_ids"] or "").split(","):
                    x = x.strip()
                    if x:
                        ids.add(int(x))
            return list(ids)

    # ── Calls ──────────────────────────────────────────────────────────────

    def create_call(self, title, assignee_ids, assignee_name, scheduled_at) -> int:
        return asyncio.get_event_loop().run_until_complete(
            self._create_call(title, assignee_ids, assignee_name, scheduled_at))

    async def _create_call(self, title, assignee_ids, assignee_name, scheduled_at):
        ids_str = ",".join(str(i) for i in assignee_ids)
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("""
                INSERT INTO calls (title, assignee_ids, assignee_name, scheduled_at)
                VALUES ($1,$2,$3,$4) RETURNING id
            """, title, ids_str, assignee_name, scheduled_at)
            return row["id"]

    def get_call(self, call_id) -> Optional[Dict]:
        return asyncio.get_event_loop().run_until_complete(self._get_call(call_id))

    async def _get_call(self, call_id):
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM calls WHERE id=$1", call_id)
            return dict(row) if row else None

    def get_active_calls(self) -> List[Dict]:
        return asyncio.get_event_loop().run_until_complete(self._get_active_calls())

    async def _get_active_calls(self):
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM calls WHERE done=FALSE ORDER BY scheduled_at ASC")
            return [dict(r) for r in rows]

    def delete_call(self, call_id):
        asyncio.get_event_loop().run_until_complete(self._delete_call(call_id))

    async def _delete_call(self, call_id):
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM calls WHERE id=$1", call_id)

    def get_calls_due_soon(self, from_sec, to_sec) -> List[Dict]:
        return asyncio.get_event_loop().run_until_complete(self._get_calls_due_soon(from_sec, to_sec))

    async def _get_calls_due_soon(self, from_sec, to_sec):
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT * FROM calls WHERE done=FALSE
                  AND scheduled_at::timestamp > NOW() + ($1 || ' seconds')::interval
                  AND scheduled_at::timestamp <= NOW() + ($2 || ' seconds')::interval
            """, str(from_sec), str(to_sec))
            return [dict(r) for r in rows]

    def mark_call_reminded_1h(self, call_id):
        asyncio.get_event_loop().run_until_complete(self._mark_call_reminded_1h(call_id))

    async def _mark_call_reminded_1h(self, call_id):
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute("UPDATE calls SET reminded_1h=TRUE WHERE id=$1", call_id)

    def mark_call_reminded_5m(self, call_id):
        asyncio.get_event_loop().run_until_complete(self._mark_call_reminded_5m(call_id))

    async def _mark_call_reminded_5m(self, call_id):
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute("UPDATE calls SET reminded_5m=TRUE WHERE id=$1", call_id)

    def mark_call_done(self, call_id):
        asyncio.get_event_loop().run_until_complete(self._mark_call_done(call_id))

    async def _mark_call_done(self, call_id):
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute("UPDATE calls SET done=TRUE WHERE id=$1", call_id)
