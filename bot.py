# v3.0
import logging
from datetime import datetime
import pytz

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

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
         [KeyboardButton("▪️ Добавить созвон"),   KeyboardButton("▪️ Созвоны")],
         [KeyboardButton("▪️ Управление"),         KeyboardButton("▪️ Команда")],
         [KeyboardButton("▪️ Отчёт команды"),      KeyboardButton("▪️ Чеклист задач")],
         [KeyboardButton("▪️ Архив задач")]],
        resize_keyboard=True
    )

def employee_menu():
    return ReplyKeyboardMarkup([[KeyboardButton("▪️ Мои задачи")]], resize_keyboard=True)


# ── Утилиты ─────────────────────────────────────────────────────────────────

def is_manager(uid: int) -> bool:
    return uid == config.MANAGER_ID

def fmt_deadline(task: dict) -> str:
    val = task.get("deadline")
    if not val:
        return ""
    try:
        tz = pytz.timezone(config.TIMEZONE)
        dt = datetime.fromisoformat(str(val)).astimezone(tz)
        return f"\n   до {dt.strftime('%d.%m %H:%M')}"
    except Exception:
        return ""

def format_task(task: dict, show_assignee: bool = True) -> str:
    status = "✅" if task["done"] else "📍"
    assignee = f"\n   {task['assignee_name']}" if show_assignee and task.get("assignee_name") else ""
    return f"{status} <b>{task['title']}</b>{assignee}{fmt_deadline(task)}"

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
        await update.message.reply_text(
            f"Привет, {user.first_name}.\n\nТы подключена как менеджер.\nИспользуй меню внизу.",
            reply_markup=manager_menu()
        )
    else:
        await update.message.reply_text(
            f"Привет, {user.first_name}.\n\nКаждое утро в 10:00 тебе будет приходить список задач.\nНажимай «Готово» под задачей когда выполнишь.",
            reply_markup=employee_menu()
        )


# ── Добавить задачу ──────────────────────────────────────────────────────────

async def add_task_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_manager(update.effective_user.id):
        return
    team = db.get_team_members()
    if not team:
        await update.message.reply_text("Нет сотрудников. Попроси команду написать /start.")
        return
    context.user_data.clear()
    context.user_data["flow"] = "task"
    context.user_data["new_item"] = {"assignee_ids": [], "assignee_names": []}
    context.user_data["state"] = "choosing_assignees"
    await _show_picker(update.message, context, team, edit=False, title="▪️ Новая задача\n\nКому назначить?")

async def _show_picker(msg, context, team=None, edit=False, title="Выбери участников:"):
    if team is None:
        team = db.get_team_members()
    selected = context.user_data["new_item"].get("assignee_ids", [])
    buttons = [
        [InlineKeyboardButton(("✅ " if m["user_id"] in selected else "") + m["name"],
                              callback_data=f"pick_{m['user_id']}")]
        for m in team
    ]
    buttons.append([InlineKeyboardButton("➡️ Готово", callback_data="pick_done")])
    if selected:
        names = ", ".join(context.user_data["new_item"]["assignee_names"])
        title += f"\n\nВыбрано: {names}"
    markup = InlineKeyboardMarkup(buttons)
    if edit:
        await msg.edit_message_text(title, reply_markup=markup, parse_mode="HTML")
    else:
        await msg.reply_text(title, reply_markup=markup, parse_mode="HTML")

async def pick_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "pick_done":
        if not context.user_data.get("new_item", {}).get("assignee_ids"):
            await query.answer("Выбери хотя бы одного!", show_alert=True)
            return
        context.user_data["state"] = "waiting_title"
        flow = context.user_data.get("flow", "task")
        names = ", ".join(context.user_data["new_item"]["assignee_names"])
        label = "задачи" if flow == "task" else "созвона"
        await query.edit_message_text(
            f"Участники: <b>{names}</b>\n\nНапиши название {label}:",
            parse_mode="HTML"
        )
        return

    uid = int(query.data.split("_")[1])
    user = db.get_user(uid)
    item = context.user_data["new_item"]
    ids, names = item.get("assignee_ids", []), item.get("assignee_names", [])
    if uid in ids:
        i = ids.index(uid); ids.pop(i); names.pop(i)
    else:
        ids.append(uid); names.append(user["name"])
    item["assignee_ids"] = ids
    item["assignee_names"] = names
    context.user_data["new_item"] = item

    flow = context.user_data.get("flow", "task")
    title = "▪️ Новая задача\n\nКому назначить?" if flow == "task" else "▪️ Новый созвон\n\nКто участвует?"
    await _show_picker(query, context, edit=True, title=title)


# ── Добавить созвон ──────────────────────────────────────────────────────────

async def add_call_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_manager(update.effective_user.id):
        return
    team = db.get_team_members()
    if not team:
        await update.message.reply_text("Нет сотрудников.")
        return
    context.user_data.clear()
    context.user_data["flow"] = "call"
    context.user_data["new_item"] = {"assignee_ids": [], "assignee_names": []}
    context.user_data["state"] = "choosing_assignees"
    await _show_picker(update.message, context, team, edit=False, title="▪️ Новый созвон\n\nКто участвует?")

async def list_calls(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_manager(update.effective_user.id):
        return
    calls = db.get_active_calls()
    if not calls:
        await update.message.reply_text("Запланированных созвонов нет.")
        return
    tz = pytz.timezone(config.TIMEZONE)
    text = "▪️ <b>Созвоны:</b>\n\n"
    buttons = []
    for c in calls:
        try:
            dt = datetime.fromisoformat(str(c["scheduled_at"])).astimezone(tz)
            dt_str = dt.strftime("%d.%m %H:%M")
        except Exception:
            dt_str = str(c["scheduled_at"])
        text += f"📞 <b>{c['title']}</b>\n   {c['assignee_name']} — {dt_str}\n\n"
        buttons.append([InlineKeyboardButton(f"🗑 {c['title'][:35]}", callback_data=f"delcall_{c['id']}")])
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons) if buttons else None, parse_mode="HTML")

async def del_call_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    call_id = int(query.data.split("_")[1])
    call = db.get_call(call_id)
    if call:
        db.delete_call(call_id)
        await query.edit_message_text(f"🗑 Созвон «{call['title']}» удалён.")
    else:
        await query.edit_message_text("Созвон не найден.")


# ── Управление задачами ──────────────────────────────────────────────────────

async def manage_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_manager(update.effective_user.id):
        return
    tasks = db.get_all_tasks(done=False)
    if not tasks:
        await update.message.reply_text("Активных задач нет.")
        return
    await update.message.reply_text(
        "▪️ <b>Управление</b>\n\nВыбери задачу:", parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(f"📍 {t['title'][:40]}", callback_data=f"mgr_{t['id']}")]
            for t in tasks
        ])
    )

async def mgr_task_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
            [InlineKeyboardButton("🗑 Удалить",           callback_data=f"deltask_{task_id}")],
        ])
    )

async def change_deadline_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    task_id = int(query.data.split("_")[1])
    context.user_data["state"] = "changing_deadline"
    context.user_data["edit_task_id"] = task_id
    await query.edit_message_text(
        "Введи новый дедлайн: <code>ДД.ММ ЧЧ:ММ</code>\nИли напиши <b>нет</b> чтобы убрать.",
        parse_mode="HTML"
    )

async def delete_task_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    task_id = int(query.data.split("_")[1])
    task = db.get_task(task_id)
    await query.edit_message_text(
        f"Удалить <b>{task['title']}</b>?", parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Да", callback_data=f"confirmdelete_{task_id}")],
            [InlineKeyboardButton("← Отмена", callback_data=f"mgr_{task_id}")],
        ])
    )

async def confirm_delete_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    task_id = int(query.data.split("_")[1])
    task = db.get_task(task_id)
    db.delete_task(task_id)
    await query.edit_message_text(f"🗑 «{task['title'] if task else '?'}» удалена.")


# ── Кнопка «Готово» ──────────────────────────────────────────────────────────

async def mark_done_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    task_id = int(query.data.split("_")[1])
    task = db.get_task(task_id)
    if not task:
        await query.edit_message_text("Задача не найдена.")
        return
    db.mark_task_done(task_id)
    await query.edit_message_text(f"✅ <b>{task['title']}</b>\n<i>Выполнено</i>", parse_mode="HTML")
    try:
        user = db.get_user(query.from_user.id)
        name = user["name"] if user else query.from_user.first_name
        await context.bot.send_message(
            config.MANAGER_ID,
            f"✅ <b>{task['title']}</b>\n{name} отметил(а) выполнение.",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Уведомление менеджера: {e}")


# ── Списки ───────────────────────────────────────────────────────────────────

async def list_all_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_manager(update.effective_user.id):
        return
    tasks = db.get_all_tasks(done=False)
    if not tasks:
        await update.message.reply_text("Активных задач нет.")
        return
    text = "▪️ <b>Активные задачи</b>\n\n" + "\n\n".join(format_task(t) for t in tasks)
    await update.message.reply_text(text, parse_mode="HTML")

async def list_team(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_manager(update.effective_user.id):
        return
    team = db.get_team_members()
    if not team:
        await update.message.reply_text("Команда пуста.")
        return
    text = "▪️ <b>Команда</b>\n\n" + "\n".join(
        f"▪️ {m['name']}" + (f"  @{m['username']}" if m.get("username") else "")
        for m in team
    )
    await update.message.reply_text(text, parse_mode="HTML")

async def my_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    tasks = db.get_user_tasks(uid, done=False)
    if not tasks:
        await update.message.reply_text("Активных задач нет.")
        return
    text = "▪️ <b>Твои задачи:</b>\n\n" + "\n\n".join(format_task(t, show_assignee=False) for t in tasks)
    buttons = [[InlineKeyboardButton(f"✅ {t['title'][:35]}", callback_data=f"done_{t['id']}")] for t in tasks]
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")

async def team_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_manager(update.effective_user.id):
        return
    team = db.get_team_members()
    tz = pytz.timezone(config.TIMEZONE)
    today = datetime.now(tz).strftime("%d.%m.%Y")
    text = f"▪️ <b>Отчёт — {today}</b>\n\n"
    total_done = total_open = 0
    for m in team:
        done = db.get_user_tasks(m["user_id"], done=True)
        active = db.get_user_tasks(m["user_id"], done=False)
        if not done and not active:
            continue
        total_done += len(done); total_open += len(active)
        text += f"<b>{m['name']}</b>\n"
        for t in done:   text += f"   ✅ {t['title']}\n"
        for t in active: text += f"   📍 {t['title']}{fmt_deadline(t).replace(chr(10)+'   до ', ' · до ')}\n"
        text += "\n"
    text += f"Итого: ✅ {total_done} выполнено  ▪️ {total_open} в работе"
    await update.message.reply_text(text, parse_mode="HTML")

async def send_checklist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_manager(update.effective_user.id):
        return
    tasks = db.get_all_tasks(done=False)
    if not tasks:
        await update.message.reply_text("Активных задач нет.")
        return
    tz = pytz.timezone(config.TIMEZONE)
    today = datetime.now(tz).strftime("%d.%m.%Y")
    lines = []
    for t in tasks:
        dl = fmt_deadline(t).replace("\n   до ", " · до ")
        lines.append(f"📍 {t['title']}\n   {t['assignee_name']}{dl}")
    text = f"▪️ <b>Чеклист — {today}</b>\n\n" + "\n\n".join(lines)
    buttons = [[InlineKeyboardButton(f"✅ {t['title'][:35]}", callback_data=f"done_{t['id']}")] for t in tasks]
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons) if buttons else None, parse_mode="HTML")

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
            dt = datetime.fromisoformat(str(t["done_at"])).astimezone(tz)
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
    if len(text) > 4000:
        text = text[:4000] + "\n\n<i>...показаны последние 50 задач</i>"
    await update.message.reply_text(text, parse_mode="HTML")


# ── Обработка текста ─────────────────────────────────────────────────────────

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    state = context.user_data.get("state")

    # Меню
    menu = {
        "▪️ Добавить задачу":  add_task_start,
        "▪️ Все задачи":       list_all_tasks,
        "▪️ Команда":          list_team,
        "▪️ Мои задачи":       my_tasks,
        "▪️ Чеклист задач":    send_checklist,
        "▪️ Отчёт команды":    team_report,
        "▪️ Управление":       manage_tasks,
        "▪️ Архив задач":      show_archive,
        "▪️ Добавить созвон":  add_call_start,
        "▪️ Созвоны":          list_calls,
    }
    if text in menu:
        return await menu[text](update, context)

    # Создание задачи / созвона
    if state == "waiting_title":
        context.user_data["new_item"]["title"] = text
        context.user_data["state"] = "waiting_deadline"
        flow = context.user_data.get("flow", "task")
        if flow == "task":
            await update.message.reply_text(
                "Укажи дедлайн: <code>ДД.ММ ЧЧ:ММ</code>  или напиши <b>нет</b>.", parse_mode="HTML"
            )
        else:
            await update.message.reply_text(
                "Когда созвон? Формат: <code>ДД.ММ ЧЧ:ММ</code>", parse_mode="HTML"
            )

    elif state == "waiting_deadline":
        tz = pytz.timezone(config.TIMEZONE)
        flow = context.user_data.get("flow", "task")
        item = context.user_data["new_item"]

        if flow == "task":
            deadline, dl_display = parse_deadline(text, tz)
            if deadline == "error":
                await update.message.reply_text("Неверный формат. Попробуй: <code>25.06 18:00</code>", parse_mode="HTML")
                return
            task_id = db.create_task(
                title=item["title"],
                assignee_ids=item["assignee_ids"],
                assignee_name=", ".join(item["assignee_names"]),
                deadline=deadline
            )
            context.user_data.clear()
            await update.message.reply_text(
                f"▪️ Задача создана\n\n<b>{item['title']}</b>\n"
                f"{', '.join(item['assignee_names'])}\n"
                f"Дедлайн: {dl_display or 'нет'}\n\nСотрудники увидят её в утреннем списке в 10:00.",
                parse_mode="HTML"
            )
            # Уведомление только в рабочее время 10:00–19:00
            tz_obj = pytz.timezone(config.TIMEZONE)
            now_hour = datetime.now(tz_obj).hour
            if 10 <= now_hour < 19:
                from banners import banner_new_task
                for uid in item["assignee_ids"]:
                    try:
                        await context.bot.send_photo(uid, photo=banner_new_task())
                        await context.bot.send_message(
                            uid,
                            f"▪️ <b>Новая задача</b>\n\n<b>{item['title']}</b>\nДедлайн: {dl_display or 'нет'}",
                            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Готово", callback_data=f"done_{task_id}")]]),
                            parse_mode="HTML"
                        )
                    except Exception as e:
                        logger.error(f"Уведомление сотрудника {uid}: {e}")

        else:  # call
            deadline, dl_display = parse_deadline(text, tz)
            if deadline == "error":
                await update.message.reply_text("Неверный формат. Попробуй: <code>25.06 14:00</code>", parse_mode="HTML")
                return
            if deadline is None:
                await update.message.reply_text("Для созвона нужно указать время.")
                return
            db.create_call(
                title=item["title"],
                assignee_ids=item["assignee_ids"],
                assignee_name=", ".join(item["assignee_names"]),
                scheduled_at=deadline
            )
            context.user_data.clear()
            await update.message.reply_text(
                f"▪️ Созвон создан\n\n<b>{item['title']}</b>\n"
                f"{', '.join(item['assignee_names'])}\n"
                f"Время: {dl_display}\n\nУчастники получат напоминание за час и за 5 минут.",
                parse_mode="HTML"
            )

    elif state == "changing_deadline":
        tz = pytz.timezone(config.TIMEZONE)
        deadline, dl_display = parse_deadline(text, tz)
        if deadline == "error":
            await update.message.reply_text("Неверный формат. Попробуй: <code>25.06 18:00</code>", parse_mode="HTML")
            return
        db.update_deadline(context.user_data.get("edit_task_id"), deadline)
        context.user_data.clear()
        await update.message.reply_text(f"▪️ Дедлайн обновлён: {dl_display or 'убран'}")

    else:
        await update.message.reply_text("Используй меню внизу.")


# ── Запуск ───────────────────────────────────────────────────────────────────

def main():
    app = Application.builder().token(config.BOT_TOKEN).build()

    app.add_handler(CommandHandler("start",     start))
    app.add_handler(CommandHandler("task",      add_task_start))
    app.add_handler(CommandHandler("tasks",     list_all_tasks))
    app.add_handler(CommandHandler("call",      add_call_start))
    app.add_handler(CommandHandler("calls",     list_calls))
    app.add_handler(CommandHandler("team",      list_team))
    app.add_handler(CommandHandler("mytasks",   my_tasks))
    app.add_handler(CommandHandler("report",    team_report))
    app.add_handler(CommandHandler("checklist", send_checklist))
    app.add_handler(CommandHandler("manage",    manage_tasks))
    app.add_handler(CommandHandler("archive",   show_archive))

    app.add_handler(CallbackQueryHandler(pick_callback,       pattern=r"^pick_"))
    app.add_handler(CallbackQueryHandler(mark_done_cb,        pattern=r"^done_"))
    app.add_handler(CallbackQueryHandler(mgr_task_cb,         pattern=r"^mgr_\d+$"))
    app.add_handler(CallbackQueryHandler(change_deadline_cb,  pattern=r"^chdeadline_"))
    app.add_handler(CallbackQueryHandler(delete_task_cb,      pattern=r"^deltask_"))
    app.add_handler(CallbackQueryHandler(confirm_delete_cb,   pattern=r"^confirmdelete_"))
    app.add_handler(CallbackQueryHandler(del_call_callback,   pattern=r"^delcall_"))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    Scheduler(app, db, config).start()
    logger.info("Бот запущен v3.0")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
