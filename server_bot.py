import os
import json
import asyncio
import uuid
from aiohttp import web
from dotenv import load_dotenv

from telegram import Update, BotCommand
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

load_dotenv()

# ================== НАЛАШТУВАННЯ ==================
BOT_TOKEN = os.getenv("BOT_TOKEN")
ALLOWED_USER_ID = int(os.getenv("ALLOWED_USER_ID", "0"))
AGENT_SECRET = os.getenv("AGENT_SECRET")
PORT = int(os.getenv("PORT", "8080"))
# =================================================

agent = None
pending = {}


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
        if agent is sock:
            agent = None
        return {"ok": False, "text": f"Send error: {e}"}

    try:
        return await asyncio.wait_for(fut, timeout)
    except asyncio.TimeoutError:
        pending.pop(rid, None)
        return {"ok": False, "text": "Timeout"}


# ================== ОБРАБОТЧИКИ КОМАНД МЕНЮ ==================

async def cmd_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ok(update): return
    text = " ".join(ctx.args)
    if not text:
        return await update.message.reply_text("Use: /cmd ipconfig")
    r = await ask("cmd", {"command": text}, 120)
    await update.message.reply_text(r.get("text", "")[:3900])


async def simple_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ok(update): return
    action = update.effective_message.text.split()[0].replace("/", "")
    r = await ask(action, timeout=120)
    await update.message.reply_text(r.get("text", str(r)))


async def cancel_shutdown_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ok(update): return
    r = await ask("cmd", {"command": "shutdown /a"})
    await update.message.reply_text(r.get("text", str(r)))


async def menu_app_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ok(update): return
    command_name = update.effective_message.text.split()[0].replace("/", "")
    
    apps_map = {
        "open_discord": ("cmd", {"command": 'start "" "C:\\shortcuts\\discord.lnk"'}),
        "close_discord": ("close", {"name": "discord"}),
        
        "open_steam": ("cmd", {"command": 'start "" "C:\\shortcuts\\steam.lnk"'}),
        "close_steam": ("close", {"name": "steam"}),
        
        "open_cs2": ("cmd", {"command": "start steam://rungameid/730"}),
        "close_cs2": ("close", {"name": "cs2"}),
        
        "open_chrome": ("cmd", {"command": 'start chrome'}),
        "close_chrome": ("close", {"name": "chrome"}),
    }
    
    if command_name in apps_map:
        action, args = apps_map[command_name]
        r = await ask(action, args)
        await update.message.reply_text(r.get("text", str(r)))


async def open_app(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ok(update): return
    name = " ".join(ctx.args)
    r = await ask("open", {"name": name})
    await update.message.reply_text(r.get("text", str(r)))


async def close_app(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ok(update): return
    name = " ".join(ctx.args)
    r = await ask("close", {"name": name})
    await update.message.reply_text(r.get("text", str(r)))


# ================== СЕТЕВАЯ ЧАСТЬ (WEBSOCKET) ==================

async def ws(request):
    global agent
    if request.query.get("secret") != AGENT_SECRET:
        return web.Response(status=403)

    sock = web.WebSocketResponse(heartbeat=25)
    await sock.prepare(request)

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


# ================== ОСНОВНОЙ ЗАПУСК ==================

async def main():
    tg = Application.builder().token(BOT_TOKEN).build()

    # Список команд на английском языке для меню Telegram
    commands = [
        BotCommand("status", "Check PC status"),
        BotCommand("screenshot", "Take a screenshot"),
        BotCommand("lock", "Lock Windows"),
        BotCommand("restart", "Restart PC"),
        BotCommand("shutdown", "Shutdown PC"),
        BotCommand("cancel_shutdown", "Cancel scheduled shutdown"),
        BotCommand("open_discord", "Launch Discord"),
        BotCommand("close_discord", "Terminate Discord process"),
        BotCommand("open_steam", "Launch Steam"),
        BotCommand("close_steam", "Terminate Steam process"),
        BotCommand("open_cs2", "Launch Counter-Strike 2"),
        BotCommand("close_cs2", "Terminate CS2 process"),
        BotCommand("open_chrome", "Launch Google Chrome"),
        BotCommand("close_chrome", "Terminate Chrome process"),
        BotCommand("cmd", "Execute console command (/cmd <command>)"),
    ]
    
    # Регистрация хэндлеров
    for name in ["status", "screenshot", "lock", "shutdown", "restart"]:
        tg.add_handler(CommandHandler(name, simple_handler))

    tg.add_handler(CommandHandler("cancel_shutdown", cancel_shutdown_handler))

    app_commands = [
        "open_discord", "close_discord", 
        "open_steam", "close_steam", 
        "open_cs2", "close_cs2", 
        "open_chrome", "close_chrome"
    ]
    for cmd_name in app_commands:
        tg.add_handler(CommandHandler(cmd_name, menu_app_handler))

    tg.add_handler(CommandHandler("cmd", cmd_handler))
    tg.add_handler(CommandHandler("open", open_app))
    tg.add_handler(CommandHandler("close", close_app))

    app = web.Application()
    app.router.add_get("/ws", ws)
    app.router.add_get("/", status_page)

    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()

    await tg.initialize()
    await tg.bot.set_my_commands(commands) # Применяем английское меню
    await tg.start()
    await tg.updater.start_polling()

    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
