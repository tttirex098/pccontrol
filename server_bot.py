import os
import json
import asyncio
import uuid
from aiohttp import web
from dotenv import load_dotenv

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

load_dotenv()

# ================== НАЛАШТУВАННЯ ==================
BOT_TOKEN = os.getenv("BOT_TOKEN")
ALLOWED_USER_ID = int(os.getenv("ALLOWED_USER_ID", "0"))
AGENT_SECRET = os.getenv("AGENT_SECRET")
PORT = int(os.getenv("PORT", "8080"))
# =================================================

agent = None
pending = {}


def kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Status", callback_data="status"), 
         InlineKeyboardButton("Screenshot", callback_data="screenshot")],
        [InlineKeyboardButton("Lock", callback_data="lock"), 
         InlineKeyboardButton("Restart", callback_data="restart")],
        [InlineKeyboardButton("Shutdown", callback_data="shutdown"), 
         InlineKeyboardButton("Cancel shutdown", callback_data="cancel_shutdown")],
        [InlineKeyboardButton("Open Discord", callback_data="open discord"), 
         InlineKeyboardButton("Close Discord", callback_data="close discord")],
        [InlineKeyboardButton("Open Steam", callback_data="open steam"), 
         InlineKeyboardButton("Close Steam", callback_data="close steam")]
    ])


def ok(u):
    return u.effective_user and u.effective_user.id == ALLOWED_USER_ID


def agent_online():
    return agent is not None and not agent.closed


async def ask(action, args=None, timeout=90):
    global agent
    sock = agent

    if sock is None or sock.closed:
        return {"ok": False, "text": "PC offline"}

    rid = str(uuid.uuid4())
    fut = asyncio.get_running_loop().create_future()
    pending[rid] = fut

    try:
        await sock.send_json({"id": rid, "action": action, "args": args or {}})
    except Exception as e:
        pending.pop(rid, None)
        # Якщо відправка не вдалась - це з'єднання точно мертве.
        # Скидаємо agent одразу, не чекаючи поки це виявить ws().
        if agent is sock:
            agent = None
        return {"ok": False, "text": f"Send error: {e}"}

    try:
        return await asyncio.wait_for(fut, timeout)
    except asyncio.TimeoutError:
        pending.pop(rid, None)
        return {"ok": False, "text": "Timeout"}


async def panel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ok(update): return
    await update.message.reply_text("PC Control", reply_markup=kb())


async def cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ok(update): return
    text = " ".join(ctx.args)
    if not text:
        return await update.message.reply_text("Use: /cmd ipconfig")
    r = await ask("cmd", {"command": text}, 120)
    await update.message.reply_text(r.get("text", "")[:3900])


async def open_app(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ok(update): return
    r = await ask("open", {"name": " ".join(ctx.args)})
    await update.message.reply_text(r.get("text", str(r)))


async def close_app(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ok(update): return
    r = await ask("close", {"name": " ".join(ctx.args)})
    await update.message.reply_text(r.get("text", str(r)))


async def simple(update: Update, ctx: ContextTypes.DEFAULT_TYPE, action: str):
    if not ok(update): return
    r = await ask(action, timeout=120)
    await update.message.reply_text(r.get("text", str(r)))


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
    else:
        r = await ask(data, timeout=120)

    await q.message.reply_text(r.get("text", str(r)))


async def ws(request):
    global agent

    if request.query.get("secret") != AGENT_SECRET:
        return web.Response(status=403)

    sock = web.WebSocketResponse(heartbeat=25)
    await sock.prepare(request)

    # Якщо в пам'яті вже висить попереднє з'єднання - закриваємо його.
    # Це усуває "гонку": старий обробник міг би пізніше скинути agent
    # вже ПІСЛЯ того, як підключився новий (саме через це бот бачив
    # "offline", хоча програма на ПК була підключена і показувала "Connected").
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

    # Скидаємо agent, ТІЛЬКИ якщо він досі вказує саме на це з'єднання.
    # Якщо тим часом підключився новий агент - не чіпаємо його.
    if agent is sock:
        agent = None
    print(f"[agent] disconnected: {request.remote}")

    return sock


async def status_page(request):
    # Зручно для швидкої перевірки в браузері, а також як ціль
    # для зовнішнього keep-alive пінгу (щоб безкоштовний Render-інстанс
    # не засинав через 15 хв без вхідного трафіку).
    return web.Response(text="agent: online" if agent_online() else "agent: offline")


async def main():
    tg = Application.builder().token(BOT_TOKEN).build()

    tg.add_handler(CommandHandler(["start", "panel"], panel))
    tg.add_handler(CommandHandler("cmd", cmd))
    tg.add_handler(CommandHandler("open", open_app))
    tg.add_handler(CommandHandler("close", close_app))

    for name in ["status", "screenshot", "lock", "shutdown", "restart"]:
        tg.add_handler(CommandHandler(name, lambda u, c, n=name: simple(u, c, n)))

    tg.add_handler(CallbackQueryHandler(button))

    app = web.Application()
    app.router.add_get("/ws", ws)
    app.router.add_get("/", status_page)

    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()

    await tg.initialize()
    await tg.start()
    await tg.updater.start_polling()

    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
