import logging
from datetime import datetime, time
import pytz
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application

logger = logging.getLogger(__name__)


class Scheduler:
    def __init__(self, app: Application, db, config):
        self.app = app
        self.db = db
        self.config = config

    def start(self):
        jq = self.app.job_queue
        tz = pytz.timezone(self.config.TIMEZONE)

        mh, mm = self.config.MORNING_TIME
        jq.run_daily(self._morning_job, time=time(mh, mm, tzinfo=tz), name="morning")

        eh, em = self.config.EVENING_TIME
        jq.run_daily(self._evening_job, time=time(eh, em, tzinfo=tz), name="evening")

        jq.run_repeating(self._reminder_job, interval=300, first=60, name="reminders")

        logger.info("Планировщик запущен через JobQueue")

    async def _morning_job(self, context):
        from banners import banner_morning
        user_ids = self.db.get_users_with_active_tasks()
        for user_id in user_ids:
            tasks = self.db.get_user_tasks(user_id, done=False)
            if not tasks:
                continue
            try:
                tz = pytz.timezone(self.config.TIMEZONE)
                text = "▪️ <b>Твои задачи на сегодня:</b>\n\n"
                for t in tasks:
                    deadline_str = ""
                    if t["deadline"]:
                        try:
                            dt = datetime.fromisoformat(t["deadline"]).astimezone(tz)
                            deadline_str = f" — до {dt.strftime('%d.%m %H:%M')}"
                        except Exception:
                            pass
                    text += f"📍 {t['title']}{deadline_str}\n"

                buttons = [
                    [InlineKeyboardButton(f"✅ {t['title'][:30]}", callback_data=f"done_{t['id']}")]
                    for t in tasks if not t["done"]
                ]

                await context.bot.send_photo(user_id, photo=banner_morning())
                await context.bot.send_message(
                    user_id, text,
                    reply_markup=InlineKeyboardMarkup(buttons) if buttons else None,
                    parse_mode="HTML"
                )
                logger.info(f"Утренняя сводка → {user_id}")
            except Exception as e:
                logger.error(f"Ошибка утренней сводки для {user_id}: {e}")

    async def _reminder_job(self, context):
        from banners import banner_reminder
        tasks = self.db.get_tasks_due_soon(within_seconds=3660)
        for task in tasks:
            try:
                tz = pytz.timezone(self.config.TIMEZONE)
                dt = datetime.fromisoformat(task["deadline"]).astimezone(tz)
                deadline_str = dt.strftime("%H:%M")
                assignee_ids = [int(x) for x in (task.get("assignee_ids") or "").split(",") if x.strip()]
                buttons = [[InlineKeyboardButton("✅ Готово", callback_data=f"done_{task['id']}")]]

                for uid in assignee_ids:
                    await context.bot.send_photo(uid, photo=banner_reminder())
                    await context.bot.send_message(
                        uid,
                        f"▪️ <b>{task['title']}</b>\nДедлайн в <b>{deadline_str}</b> — через час.",
                        reply_markup=InlineKeyboardMarkup(buttons),
                        parse_mode="HTML"
                    )

                self.db.mark_reminded(task["id"])

                if self.config.MANAGER_ID:
                    await context.bot.send_message(
                        self.config.MANAGER_ID,
                        f"⏰ Скоро дедлайн: <b>{task['title']}</b>\n{task['assignee_name']} — в {deadline_str}",
                        parse_mode="HTML"
                    )
            except Exception as e:
                logger.error(f"Ошибка напоминания задача {task['id']}: {e}")

    async def _evening_job(self, context):
        from banners import banner_report
        try:
            team = self.db.get_team_members()
            if not team:
                return
            tz = pytz.timezone(self.config.TIMEZONE)
            today_str = datetime.now(tz).strftime("%d.%m.%Y")
            text = f"▪️ <b>Отчёт — {today_str}</b>\n\n"
            total_done = total_open = 0

            for member in team:
                done_tasks = self.db.get_user_tasks(member["user_id"], done=True)
                open_tasks = self.db.get_user_tasks(member["user_id"], done=False)
                if not done_tasks and not open_tasks:
                    continue
                total_done += len(done_tasks)
                total_open += len(open_tasks)
                text += f"<b>{member['name']}</b>\n"
                for t in done_tasks:
                    text += f"   ✅ {t['title']}\n"
                for t in open_tasks:
                    dl = ""
                    if t["deadline"]:
                        try:
                            dt = datetime.fromisoformat(t["deadline"]).astimezone(tz)
                            dl = f" (до {dt.strftime('%d.%m %H:%M')})"
                        except Exception:
                            pass
                    text += f"   📍 {t['title']}{dl}\n"
                text += "\n"

            text += f"Итого: ✅ {total_done} выполнено  ▪️ {total_open} в работе"

            await context.bot.send_photo(self.config.MANAGER_ID, photo=banner_report())
            await context.bot.send_message(self.config.MANAGER_ID, text, parse_mode="HTML")
            self.db.archive_done_tasks()
            logger.info("Вечерний отчёт отправлен")
        except Exception as e:
            logger.error(f"Ошибка вечернего отчёта: {e}")
