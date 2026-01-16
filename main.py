import os
import logging
import random
import psycopg2
from datetime import datetime, timedelta, date
import pytz
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, ContextTypes, CommandHandler, 
    CallbackQueryHandler, MessageHandler, ConversationHandler, filters
)

# --- 1. 配置区域 ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
ADMIN_ID = int(os.getenv("ADMIN_ID"))

# ★★★ 请在这里填入你的 FILE ID (运行后用 /admin 获取填入) ★★★
FILE_CONFIG = {
    "vip_intro": None,  # VIP特权说明下方的图片/视频
    "vip_pay_guide": None, # "我已付款"后显示的查找订单号教程图片
    "wx_pay_qr": None, # 微信充值页面的图片
    "wx_order_guide": None, # 微信查找订单号教程图片
    "ali_pay_qr": None, # 支付宝充值页面的图片
    "ali_order_guide": None # 支付宝查找订单号教程图片
}

# --- 2. 状态定义 ---
# Conversation States
(
    WAIT_VIP_ORDER, 
    WAIT_WX_ORDER, 
    WAIT_ALI_ORDER,
    ADMIN_ADD_NAME, ADMIN_ADD_COST, ADMIN_ADD_TYPE, ADMIN_ADD_CONTENT
) = range(7)

# --- 3. 日志与数据库 ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

def get_db():
    conn = psycopg2.connect(DATABASE_URL)
    return conn

# --- 4. 辅助函数 ---

def get_user(user_id, username):
    """获取用户信息，不存在则创建"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE user_id = %s", (user_id,))
    user = cur.fetchone()
    if not user:
        cur.execute(
            "INSERT INTO users (user_id, username) VALUES (%s, %s) RETURNING *",
            (user_id, username)
        )
        user = cur.fetchone()
        conn.commit()
    conn.close()
    return user

def update_points(user_id, amount, reason):
    """增加/扣除积分并记录日志"""
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("UPDATE users SET points = points + %s WHERE user_id = %s", (amount, user_id))
        cur.execute(
            "INSERT INTO point_logs (user_id, change_amount, reason) VALUES (%s, %s, %s)",
            (user_id, amount, reason)
        )
        conn.commit()
    except Exception as e:
        logger.error(f"Update Points Error: {e}")
    finally:
        conn.close()

def check_cooldown(user_row, type_prefix):
    """检查冷却时间. type_prefix: 'vip', 'wx', 'ali'"""
    # 映射数据库列索引 (根据 CREATE TABLE 的顺序)
    # user_id(0), username(1), points(2), vip_status(3), 
    # vip_retries(4), vip_cooldown(5), 
    # wx_used(6), wx_retries(7), wx_cooldown(8), 
    # ali_used(9), ali_retries(10), ali_cooldown(11)
    
    idx_map = {'vip': 5, 'wx': 8, 'ali': 11}
    cooldown_idx = idx_map[type_prefix]
    
    cooldown_until = user_row[cooldown_idx]
    
    if cooldown_until:
        # 确保时区一致，数据库取出的通常是 naive 或 UTC
        now = datetime.now()
        if cooldown_until > now:
            remaining = cooldown_until - now
            hours, remainder = divmod(remaining.seconds, 3600)
            minutes, _ = divmod(remainder, 60)
            return True, f"❄️ 系统冷却中，请 {hours}小时{minutes}分 后再试。"
    return False, ""

async def send_file_helper(chat_id, file_id, context, caption=None, reply_markup=None):
    """安全发送文件的辅助函数"""
    try:
        if not file_id:
            if caption:
                await context.bot.send_message(chat_id, caption, reply_markup=reply_markup, parse_mode='HTML')
            return
            
        # 简单判断文件类型 (实际上file_id很难判断，这里假设用户填对了)
        # 尝试作为图片发送，失败则作为视频，再失败作为文档
        try:
            await context.bot.send_photo(chat_id, file_id, caption=caption, reply_markup=reply_markup, parse_mode='HTML')
        except:
            await context.bot.send_video(chat_id, file_id, caption=caption, reply_markup=reply_markup, parse_mode='HTML')
    except Exception as e:
        logger.error(f"Send file error: {e}")
        await context.bot.send_message(chat_id, caption or "内容加载失败", reply_markup=reply_markup)

# --- 5. 核心功能 Handlers ---

# /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    get_user(user.id, user.username) # 初始化用户
    
    text = (
        "👋 <b>欢迎加入【VIP中转】！我是守门员小卫，你的身份验证小助手~</b>\n\n"
        "📢 <b>小卫小卫，守门员小卫！</b>\n"
        "一键入群，小卫帮你搞定！\n"
        "新人来报到，小卫查身份！"
    )
    keyboard = [
        [InlineKeyboardButton("💎 开始验证", callback_data='menu_vip')],
        [InlineKeyboardButton("💰 积分中心", callback_data='menu_points')]
    ]
    
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')

# --- VIP 验证流程 ---
async def vip_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    # 检查冷却
    user_row = get_user(user_id, query.from_user.username)
    is_cool, msg = check_cooldown(user_row, 'vip')
    if is_cool:
        await query.message.reply_text(msg)
        return ConversationHandler.END

    if user_row[3]: # vip_status
        await query.message.reply_text("✅ 您已经是尊贵的VIP会员，无需重复验证！")
        return ConversationHandler.END

    text = (
        "💎 <b>VIP会员特权说明：</b>\n"
        "✅ 专属中转通道\n"
        "✅ 优先审核入群\n"
        "✅ 7x24小时客服支持\n"
        "✅ 定期福利活动"
    )
    keyboard = [[InlineKeyboardButton("✅ 我已付款，开始验证", callback_data='vip_start_input')]]
    
    # 发送带图的消息
    await query.message.delete() # 删掉旧菜单
    await send_file_helper(query.message.chat_id, FILE_CONFIG['vip_intro'], context, text, InlineKeyboardMarkup(keyboard))

async def vip_input_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    text = (
        "📝 <b>订单验证步骤：</b>\n"
        "1. 打开支付软件\n"
        "2. 点击 [我的] -> [账单]\n"
        "3. 找到对应交易 -> [账单详情]\n"
        "4. 点击 [更多] -> 复制 [商户订单号]\n\n"
        "👇 <b>请直接在下方发送您的订单号：</b>"
    )
    # 发送教程图
    await query.message.delete()
    await send_file_helper(query.message.chat_id, FILE_CONFIG['vip_pay_guide'], context, text)
    return WAIT_VIP_ORDER

async def vip_process_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text.strip()
    conn = get_db()
    cur = conn.cursor()
    
    # 验证逻辑
    if text.startswith("20260"):
        cur.execute("UPDATE users SET vip_status = TRUE, vip_retries = 0 WHERE user_id = %s", (user.id,))
        conn.commit()
        
        keyboard = [[InlineKeyboardButton("🚀 点击加入会员群", url="https://t.me/+495j5rWmApsxYzg9")]]
        await update.message.reply_text("🎉 <b>订单验证成功！</b>\n欢迎加入大家庭！", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
        cur.close()
        conn.close()
        return ConversationHandler.END
    else:
        # 失败逻辑
        cur.execute("UPDATE users SET vip_retries = vip_retries + 1 WHERE user_id = %s RETURNING vip_retries", (user.id,))
        retries = cur.fetchone()[0]
        conn.commit()
        
        if retries >= 2:
            # 冷却5小时
            cooldown_time = datetime.now() + timedelta(hours=5)
            cur.execute("UPDATE users SET vip_cooldown_until = %s WHERE user_id = %s", (cooldown_time, user.id))
            conn.commit()
            await update.message.reply_text("⛔️ <b>验证失败次数过多</b>\n系统已开启安全保护，请 5小时 后再试。")
            cur.close()
            conn.close()
            return ConversationHandler.END
        else:
            await update.message.reply_text(f"❌ <b>未查询到订单信息</b>\n请检查是否复制正确。\n您还有 {2-retries} 次机会。请重新输入：", parse_mode='HTML')
            cur.close()
            conn.close()
            return WAIT_VIP_ORDER

# --- 积分中心流程 ---
async def points_home(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query: await query.answer()
    
    # 统一入口，判断是 callback 还是 command
    chat_id = update.effective_chat.id
    
    text = "💰 <b>积分中心</b>\n请选择您需要的服务："
    keyboard = [
        [InlineKeyboardButton("📅 每日签到", callback_data='pt_checkin'), InlineKeyboardButton("🏆 积分排行榜", callback_data='pt_rank')],
        [InlineKeyboardButton("💳 积分充值", callback_data='pt_topup'), InlineKeyboardButton("🎁 积分兑换", callback_data='pt_exchange')],
        [InlineKeyboardButton("👛 我的余额/记录", callback_data='pt_balance')],
        [InlineKeyboardButton("🔙 返回首页", callback_data='back_home')]
    ]
    
    if query:
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')

# 1. 签到
async def point_checkin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    user_row = get_user(user_id, query.from_user.username)
    last_checkin = user_row[12] # last_checkin_date
    today = date.today()
    
    if last_checkin == today:
        await query.answer("⚠️ 今天已经签到过啦，明天再来吧！", show_alert=True)
        return

    points = random.randint(3, 8)
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE users SET last_checkin_date = %s WHERE user_id = %s", (today, user_id))
    conn.commit()
    conn.close()
    
    update_points(user_id, points, "每日签到")
    await query.edit_message_text(f"✅ <b>签到成功！</b>\n获得积分：+{points}\n明天记得再来哦！", 
                                  reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data='menu_points')]]), 
                                  parse_mode='HTML')

# 2. 充值菜单
async def point_topup_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    keyboard = [
        [InlineKeyboardButton("🟢 微信充值 (5元=100积分)", callback_data='topup_wx')],
        [InlineKeyboardButton("🔵 支付宝充值 (5元=100积分)", callback_data='topup_ali')],
        [InlineKeyboardButton("🔙 返回", callback_data='menu_points')]
    ]
    await query.edit_message_text(
        "💳 <b>积分充值</b>\n\n⚠️ <b>温馨提示：</b>\n微信和支付宝每位用户仅限使用一次首充优惠！\n请勿重复充值。",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='HTML'
    )

# 微信充值流程
async def wx_topup_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    # 检查是否用过
    user = get_user(user_id, query.from_user.username)
    if user[6]: # wx_used
        await query.answer("🚫 您已使用过微信首充优惠，无法再次使用。", show_alert=True)
        return ConversationHandler.END
    
    # 检查冷却
    is_cool, msg = check_cooldown(user, 'wx')
    if is_cool:
        await query.message.reply_text(msg)
        return ConversationHandler.END

    await query.message.delete()
    text = "🟢 <b>微信充值</b>\n💰 价格：5元 = 100积分\n\n请扫描下方二维码支付："
    kb = [[InlineKeyboardButton("✅ 我已支付，开始验证", callback_data='wx_input')]]
    await send_file_helper(query.message.chat_id, FILE_CONFIG['wx_pay_qr'], context, text, InlineKeyboardMarkup(kb))

async def wx_input_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.delete()
    text = "📝 请在微信支付账单找到【交易单号】。\n请输入以 <b>4200</b> 开头的订单编号："
    await send_file_helper(query.message.chat_id, FILE_CONFIG['wx_order_guide'], context, text)
    return WAIT_WX_ORDER

async def wx_process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text.strip()
    conn = get_db()
    cur = conn.cursor()
    
    if text.startswith("4200") and text.isdigit():
        update_points(user.id, 100, "微信充值")
        cur.execute("UPDATE users SET wx_used = TRUE, wx_retries = 0 WHERE user_id = %s", (user.id,))
        conn.commit()
        await update.message.reply_text("✅ <b>充值成功！</b>\n已到账 100 积分。", parse_mode='HTML')
        cur.close(); conn.close()
        return ConversationHandler.END
    else:
        cur.execute("UPDATE users SET wx_retries = wx_retries + 1 WHERE user_id = %s RETURNING wx_retries", (user.id,))
        retries = cur.fetchone()[0]
        conn.commit()
        
        if retries >= 2:
            cd = datetime.now() + timedelta(hours=10)
            cur.execute("UPDATE users SET wx_cooldown_until = %s WHERE user_id = %s", (cd, user.id))
            conn.commit()
            await update.message.reply_text("⛔️ <b>验证失败次数过多</b>\n请 10小时 后再试。")
            cur.close(); conn.close()
            return ConversationHandler.END
        else:
            await update.message.reply_text(f"❌ <b>订单识别失败</b>\n请重试，剩余机会：{2-retries} 次。")
            cur.close(); conn.close()
            return WAIT_WX_ORDER

# 支付宝充值流程 (类似微信，只是前缀 4768，冷却字段 ali)
async def ali_topup_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    user = get_user(user_id, query.from_user.username)
    if user[9]: # ali_used
        await query.answer("🚫 您已使用过支付宝首充优惠。", show_alert=True)
        return ConversationHandler.END
    
    is_cool, msg = check_cooldown(user, 'ali')
    if is_cool:
        await query.message.reply_text(msg)
        return ConversationHandler.END

    await query.message.delete()
    text = "🔵 <b>支付宝充值</b>\n💰 价格：5元 = 100积分\n\n请扫描下方二维码支付："
    kb = [[InlineKeyboardButton("✅ 我已支付，开始验证", callback_data='ali_input')]]
    await send_file_helper(query.message.chat_id, FILE_CONFIG['ali_pay_qr'], context, text, InlineKeyboardMarkup(kb))

async def ali_input_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.delete()
    text = "📝 请在账单详情找到【商家订单号】。\n请输入以 <b>4768</b> 开头的订单编号："
    await send_file_helper(query.message.chat_id, FILE_CONFIG['ali_order_guide'], context, text)
    return WAIT_ALI_ORDER

async def ali_process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text.strip()
    conn = get_db()
    cur = conn.cursor()
    
    if text.startswith("4768") and text.isdigit():
        update_points(user.id, 100, "支付宝充值")
        cur.execute("UPDATE users SET ali_used = TRUE, ali_retries = 0 WHERE user_id = %s", (user.id,))
        conn.commit()
        await update.message.reply_text("✅ <b>充值成功！</b>\n已到账 100 积分。", parse_mode='HTML')
        cur.close(); conn.close()
        return ConversationHandler.END
    else:
        cur.execute("UPDATE users SET ali_retries = ali_retries + 1 WHERE user_id = %s RETURNING ali_retries", (user.id,))
        retries = cur.fetchone()[0]
        conn.commit()
        if retries >= 2:
            cd = datetime.now() + timedelta(hours=10)
            cur.execute("UPDATE users SET ali_cooldown_until = %s WHERE user_id = %s", (cd, user.id))
            conn.commit()
            await update.message.reply_text("⛔️ <b>验证失败次数过多</b>\n请 10小时 后再试。")
            cur.close(); conn.close()
            return ConversationHandler.END
        else:
            await update.message.reply_text(f"❌ <b>订单识别失败</b>\n请重试，剩余机会：{2-retries} 次。")
            cur.close(); conn.close()
            return WAIT_ALI_ORDER

# 3. 兑换中心
async def exchange_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    conn = get_db()
    cur = conn.cursor()
    # 获取商品列表
    cur.execute("SELECT id, name, cost FROM products WHERE is_active = TRUE ORDER BY id")
    products = cur.fetchall()
    
    # 获取用户已兑换的列表
    cur.execute("SELECT product_id FROM user_redemptions WHERE user_id = %s", (user_id,))
    redeemed = {row[0] for row in cur.fetchall()}
    conn.close()
    
    keyboard = []
    for pid, name, cost in products:
        status_text = f"{cost} 积分"
        if pid in redeemed:
            status_text = "✅ 已兑换 (点击查看)"
        keyboard.append([InlineKeyboardButton(f"{name} - {status_text}", callback_data=f"buy_{pid}_{cost}")])
    
    keyboard.append([InlineKeyboardButton("🔙 返回", callback_data='menu_points')])
    
    await query.edit_message_text("🎁 <b>积分兑换商城</b>\n点击商品进行兑换或查看：", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')

async def exchange_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data.split('_') # buy_pid_cost
    pid = int(data[1])
    cost = int(data[2])
    
    # 检查是否已兑换
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM user_redemptions WHERE user_id = %s AND product_id = %s", (query.from_user.id, pid))
    is_redeemed = cur.fetchone()
    
    if is_redeemed:
        # 直接发送内容
        cur.execute("SELECT type, content FROM products WHERE id = %s", (pid,))
        prod = cur.fetchone()
        conn.close()
        
        if prod[0] == 'text':
            await query.message.reply_text(f"📦 <b>兑换内容：</b>\n{prod[1]}", parse_mode='HTML')
        else:
            await send_file_helper(query.message.chat_id, prod[1], context, "📦 <b>兑换内容</b>")
        return

    # 未兑换，弹出确认
    kb = [
        [InlineKeyboardButton("✅ 确认兑换", callback_data=f"confirm_{pid}_{cost}"), 
         InlineKeyboardButton("❌ 取消", callback_data="pt_exchange")]
    ]
    await query.edit_message_text(f"❓ <b>确认兑换？</b>\n\n将消耗：{cost} 积分", reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')
    conn.close()

async def exchange_execute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data.split('_') # confirm_pid_cost
    pid = int(data[1])
    cost = int(data[2])
    user_id = query.from_user.id
    
    conn = get_db()
    cur = conn.cursor()
    
    # 检查余额
    cur.execute("SELECT points FROM users WHERE user_id = %s", (user_id,))
    current_points = cur.fetchone()[0]
    
    if current_points < cost:
        await query.answer("❌ 余额不足，请去赚取积分吧！", show_alert=True)
        await query.edit_message_text("❌ 余额不足，请充值或签到。", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data='pt_exchange')]]))
    else:
        # 扣款 + 记录
        update_points(user_id, -cost, f"兑换商品ID:{pid}")
        cur.execute("INSERT INTO user_redemptions (user_id, product_id) VALUES (%s, %s)", (user_id, pid))
        conn.commit()
        
        await query.answer("✅ 兑换成功！", show_alert=True)
        await query.edit_message_text("🎉 <b>兑换成功！</b>\n您可以返回列表点击查看内容。", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📦 查看商品", callback_data='pt_exchange')]]), parse_mode='HTML')
    
    conn.close()

# 4. 余额与记录
async def balance_view(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT points FROM users WHERE user_id = %s", (user_id,))
    points = cur.fetchone()[0]
    
    cur.execute("SELECT reason, change_amount, created_at FROM point_logs WHERE user_id = %s ORDER BY created_at DESC LIMIT 5", (user_id,))
    logs = cur.fetchall()
    conn.close()
    
    log_text = ""
    for reason, amount, time in logs:
        sign = "+" if amount > 0 else ""
        time_str = time.strftime("%m-%d %H:%M")
        log_text += f"• {time_str} | {reason} | <b>{sign}{amount}</b>\n"
        
    text = f"👛 <b>我的钱包</b>\n\n💰 当前积分：<b>{points}</b>\n\n📝 <b>最近5条记录：</b>\n{log_text}"
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data='menu_points')]]), parse_mode='HTML')

# 5. 排行榜 (3天内获得积分排行)
async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    conn = get_db()
    cur = conn.cursor()
    
    # 查询过去3天增加积分的总和 (不计算消费)
    sql = """
    SELECT user_id, SUM(change_amount) as total 
    FROM point_logs 
    WHERE change_amount > 0 AND created_at > NOW() - INTERVAL '3 days' 
    GROUP BY user_id 
    ORDER BY total DESC 
    LIMIT 10
    """
    cur.execute(sql)
    ranks = cur.fetchall()
    conn.close()
    
    text = "🏆 <b>近3日积分风云榜</b>\n(仅统计获得积分，不含消费)\n\n"
    my_rank = "未上榜"
    
    for idx, (uid, score) in enumerate(ranks):
        medal = ["🥇", "🥈", "🥉"][idx] if idx < 3 else f"{idx+1}."
        # 隐藏用户ID中间部分
        uid_str = str(uid)
        masked_uid = uid_str[:3] + "***" + uid_str[-3:]
        text += f"{medal} {masked_uid} : <b>{score}</b> 分\n"
        
        if uid == user_id:
            my_rank = f"第 {idx+1} 名"
            
    text += f"\n👤 <b>我的排名：</b> {my_rank}"
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data='menu_points')]]), parse_mode='HTML')

# --- Admin 后台 ---
async def admin_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id != ADMIN_ID:
        return ConversationHandler.END
        
    text = "👮‍♂️ <b>守门员小卫 - 管理后台</b>\n\n请选择操作："
    kb = [
        [InlineKeyboardButton("➕ 上架商品", callback_data='adm_add'), InlineKeyboardButton("➖ 下架/管理", callback_data='adm_del')],
        [InlineKeyboardButton("🆔 获取文件ID (用于配置)", callback_data='adm_getid')],
        [InlineKeyboardButton("❌ 关闭", callback_data='adm_close')]
    ]
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')
    return 0 # Admin State (简单起见，这里复用状态或新建)

async def admin_get_file_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("请发送图片/视频，我将返回 file_id。\n完成后请复制到代码 CONFIG 区域。\n发送 /cancel 退出。")
    return 99 # Special state for get id

async def admin_return_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    fid = "未识别"
    if update.message.photo: fid = update.message.photo[-1].file_id
    elif update.message.video: fid = update.message.video.file_id
    elif update.message.document: fid = update.message.document.file_id
    
    await update.message.reply_text(f"🆔 <b>File ID:</b>\n<code>{fid}</code>", parse_mode='HTML')
    return 99

# 上架商品流程
async def admin_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("请输入商品名称：")
    return ADMIN_ADD_NAME

async def admin_add_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['p_name'] = update.message.text
    await update.message.reply_text("请输入兑换所需积分 (数字)：")
    return ADMIN_ADD_COST

async def admin_add_cost(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.text.isdigit():
        await update.message.reply_text("请输入纯数字！")
        return ADMIN_ADD_COST
    context.user_data['p_cost'] = int(update.message.text)
    
    kb = [
        [InlineKeyboardButton("纯文本", callback_data='type_text')],
        [InlineKeyboardButton("图片", callback_data='type_image')],
        [InlineKeyboardButton("视频", callback_data='type_video')]
    ]
    await update.message.reply_text("请选择商品内容类型：", reply_markup=InlineKeyboardMarkup(kb))
    return ADMIN_ADD_TYPE

async def admin_add_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    t = query.data.split('_')[1]
    context.user_data['p_type'] = t
    
    if t == 'text':
        await query.message.reply_text("请输入显示的文本内容：")
    else:
        await query.message.reply_text("请发送该图片或视频：")
    return ADMIN_ADD_CONTENT

async def admin_add_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    content = ""
    t = context.user_data['p_type']
    
    if t == 'text':
        content = update.message.text
    elif t == 'image':
        content = update.message.photo[-1].file_id if update.message.photo else None
    elif t == 'video':
        content = update.message.video.file_id if update.message.video else None
        
    if not content:
        await update.message.reply_text("格式错误，请重新发送内容。")
        return ADMIN_ADD_CONTENT
        
    # 保存到DB
    conn = get_db()
    cur = conn.cursor()
    cur.execute("INSERT INTO products (name, cost, type, content) VALUES (%s, %s, %s, %s)",
                (context.user_data['p_name'], context.user_data['p_cost'], t, content))
    conn.commit()
    conn.close()
    
    await update.message.reply_text("✅ 商品上架成功！")
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("操作已取消。")
    return ConversationHandler.END

# --- 6. 主程序 Setup ---

def main():
    if not BOT_TOKEN:
        print("Error: BOT_TOKEN is missing")
        return

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    # 验证流程
    vip_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(vip_input_start, pattern='vip_start_input')],
        states={
            WAIT_VIP_ORDER: [MessageHandler(filters.TEXT & ~filters.COMMAND, vip_process_input)]
        },
        fallbacks=[CommandHandler('start', start)]
    )
    
    # 充值流程
    topup_wx = ConversationHandler(
        entry_points=[CallbackQueryHandler(wx_input_step, pattern='wx_input')],
        states={WAIT_WX_ORDER: [MessageHandler(filters.TEXT & ~filters.COMMAND, wx_process)]},
        fallbacks=[CommandHandler('start', start)]
    )
    topup_ali = ConversationHandler(
        entry_points=[CallbackQueryHandler(ali_input_step, pattern='ali_input')],
        states={WAIT_ALI_ORDER: [MessageHandler(filters.TEXT & ~filters.COMMAND, ali_process)]},
        fallbacks=[CommandHandler('start', start)]
    )

    # Admin流程
    admin_conv = ConversationHandler(
        entry_points=[CommandHandler('admin', admin_start)],
        states={
            0: [
                CallbackQueryHandler(admin_add_start, pattern='adm_add'),
                CallbackQueryHandler(admin_get_file_id, pattern='adm_getid'),
                CallbackQueryHandler(cancel, pattern='adm_close')
            ],
            99: [MessageHandler(filters.ALL & ~filters.COMMAND, admin_return_id)],
            ADMIN_ADD_NAME: [MessageHandler(filters.TEXT, admin_add_name)],
            ADMIN_ADD_COST: [MessageHandler(filters.TEXT, admin_add_cost)],
            ADMIN_ADD_TYPE: [CallbackQueryHandler(admin_add_type)],
            ADMIN_ADD_CONTENT: [MessageHandler(filters.ALL, admin_add_content)]
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )

    # 注册 Handlers
    app.add_handler(vip_conv)
    app.add_handler(topup_wx)
    app.add_handler(topup_ali)
    app.add_handler(admin_conv)
    
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CallbackQueryHandler(vip_menu, pattern='menu_vip'))
    app.add_handler(CallbackQueryHandler(points_home, pattern='^menu_points|back_home$'))
    app.add_handler(CallbackQueryHandler(point_checkin, pattern='pt_checkin'))
    app.add_handler(CallbackQueryHandler(point_topup_menu, pattern='pt_topup'))
    app.add_handler(CallbackQueryHandler(exchange_list, pattern='pt_exchange'))
    app.add_handler(CallbackQueryHandler(balance_view, pattern='pt_balance'))
    app.add_handler(CallbackQueryHandler(leaderboard, pattern='pt_rank'))
    app.add_handler(CallbackQueryHandler(wx_topup_start, pattern='topup_wx'))
    app.add_handler(CallbackQueryHandler(ali_topup_start, pattern='topup_ali'))
    app.add_handler(CallbackQueryHandler(exchange_confirm, pattern='^buy_'))
    app.add_handler(CallbackQueryHandler(exchange_execute, pattern='^confirm_'))
    
    # 兜底
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, start))

    print("Bot is running...")
    app.run_polling()

if __name__ == '__main__':
    main()
