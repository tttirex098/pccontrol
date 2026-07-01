import os, json, asyncio, uuid
from aiohttp import web
from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

load_dotenv()
BOT_TOKEN=os.getenv("8706037093:AAGeq4K18gbZRSvJnfz41fIcahS8dv0W-9I")
ALLOWED_USER_ID=int(os.getenv("6027204124","0"))
AGENT_SECRET=os.getenv("pccontrol_7Kq9vX2mN8sR4tY6pL3aW5zB1cD0fH")
PORT=int(os.getenv("PORT","8080"))
agent=None
pending={}

def kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Status",callback_data="status"), InlineKeyboardButton("Screenshot",callback_data="screenshot")],
        [InlineKeyboardButton("Lock",callback_data="lock"), InlineKeyboardButton("Restart",callback_data="restart")],
        [InlineKeyboardButton("Shutdown",callback_data="shutdown"), InlineKeyboardButton("Cancel shutdown",callback_data="cancel_shutdown")],
        [InlineKeyboardButton("Open Discord",callback_data="open discord"), InlineKeyboardButton("Close Discord",callback_data="close discord")],
        [InlineKeyboardButton("Open Steam",callback_data="open steam"), InlineKeyboardButton("Close Steam",callback_data="close steam")]
    ])

def ok(u): return u.effective_user and u.effective_user.id==ALLOWED_USER_ID

async def ask(action,args=None,timeout=90):
    if not agent or agent.closed: return {"ok":False,"text":"PC offline"}
    rid=str(uuid.uuid4()); fut=asyncio.get_running_loop().create_future(); pending[rid]=fut
    await agent.send_json({"id":rid,"action":action,"args":args or {}})
    try: return await asyncio.wait_for(fut,timeout)
    except asyncio.TimeoutError: return {"ok":False,"text":"Timeout"}

async def panel(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    if not ok(update): return
    await update.message.reply_text("PC Control",reply_markup=kb())

async def cmd(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    if not ok(update): return
    text=" ".join(ctx.args)
    if not text: return await update.message.reply_text("Use: /cmd ipconfig")
    r=await ask("cmd",{"command":text},120)
    await update.message.reply_text(r.get("text","")[:3900])

async def open_app(update,ctx):
    if not ok(update): return
    r=await ask("open",{"name":" ".join(ctx.args)})
    await update.message.reply_text(r.get("text",str(r)))

async def close_app(update,ctx):
    if not ok(update): return
    r=await ask("close",{"name":" ".join(ctx.args)})
    await update.message.reply_text(r.get("text",str(r)))

async def simple(update,ctx,action):
    if not ok(update): return
    r=await ask(action,timeout=120)
    await update.message.reply_text(r.get("text",str(r)))

async def button(update,ctx):
    if not ok(update): return
    q=update.callback_query; await q.answer()
    data=q.data
    if data.startswith("open "): r=await ask("open",{"name":data[5:]})
    elif data.startswith("close "): r=await ask("close",{"name":data[6:]})
    elif data=="cancel_shutdown": r=await ask("cmd",{"command":"shutdown /a"})
    else: r=await ask(data,timeout=120)
    await q.message.reply_text(r.get("text",str(r)))

async def ws(request):
    global agent
    if request.query.get("secret")!=AGENT_SECRET: return web.Response(status=403)
    sock=web.WebSocketResponse(); await sock.prepare(request); agent=sock
    async for msg in sock:
        data=json.loads(msg.data); fut=pending.pop(data.get("id"),None)
        if fut and not fut.done(): fut.set_result(data)
    agent=None
    return sock

async def main():
    tg=Application.builder().token(BOT_TOKEN).build()
    tg.add_handler(CommandHandler(["start","panel"],panel))
    tg.add_handler(CommandHandler("cmd",cmd))
    tg.add_handler(CommandHandler("open",open_app))
    tg.add_handler(CommandHandler("close",close_app))
    for name in ["status","screenshot","lock","shutdown","restart"]:
        tg.add_handler(CommandHandler(name,lambda u,c,n=name: simple(u,c,n)))
    tg.add_handler(CallbackQueryHandler(button))
    app=web.Application(); app.router.add_get("/ws",ws); app.router.add_get("/",lambda r:web.Response(text="ok"))
    runner=web.AppRunner(app); await runner.setup(); await web.TCPSite(runner,"0.0.0.0",PORT).start()
    await tg.initialize(); await tg.start(); await tg.updater.start_polling()
    await asyncio.Event().wait()

asyncio.run(main())
