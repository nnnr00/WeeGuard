import os
import random
import psycopg2
import asyncio
from datetime import datetime, timedelta
from psycopg2.extras import Json
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, ContextTypes, 
    MessageHandler, CallbackQueryHandler, filters, ConversationHandler
)

# --- 【需手动配置区】 ---
ID_WELCOME = "这里填入首页图ID"
ID_VIP_GUIDE = "这里填入VIP教程图ID"
ID_WX_PAY = "微信码图ID"
ID_WX_GUIDE = "微信教程图ID"
ID_ALI_PAY = "支付宝码图ID"
ID_ALI_GUIDE = "支付宝教程图ID"
VIP_GROUP_LINK = "https://t.me/your_group_link"

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
DB_URL = os.getenv("DATABASE_URL").replace("postgres://", "postgresql://")

# --- 数据库表初始化 ---
def init_db():
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    cur.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id BIGINT PRIMARY KEY, points INTEGER DEFAULT 0, last_checkin DATE,
        v_fails INTEGER DEFAULT 0, v_lock TIMESTAMP,
        wx_done BOOLEAN DEFAULT FALSE, wx_fails INTEGER DEFAULT 0, wx_lock TIMESTAMP,
        ali_done BOOLEAN DEFAULT FALSE, ali_fails INTEGER DEFAULT 0, ali_lock TIMESTAMP)''')
    cur.execute('''CREATE TABLE IF NOT EXISTS forward_lib (cmd_text TEXT PRIMARY KEY, messages JSONB)''')
    cur.execute('''CREATE TABLE IF NOT EXISTS products (
        id SERIAL PRIMARY KEY, name TEXT, price INTEGER, ptype TEXT, fid TEXT, txt TEXT)''')
    cur.execute('''CREATE TABLE IF NOT EXISTS redemptions (user_id BIGINT, p_id INTEGER)''')
    cur.execute('''CREATE TABLE IF NOT EXISTS history (
        user_id BIGINT, action TEXT, amount TEXT, ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    conn.commit()
    cur.close(); conn.close()

# --- 状态机 ---
(V_ORD, WX_ORD, ALI_ORD, P_NAME, P_PRICE, P_CONT, L_CMD, L_CONT, GET_ID) = range(9)

# --- 修正后的查询函数：显式指定列名防止索引错乱 ---
def get_u(uid):
    conn = psycopg2.connect(DB_URL); cur = conn.cursor()
    # 显式查询所有需要的列，确保索引固定
    cols = "points, last_checkin, v_fails, v_lock, wx_done, wx_fails, wx_lock, ali_done, ali_fails, ali_lock"
    cur.execute(f"SELECT {cols} FROM users WHERE user_id = %s", (uid,))
    u = cur.fetchone()
    if not u:
        cur.execute("INSERT INTO users (user_id) VALUES (%s)", (uid,))
        conn.commit()
        cur.execute(f"SELECT {cols} FROM users WHERE user_id = %s", (uid,))
        u = cur.fetchone()
    cur.close(); conn.close()
    return u # 索引: 0:pts, 1:check, 2:vf, 3:vlock, 4:wxd, 5:wxf, 6:wxlock, 7:ald, 8:alf, 9:allock

def log_h(uid, act, amt):
    conn = psycopg2.connect(DB_URL); cur = conn.cursor()
    cur.execute("INSERT INTO history (user_id, action, amount) VALUES (%s, %s, %s)", (uid, act, amt))
    conn.commit(); cur.close(); conn.close()

# --- 核心首页 ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id; u = get_u(uid)
    # 修正索引：u[3] 对应 v_lock
    v_lock = u[3]
    v_locked = v_lock and isinstance(v_lock, datetime) and datetime.now() < v_lock
    
    txt = "👋 欢迎使用【VIP服务机器人】\n━━━━━━━━━━━━━\n💎 验证身份入群\n🪙 签到领分兑换商品\n📦 获取私密资源包"
    kbd = [
        [InlineKeyboardButton("💎 身份验证" if not v_locked else "🔒 锁定中", callback_data="v_start" if not v_locked else "v_is_locked")],
        [InlineKeyboardButton("🪙 积分钱包", callback_data="j_page"), InlineKeyboardButton("🎁 兑换中心", callback_data="d_page")]
    ]
    
    # 统一回复逻辑
    msg = update.callback_query.message if update.callback_query else update.message
    if ID_WELCOME.startswith("这里"):
        if update.callback_query: await msg.edit_text(txt, reply_markup=InlineKeyboardMarkup(kbd))
        else: await msg.reply_text(txt, reply_markup=InlineKeyboardMarkup(kbd))
    else:
        if update.callback_query: await msg.reply_photo(ID_WELCOME, caption=txt, reply_markup=InlineKeyboardMarkup(kbd))
        else: await msg.reply_photo(ID_WELCOME, caption=txt, reply_markup=InlineKeyboardMarkup(kbd))

# --- 积分钱包 ---
async def jf_ui(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = get_u(update.effective_user.id)
    txt = f"🪙 **积分钱包**\n\n💰 当前余额：**{u[0]}**\n🆔 账户ID：`{update.effective_user.id}`"
    kbd = [
        [InlineKeyboardButton("📅 每日签到", callback_data="j_sign"), InlineKeyboardButton("💳 积分充值", callback_data="j_pay")],
        [InlineKeyboardButton("📝 账单明细", callback_data="j_hist"), InlineKeyboardButton("🏠 返回首页", callback_data="home")]
    ]
    await update.callback_query.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kbd), parse_mode="Markdown")

async def jf_sign(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; uid = q.from_user.id; u = get_u(uid); today = datetime.now().date()
    if u[1] == today: await q.answer("❌ 今日已签到", show_alert=True); return
    r = random.randint(3, 8); conn = psycopg2.connect(DB_URL); cur = conn.cursor()
    cur.execute("UPDATE users SET points = points + %s, last_checkin = %s WHERE user_id = %s", (r, today, uid))
    conn.commit(); cur.close(); conn.close()
    log_h(uid, "每日签到", f"+{r}"); await q.answer(f"🎉 获得 {r} 积分！", show_alert=True); await jf_ui(update, context)

async def jf_pay_ui(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = get_u(update.effective_user.id)
    # 索引对应：4:wx_done, 6:wx_lock, 7:ali_done, 9:ali_lock
    w_l = u[4] or (u[6] and isinstance(u[6], datetime) and datetime.now() < u[6])
    a_l = u[7] or (u[9] and isinstance(u[9], datetime) and datetime.now() < u[9])
    txt = "💳 **充值中心 (5元=100分)**\n⚠️ 微信/支付宝各限一次，请勿重复充值。"
    kbd = [[InlineKeyboardButton("💹 微信支付" if not w_l else "❌ 锁定/已充", callback_data="p_wx" if not w_l else "pay_is_locked")],
           [InlineKeyboardButton("💹 支付宝" if not a_l else "❌ 锁定/已充", callback_data="p_ali" if not a_l else "pay_is_locked")],
           [InlineKeyboardButton("🔙 返回", callback_data="j_page")]]
    await update.callback_query.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kbd), parse_mode="Markdown")

# --- 兑换中心 ---
async def dh_ui(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id; conn = psycopg2.connect(DB_URL); cur = conn.cursor()
    cur.execute("SELECT id, name, price FROM products"); prods = cur.fetchall()
    cur.execute("SELECT p_id FROM redemptions WHERE user_id = %s", (uid,)); b_list = [r[0] for r in cur.fetchall()]
    cur.close(); conn.close()
    kbd = [[InlineKeyboardButton("✅ [测试] 哈哈" if -1 in b_list else "🛒 [测试] 哈哈 (0分)", callback_data="buy_-1")]]
    for p in prods:
        btn = f"✅ {p[1]}" if p[0] in b_list else f"🛒 {p[1]} ({p[2]}分)"
        kbd.append([InlineKeyboardButton(btn, callback_data=f"buy_{p[0]}")])
    kbd.append([InlineKeyboardButton("🏠 返回首页", callback_data="home")])
    txt = "🎁 **兑换中心**\n请选择心仪的商品兑换："
    if update.callback_query: await update.callback_query.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kbd), parse_mode="Markdown")
    else: await update.message.reply_text(txt, reply_markup=InlineKeyboardMarkup(kbd), parse_mode="Markdown")

# --- 验证回调 ---
async def on_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; d = q.data; await q.answer()
    if d == "home": await start(update, context)
    elif d == "j_page": await jf_ui(update, context)
    elif d == "j_sign": await jf_sign(update, context)
    elif d == "j_pay": await jf_pay_ui(update, context)
    elif d == "v_is_locked": await q.answer("❌ 身份验证功能锁定中，请稍后再试", show_alert=True)
    elif d == "pay_is_locked": await q.answer("❌ 该充值通道已使用或锁定中", show_alert=True)
    elif d == "j_hist":
        conn = psycopg2.connect(DB_URL); cur = conn.cursor()
        cur.execute("SELECT action, amount, ts FROM history WHERE user_id = %s ORDER BY ts DESC LIMIT 10", (q.from_user.id,))
        rows = cur.fetchall(); cur.close(); conn.close()
        lt = "\n".join([f"• `{r[2].strftime('%m-%d')}` {r[0]} ({r[1]})" for r in rows])
        await q.edit_message_text(f"💰 最近账单记录：\n\n{lt or '暂无'}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="j_page")]]), parse_mode="Markdown")
    elif d == "v_start":
        await q.message.reply_photo(ID_VIP_GUIDE, "💎 **VIP验证**\n请支付后点击下方验证订单。", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ 我已支付，开始验证", callback_data="v_go")]]))
    elif d == "v_go": await q.message.reply_text("请输入订单号(20260开头)："); return V_ORD
    elif d == "p_wx":
        await q.message.reply_photo(ID_WX_PAY, "💹 **微信支付 (5元)**\n\n请支付后验证。", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ 已支付，开始验证", callback_data="wx_go")]]))
    elif d == "wx_go": await q.message.reply_photo(ID_WX_GUIDE, "请输入微信【交易单号】："); return WX_ORD
    elif d == "p_ali":
        await q.message.reply_photo(ID_ALI_PAY, "💹 **支付宝 (5元)**\n\n请支付后验证。", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ 已支付，开始验证", callback_data="ali_go")]]))
    elif d == "ali_go": await q.message.reply_photo(ID_ALI_GUIDE, "请输入支付宝【商家订单号】："); return ALI_ORD
    elif d.startswith("buy_"):
        pid = int(d.split("_")[1]); uid = q.from_user.id; conn = psycopg2.connect(DB_URL); cur = conn.cursor()
        cur.execute("SELECT 1 FROM redemptions WHERE user_id = %s AND p_id = %s", (uid, pid))
        if cur.fetchone():
            if pid == -1: await q.message.reply_text("哈哈！")
            else:
                cur.execute("SELECT ptype, fid, txt FROM products WHERE id = %s", (pid,)); p = cur.fetchone()
                if p[0]=='text': await q.message.reply_text(p[2])
                elif p[0]=='photo': await q.message.reply_photo(p[1], caption=p[2])
                elif p[0]=='video': await q.message.reply_video(p[1], caption=p[2])
            cur.close(); conn.close(); return
        if pid == -1:
            cur.execute("INSERT INTO redemptions (user_id, p_id) VALUES (%s, %s)", (uid, -1))
            conn.commit(); await q.answer("兑换成功！"); await dh_ui(update, context)
        else:
            cur.execute("SELECT name, price FROM products WHERE id = %s", (pid,)); p = cur.fetchone()
            context.user_data['tmp_b'] = {'id': pid, 'price': p[1], 'name': p[0]}
            await q.edit_message_text(f"❓ 确定消耗 {p[1]} 积分兑换【{p[0]}】吗？", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ 确定", callback_data="buy_confirm"), InlineKeyboardButton("❌ 取消", callback_data="d_page")]]))
        cur.close(); conn.close()
    elif d == "buy_confirm":
        b = context.user_data.get('tmp_b'); uid = q.from_user.id; u = get_u(uid)
        if u[0] < b['price']: await q.answer("❌ 余额不足", show_alert=True); await dh_ui(update, context); return
        conn = psycopg2.connect(DB_URL); cur = conn.cursor()
        cur.execute("UPDATE users SET points = points - %s WHERE user_id = %s", (b['price'], uid))
        cur.execute("INSERT INTO redemptions (user_id, p_id) VALUES (%s, %s)", (uid, b['id']))
        log_h(uid, f"兑换:{b['name']}", f"-{b['price']}"); conn.commit(); cur.close(); conn.close()
        await q.answer("🎉 兑换成功！"); await dh_ui(update, context)

# --- 验证处理器 ---
async def val_proc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id; msg = update.message.text.strip(); s = context.user_data.get('state')
    conn = psycopg2.connect(DB_URL); cur = conn.cursor()
    
    if s == V_ORD:
        if msg.startswith("20260"):
            cur.execute("UPDATE users SET v_fails = 0 WHERE user_id = %s", (uid,))
            await update.message.reply_text("✅ VIP验证成功！", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🚀 点击入群", url=VIP_GROUP_LINK)]]))
            await start(update, context)
        else:
            cur.execute("UPDATE users SET v_fails = v_fails + 1, v_lock = %s WHERE user_id = %s RETURNING v_fails", (datetime.now()+timedelta(hours=5), uid))
            if cur.fetchone()[0] >= 2: await update.message.reply_text("❌ 失败2次，锁定5小时。"); await start(update, context); return ConversationHandler.END
            await update.message.reply_text("❌ 识别错误，请重新输入："); return V_ORD
    elif s in [WX_ORD, ALI_ORD]:
        pre = "4200" if s == WX_ORD else "4768"; mname = "wx" if s == WX_ORD else "ali"
        if msg.startswith(pre):
            cur.execute(f"UPDATE users SET points = points + 100, {mname}_done = TRUE WHERE user_id = %s", (uid,))
            log_h(uid, f"{mname}充值", "+100"); await update.message.reply_text("✅ 充值成功！"); await jf_ui(update, context)
        else:
            cur.execute(f"UPDATE users SET {mname}_fails = {mname}_fails + 1, {mname}_lock = %s WHERE user_id = %s RETURNING {mname}_fails", (datetime.now()+timedelta(hours=10), uid))
            if cur.fetchone()[0] >= 2: await update.message.reply_text("❌ 锁定10小时。"); await jf_ui(update, context); return ConversationHandler.END
            await update.message.reply_text("❌ 单号错误，请重试："); return s
    
    conn.commit(); cur.close(); conn.close(); return ConversationHandler.END

# --- 管理员后台 ---
async def adm_h(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    kbd = [[InlineKeyboardButton("📸 获取ID", callback_data="a_id"), InlineKeyboardButton("📦 转发库", callback_data="a_lib")],
           [InlineKeyboardButton("🛍 商品管理", callback_data="a_prod")]]
    await update.message.reply_text("🛠 管理员后台", reply_markup=InlineKeyboardMarkup(kbd))

async def adm_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; d = q.data; await q.answer()
    if d == "a_id": await q.message.reply_text("请发送图/视频："); return GET_ID
    elif d == "a_prod":
        conn = psycopg2.connect(DB_URL); cur = conn.cursor(); cur.execute("SELECT id, name FROM products"); ps = cur.fetchall(); cur.close(); conn.close()
        kbd = [[InlineKeyboardButton("➕ 上架", callback_data="p_add")]]
        for p in ps: kbd.append([InlineKeyboardButton(f"🗑 删除:{p[1]}", callback_data=f"p_del_{p[0]}")])
        await q.edit_message_text("🛍 商品管理", reply_markup=InlineKeyboardMarkup(kbd))
    elif d == "p_add": await q.message.reply_text("输入商品名："); return P_NAME
    elif d.startswith("p_del_"):
        pid = d.split("_")[2]; conn = psycopg2.connect(DB_URL); cur = conn.cursor()
        cur.execute("DELETE FROM products WHERE id = %s", (pid,)); conn.commit(); cur.close(); conn.close()
        await q.answer("已删除"); await adm_cb(update, context)
    elif d == "a_lib": await q.message.reply_text("输入触发指令："); return L_CMD

async def fwd_proc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = psycopg2.connect(DB_URL); cur = conn.cursor()
    cur.execute("SELECT messages FROM forward_lib WHERE cmd_text = %s", (update.message.text.strip(),))
    res = cur.fetchone(); cur.close(); conn.close()
    if res:
        mids = [update.message.message_id]
        for m in res[0]:
            try: s = await context.bot.copy_message(update.effective_chat.id, m['cid'], m['mid']); mids.append(s.message_id)
            except: pass
        n = await update.message.reply_text("✅ 已全部发送，20分钟后销毁。")
        context.job_queue.run_once(lambda c: [asyncio.create_task(c.bot.delete_message(update.effective_chat.id, mid)) for mid in mids+[n.message_id]], 1200)

if __name__ == '__main__':
    init_db()
    app = ApplicationBuilder().token(TOKEN).build()
    
    v_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(on_cb, pattern="^v_go$|^wx_go$|^ali_go$"), 
                      CallbackQueryHandler(adm_cb, pattern="^p_add$|^a_id$|^a_lib$")],
        states={
            V_ORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u,c: [c.user_data.update({'state':V_ORD}), val_proc(u,c)][1])],
            WX_ORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u,c: [c.user_data.update({'state':WX_ORD}), val_proc(u,c)][1])],
            ALI_ORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u,c: [c.user_data.update({'state':ALI_ORD}), val_proc(u,c)][1])],
            P_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u,c: [c.user_data.update({'pn':u.message.text}), u.message.reply_text("输入价格：")][1] and P_PRICE)],
            P_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u,c: [c.user_data.update({'pp':u.message.text}), u.message.reply_text("发送内容：")][1] and P_CONT)],
            P_CONT: [MessageHandler(filters.ALL & ~filters.COMMAND, lambda u,c: [psycopg2.connect(DB_URL).cursor().execute("INSERT INTO products (name,price,ptype,fid,txt) VALUES (%s,%s,%s,%s,%s)", (c.user_data['pn'], c.user_data['pp'], 'photo' if u.message.photo else 'text', u.message.photo[-1].file_id if u.message.photo else None, u.message.text or u.message.caption)), u.message.reply_text("✅ 上架成功")][1] and ConversationHandler.END)],
            GET_ID: [MessageHandler(filters.ALL & ~filters.COMMAND, lambda u,c: [u.message.reply_text(f"`{u.message.photo[-1].file_id if u.message.photo else u.message.video.file_id}`")][1] and ConversationHandler.END)],
            L_CMD: [MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u,c: [c.user_data.update({'lc':u.message.text, 'lm':[]}), u.message.reply_text("发送内容，完成后点结束", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("结束", callback_data="l_fin")]]))][1] and L_CONT)],
            L_CONT: [CallbackQueryHandler(lambda u,c: [psycopg2.connect(DB_URL).cursor().execute("INSERT INTO forward_lib (cmd_text,messages) VALUES (%s,%s)", (c.user_data['lc'], Json(c.user_data['lm']))), u.callback_query.message.reply_text("✅ 保存成功")][1] and ConversationHandler.END, pattern="^l_fin$"), MessageHandler(filters.ALL & ~filters.COMMAND, lambda u,c: c.user_data['lm'].append({'cid':u.message.chat_id, 'mid':u.message.message_id}))]
        },
        fallbacks=[CommandHandler("start", start)],
        per_message=True # 解决 PTBUserWarning
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("jf", jf_ui))
    app.add_handler(CommandHandler("dh", dh_ui))
    app.add_handler(CommandHandler("admin", adm_h))
    app.add_handler(v_conv)
    app.add_handler(CallbackQueryHandler(on_cb))
    app.add_handler(CallbackQueryHandler(adm_cb, pattern="^p_del_|^a_prod$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, fwd_proc))
    app.run_polling()
