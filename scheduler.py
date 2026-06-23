import logging
from datetime import datetime, time
import pytz
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

logger = logging.getLogger(__name__)


class Scheduler:
    def __init__(self, app, db, config):
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
        logger.info("Планировщик запущен")

    # ── Утро: каждому его задачи одним сообщением ─────────────────────────

    async def _morning_job(self, context):
        from banners import banner_morning
        tz = pytz.timezone(self.config.TIMEZONE)
        user_ids = self.db.get_users_with_active_tasks()

        for user_id in user_ids:
            tasks = self.db.get_user_tasks(user_id, done=False)
            if not tasks:
                continue
            try:
                lines = []
                for t in tasks:
                    dl = ""
                    if t["deadline"]:
                        try:
                            dt = datetime.fromisoformat(str(t["deadline"])).astimezone(tz)
                            dl = f" — до {dt.strftime('%d.%m %H:%M')}"
                        except Exception:
                            pass
                    lines.append(f"📍 {t['title']}{dl}")

                text = "▪️ <b>Твои задачи на сегодня:</b>\n\n" + "\n".join(lines)
                buttons = [
                    [InlineKeyboardButton(f"✅ {t['title'][:35]}", callback_data=f"done_{t['id']}")]
                    for t in tasks
                ]
                await context.bot.send_photo(user_id, photo=banner_morning())
                await context.bot.send_message(
                    user_id, text,
                    reply_markup=InlineKeyboardMarkup(buttons),
                    parse_mode="HTML"
                )
                logger.info(f"Утренняя сводка → {user_id}")
            except Exception as e:
                logger.error(f"Утро ошибка {user_id}: {e}")

    # ── Вечер: каждому его итог + менеджеру общий ─────────────────────────

    async def _evening_job(self, context):
        from banners import banner_report
        try:
            tz = pytz.timezone(self.config.TIMEZONE)
            team = self.db.get_team_members()
            if not team:
                return

            today = datetime.now(tz).strftime("%d.%m.%Y")
            total_done = total_open = 0

            # Каждому сотруднику — его личный итог
            for member in team:
                done_tasks = self.db.get_user_tasks(member["user_id"], done=True)
                open_tasks = self.db.get_user_tasks(member["user_id"], done=False)
                if not done_tasks and not open_tasks:
                    continue

                total_done += len(done_tasks)
                total_open += len(open_tasks)

                text = f"▪️ <b>Твои итоги — {today}</b>\n\n"
                if done_tasks:
                    text += "Выполнено:\n"
                    for t in done_tasks:
                        text += f"   ✅ {t['title']}\n"
                    text += "\n"
                if open_tasks:
                    text += "Не выполнено:\n"
                    for t in open_tasks:
                        dl = ""
                        if t["deadline"]:
                            try:
                                dt = datetime.fromisoformat(str(t["deadline"])).astimezone(tz)
                                dl = f" (до {dt.strftime('%d.%m %H:%M')})"
                            except Exception:
                                pass
                        text += f"   📍 {t['title']}{dl}\n"

                try:
                    await context.bot.send_photo(member["user_id"], photo=banner_report())
                    await context.bot.send_message(member["user_id"], text, parse_mode="HTML")
                except Exception as e:
                    logger.error(f"Вечерний отчёт сотрудник {member['user_id']}: {e}")

            # Менеджеру — общий по всем
            manager_text = f"▪️ <b>Отчёт команды — {today}</b>\n\n"
            for member in team:
                done_tasks = self.db.get_user_tasks(member["user_id"], done=True)
                open_tasks = self.db.get_user_tasks(member["user_id"], done=False)
                if not done_tasks and not open_tasks:
                    continue
                manager_text += f"<b>{member['name']}</b>\n"
                for t in done_tasks:
                    manager_text += f"   ✅ {t['title']}\n"
                for t in open_tasks:
                    dl = ""
                    if t["deadline"]:
                        try:
                            dt = datetime.fromisoformat(str(t["deadline"])).astimezone(tz)
                            dl = f" (до {dt.strftime('%d.%m %H:%M')})"
                        except Exception:
                            pass
                    manager_text += f"   📍 {t['title']}{dl}\n"
                manager_text += "\n"

            manager_text += f"Итого: ✅ {total_done} выполнено  ▪️ {total_open} в работе"

            await context.bot.send_photo(self.config.MANAGER_ID, photo=banner_report())
            await context.bot.send_message(self.config.MANAGER_ID, manager_text, parse_mode="HTML")

            self.db.archive_done_tasks()
            logger.info("Вечерний отчёт отправлен всем")
        except Exception as e:
            logger.error(f"Вечер ошибка: {e}")

    # ── Напоминания ───────────────────────────────────────────────────────

    async def _reminder_job(self, context):
        await self._task_reminders(context)
        await self._call_reminders(context)

    async def _task_reminders(self, context):
        from banners import banner_reminder
        tasks = self.db.get_tasks_due_soon(within_seconds=3660)
        for task in tasks:
            try:
                tz = pytz.timezone(self.config.TIMEZONE)
                dt = datetime.fromisoformat(str(task["deadline"])).astimezone(tz)
                dl_str = dt.strftime("%H:%M")
                assignee_ids = [int(x) for x in (task.get("assignee_ids") or "").split(",") if x.strip()]
                buttons = [[InlineKeyboardButton("✅ Готово", callback_data=f"done_{task['id']}")]]

                for uid in assignee_ids:
                    await context.bot.send_photo(uid, photo=banner_reminder())
                    await context.bot.send_message(
                        uid,
                        f"▪️ <b>{task['title']}</b>\nДедлайн в <b>{dl_str}</b> — через час.",
                        reply_markup=InlineKeyboardMarkup(buttons),
                        parse_mode="HTML"
                    )
                self.db.mark_reminded(task["id"])
                await context.bot.send_message(
                    self.config.MANAGER_ID,
                    f"▪️ Скоро дедлайн: <b>{task['title']}</b>\n{task['assignee_name']} — в {dl_str}",
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.error(f"Напоминание задача {task['id']}: {e}")

    async def _call_reminders(self, context):
        from banners import banner_call
        for call in self.db.get_calls_due_soon(3600, 3900):
            if not call["reminded_1h"]:
                await self._send_call_reminder(context, call, "через час", banner_call)
                self.db.mark_call_reminded_1h(call["id"])

        for call in self.db.get_calls_due_soon(300, 600):
            if not call["reminded_5m"]:
                await self._send_call_reminder(context, call, "через 5 минут", banner_call)
                self.db.mark_call_reminded_5m(call["id"])
                self.db.mark_call_done(call["id"])

    async def _send_call_reminder(self, context, call, when_str, banner_fn):
        try:
            tz = pytz.timezone(self.config.TIMEZONE)
            dt = datetime.fromisoformat(str(call["scheduled_at"])).astimezone(tz)
            time_str = dt.strftime("%H:%M")
            assignee_ids = [int(x) for x in (call.get("assignee_ids") or "").split(",") if x.strip()]
            for uid in assignee_ids:
                await context.bot.send_photo(uid, photo=banner_fn())
                await context.bot.send_message(
                    uid,
                    f"▪️ <b>{call['title']}</b>\nНачало в <b>{time_str}</b> — {when_str}.",
                    parse_mode="HTML"
                )
        except Exception as e:
            logger.error(f"Напоминание созвон {call['id']}: {e}")
