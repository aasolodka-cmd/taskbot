import asyncio
import logging
from datetime import datetime, time
import pytz
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from banners import banner_morning, banner_reminder, banner_report

logger = logging.getLogger(__name__)


class Scheduler:
    def __init__(self, app, db, config):
        self.app = app
        self.db = db
        self.config = config

    def start(self):
        loop = asyncio.get_event_loop()
        loop.create_task(self._morning_loop())
        loop.create_task(self._reminder_loop())
        loop.create_task(self._evening_loop())
        logger.info("Планировщик запущен")

    async def _morning_loop(self):
        """Каждый день в config.MORNING_TIME отправляет сводку команде."""
        tz = pytz.timezone(self.config.TIMEZONE)
        target_h, target_m = self.config.MORNING_TIME

        while True:
            now = datetime.now(tz)
            # Секунды до следующего срабатывания
            next_run = now.replace(hour=target_h, minute=target_m, second=0, microsecond=0)
            if now >= next_run:
                # Уже прошло — ждём до завтра
                next_run = next_run.replace(day=next_run.day + 1)

            wait_sec = (next_run - now).total_seconds()
            logger.info(f"Утренняя сводка через {wait_sec:.0f} сек")
            await asyncio.sleep(wait_sec)

            await self._send_morning_digests()

    async def _send_morning_digests(self):
        user_ids = self.db.get_users_with_active_tasks()
        for user_id in user_ids:
            tasks = self.db.get_user_tasks(user_id, done=False)
            if not tasks:
                continue
            try:
                # Баннер
                await self.app.bot.send_photo(user_id, photo=banner_morning())

                text = "☀️ <b>Твои задачи на сегодня:</b>\n\n"
                for t in tasks:
                    status = "✅" if t["done"] else "🔲"
                    deadline_str = ""
                    if t["deadline"]:
                        try:
                            tz = pytz.timezone(self.config.TIMEZONE)
                            dt = datetime.fromisoformat(t["deadline"]).astimezone(tz)
                            deadline_str = f" — до {dt.strftime('%d.%m %H:%M')}"
                        except Exception:
                            pass
                    text += f"{status} {t['title']}{deadline_str}\n"

                # Кнопки «Готово» для каждой задачи
                buttons = [
                    [InlineKeyboardButton(f"✅ {t['title'][:30]}", callback_data=f"done_{t['id']}")]
                    for t in tasks if not t["done"]
                ]

                await self.app.bot.send_message(
                    user_id,
                    text,
                    reply_markup=InlineKeyboardMarkup(buttons) if buttons else None,
                    parse_mode="HTML"
                )
                logger.info(f"Утренняя сводка отправлена → {user_id}")
            except Exception as e:
                logger.error(f"Ошибка утренней сводки для {user_id}: {e}")

    async def _reminder_loop(self):
        """Каждые 5 минут проверяет задачи с дедлайном через ~1 час."""
        while True:
            await asyncio.sleep(300)  # проверяем каждые 5 минут
            await self._send_reminders()

    async def _send_reminders(self):
        tasks = self.db.get_tasks_due_soon(within_seconds=3660)  # ~1 час + буфер
        for task in tasks:
            try:
                tz = pytz.timezone(self.config.TIMEZONE)
                dt = datetime.fromisoformat(task["deadline"]).astimezone(tz)
                deadline_str = dt.strftime("%H:%M")

                buttons = [[InlineKeyboardButton("✅ Готово", callback_data=f"done_{task['id']}")]]
                await self.app.bot.send_photo(task["assignee_id"], photo=banner_reminder())
                await self.app.bot.send_message(
                    task["assignee_id"],
                    f"⏰ <b>Напоминание!</b>\n\n"
                    f"Задача <b>{task['title']}</b>\n"
                    f"Дедлайн сегодня в <b>{deadline_str}</b> — через ~1 час!",
                    reply_markup=InlineKeyboardMarkup(buttons),
                    parse_mode="HTML"
                )
                self.db.mark_reminded(task["id"])

                # Уведомить менеджера тоже
                if self.config.MANAGER_ID:
                    await self.app.bot.send_message(
                        self.config.MANAGER_ID,
                        f"⏰ Скоро дедлайн!\n"
                        f"<b>{task['title']}</b>\n"
                        f"👤 {task['assignee_name']} — в {deadline_str}",
                        parse_mode="HTML"
                    )
            except Exception as e:
                logger.error(f"Ошибка напоминания для задачи {task['id']}: {e}")

    async def _evening_loop(self):
        """Каждый день в config.EVENING_TIME шлёт менеджеру итоговый отчёт."""
        tz = pytz.timezone(self.config.TIMEZONE)
        target_h, target_m = self.config.EVENING_TIME

        while True:
            now = datetime.now(tz)
            next_run = now.replace(hour=target_h, minute=target_m, second=0, microsecond=0)
            if now >= next_run:
                next_run = next_run.replace(day=next_run.day + 1)

            wait_sec = (next_run - now).total_seconds()
            logger.info(f"Вечерний отчёт через {wait_sec:.0f} сек")
            await asyncio.sleep(wait_sec)

            await self._send_evening_report()

    async def _send_evening_report(self):
        try:
            team = self.db.get_team_members()
            if not team:
                return

            tz = pytz.timezone(self.config.TIMEZONE)
            today_str = datetime.now(tz).strftime("%d.%m.%Y")

            text = f"📊 <b>Итоги дня — {today_str}</b>\n\n"
            total_done = 0
            total_open = 0

            for member in team:
                done_tasks = self.db.get_user_tasks(member["user_id"], done=True)
                open_tasks = self.db.get_user_tasks(member["user_id"], done=False)

                # Только если у человека вообще есть задачи
                if not done_tasks and not open_tasks:
                    continue

                total_done += len(done_tasks)
                total_open += len(open_tasks)

                text += f"👤 <b>{member['name']}</b>\n"

                if done_tasks:
                    for t in done_tasks:
                        text += f"  ✅ {t['title']}\n"
                if open_tasks:
                    for t in open_tasks:
                        deadline_str = ""
                        if t["deadline"]:
                            try:
                                dt = datetime.fromisoformat(t["deadline"]).astimezone(tz)
                                deadline_str = f" (до {dt.strftime('%d.%m %H:%M')})"
                            except Exception:
                                pass
                        text += f"  🔲 {t['title']}{deadline_str}\n"
                text += "\n"

            text += f"─────────────────\n"
            text += f"Итого: ✅ выполнено {total_done} | 🔲 осталось {total_open}"

            await self.app.bot.send_photo(self.config.MANAGER_ID, photo=banner_report())
            await self.app.bot.send_message(
                self.config.MANAGER_ID,
                text,
                parse_mode="HTML"
            )
            self.db.archive_done_tasks()
            logger.info("Вечерний отчёт отправлен, выполненные архивированы")
        except Exception as e:
            logger.error(f"Ошибка вечернего отчёта: {e}")
