import os
import random
import logging
import psycopg2
import asyncio
from datetime import datetime, timedelta
from psycopg2.extras import Json
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, ContextTypes, 
    MessageHandler, CallbackQueryHandler, filters, ConversationHandler, JobQueue
)

# --- 【核心配置区】：请在部署后使用 /id 获取 ID 并填入此处 ---
ID_WELCOME = "这里填入首页欢迎图ID"
ID_VIP_GUIDE = "这里填入VIP验证教程图ID"
ID_WX_PAY = "这里填入微信收款码图ID"
ID_WX_GUIDE = "这里填入微信账单教程图ID"
ID_ALI_PAY = "这里填入支付宝收款码图ID"
ID_ALI_GUIDE = "这里填入支付宝账单教程图ID"
VIP_GROUP_LINK = "https://t.me/your_group_link" # 替换为你的VIP群链接

# --- 环境变量读取 ---
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
DB_URL = os.getenv("DATABASE_URL").replace("postgres://", "postgresql://")

# --- 状态定义 ---
(ST_VIP, ST_WX, ST_ALI, ST_PROD_NAME, ST_PROD_PRICE, ST_PROD_CONTENT, ST_FWD_CMD, ST_FWD_CONTENT, ST_GETID) = range(9)

# --- 数据库初始化 ---
def init_db():
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    # 用户表：包含VIP状态、积分、签到、充值限制和失败锁定
    cur.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id BIGINT PRIMARY KEY,
        points INTEGER DEFAULT 0,
        last_checkin DATE,
        vip_fails INTEGER DEFAULT 0, vip_lock_until TIMESTAMP,
        wx_used BOOLEAN DEFAULT FALSE, wx_fails INTEGER DEFAULT 0, wx_lock_until TIMESTAMP,
        ali_used BOOLEAN DEFAULT FALSE, ali_fails INTEGER DEFAULT 0, ali_lock_until TIMESTAMP)''')
    # 转发库
    cur.execute('''CREATE TABLE IF NOT EXISTS forward_lib (cmd_text TEXT PRIMARY KEY, messages JSONB)''')
    # 商品表
    cur.execute('''CREATE TABLE IF NOT EXISTS products (
        id SERIAL PRIMARY KEY, name TEXT, price INTEGER, type TEXT, f_id TEXT, txt TEXT)''')
    # 兑换记录
    cur.execute('''CREATE TABLE IF NOT EXISTS redemptions (user_id BIGINT, p_id INTEGER)''')
    # 积分历史
    cur.execute('''CREATE TABLE IF NOT EXISTS history (
        user_id BIGINT, action TEXT, amount TEXT, ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    conn.commit()
    cur.close(); conn.close()

# --- 辅助功能 ---
def db_query(sql, params=None, fetch=True):
    conn = psycopg2.connect(DB_URL); cur = conn.cursor()
    cur.execute(sql, params or ())
    res = cur.fetchall() if fetch else None
    conn.commit(); cur.close(); conn.close()
    return res

def get_user(uid):
    res = db_query("SELECT * FROM users WHERE user_id = %s", (uid,))
    if not res:
        db_query("INSERT INTO users (user_id) VALUES (%s)", (uid,), False)
        res = db_query("SELECT * FROM users WHERE user_id = %s", (uid,))
    return res[0]

def add_history(uid, act, amt):
    db_query("INSERT INTO history (user_id, action, amount) VALUES (%s, %s, %s)", (uid, act, amt), False)

# --- 1. 首页逻辑 ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user = get_user(uid)
    # VIP 锁定检查
    vip_lock = user[4] and datetime.now() < user[4]
    
    txt = (
        "👋 **您好！欢迎使用 VIP 中转管家**\n\n"
        "我是您的守门员小卫，竭诚为您服务：\n"
        "━━━━━━━━━━━━━━\n"
        "📢 新人入群身份验证\n"
        "💰 积分签到与福利兑换\n"
        "📦 专属私密资源获取\n"
        "━━━━━━━━━━━━━━"
    )
    kbd = [
        [InlineKeyboardButton("💎 开始身份验证" if not vip_lock else "❌ 验证锁定中", callback_data="v_start" if not vip_lock else "v_locked")],
        [InlineKeyboardButton("🪙 积分钱包", callback_data="j_main"), InlineKeyboardButton("🎁 兑换中心", callback_data="d_main")]
    ]
    if ID_WELCOME.startswith("这里"):
        await (update.callback_query.message.edit_text(txt, reply_markup=InlineKeyboardMarkup(kbd), parse_mode="Markdown") if update.callback_query else update.message.reply_text(txt, reply_markup=InlineKeyboardMarkup(kbd), parse_mode="Markdown"))
    else:
        await (update.callback_query.message.reply_photo(photo=ID_WELCOME, caption=txt, reply_markup=InlineKeyboardMarkup(kbd), parse_mode="Markdown") if update.callback_query else update.message.reply_photo(photo=ID_WELCOME, caption=txt, reply_markup=InlineKeyboardMarkup(kbd), parse_mode="Markdown"))

# --- 2. 积分系统 (/jf) ---
async def jf_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user(update.effective_user.id)
    txt = f"🪙 **我的积分中心**\n\n💰 当前可用余额：**{user[1]}** 积分\n🆔 用户UID：`{user[0]}`\n\n您可以点击下方按钮进行签到或充值。"
    kbd = [
        [InlineKeyboardButton("📅 每日签到", callback_data="j_sign"), InlineKeyboardButton("💳 积分充值", callback_data="j_pay")],
        [InlineKeyboardButton("📊 余额变动明细", callback_data="j_hist")],
        [InlineKeyboardButton("🏠 返回首页", callback_data="back_home")]
    ]
    await update.callback_query.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kbd), parse_mode="Markdown")

async def jf_sign(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; uid = query.from_user.id
    user = get_user(uid); today = datetime.now().date()
    if user[2] == today:
        await query.answer("❌ 今天已经签到过了，明天再来吧！", show_alert=True); return
    reward = random.randint(3, 8)
    db_query("UPDATE users SET points = points + %s, last_checkin = %s WHERE user_id = %s", (reward, today, uid), False)
    add_history(uid, "每日签到", f"+{reward}")
    await query.answer(f"🎉 签到成功，获得 {reward} 积分！", show_alert=True)
    await jf_main(update, context)

async def jf_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id; user = get_user(uid)
    rows = db_query("SELECT action, amount, ts FROM history WHERE user_id = %s ORDER BY ts DESC LIMIT 10", (uid,))
    log = "\n".join([f"• `{r[2].strftime('%m-%d %H:%M')}` {r[0]} ({r[1]})" for r in rows]) if rows else "暂无记录"
    txt = f"💰 **账户当前余额：{user[1]}**\n\n📋 **最近10条历史记录：**\n{log}"
    await update.callback_query.edit_message_text(txt, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="j_main")]]), parse_mode="Markdown")

# --- 3. 充值逻辑 (微信/支付宝) ---
async def jf_pay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user(update.effective_user.id)
    # 锁定逻辑 (10小时)
    wx_l = user[5] or (user[7] and datetime.now() < user[7])
    ali_l = user[8] or (user[10] and datetime.now() < user[10])
    
    txt = "💳 **积分充值中心**\n\n✨ 价格：**5.00 元 = 100 积分**\n\n⚠️ **温馨提示：**\n微信与支付宝各限充值一次，请勿重复支付。订单号输入错误2次将锁定10小时。"
    kbd = [
        [InlineKeyboardButton("💹 微信支付" if not wx_l else "❌ 微信(已用/锁定)", callback_data="p_wx" if not wx_l else "p_lock")],
        [InlineKeyboardButton("💹 支付宝支付" if not ali_l else "❌ 支付宝(已用/锁定)", callback_data="p_ali" if not ali_l else "p_lock")],
        [InlineKeyboardButton("🔙 返回钱包", callback_data="j_main")]
    ]
    await update.callback_query.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kbd), parse_mode="Markdown")

# --- 4. 兑换中心 (/dh) ---
async def dh_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    prods = db_query("SELECT id, name, price FROM products")
    bought = [r[0] for r in db_query("SELECT p_id FROM redemptions WHERE user_id = %s", (uid,))]
    
    txt = "🎁 **兑换中心**\n使用积分兑换您的专属内容："
    # 始终存在的测试按钮
    test_status = "✅ [测试] 哈哈 (已兑换)" if -1 in bought else "🛒 [测试] 哈哈 (0 积分)"
    kbd = [[InlineKeyboardButton(test_status, callback_data="buy_-1")]]
    
    for p in prods:
        btn_txt = f"✅ {p[1]} (已兑换)" if p[0] in bought else f"🛒 {p[1]} ({p[2]} 积分)"
        kbd.append([InlineKeyboardButton(btn_txt, callback_data=f"buy_{p[0]}")])
    kbd.append([InlineKeyboardButton("🏠 返回首页", callback_data="back_home")])
    
    if update.callback_query: await update.callback_query.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kbd), parse_mode="Markdown")
    else: await update.message.reply_text(txt, reply_markup=InlineKeyboardMarkup(kbd), parse_mode="Markdown")

async def handle_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; uid = query.from_user.id; pid = int(query.data.split("_")[1])
    # 检查是否已买
    res = db_query("SELECT 1 FROM redemptions WHERE user_id = %s AND p_id = %s", (uid, pid))
    if res:
        if pid == -1: await query.message.reply_text("哈哈！")
        else:
            p = db_query("SELECT type, f_id, txt FROM products WHERE id = %s", (pid,))[0]
            if p[0] == 'text': await query.message.reply_text(p[2])
            elif p[0] == 'photo': await query.message.reply_photo(p[1], caption=p[2])
            elif p[0] == 'video': await query.message.reply_video(p[1], caption=p[2])
        await dh_main(update, context); return

    if pid == -1: # 测试商品直接兑换
        db_query("INSERT INTO redemptions (user_id, p_id) VALUES (%s, %s)", (uid, -1), False)
        await query.answer("兑换成功！"); await query.message.reply_text("哈哈！"); await dh_main(update, context)
    else:
        p = db_query("SELECT name, price FROM products WHERE id = %s", (pid,))[0]
        context.user_data['tmp_buy'] = {'id': pid, 'price': p[1], 'name': p[0]}
        await query.edit_message_text(f"❓ **兑换确认**\n\n确定消耗 **{p[1]}** 积分兑换【{p[0]}】吗？", 
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ 确定", callback_data="conf_buy"), InlineKeyboardButton("❌ 取消", callback_data="d_main")]]), parse_mode="Markdown")

async def conf_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; uid = query.from_user.id; buy = context.user_data.get('tmp_buy')
    user = get_user(uid)
    if user[1] < buy['price']:
        await query.answer("❌ 积分不足，充值后再试", show_alert=True); await dh_main(update, context); return
    
    db_query("UPDATE users SET points = points - %s WHERE user_id = %s", (buy['price'], uid), False)
    db_query("INSERT INTO redemptions (user_id, p_id) VALUES (%s, %s)", (uid, buy['id']), False)
    add_history(uid, f"兑换:{buy['name']}", f"-{buy['price']}")
    
    p = db_query("SELECT type, f_id, txt FROM products WHERE id = %s", (buy['id'],))[0]
    await query.answer("🎉 兑换成功！")
    if p[0] == 'text': await query.message.reply_text(p[2])
    elif p[0] == 'photo': await query.message.reply_photo(p[1], caption=p[2])
    elif p[0] == 'video': await query.message.reply_video(p[1], caption=p[2])
    await dh_main(update, context)

# --- 5. 管理员后台 (/admin) ---
async def admin_home(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    kbd = [
        [InlineKeyboardButton("📸 获取 File ID", callback_data="a_getid")],
        [InlineKeyboardButton("📦 频道转发库", callback_data="a_lib")],
        [InlineKeyboardButton("🛍 商品管理", callback_data="a_prod")],
        [InlineKeyboardButton("🏠 退出后台", callback_data="back_home")]
    ]
    txt = "🛠 **管理员后台管理系统**"
    if update.callback_query: await update.callback_query.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kbd), parse_mode="Markdown")
    else: await update.message.reply_text(txt, reply_markup=InlineKeyboardMarkup(kbd), parse_mode="Markdown")

async def admin_prod_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    prods = db_query("SELECT id, name, price FROM products")
    kbd = [[InlineKeyboardButton("➕ 上架新商品", callback_data="p_add")]]
    for p in prods: kbd.append([InlineKeyboardButton(f"🗑 下架：{p[1]} ({p[2]}分)", callback_data=f"p_del_{p[0]}")])
    kbd.append([InlineKeyboardButton("🔙 返回", callback_data="a_home")])
    await update.callback_query.edit_message_text("🛍 **商品上架/下架管理**", reply_markup=InlineKeyboardMarkup(kbd), parse_mode="Markdown")

# --- 6. 各类 Conversation 处理 (VIP, 充值, 商品, 转发库) ---
async def flow_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; data = query.data; await query.answer()
    if data == "v_start":
        await query.message.reply_photo(ID_VIP_GUIDE, "💎 **VIP特权说明**\n✅ 专属通道 ✅ 优先审核\n\n请支付后点击下方按钮验证。", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ 我已付款，开始验证", callback_data="v_go")]]))
    elif data == "v_go":
        await query.message.reply_text("📝 请输入订单号（20260开头）："); return ST_VIP
    elif data == "p_wx":
        await query.message.reply_photo(ID_WX_PAY, "💹 **微信支付：5.00元**\n\n⚠️ 限充一次。请付完款点击下方验证。", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ 我已付款，开始验证", callback_data="p_wx_go")]]))
    elif data == "p_wx_go":
        await query.message.reply_photo(ID_WX_GUIDE, "🔍 请输入微信支付的【交易单号】："); return ST_WX
    elif data == "p_ali":
        await query.message.reply_photo(ID_ALI_PAY, "💹 **支付宝支付：5.00元**\n\n⚠️ 限充一次。请付完款点击下方验证。", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ 我已付款，开始验证", callback_data="p_ali_go")]]))
    elif data == "p_ali_go":
        await query.message.reply_photo(ID_ALI_GUIDE, "🔍 请输入支付宝的【商家订单号】："); return ST_ALI
    elif data == "p_add":
        await query.message.reply_text("📦 请输入商品名称："); return ST_PROD_NAME
    elif data == "a_getid":
        await query.message.reply_text("📸 请发送图片或视频，我将返回 ID："); return ST_GETID
    elif data == "a_lib":
        await query.message.reply_text("📝 请输入触发指令（如：教程）："); return ST_FWD_CMD
    return None

async def handle_vip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id; msg = update.message.text.strip()
    if msg.startswith("20260"):
        db_query("UPDATE users SET vip_fails = 0 WHERE user_id = %s", (uid,), False)
        await update.message.reply_text("✅ 验证通过！", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🚀 立即入群", url=VIP_GROUP_LINK)]]))
        await start(update, context); return ConversationHandler.END
    else:
        db_query("UPDATE users SET vip_fails = vip_fails + 1, vip_lock_until = %s WHERE user_id = %s", (datetime.now() + timedelta(hours=5), uid), False)
        user = get_user(uid)
        if user[3] >= 2: await update.message.reply_text("❌ 失败2次，锁定5小时。"); await start(update, context); return ConversationHandler.END
        await update.message.reply_text("❌ 识别失败，请重试："); return ST_VIP

async def handle_pay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id; msg = update.message.text.strip()
    mode = 'wx' if context.user_data.get('pay_mode') == 'wx' else 'ali'
    prefix = '4200' if mode == 'wx' else '4768'
    
    if msg.startswith(prefix):
        db_query(f"UPDATE users SET points = points + 100, {mode}_used = TRUE WHERE user_id = %s", (uid,), False)
        add_history(uid, f"{'微信' if mode=='wx' else '支付宝'}充值", "+100")
        await update.message.reply_text("✅ 成功到账 100 积分！"); await jf_main(update, context); return ConversationHandler.END
    else:
        db_query(f"UPDATE users SET {mode}_fails = {mode}_fails + 1, {mode}_lock_until = %s WHERE user_id = %s", (datetime.now() + timedelta(hours=10), uid), False)
        user = get_user(uid)
        idx = 6 if mode == 'wx' else 9
        if user[idx] >= 2: await update.message.reply_text("❌ 失败2次，该通道锁定10小时。"); await jf_main(update, context); return ConversationHandler.END
        await update.message.reply_text("❌ 识别失败，请检查后重新输入："); return ST_WX if mode=='wx' else ST_ALI

# --- 7. 商品上架/删除 ---
async def prod_n(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['p_n'] = update.message.text
    await update.message.reply_text("💰 请输入所需积分："); return ST_PROD_PRICE
async def prod_p(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.text.isdigit(): await update.message.reply_text("❌ 请输入数字价格："); return ST_PROD_PRICE
    context.user_data['p_p'] = int(update.message.text)
    await update.message.reply_text("📁 请发送商品内容（文本/图片/视频）："); return ST_PROD_CONTENT
async def prod_c(update: Update, context: ContextTypes.DEFAULT_TYPE):
    m = update.message; tp = 'text'; fid = None; txt = m.text
    if m.photo: tp = 'photo'; fid = m.photo[-1].file_id; txt = m.caption
    elif m.video: tp = 'video'; fid = m.video.file_id; txt = m.caption
    db_query("INSERT INTO products (name, price, type, f_id, txt) VALUES (%s, %s, %s, %s, %s)", (context.user_data['p_n'], context.user_data['p_p'], tp, fid, txt), False)
    await update.message.reply_text("✅ 商品上架成功！"); await admin_home(update, context); return ConversationHandler.END

# --- 8. 转发库与销毁逻辑 ---
async def lib_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['l_c'] = update.message.text; context.user_data['l_m'] = []
    await update.message.reply_text(f"已设定指令「{update.message.text}」，请开始发送内容（支持多条），完成后点击结束。", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ 完成绑定", callback_data="l_save")]]))
    return ST_FWD_CONTENT
async def lib_con(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['l_m'].append({'cid': update.message.chat_id, 'mid': update.message.message_id})
    return ST_FWD_CONTENT
async def lib_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db_query("INSERT INTO forward_lib (cmd_text, messages) VALUES (%s, %s) ON CONFLICT (cmd_text) DO UPDATE SET messages = EXCLUDED.messages", (context.user_data['l_c'], Json(context.user_data['l_m'])), False)
    await update.callback_query.message.reply_text("✅ 转发库命令已保存！"); await admin_home(update, context); return ConversationHandler.END

async def trigger_lib(update: Update, context: ContextTypes.DEFAULT_TYPE):
    res = db_query("SELECT messages FROM forward_lib WHERE cmd_text = %s", (update.message.text.strip(),))
    if res:
        mids = [update.message.message_id]
        for m in res[0][0]:
            try:
                s = await context.bot.copy_message(update.effective_chat.id, m['cid'], m['mid'])
                mids.append(s.message_id)
            except: pass
        n = await update.message.reply_text("✅ 内容已发送，20分钟后自动销毁销毁。")
        mids.append(n.message_id)
        context.job_queue.run_once(auto_del, 1200, data={'cid': update.effective_chat.id, 'mids': mids})

async def auto_del(context: ContextTypes.DEFAULT_TYPE):
    for mid in context.job.data['mids']:
        try: await context.bot.delete_message(context.job.data['cid'], mid)
        except: pass
    await context.bot.send_message(context.job.data['cid'], "⏰ 消息已到期销毁。已购用户可再次输入命令重新获取内容。")

# --- 9. 主程序入口 ---
if __name__ == '__main__':
    init_db()
    app = ApplicationBuilder().token(TOKEN).build()

    # 综合对话处理器
    conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(flow_entry, pattern="^v_go$|^p_wx_go$|^p_ali_go$|^p_add$|^a_getid$|^a_lib$"),
            CommandHandler("admin", admin_home), CommandHandler("id", admin_home)
        ],
        states={
            ST_VIP: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_vip)],
            ST_WX: [MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u,c: (c.user_data.update({'pay_mode':'wx'}), handle_pay(u,c))[1])],
            ST_ALI: [MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u,c: (c.user_data.update({'pay_mode':'ali'}), handle_pay(u,c))[1])],
            ST_PROD_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, prod_n)],
            ST_PROD_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, prod_p)],
            ST_PROD_CONTENT: [MessageHandler(filters.ALL & ~filters.COMMAND, prod_c)],
            ST_GETID: [MessageHandler(filters.PHOTO | filters.VIDEO, lambda u,c: (u.message.reply_text(f"`{u.message.photo[-1].file_id if u.message.photo else u.message.video.file_id}`", parse_mode="Markdown"), ConversationHandler.END)[1])],
            ST_FWD_CMD: [MessageHandler(filters.TEXT & ~filters.COMMAND, lib_cmd)],
            ST_FWD_CONTENT: [CallbackQueryHandler(lib_save, pattern="^l_save$"), MessageHandler(filters.ALL & ~filters.COMMAND, lib_con)],
        },
        fallbacks=[CommandHandler("start", start)]
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("jf", jf_main))
    app.add_handler(CommandHandler("dh", dh_main))
    app.add_handler(conv)
    app.add_handler(CallbackQueryHandler(start, pattern="^back_home$"))
    app.add_handler(CallbackQueryHandler(jf_main, pattern="^j_main$"))
    app.add_handler(CallbackQueryHandler(jf_sign, pattern="^j_sign$"))
    app.add_handler(CallbackQueryHandler(jf_pay, pattern="^j_pay$"))
    app.add_handler(CallbackQueryHandler(jf_history, pattern="^j_hist$"))
    app.add_handler(CallbackQueryHandler(dh_main, pattern="^d_main$"))
    app.add_handler(CallbackQueryHandler(handle_buy, pattern="^buy_"))
    app.add_handler(CallbackQueryHandler(conf_buy, pattern="^conf_buy$"))
    app.add_handler(CallbackQueryHandler(admin_home, pattern="^a_home$"))
    app.add_handler(CallbackQueryHandler(admin_prod_list, pattern="^a_prod$"))
    app.add_handler(CallbackQueryHandler(flow_entry, pattern="^v_start$|^p_wx$|^p_ali$"))
    app.add_handler(CallbackQueryHandler(lambda u,c: db_query("DELETE FROM products WHERE id=%s",(u.callback_query.data.split("_")[2],),False) or admin_prod_list(u,c), pattern="^p_del_"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, trigger_lib))

    print("--- 机器人已启动 ---")
    app.run_polling()
