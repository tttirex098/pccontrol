import asyncio
import json
from aiohttp import web
from telegram import Update
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

# Припускаємо, що ці константи та функції визначені у твоїй повній версії коду:
# BOT_TOKEN, PORT, AGENT_SECRET, agent, pending, ok, ask, agent_online, panel, cmd

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
    # --- Додано обробку нових кнопок ---
    elif data == "faceit":
        r = await ask("faceit", timeout=120)
    elif data == "cs2":
        r = await ask("cs2", timeout=120)
    elif data == "chrome":
        r = await ask("chrome", timeout=120)
    # ----------------------------------
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

    # Базові команди
    for name in ["status", "screenshot", "lock", "shutdown", "restart"]:
        tg.add_handler(CommandHandler(name, lambda u, c, n=name: simple(u, c, n)))

    # --- Додано нові команди для запуску програм ---
    for name in ["faceit", "cs2", "chrome"]:
        tg.add_handler(CommandHandler(name, lambda u, c, n=name: simple(u, c, n)))
    # ----------------------------------------------

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
