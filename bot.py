# v2.0
import logging
from datetime import datetime
import pytz

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)

from database import Database
from scheduler import Scheduler
from config import Config

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

db = Database()
config = Config()


# ── Меню ────────────────────────────────────────────────────────────────────

def manager_menu():
    return ReplyKeyboardMarkup(
        [[KeyboardButton("▪️ Добавить задачу"),  KeyboardButton("▪️ Все задачи")],
         [KeyboardButton("▪️ Управление"),        KeyboardButton("▪️ Команда")],
         [KeyboardButton("▪️ Отчёт команды"),     KeyboardButton("▪️ Чеклист задач")],
         [KeyboardButton("▪️ Архив задач")]],
        resize_keyboard=True
    )

def employee_menu():
    return ReplyKeyboardMarkup([[KeyboardButton("▪️ Мои задачи")]], resize_keyboard=True)


# ── Утилиты ─────────────────────────────────────────────────────────────────

def is_manager(user_id: int) -> bool:
    return user_id == config.MANAGER_ID

def fmt_deadline(task: dict) -> str:
    if not task.get("deadline"):
        return ""
    try:
        tz = pytz.timezone(config.TIMEZONE)
        dt = datetime.fromisoformat(task["deadline"]).astimezone(tz)
        return f"\n   до {dt.strftime('%d.%m %H:%M')}"
    except Exception:
        return ""

def format_task(task: dict, show_assignee: bool = True) -> str:
    status = "✅" if task["done"] else "📍"
    assignee_str = f"\n   {task['assignee_name']}" if show_assignee and task.get("assignee_name") else ""
    return f"{status} <b>{task['title']}</b>{assignee_str}{fmt_deadline(task)}"

def parse_deadline(text: str, tz):
    low = text.strip().lower()
    if low == "нет":
        return None, None
    try:
        dt = datetime.strptime(low, "%d.%m %H:%M").replace(year=datetime.now().year)
        return tz.localize(dt).isoformat(), low
    except ValueError:
        return "error", None


# ── /start ───────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db.register_user(user.id, user.first_name, user.username)
    if is_manager(user.id):
        text = f"Привет, {user.first_name}.\n\nТы подключена как менеджер.\nИспользуй меню внизу."
        markup = manager_menu()
    else:
        text = f"Привет, {user.first_name}.\n\nКаждое утро в 10:00 тебе будет приходить список задач.\nНажимай «Готово» под задачей когда выполнишь."
        markup = employee_menu()
    await update.message.reply_text(text, reply_markup=markup)


# ── Добавить задачу (несколько ответственных) ────────────────────────────────

async def add_task_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_manager(update.effective_user.id):
        return
    team = db.get_team_members()
    if not team:
        await update.message.reply_text("Нет сотрудников. Попроси команду написать /start.")
        return
    context.user_data.clear()
    context.user_data["new_task"] = {"assignee_ids": [], "assignee_names": []}
    context.user_data["state"] = "choosing_assignees"
    await _show_assignee_picker(update.message, context, team, edit=False)

async def _show_assignee_picker(msg_or_query, context, team=None, edit=False):
    if team is None:
        team = db.get_team_members()
    selected = context.user_data["new_task"].get("assignee_ids", [])
    buttons = []
    for m in team:
        mark = "✅ " if m["user_id"] in selected else ""
        buttons.append([InlineKeyboardButton(f"{mark}{m['name']}", callback_data=f"pick_{m['user_id']}")])
    buttons.append([InlineKeyboardButton("➡️ Готово, ввести задачу", callback_data="pick_done")])
    text = "▪️ <b>Новая задача</b>\n\nВыбери ответственных (можно несколько):"
    if selected:
        names = context.user_data["new_task"].get("assignee_names", [])
        text += f"\n\nВыбрано: {', '.join(names)}"
    markup = InlineKeyboardMarkup(buttons)
    if edit:
        await msg_or_query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")
    else:
        await msg_or_query.reply_text(text, reply_markup=markup, parse_mode="HTML")

async def pick_assignee_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "pick_done":
        if not context.user_data.get("new_task", {}).get("assignee_ids"):
            await query.answer("Выбери хотя бы одного!", show_alert=True)
            return
        context.user_data["state"] = "waiting_title"
        names = ", ".join(context.user_data["new_task"]["assignee_names"])
        await query.edit_message_text(
            f"Назначаешь: <b>{names}</b>\n\nНапиши название задачи:",
            parse_mode="HTML"
        )
        return

    uid = int(data.split("_")[1])
    user = db.get_user(uid)
    task_data = context.user_data["new_task"]
    ids = task_data.get("assignee_ids", [])
    names = task_data.get("assignee_names", [])

    if uid in ids:
        idx = ids.index(uid)
        ids.pop(idx)
        names.pop(idx)
    else:
        ids.append(uid)
        names.append(user["name"])

    task_data["assignee_ids"] = ids
    task_data["assignee_names"] = names
    context.user_data["new_task"] = task_data
    await _show_assignee_picker(query, context, edit=True)


# ── Управление задачами (изменить дедлайн / удалить) ────────────────────────

async def manage_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_manager(update.effective_user.id):
        return
    tasks = db.get_all_tasks(done=False)
    if not tasks:
        await update.message.reply_text("Активных задач нет.")
        return
    await update.message.reply_text("▪️ <b>Управление задачами</b>\n\nВыбери задачу:", parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(f"📍 {t['title'][:40]}", callback_data=f"mgr_{t['id']}")]
            for t in tasks
        ])
    )

async def mgr_task_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    task_id = int(query.data.split("_")[1])
    task = db.get_task(task_id)
    if not task:
        await query.edit_message_text("Задача не найдена.")
        return
    dl = fmt_deadline(task).strip() or "нет"
    await query.edit_message_text(
        f"▪️ <b>{task['title']}</b>\n{task['assignee_name']}\nДедлайн: {dl}\n\nЧто сделать?",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✏️ Изменить дедлайн", callback_data=f"chdeadline_{task_id}")],
            [InlineKeyboardButton("🗑 Удалить задачу",    callback_data=f"deltask_{task_id}")],
            [InlineKeyboardButton("← Назад",              callback_data="mgr_back")],
        ])
    )

async def mgr_back_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tasks = db.get_all_tasks(done=False)
    if not tasks:
        await query.edit_message_text("Активных задач нет.")
        return
    await query.edit_message_text("▪️ <b>Управление задачами</b>\n\nВыбери задачу:", parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(f"📍 {t['title'][:40]}", callback_data=f"mgr_{t['id']}")]
            for t in tasks
        ])
    )

async def change_deadline_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    task_id = int(query.data.split("_")[1])
    context.user_data["state"] = "changing_deadline"
    context.user_data["edit_task_id"] = task_id
    await query.edit_message_text(
        "Введи новый дедлайн: <code>ДД.ММ ЧЧ:ММ</code>\nИли напиши <b>нет</b> чтобы убрать.",
        parse_mode="HTML"
    )

async def delete_task_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    task_id = int(query.data.split("_")[1])
    task = db.get_task(task_id)
    await query.edit_message_text(
        f"Удалить задачу <b>{task['title']}</b>?\nЭто действие нельзя отменить.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Да, удалить", callback_data=f"confirmdelete_{task_id}")],
            [InlineKeyboardButton("← Отмена",       callback_data=f"mgr_{task_id}")],
        ])
    )

async def confirm_delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    task_id = int(query.data.split("_")[1])
    task = db.get_task(task_id)
    title = task["title"] if task else "?"
    db.delete_task(task_id)
    await query.edit_message_text(f"🗑 Задача «{title}» удалена.")


# ── Командный отчёт по запросу ───────────────────────────────────────────────

async def team_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_manager(update.effective_user.id):
        return
    team = db.get_team_members()
    if not team:
        await update.message.reply_text("Команда пуста.")
        return
    tz = pytz.timezone(config.TIMEZONE)
    today = datetime.now(tz).strftime("%d.%m.%Y")
    text = f"▪️ <b>Отчёт команды — {today}</b>\n\n"
    total_done = total_open = 0
    for m in team:
        done = db.get_user_tasks(m["user_id"], done=True)
        active = db.get_user_tasks(m["user_id"], done=False)
        if not done and not active:
            continue
        total_done += len(done)
        total_open += len(active)
        text += f"<b>{m['name']}</b>\n"
        for t in done:
            text += f"   ✅ {t['title']}\n"
        for t in active:
            dl = fmt_deadline(t).replace("\n   до ", " · до ") if t.get("deadline") else ""
            text += f"   📍 {t['title']}{dl}\n"
        text += "\n"
    text += f"Итого: ✅ {total_done} выполнено  ▪️ {total_open} в работе"
    await update.message.reply_text(text, parse_mode="HTML")


# ── Чеклист ──────────────────────────────────────────────────────────────────

async def send_checklist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_manager(update.effective_user.id):
        return
    tasks = db.get_all_tasks(done=False)
    if not tasks:
        await update.message.reply_text("Активных задач нет.")
        return
    tz = pytz.timezone(config.TIMEZONE)
    today = datetime.now(tz).strftime("%d.%m.%Y")
    text = f"▪️ <b>Чеклист — {today}</b>\n\n"
    buttons = []
    for t in tasks:
        dl = fmt_deadline(t).replace("\n   до ", " · до ") if t.get("deadline") else ""
        text += f"📍 {t['title']}\n   {t['assignee_name']}{dl}\n\n"
        buttons.append([InlineKeyboardButton(f"✅ {t['title'][:35]}", callback_data=f"done_{t['id']}")])
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons) if buttons else None, parse_mode="HTML")


# ── Списки ───────────────────────────────────────────────────────────────────

async def list_all_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_manager(update.effective_user.id):
        return
    tasks = db.get_all_tasks(done=False)
    if not tasks:
        await update.message.reply_text("Активных задач нет.")
        return
    text = "▪️ <b>Активные задачи</b>\n\n"
    for t in tasks:
        text += format_task(t) + "\n\n"
    await update.message.reply_text(text, parse_mode="HTML")

async def list_team(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_manager(update.effective_user.id):
        return
    team = db.get_team_members()
    if not team:
        await update.message.reply_text("Команда пуста.")
        return
    text = "▪️ <b>Команда</b>\n\n"
    for m in team:
        un = f"  @{m['username']}" if m.get("username") else ""
        text += f"▪️ {m['name']}{un}\n"
    await update.message.reply_text(text, parse_mode="HTML")

async def my_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    tasks = db.get_user_tasks(user_id, done=False)
    if not tasks:
        await update.message.reply_text("Активных задач нет.")
        return
    await update.message.reply_text("▪️ <b>Твои задачи:</b>", parse_mode="HTML")
    for t in tasks:
        await update.message.reply_text(
            format_task(t, show_assignee=False),
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Готово", callback_data=f"done_{t['id']}")]]),
            parse_mode="HTML"
        )


# ── Кнопка «Готово» ──────────────────────────────────────────────────────────

async def mark_done_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    task_id = int(query.data.split("_")[1])
    task = db.get_task(task_id)
    if not task:
        await query.edit_message_text("Задача не найдена.")
        return
    db.mark_task_done(task_id)
    await query.edit_message_text(f"✅ <b>{task['title']}</b>\n<i>Выполнено</i>", parse_mode="HTML")
    if config.MANAGER_ID:
        user = db.get_user(query.from_user.id)
        name = user["name"] if user else query.from_user.first_name
        try:
            await context.bot.send_message(config.MANAGER_ID,
                f"✅ <b>{task['title']}</b>\n{name} отметил(а) выполнение.", parse_mode="HTML")
        except Exception as e:
            logger.error(f"Ошибка уведомления менеджера: {e}")


# ── Обработка текста ─────────────────────────────────────────────────────────

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    state = context.user_data.get("state")

    # Меню
    if text == "▪️ Добавить задачу":  return await add_task_start(update, context)
    if text == "▪️ Все задачи":        return await list_all_tasks(update, context)
    if text == "▪️ Команда":           return await list_team(update, context)
    if text == "▪️ Мои задачи":        return await my_tasks(update, context)
    if text == "▪️ Чеклист задач":     return await send_checklist(update, context)
    if text == "▪️ Отчёт команды":     return await team_report(update, context)
    if text == "▪️ Управление":        return await manage_tasks(update, context)
    if text == "▪️ Архив задач":          return await show_archive(update, context)

    # Диалог создания задачи
    if state == "waiting_title":
        context.user_data["new_task"]["title"] = text
        context.user_data["state"] = "waiting_deadline"
        await update.message.reply_text(
            "Укажи дедлайн: <code>ДД.ММ ЧЧ:ММ</code>\nНапример: <code>25.06 18:00</code>\n\nИли напиши <b>нет</b>.",
            parse_mode="HTML"
        )

    elif state == "waiting_deadline":
        tz = pytz.timezone(config.TIMEZONE)
        deadline, dl_display = parse_deadline(text, tz)
        if deadline == "error":
            await update.message.reply_text("Неверный формат. Попробуй: <code>25.06 18:00</code>", parse_mode="HTML")
            return
        task_data = context.user_data["new_task"]
        db.create_task(
            title=task_data["title"],
            assignee_ids=task_data["assignee_ids"],
            assignee_name=", ".join(task_data["assignee_names"]),
            deadline=deadline
        )
        context.user_data.clear()
        await update.message.reply_text(
            f"▪️ Задача создана\n\n<b>{task_data['title']}</b>\n"
            f"{', '.join(task_data['assignee_names'])}\n"
            f"Дедлайн: {dl_display or 'нет'}\n\nСотрудники увидят её в утреннем списке.",
            parse_mode="HTML"
        )

    elif state == "changing_deadline":
        tz = pytz.timezone(config.TIMEZONE)
        deadline, dl_display = parse_deadline(text, tz)
        if deadline == "error":
            await update.message.reply_text("Неверный формат. Попробуй: <code>25.06 18:00</code>", parse_mode="HTML")
            return
        task_id = context.user_data.get("edit_task_id")
        db.update_deadline(task_id, deadline)
        context.user_data.clear()
        await update.message.reply_text(f"▪️ Дедлайн обновлён: {dl_display or 'убран'}")

    else:
        await update.message.reply_text("Используй меню внизу.")


# ── Архив ────────────────────────────────────────────────────────────────────

async def show_archive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_manager(update.effective_user.id):
        return
    tasks = db.get_archive()
    if not tasks:
        await update.message.reply_text("Архив пуст.")
        return

    tz = pytz.timezone(config.TIMEZONE)
    from collections import defaultdict
    groups = defaultdict(list)
    for t in tasks:
        try:
            dt = datetime.fromisoformat(t["done_at"]).astimezone(tz)
            day = dt.strftime("%d.%m.%Y")
        except Exception:
            day = "Дата неизвестна"
        groups[day].append(t)

    text = "▪️ <b>Архив выполненных задач</b>\n\n"
    for day, day_tasks in groups.items():
        text += f"<b>{day}</b>\n"
        for t in day_tasks:
            text += f"   ✅ {t['title']}\n   {t['assignee_name']}\n"
        text += "\n"

    # Telegram ограничивает сообщения 4096 символами
    if len(text) > 4000:
        text = text[:4000] + "\n\n<i>...показаны последние 50 задач</i>"

    await update.message.reply_text(text, parse_mode="HTML")


# ── Запуск ───────────────────────────────────────────────────────────────────

def main():
    app = Application.builder().token(config.BOT_TOKEN).build()

    app.add_handler(CommandHandler("start",     start))
    app.add_handler(CommandHandler("task",      add_task_start))
    app.add_handler(CommandHandler("tasks",     list_all_tasks))
    app.add_handler(CommandHandler("team",      list_team))
    app.add_handler(CommandHandler("mytasks",   my_tasks))
    app.add_handler(CommandHandler("report",    team_report))
    app.add_handler(CommandHandler("checklist", send_checklist))
    app.add_handler(CommandHandler("manage",    manage_tasks))
    app.add_handler(CommandHandler("archive",   show_archive))

    app.add_handler(CallbackQueryHandler(pick_assignee_callback,  pattern="^pick_"))
    app.add_handler(CallbackQueryHandler(mark_done_callback,      pattern="^done_"))
    app.add_handler(CallbackQueryHandler(mgr_task_callback,       pattern=r"^mgr_d+"))
    app.add_handler(CallbackQueryHandler(mgr_back_callback,       pattern="^mgr_back"))
    app.add_handler(CallbackQueryHandler(change_deadline_callback,pattern="^chdeadline_"))
    app.add_handler(CallbackQueryHandler(delete_task_callback,    pattern="^deltask_"))
    app.add_handler(CallbackQueryHandler(confirm_delete_callback, pattern="^confirmdelete_"))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    Scheduler(app, db, config).start()
    logger.info("Бот запущен!")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()

