import asyncio
import json
from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

# Припускаємо, що ці константи та інші функції визначені у твоєму коді:
# BOT_TOKEN, PORT, AGENT_SECRET, agent, pending, ok, ask, agent_online, cmd

# --- НОВА ФУНКЦІЯ: Генерація та надсилання головного меню ---
async def send_panel(update: Update, text: str = "Оберіть дію:"):
    """Створює та надсилає плашку з кнопками керування."""
    keyboard = [
        [
            InlineKeyboardButton("📊 Статус", callback_data="status"),
            InlineKeyboardButton("📸 Скріншот", callback_data="screenshot"),
        ],
        [
            InlineKeyboardButton("🛡️ FACEIT AC", callback_data="faceit"),
            InlineKeyboardButton("🎮 CS2", callback_data="cs2"),
            InlineKeyboardButton("🌐 Chrome", callback_data="chrome"),
        ],
        [
            InlineKeyboardButton("🔒 Блокувати", callback_data="lock"),
            InlineKeyboardButton("🛑 Вимкнути ПК", callback_data="shutdown"),
        ],
        [
            InlineKeyboardButton("❌ Скасувати вимкнення", callback_data="cancel_shutdown")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Перевіряємо, звідки прийшов запит (з команди чи з натискання кнопки)
    if update.message:
        await update.message.reply_text(text, reply_markup=reply_markup)
    elif update.callback_query and update.callback_query.message:
        await update.callback_query.message.reply_text(text, reply_markup=reply_markup)

# Перезаписуємо або доповнюємо функцію panel для виклику нашого меню
async def panel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ok(update): return
    await send_panel(update, "Головне меню:")


async def close_app(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ok(update): return
    r = await ask("close", {"name": " ".join(ctx.args)})
    await update.message.reply_text(r.get("text", str(r)))
    # Після закриття програми знову виводимо плашку дій
    await send_panel(update)


async def simple(update: Update, ctx: ContextTypes.DEFAULT_TYPE, action: str):
    if not ok(update): return
    r = await ask(action, timeout=120)
    await update.message.reply_text(r.get("text", str(r)))
    # Після виконання базової команди знову виводимо плашку дій
    await send_panel(update)


async def button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ok(update): return
    q = update.callback_query
    await q.answer()
    data = q.data

    if data.startswith("open "):
        r = await ask("open", {"name": data[5:]})
    elif data.startswith("close "):
        r = await ask("close", {"name": data[6:]})
    elif data == "cancel_shutdown":
        r = await ask("cmd", {"command": "shutdown /a"})
    # Обробка кнопок запуску програм
    elif data == "faceit":
        r = await ask("faceit", timeout=120)
    elif data == "cs2":
        r = await ask("cs2", timeout=120)
    elif data == "chrome":
        r = await ask("chrome", timeout=120)
    else:
        r = await ask(data, timeout=120)

    # Надсилаємо результат виконання команди
    await q.message.reply_text(r.get("text", str(r)))
    
    # ВСЛІД за результатом знову викидаємо плашку з варіантами дій
    await send_panel(update)


async def ws(request):
    global agent

    if request.query.get("secret") != AGENT_SECRET:
        return web.Response(status=403)

    sock = web.WebSocketResponse(heartbeat=25)
    await sock.prepare(request)

    # Якщо в пам'яті вже висить попереднє з'єднання - закриваємо його.
    old = agent
    if old is not None and not old.closed:
        try:
            await old.close()
        except Exception:
            pass

    agent = sock
    print(f"[agent] connected: {request.remote}")

    async for msg in sock:
        if msg.type == web.WSMsgType.TEXT:
            data = json.loads(msg.data)
            fut = pending.pop(data.get("id"), None)
            if fut and not fut.done():
                fut.set_result(data)
        elif msg.type in (web.WSMsgType.ERROR, web.WSMsgType.CLOSE, web.WSMsgType.CLOSING):
            break

    if agent is sock:
        agent = None
    print(f"[agent] disconnected: {request.remote}")

    return sock


async def status_page(request):
    return web.Response(text="agent: online" if agent_online() else "agent: offline")


async def main():
    tg = Application.builder().token(BOT_TOKEN).build()
