import os, datetime, random, psycopg2, asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler, 
    filters, ContextTypes, ConversationHandler
)

# ================= 配置区 =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
ADMIN_IDS = [int(i) for i in os.getenv("ADMIN_IDS", "").split(",") if i]

# --- FILE ID 配置 (在这里修改) ---
ID_VIP_INFO = "FILE_ID_1"        # VIP特权介绍图
ID_ORDER_TUTORIAL = "FILE_ID_2"   # 验证订单号教程图
ID_WX_PAY = "FILE_ID_3"          # 微信支付码
ID_WX_GUIDE = "FILE_ID_4"        # 微信订单号教程图
ID_ALI_PAY = "FILE_ID_5"         # 支付宝支付码
ID_ALI_GUIDE = "FILE_ID_6"       # 支付宝订单号教程图

# 状态定义
(STATE_VERIFY, STATE_RECHARGE, STATE_ADMIN_CMD_NAME, STATE_ADMIN_CMD_CONT, 
 STATE_ADMIN_PROD_NAME, STATE_ADMIN_PROD_PRICE, STATE_ADMIN_PROD_CONT) = range(7)

# --- 数据库初始化 ---
def get_db():
    return psycopg2.connect(DATABASE_URL, sslmode='require')

def init_db():
    conn = get_db(); cur = conn.cursor()
    cur.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id BIGINT PRIMARY KEY, credits INTEGER DEFAULT 0, last_sign_in DATE,
        wx_used BOOLEAN DEFAULT FALSE, ali_used BOOLEAN DEFAULT FALSE,
        recharge_fails INTEGER DEFAULT 0, recharge_lock TIMESTAMP,
        fail_count INTEGER DEFAULT 0, last_fail_time TIMESTAMP)''')
    cur.execute("CREATE TABLE IF NOT EXISTS commands (cmd_name TEXT PRIMARY KEY)")
    cur.execute("CREATE TABLE IF NOT EXISTS contents (id SERIAL PRIMARY KEY, cmd_name TEXT REFERENCES commands(cmd_name) ON DELETE CASCADE, chat_id BIGINT, message_id BIGINT)")
    cur.execute("CREATE TABLE IF NOT EXISTS products (id SERIAL PRIMARY KEY, name TEXT, price INTEGER, content_id TEXT, content_type TEXT)")
    cur.execute("CREATE TABLE IF NOT EXISTS user_redeemed (user_id BIGINT, product_id INTEGER, PRIMARY KEY(user_id, product_id))")
    cur.execute("CREATE TABLE IF NOT EXISTS logs (id SERIAL PRIMARY KEY, user_id BIGINT, amount TEXT, reason TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    conn.commit(); cur.close(); conn.close()

def add_log(user_id, amount, reason):
    conn = get_db(); cur = conn.cursor()
    cur.execute("INSERT INTO logs (user_id, amount, reason) VALUES (%s, %s, %s)", (user_id, amount, reason))
    conn.commit(); cur.close(); conn.close()

# --- 首页逻辑 ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT fail_count, last_fail_time FROM users WHERE user_id = %s", (user_id,))
    res = cur.fetchone(); cur.close(); conn.close()
    
    lock_text = "🛡️ 立即开启验证"
    if res and res[0] >= 2 and res[1]:
        if datetime.datetime.now() < res[1] + datetime.timedelta(hours=5):
            lock_text = "❌ 验证锁定中"

    text = "👋 欢迎加入【VIP中转】！我是守门员小卫，你的身份验证小助手~\n\n📢 小卫小卫，守门员小卫！\n一键入群，小卫帮你搞定！\n新人来报到，小卫查身份！"
    keyboard = [
        [InlineKeyboardButton(lock_text, callback_query_data="go_verify")],
        [InlineKeyboardButton("💰 积分中心", callback_query_data="go_jf"), InlineKeyboardButton("🎁 兑换中心", callback_query_data="go_dh")]
    ]
    markup = InlineKeyboardMarkup(keyboard)
    if update.message: await update.message.reply_text(text, reply_markup=markup)
    else: await update.callback_query.edit_message_text(text, reply_markup=markup)
    return ConversationHandler.END

# --- 身份验证流程 ---
async def verify_step_1(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    # 检查是否锁定
    uid = query.from_user.id
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT last_fail_time, fail_count FROM users WHERE user_id = %s", (uid,))
    res = cur.fetchone(); cur.close(); conn.close()
    if res and res[1] >= 2 and res[0] and datetime.datetime.now() < res[0] + datetime.timedelta(hours=5):
        await query.answer("❌ 验证功能锁定中，请5小时后再试", show_alert=True); return
    
    await query.message.reply_photo(photo=ID_VIP_INFO, caption="💎 **VIP会员特权说明**：\n✅ 专属中转通道\n✅ 优先审核入群\n✅ 7x24小时客服支持\n✅ 定期福利活动", 
                                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ 我已付款，开始验证", callback_query_data="verify_step_2")]]), parse_mode="Markdown")

async def verify_step_2(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.message.reply_photo(photo=ID_ORDER_TUTORIAL, caption="📖 **查询教程**：\n请在账单详情中找到订单号并输入。\n\n👇 **请在下方输入订单号：**")
    return STATE_VERIFY

async def verify_logic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    order_id = update.message.text.strip()
    if order_id.startswith("20260"):
        await update.message.reply_text("🎉 验证成功！欢迎加入 VIP 家族！", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔗 点击入群", url="https://t.me/your_link")]]))
        return await start(update, context)
    else:
        conn = get_db(); cur = conn.cursor()
        cur.execute("INSERT INTO users (user_id, fail_count, last_fail_time) VALUES (%s, 1, %s) ON CONFLICT (user_id) DO UPDATE SET fail_count=users.fail_count+1, last_fail_time=%s RETURNING fail_count", (uid, datetime.datetime.now(), datetime.datetime.now()))
        count = cur.fetchone()[0]; conn.commit(); cur.close(); conn.close()
        if count >= 2:
            await update.message.reply_text("❌ 订单错误。已连续失败2次，请5小时后再试。")
            return await start(update, context)
        await update.message.reply_text("⚠️ 未查询到订单信息，请重试：")
        return STATE_VERIFY

# --- 积分中心 ---
async def jf_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT credits FROM users WHERE user_id = %s", (uid,))
    res = cur.fetchone(); credits = res[0] if res else 0; cur.close(); conn.close()
    text = f"✨ **积分中心** ✨\n━━━━━━━━━━━━━━\n💰 当前余额：`{credits}` 积分"
    keyboard = [[InlineKeyboardButton("📝 每日签到", callback_query_data="sign_in"), InlineKeyboardButton("💳 充值积分", callback_query_data="recharge_home")],
                [InlineKeyboardButton("📊 余额记录", callback_query_data="view_logs"), InlineKeyboardButton("🔙 返回首页", callback_query_data="back_home")]]
    if update.callback_query: await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    else: await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def sign_in(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    today = datetime.date.today()
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT last_sign_in FROM users WHERE user_id = %s", (uid,))
    res = cur.fetchone()
    if res and res[0] == today: await update.callback_query.answer("❌ 今日已签到", show_alert=True)
    else:
        pts = random.randint(3, 8)
        cur.execute("INSERT INTO users (user_id, credits, last_sign_in) VALUES (%s,%s,%s) ON CONFLICT (user_id) DO UPDATE SET credits=users.credits+%s, last_sign_in=%s", (uid, pts, today, pts, today))
        conn.commit(); add_log(uid, f"+{pts}", "每日签到")
        await update.callback_query.answer(f"🎉 成功领取 {pts} 积分！", show_alert=True)
    cur.close(); conn.close(); return await jf_menu(update, context)

async def view_logs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT amount, reason, created_at FROM logs WHERE user_id = %s ORDER BY created_at DESC LIMIT 10", (uid,))
    rows = cur.fetchall(); cur.close(); conn.close()
    log_text = "📊 **最近10条账单记录**\n\n"
    for a, r, t in rows: log_text += f"📅 `{t.strftime('%m-%d %H:%M')}` | `{a}` | {r}\n"
    await update.callback_query.edit_message_text(log_text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_query_data="go_jf")]]), parse_mode="Markdown")

# --- 充值逻辑 ---
async def recharge_home(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT recharge_lock FROM users WHERE user_id = %s", (uid,))
    res = cur.fetchone(); cur.close(); conn.close()
    if res and res[0] and datetime.datetime.now() < res[0]:
        await update.callback_query.answer("❌ 充值锁定中，请稍后再试", show_alert=True); return
    text = "💳 **选择充值方式**\n\n⚠️ **温馨提示**：\n微信与支付宝每人仅限充值一次。请勿重复充值！"
    keyboard = [[InlineKeyboardButton("🟢 微信 (5元=100积分)", callback_query_data="pay_wx")],
                [InlineKeyboardButton("🔵 支付宝 (5元=100积分)", callback_query_data="pay_ali")],
                [InlineKeyboardButton("🔙 返回", callback_query_data="go_jf")]]
    await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def pay_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    method = "wx" if query.data == "pay_wx" else "ali"
    uid = query.from_user.id
    conn = get_db(); cur = conn.cursor()
    cur.execute(f"SELECT {method}_used FROM users WHERE user_id = %s", (uid,))
    res = cur.fetchone()
    if res and res[0]: await query.answer("❌ 您已充值过，请勿重复操作", show_alert=True); return
    context.user_data['method'] = method
    img = ID_WX_PAY if method == "wx" else ID_ALI_PAY
    await query.message.reply_photo(photo=img, caption="💰 **扫码支付 5.00 元**\n完成后点击下方按钮验证。", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ 我已支付，开始验证", callback_query_data="pay_verify_input")]]))

async def pay_verify_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    method = context.user_data.get('method')
    img = ID_WX_GUIDE if method == "wx" else ID_ALI_GUIDE
    txt = "请输入微信交易单号：" if method == "wx" else "请输入支付宝商家订单号："
    await update.callback_query.message.reply_photo(photo=img, caption=f"📖 **查找教程**：\n在详情页复制订单号。\n\n👇 **{txt}**")
    return STATE_RECHARGE

async def verify_recharge_logic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    oid = update.message.text.strip()
    method = context.user_data.get('method')
    success = (method == "wx" and oid.startswith("4200")) or (method == "ali" and oid.startswith("4768"))
    conn = get_db(); cur = conn.cursor()
    if success:
        cur.execute(f"UPDATE users SET credits=credits+100, {method}_used=TRUE, recharge_fails=0 WHERE user_id=%s", (uid,))
        conn.commit(); add_log(uid, "+100", f"{method.upper()}充值")
        await update.message.reply_text("🎉 充值成功！已获得100积分。"); cur.close(); conn.close()
        return await jf_menu(update, context)
    else:
        cur.execute("UPDATE users SET recharge_fails=recharge_fails+1, recharge_lock=%s WHERE user_id=%s RETURNING recharge_fails", (datetime.datetime.now()+datetime.timedelta(hours=10), uid))
        fails = cur.fetchone()[0]; conn.commit(); cur.close(); conn.close()
        if fails >= 2: await update.message.reply_text("❌ 错误2次。充值功能锁定10小时。"); return await jf_menu(update, context)
        await update.message.reply_text("⚠️ 订单识别失败，还剩一次机会，请重新输入："); return STATE_RECHARGE

# --- 兑换中心 ---
async def dh_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT id, name, price FROM products"); prods = cur.fetchall()
    cur.execute("SELECT product_id FROM user_redeemed WHERE user_id=%s", (uid,)); bought = [r[0] for r in cur.fetchall()]
    cur.close(); conn.close()
    keyboard = [[InlineKeyboardButton("🛠 测试 (0积分)", callback_query_data="buy_0")]]
    for pid, name, price in prods:
        btn = f"✅ 已拥有: {name}" if pid in bought else f"💎 {price}积分 | {name}"
        keyboard.append([InlineKeyboardButton(btn, callback_query_data=f"buy_{pid}")])
    keyboard.append([InlineKeyboardButton("🔙 返回", callback_query_data="back_home")])
    if update.callback_query: await update.callback_query.edit_message_text("🎁 兑换中心", reply_markup=InlineKeyboardMarkup(keyboard))
    else: await update.message.reply_text("🎁 兑换中心", reply_markup=InlineKeyboardMarkup(keyboard))

async def handle_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; uid = query.from_user.id; pid = int(query.data.split("_")[1])
    if pid == 0: await query.answer("兑换成功"); await query.message.reply_text("测试结果：哈哈"); return
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT 1 FROM user_redeemed WHERE user_id=%s AND product_id=%s", (uid, pid))
    if cur.fetchone():
        cur.execute("SELECT name, content_id, content_type FROM products WHERE id=%s", (pid,))
        p = cur.fetchone(); await query.answer(f"查看: {p[0]}")
        await send_content(query.message, p[1], p[2]); cur.close(); conn.close(); return
    
    if "confirm" not in query.data:
        cur.execute("SELECT name, price FROM products WHERE id=%s", (pid,))
        p = cur.fetchone(); cur.close(); conn.close()
        await query.edit_message_text(f"❓ 确认花费 {p[1]} 积分兑换【{p[0]}】？", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ 确定", callback_query_data=f"buy_{pid}_confirm"), InlineKeyboardButton("❌ 取消", callback_query_data="go_dh")]]))
    else:
        cur.execute("SELECT name, price, content_id, content_type FROM products WHERE id=%s", (pid,))
        p = cur.fetchone(); cur.execute("SELECT credits FROM users WHERE user_id=%s", (uid,))
        balance = cur.fetchone()[0]
        if balance < p[1]: await query.answer("❌ 余额不足", show_alert=True); cur.close(); conn.close(); return await dh_menu(update, context)
        cur.execute("UPDATE users SET credits=credits-%s WHERE user_id=%s", (p[1], uid))
        cur.execute("INSERT INTO user_redeemed (user_id, product_id) VALUES (%s,%s)", (uid, pid))
        conn.commit(); add_log(uid, f"-{p[1]}", f"兑换: {p[0]}"); await query.answer("兑换成功")
        await send_content(query.message, p[2], p[3]); cur.close(); conn.close(); return await dh_menu(update, context)

async def send_content(msg, cid, ctype):
    if ctype == "text": await msg.reply_text(cid)
    elif ctype == "photo": await msg.reply_photo(cid)
    elif ctype == "video": await msg.reply_video(cid)

# --- 阅后即焚转发 ---
async def delete_msg_job(context: ContextTypes.DEFAULT_TYPE):
    j = context.job
    for mid in j.data['mids']:
        try: await context.bot.delete_message(j.data['chat'], mid)
        except: pass
    await context.bot.send_message(j.data['chat'], "⌛️ **权限过期**\n如需再次查看请重新获取。", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 返回首页", callback_query_data="back_home")]]))

async def handle_fwd_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cmd = update.message.text.strip(); conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT chat_id, message_id FROM contents WHERE cmd_name=%s", (cmd,))
    items = cur.fetchall(); cur.close(); conn.close()
    if not items: return
    mids = [update.message.message_id]
    for cid, mid in items:
        m = await context.bot.copy_message(update.effective_chat.id, cid, mid)
        mids.append(m.message_id)
    notif = await update.message.reply_text("✅ 已发送，20分钟后销毁。")
    mids.append(notif.message_id)
    context.job_queue.run_once(delete_msg_job, 1200, data={'chat': update.effective_chat.id, 'mids': mids})
    await start(update, context)

# --- 管理员逻辑 (略: 包含 /admin 的商品和转发库管理) ---
async def admin_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS: return
    kb = [[InlineKeyboardButton("📂 转发库", callback_query_data="adm_cmd"), InlineKeyboardButton("🎁 商城管理", callback_query_data="adm_shop")],
          [InlineKeyboardButton("🖼 获取 ID", callback_query_data="adm_id")]]
    await update.message.reply_text("🛠 管理后台", reply_markup=InlineKeyboardMarkup(kb))

if __name__ == '__main__':
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()
    
    conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(verify_step_2, pattern="^verify_step_2$"), 
                      CallbackQueryHandler(pay_verify_input, pattern="^pay_verify_input$")],
        states={
            STATE_VERIFY: [MessageHandler(filters.TEXT & ~filters.COMMAND, verify_logic)],
            STATE_RECHARGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, verify_recharge_logic)]
        },
        fallbacks=[CommandHandler("start", start)],
        allow_reentry=True
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_main))
    app.add_handler(conv_handler)
    app.add_handler(CallbackQueryHandler(start, pattern="^back_home$"))
    app.add_handler(CallbackQueryHandler(jf_menu, pattern="^go_jf$"))
    app.add_handler(CallbackQueryHandler(dh_menu, pattern="^go_dh$"))
    app.add_handler(CallbackQueryHandler(sign_in, pattern="^sign_in$"))
    app.add_handler(CallbackQueryHandler(recharge_home, pattern="^recharge_home$"))
    app.add_handler(CallbackQueryHandler(pay_step, pattern="^pay_wx$|^pay_ali$"))
    app.add_handler(CallbackQueryHandler(handle_buy, pattern="^buy_"))
    app.add_handler(CallbackQueryHandler(view_logs, pattern="^view_logs$"))
    app.add_handler(CallbackQueryHandler(verify_step_1, pattern="^go_verify$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_fwd_cmd))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, start))

    app.run_polling()
