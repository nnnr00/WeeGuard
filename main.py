import os
import time
import random
import re
import json
import threading
import psycopg2
from datetime import datetime, date
import telebot
from telebot import types

BOT_TOKEN = os.environ.get('BOT_TOKEN')
ADMIN_ID = int(os.environ.get('ADMIN_ID'))
DATABASE_URL = os.environ.get('DATABASE_URL')

bot = telebot.TeleBot(BOT_TOKEN)

# ==================== 常量 ====================
VIP_LOCK_TIME = 5 * 60 * 60
RECHARGE_LOCK_TIME = 10 * 60 * 60
MESSAGE_EXPIRE_TIME = 20 * 60
GROUP_LINK = "https://t.me/+495j5rWmApsxYzg9"

# ==================== 图片 ====================
VIP_IMAGE = "AgACAgUAAxkBAAIBJ2loboOm15d-Qog2KkzAVSTLG-1eAAKaD2sbQNhBV_UKRl5JPolfAQADAgADeAADOAQ"
ORDER_IMAGE = "AgACAgUAAxkBAAIBHWlobOW8SVMC9dk6a5KquMiQHPh1AAKVD2sbQNhBV9mV11AQnf1xAQADAgADeQADOAQ"
WECHAT_PAY_IMAGE = "AgACAgUAAxkBAAIBImlobmPLtn9DWUFZJ53t1mhkVIA7AAKYD2sbQNhBV_A-2IdqoG-dAQADAgADeAADOAQ"
WECHAT_ORDER_IMAGE = "AgACAgUAAxkBAAIBLWlocIlhveHnlgntE7dGi1ri56i2AAKeD2sbQNhBVyZ8_L3zE7qwAQADAgADeQADOAQ"
ALIPAY_PAY_IMAGE = "AgACAgUAAxkBAAIBJWlobnt_eXxhfHqg5bpF8WFwDDESAAKZD2sbQNhBVyWCVUCv9Q3iAQADAgADeAADOAQ"
ALIPAY_ORDER_IMAGE = "AgACAgUAAxkBAAIBMGlocJCdAlLyJie451mVeM6gi7xhAAKfD2sbQNhBV-EDx2qKNqc-AQADAgADeQADOAQ"

# ==================== 消息文本 ====================
WELCOME_MSG = """👋 欢迎加入【VIP中转】！我是守门员小卫，你的身份验证小助手~

📢 小卫小卫，守门员小卫！
一键入群，小卫帮你搞定！
新人来报到，小卫查身份！"""

VIP_MSG = """💎 VIP会员特权说明：
✅ 专属中转通道
✅ 优先审核入群
✅ 7x24小时客服支持
✅ 定期福利活动"""

ORDER_MSG = """📋 订单号查询步骤：

1️⃣ 打开支付APP → 点击【我的】
2️⃣ 进入【账单】
3️⃣ 找到付款记录 → 点击进入【账单详情】
4️⃣ 点击右上角【更多】
5️⃣ 找到并复制【订单号】

⬇️ 请在下方发送您的订单号"""

WECHAT_PAY_MSG = """💰 微信充值

━━━━━━━━━━━━━━━━
💎 5元 = 100积分
━━━━━━━━━━━━━━━━

⚠️ ════════════════════ ⚠️
       ⛔ 温馨提示 ⛔
   
   微信充值仅限使用 1 次
   请勿重复支付！
   重复支付无法到账！
   
⚠️ ════════════════════ ⚠️"""

WECHAT_ORDER_MSG = """📋 微信订单验证

━━━━━━━━━━━━━━━━━━━━━
📱 查找交易单号步骤：

1️⃣ 打开【微信】
2️⃣ 点击【我】→【服务】→【钱包】
3️⃣ 点击【账单】
4️⃣ 找到该笔付款记录，点击进入
5️⃣ 复制【交易单号】
━━━━━━━━━━━━━━━━━━━━━

⬇️ 请在下方发送交易单号"""

ALIPAY_PAY_MSG = """💰 支付宝充值

━━━━━━━━━━━━━━━━
💎 5元 = 100积分
━━━━━━━━━━━━━━━━

⚠️ ════════════════════ ⚠️
       ⛔ 温馨提示 ⛔
   
   支付宝充值仅限使用 1 次
   请勿重复支付！
   重复支付无法到账！
   
⚠️ ════════════════════ ⚠️"""

ALIPAY_ORDER_MSG = """📋 支付宝订单验证

━━━━━━━━━━━━━━━━━━━━━
📱 查找商家订单号步骤：

1️⃣ 打开【支付宝】
2️⃣ 点击【我的】→【账单】
3️⃣ 找到该笔付款记录，点击进入
4️⃣ 点击【更多】→【账单详情】
5️⃣ 复制【商家订单号】
━━━━━━━━━━━━━━━━━━━━━

⬇️ 请在下方发送商家订单号"""

# ==================== 用户状态 ====================
user_state = {}

# ==================== 数据库操作 ====================
def get_db():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            points INTEGER DEFAULT 0,
            last_checkin DATE,
            wechat_used BOOLEAN DEFAULT FALSE,
            alipay_used BOOLEAN DEFAULT FALSE,
            wechat_attempts INTEGER DEFAULT 0,
            alipay_attempts INTEGER DEFAULT 0,
            wechat_locked_until BIGINT DEFAULT 0,
            alipay_locked_until BIGINT DEFAULT 0,
            vip_attempts INTEGER DEFAULT 0,
            vip_locked_until BIGINT DEFAULT 0
        )
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS commands (
            command_name TEXT PRIMARY KEY,
            message_links TEXT,
            points_cost INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS user_purchases (
            user_id BIGINT,
            command_name TEXT,
            purchased_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, command_name)
        )
    ''')
    conn.commit()
    cur.close()
    conn.close()

def get_user(user_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute('SELECT * FROM users WHERE user_id = %s', (user_id,))
    row = cur.fetchone()
    if not row:
        cur.execute('INSERT INTO users (user_id) VALUES (%s)', (user_id,))
        conn.commit()
        cur.execute('SELECT * FROM users WHERE user_id = %s', (user_id,))
        row = cur.fetchone()
    cur.close()
    conn.close()
    return {
        'user_id': row[0],
        'points': row[1],
        'last_checkin': row[2],
        'wechat_used': row[3],
        'alipay_used': row[4],
        'wechat_attempts': row[5],
        'alipay_attempts': row[6],
        'wechat_locked_until': row[7],
        'alipay_locked_until': row[8],
        'vip_attempts': row[9],
        'vip_locked_until': row[10]
    }

def update_user(user_id, **kwargs):
    conn = get_db()
    cur = conn.cursor()
    sets = ', '.join([f"{k} = %s" for k in kwargs.keys()])
    values = list(kwargs.values()) + [user_id]
    cur.execute(f'UPDATE users SET {sets} WHERE user_id = %s', values)
    conn.commit()
    cur.close()
    conn.close()

def add_points(user_id, amount):
    conn = get_db()
    cur = conn.cursor()
    cur.execute('UPDATE users SET points = points + %s WHERE user_id = %s', (amount, user_id))
    conn.commit()
    cur.close()
    conn.close()

def deduct_points(user_id, amount):
    conn = get_db()
    cur = conn.cursor()
    cur.execute('UPDATE users SET points = points - %s WHERE user_id = %s', (amount, user_id))
    conn.commit()
    cur.close()
    conn.close()

# ==================== 命令管理 ====================
def save_command(command_name, message_links, points_cost=0):
    conn = get_db()
    cur = conn.cursor()
    cur.execute('''
        INSERT INTO commands (command_name, message_links, points_cost) 
        VALUES (%s, %s, %s)
        ON CONFLICT (command_name) DO UPDATE SET 
        message_links = EXCLUDED.message_links,
        points_cost = EXCLUDED.points_cost
    ''', (command_name, json.dumps(message_links), points_cost))
    conn.commit()
    cur.close()
    conn.close()

def get_command(command_name):
    conn = get_db()
    cur = conn.cursor()
    cur.execute('SELECT * FROM commands WHERE command_name = %s', (command_name,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    if row:
        return {
            'command_name': row[0],
            'message_links': json.loads(row[1]),
            'points_cost': row[2]
        }
    return None

def get_all_commands():
    conn = get_db()
    cur = conn.cursor()
    cur.execute('SELECT command_name, points_cost FROM commands ORDER BY created_at DESC')
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [{'command_name': r[0], 'points_cost': r[1]} for r in rows]

def delete_command(command_name):
    conn = get_db()
    cur = conn.cursor()
    cur.execute('DELETE FROM commands WHERE command_name = %s', (command_name,))
    conn.commit()
    cur.close()
    conn.close()

def has_purchased(user_id, command_name):
    conn = get_db()
    cur = conn.cursor()
    cur.execute('SELECT 1 FROM user_purchases WHERE user_id = %s AND command_name = %s', (user_id, command_name))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row is not None

def add_purchase(user_id, command_name):
    conn = get_db()
    cur = conn.cursor()
    cur.execute('''
        INSERT INTO user_purchases (user_id, command_name) 
        VALUES (%s, %s) ON CONFLICT DO NOTHING
    ''', (user_id, command_name))
    conn.commit()
    cur.close()
    conn.close()

# ==================== 工具函数 ====================
def format_time(seconds):
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    return f"{hours}小时{minutes}分钟"

def is_vip_locked(user_id):
    user = get_user(user_id)
    now = int(time.time())
    if user['vip_locked_until'] > now:
        return True
    elif user['vip_locked_until'] > 0:
        update_user(user_id, vip_attempts=0, vip_locked_until=0)
    return False

def is_wechat_locked(user_id):
    user = get_user(user_id)
    now = int(time.time())
    if user['wechat_locked_until'] > now:
        return True
    elif user['wechat_locked_until'] > 0:
        update_user(user_id, wechat_attempts=0, wechat_locked_until=0)
    return False

def is_alipay_locked(user_id):
    user = get_user(user_id)
    now = int(time.time())
    if user['alipay_locked_until'] > now:
        return True
    elif user['alipay_locked_until'] > 0:
        update_user(user_id, alipay_attempts=0, alipay_locked_until=0)
    return False

def get_vip_remaining(user_id):
    user = get_user(user_id)
    remaining = int(user['vip_locked_until'] - time.time())
    return format_time(max(0, remaining))

def get_wechat_remaining(user_id):
    user = get_user(user_id)
    remaining = int(user['wechat_locked_until'] - time.time())
    return format_time(max(0, remaining))

def get_alipay_remaining(user_id):
    user = get_user(user_id)
    remaining = int(user['alipay_locked_until'] - time.time())
    return format_time(max(0, remaining))

def parse_message_link(link):
    link = link.strip()
    match = re.match(r'https://t\.me/c/(\d+)/(\d+)', link)
    if match:
        channel_id = int('-100' + match.group(1))
        message_id = int(match.group(2))
        return channel_id, message_id
    match = re.match(r'https://t\.me/([^/]+)/(\d+)', link)
    if match:
        channel_username = '@' + match.group(1)
        message_id = int(match.group(2))
        return channel_username, message_id
    return None, None

def delete_messages_later(chat_id, message_ids, user_id, delay=MESSAGE_EXPIRE_TIME):
    def do_delete():
        for msg_id in message_ids:
            try:
                bot.delete_message(chat_id, msg_id)
            except:
                pass
        try:
            msg = """⏰ 消息已过期

━━━━━━━━━━━━━━━━━━━━━
📌 内容查看时效已结束

💡 如需再次查看：
• 已兑换用户：无需重复付费
• 请返回首页重新获取
━━━━━━━━━━━━━━━━━━━━━"""
            markup = types.InlineKeyboardMarkup()
            btn = types.InlineKeyboardButton("🏠 返回首页", callback_data="back_home")
            markup.add(btn)
            bot.send_message(chat_id, msg, reply_markup=markup)
        except:
            pass
    
    timer = threading.Timer(delay, do_delete)
    timer.start()

# ==================== 发送欢迎消息 ====================
def send_welcome(chat_id, user_id):
    markup = types.InlineKeyboardMarkup(row_width=1)
    
    if is_vip_locked(user_id):
        btn1 = types.InlineKeyboardButton(f"⏳ {get_vip_remaining(user_id)}后重试", callback_data="locked")
    else:
        btn1 = types.InlineKeyboardButton("🚀 开始验证", callback_data="start_verify")
    
    btn2 = types.InlineKeyboardButton("💰 积分中心", callback_data="points_center")
    markup.add(btn1, btn2)
    bot.send_message(chat_id, WELCOME_MSG, reply_markup=markup)

# ==================== 发送积分中心 ====================
def send_points_center(chat_id, user_id):
    user = get_user(user_id)
    
    msg = f"""💰 积分中心

━━━━━━━━━━━━━━━━
💎 当前积分：{user['points']}
━━━━━━━━━━━━━━━━

选择以下操作："""
    
    markup = types.InlineKeyboardMarkup(row_width=1)
    btn1 = types.InlineKeyboardButton("📅 每日签到", callback_data="checkin")
    btn2 = types.InlineKeyboardButton("💳 充值积分", callback_data="recharge")
    btn3 = types.InlineKeyboardButton("🎁 兑换中心", callback_data="exchange_center")
    btn4 = types.InlineKeyboardButton("🔙 返回首页", callback_data="back_home")
    markup.add(btn1, btn2, btn3, btn4)
    bot.send_message(chat_id, msg, reply_markup=markup)

# ==================== 发送兑换中心 ====================
def send_exchange_center(chat_id, user_id):
    user = get_user(user_id)
    commands = get_all_commands()
    
    msg = f"""🎁 兑换中心

━━━━━━━━━━━━━━━━
💎 当前积分：{user['points']}
━━━━━━━━━━━━━━━━

📦 可兑换内容："""
    
    markup = types.InlineKeyboardMarkup(row_width=1)
    
    if commands:
        for cmd in commands:
            purchased = has_purchased(user_id, cmd['command_name'])
            if purchased:
                btn_text = f"✅ {cmd['command_name']}（已拥有）"
            else:
                btn_text = f"🎁 {cmd['command_name']}（{cmd['points_cost']}积分）"
            markup.add(types.InlineKeyboardButton(btn_text, callback_data=f"exchange_{cmd['command_name']}"))
    else:
        msg += "\n\n暂无可兑换内容"
    
    btn_back = types.InlineKeyboardButton("🔙 返回积分中心", callback_data="points_center")
    markup.add(btn_back)
    bot.send_message(chat_id, msg, reply_markup=markup)

# ==================== 发送充值选择 ====================
def send_recharge_menu(chat_id, user_id):
    user = get_user(user_id)
    
    msg = """💳 充值积分

请选择支付方式："""
    
    markup = types.InlineKeyboardMarkup(row_width=1)
    
    if user['wechat_used']:
        btn1 = types.InlineKeyboardButton("✅ 微信充值（已使用）", callback_data="used")
    elif is_wechat_locked(user_id):
        btn1 = types.InlineKeyboardButton(f"⏳ 微信（{get_wechat_remaining(user_id)}后重试）", callback_data="locked")
    else:
        btn1 = types.InlineKeyboardButton("💚 微信充值", callback_data="wechat_pay")
    
    if user['alipay_used']:
        btn2 = types.InlineKeyboardButton("✅ 支付宝充值（已使用）", callback_data="used")
    elif is_alipay_locked(user_id):
        btn2 = types.InlineKeyboardButton(f"⏳ 支付宝（{get_alipay_remaining(user_id)}后重试）", callback_data="locked")
    else:
        btn2 = types.InlineKeyboardButton("💙 支付宝充值", callback_data="alipay_pay")
    
    btn3 = types.InlineKeyboardButton("🔙 返回积分中心", callback_data="points_center")
    markup.add(btn1, btn2, btn3)
    bot.send_message(chat_id, msg, reply_markup=markup)

# ==================== 管理员面板 ====================
def send_admin_panel(chat_id):
    markup = types.InlineKeyboardMarkup(row_width=1)
    btn1 = types.InlineKeyboardButton("📁 获取 File ID", callback_data="get_file_id")
    btn2 = types.InlineKeyboardButton("📦 频道转发库", callback_data="channel_library")
    markup.add(btn1, btn2)
    bot.send_message(chat_id, "🔧 管理员面板", reply_markup=markup)

def send_channel_library(chat_id):
    commands = get_all_commands()
    
    msg = """📦 频道转发库

━━━━━━━━━━━━━━━━
管理频道内容转发命令
━━━━━━━━━━━━━━━━"""
    
    if commands:
        msg += "\n\n📋 已创建命令：\n"
        for cmd in commands:
            msg += f"• {cmd['command_name']}（{cmd['points_cost']}积分）\n"
    
    markup = types.InlineKeyboardMarkup(row_width=1)
    btn1 = types.InlineKeyboardButton("➕ 添加新命令", callback_data="add_command")
    btn2 = types.InlineKeyboardButton("🗑️ 删除命令", callback_data="delete_command_menu")
    btn3 = types.InlineKeyboardButton("🔙 返回管理面板", callback_data="admin_panel")
    markup.add(btn1, btn2, btn3)
    bot.send_message(chat_id, msg, reply_markup=markup)

# ==================== /admin 命令 ====================
@bot.message_handler(commands=['admin'])
def admin(message):
    if message.from_user.id != ADMIN_ID:
        send_welcome(message.chat.id, message.from_user.id)
        return
    send_admin_panel(message.chat.id)

# ==================== 处理文件 ====================
@bot.message_handler(content_types=['document', 'photo', 'video', 'audio', 'voice', 'sticker', 'animation', 'video_note'])
def handle_files(message):
    user_id = message.from_user.id
    
    if user_id == ADMIN_ID:
        file_id = None
        file_type = None
        
        if message.document:
            file_id = message.document.file_id
            file_type = "Document"
        elif message.photo:
            file_id = message.photo[-1].file_id
            file_type = "Photo"
        elif message.video:
            file_id = message.video.file_id
            file_type = "Video"
        elif message.audio:
            file_id = message.audio.file_id
            file_type = "Audio"
        elif message.voice:
            file_id = message.voice.file_id
            file_type = "Voice"
        elif message.sticker:
            file_id = message.sticker.file_id
            file_type = "Sticker"
        elif message.animation:
            file_id = message.animation.file_id
            file_type = "GIF"
        elif message.video_note:
            file_id = message.video_note.file_id
            file_type = "VideoNote"
        
        if file_id:
            bot.reply_to(message, f"📁 *{file_type}*\n\n`{file_id}`", parse_mode="Markdown")
    else:
        state = user_state.get(user_id, {})
        if state.get('waiting'):
            bot.send_message(message.chat.id, "⚠️ 请输入正确的订单号")
        else:
            send_welcome(message.chat.id, user_id)

# ==================== 处理文本消息 ====================
@bot.message_handler(func=lambda message: True)
def handle_text(message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    text = message.text.strip()
    state = user_state.get(user_id, {})
    
    # ========== 管理员：添加命令流程 ==========
    if user_id == ADMIN_ID and state.get('admin_step') == 'waiting_command_name':
        command_name = text
        user_state[user_id] = {'admin_step': 'waiting_links', 'command_name': command_name}
        
        msg = f"""📝 命令名称：{command_name}

━━━━━━━━━━━━━━━━━━━━━
📌 请发送频道消息链接

支持格式：
• 每行一个链接
• 最多支持50条

示例：
https://t.me/c/1234567890/1
https://t.me/c/1234567890/2
━━━━━━━━━━━━━━━━━━━━━"""
        
        markup = types.InlineKeyboardMarkup()
        btn = types.InlineKeyboardButton("❌ 取消", callback_data="channel_library")
        markup.add(btn)
        bot.send_message(chat_id, msg, reply_markup=markup)
        return
    
    if user_id == ADMIN_ID and state.get('admin_step') == 'waiting_links':
        lines = text.strip().split('\n')
        links = []
        
        for line in lines[:50]:
            line = line.strip()
            if line:
                channel_id, msg_id = parse_message_link(line)
                if channel_id and msg_id:
                    links.append({'channel_id': channel_id, 'message_id': msg_id, 'link': line})
        
        if not links:
            bot.send_message(chat_id, "❌ 未识别到有效链接，请重新发送")
            return
        
        user_state[user_id] = {
            'admin_step': 'waiting_points',
            'command_name': state['command_name'],
            'links': links
        }
        
        msg = f"""✅ 已识别 {len(links)} 条消息链接

━━━━━━━━━━━━━━━━━━━━━
💰 请输入兑换所需积分

输入数字（0 = 免费）
━━━━━━━━━━━━━━━━━━━━━"""
        
        markup = types.InlineKeyboardMarkup()
        btn = types.InlineKeyboardButton("❌ 取消", callback_data="channel_library")
        markup.add(btn)
        bot.send_message(chat_id, msg, reply_markup=markup)
        return
    
    if user_id == ADMIN_ID and state.get('admin_step') == 'waiting_points':
        try:
            points_cost = int(text)
            if points_cost < 0:
                points_cost = 0
        except:
            bot.send_message(chat_id, "❌ 请输入有效数字")
            return
        
        command_name = state['command_name']
        links = state['links']
        
        save_command(command_name, links, points_cost)
        user_state[user_id] = {}
        
        msg = f"""✅ 命令创建成功！

━━━━━━━━━━━━━━━━━━━━━
📌 命令：{command_name}
📦 消息数：{len(links)} 条
💰 积分：{points_cost}
━━━━━━━━━━━━━━━━━━━━━

用户发送「{command_name}」即可触发"""
        
        markup = types.InlineKeyboardMarkup()
        btn = types.InlineKeyboardButton("📦 返回转发库", callback_data="channel_library")
        markup.add(btn)
        bot.send_message(chat_id, msg, reply_markup=markup)
        return
    
    if user_id == ADMIN_ID and state.get('admin_step') == 'waiting_delete_name':
        command_name = text
        cmd = get_command(command_name)
        
        if cmd:
            delete_command(command_name)
            bot.send_message(chat_id, f"✅ 命令「{command_name}」已删除")
        else:
            bot.send_message(chat_id, f"❌ 命令「{command_name}」不存在")
        
        user_state[user_id] = {}
        send_channel_library(chat_id)
        return
    
    # ========== VIP验证 ==========
    if state.get('waiting') == 'vip_order':
        order = text
        user = get_user(user_id)
        
        if order.startswith('20260'):
            user_state[user_id] = {}
            update_user(user_id, vip_attempts=0)
            
            markup = types.InlineKeyboardMarkup()
            btn = types.InlineKeyboardButton("🎉 加入VIP会员群", url=GROUP_LINK)
            markup.add(btn)
            
            bot.send_message(chat_id, """✅ 订单验证成功！

🎊 恭喜您成为VIP会员！
点击下方按钮加入专属会员群""", reply_markup=markup)
        else:
            attempts = user['vip_attempts'] + 1
            update_user(user_id, vip_attempts=attempts)
            
            if attempts >= 2:
                update_user(user_id, vip_locked_until=int(time.time()) + VIP_LOCK_TIME)
                user_state[user_id] = {}
                bot.send_message(chat_id, "❌ 验证失败次数过多\n\n⏳ 请5小时后重试")
                send_welcome(chat_id, user_id)
            else:
                bot.send_message(chat_id, "❌ 未查询到订单信息\n\n⚠️ 剩余尝试次数：1次")
                send_vip_order_page(chat_id, user_id)
        return
    
    # ========== 微信订单验证 ==========
    if state.get('waiting') == 'wechat_order':
        order = text
        user = get_user(user_id)
        
        if order.startswith('4200'):
            user_state[user_id] = {}
            add_points(user_id, 100)
            update_user(user_id, wechat_used=True, wechat_attempts=0)
            
            bot.send_message(chat_id, """✅ 充值成功！

💎 已到账：100积分

感谢您的支持！""")
            send_points_center(chat_id, user_id)
        else:
            attempts = user['wechat_attempts'] + 1
            update_user(user_id, wechat_attempts=attempts)
            
            if attempts >= 2:
                update_user(user_id, wechat_locked_until=int(time.time()) + RECHARGE_LOCK_TIME)
                user_state[user_id] = {}
                bot.send_message(chat_id, "❌ 验证失败次数过多\n\n⏳ 请10小时后重试")
                send_points_center(chat_id, user_id)
            else:
                bot.send_message(chat_id, "❌ 订单验证失败\n\n⚠️ 剩余尝试次数：1次")
                send_wechat_order_page(chat_id, user_id)
        return
    
    # ========== 支付宝订单验证 ==========
    if state.get('waiting') == 'alipay_order':
        order = text
        user = get_user(user_id)
        
        if order.startswith('4768'):
            user_state[user_id] = {}
            add_points(user_id, 100)
            update_user(user_id, alipay_used=True, alipay_attempts=0)
            
            bot.send_message(chat_id, """✅ 充值成功！

💎 已到账：100积分

感谢您的支持！""")
            send_points_center(chat_id, user_id)
        else:
            attempts = user['alipay_attempts'] + 1
            update_user(user_id, alipay_attempts=attempts)
            
            if attempts >= 2:
                update_user(user_id, alipay_locked_until=int(time.time()) + RECHARGE_LOCK_TIME)
                user_state[user_id] = {}
                bot.send_message(chat_id, "❌ 验证失败次数过多\n\n⏳ 请10小时后重试")
                send_points_center(chat_id, user_id)
            else:
                bot.send_message(chat_id, "❌ 订单验证失败\n\n⚠️ 剩余尝试次数：1次")
                send_alipay_order_page(chat_id, user_id)
        return
    
    # ========== 检查是否触发命令 ==========
    cmd = get_command(text)
    if cmd:
        user = get_user(user_id)
        purchased = has_purchased(user_id, text)
        
        try:
            bot.delete_message(chat_id, message.message_id)
        except:
            pass
        
        if purchased or cmd['points_cost'] == 0:
            if not purchased and cmd['points_cost'] == 0:
                add_purchase(user_id, text)
            
            sent_message_ids = []
            
            for link_info in cmd['message_links']:
                try:
                    sent = bot.copy_message(
                        chat_id=chat_id,
                        from_chat_id=link_info['channel_id'],
                        message_id=link_info['message_id'],
                        protect_content=True
                    )
                    sent_message_ids.append(sent.message_id)
                except:
                    pass
            
            if sent_message_ids:
                delete_messages_later(chat_id, sent_message_ids, user_id)
            
            markup = types.InlineKeyboardMarkup()
            btn = types.InlineKeyboardButton("🎁 返回兑换中心", callback_data="exchange_center")
            markup.add(btn)
            hint_msg = bot.send_message(chat_id, "✅ 内容已发送\n\n⏰ 消息将在20分钟后自动删除\n💡 已兑换内容可随时重新获取", reply_markup=markup)
            sent_message_ids.append(hint_msg.message_id)
        else:
            if user['points'] < cmd['points_cost']:
                msg = f"""🎁 兑换内容：{text}

━━━━━━━━━━━━━━━━━━━━━
💰 需要积分：{cmd['points_cost']}
💎 当前积分：{user['points']}
━━━━━━━━━━━━━━━━━━━━━

❌ 积分不足，请先充值"""
                
                markup = types.InlineKeyboardMarkup(row_width=1)
                btn1 = types.InlineKeyboardButton("💳 去充值", callback_data="recharge")
                btn2 = types.InlineKeyboardButton("🔙 返回兑换中心", callback_data="exchange_center")
                markup.add(btn1, btn2)
            else:
                msg = f"""🎁 兑换内容：{text}

━━━━━━━━━━━━━━━━━━━━━
💰 需要积分：{cmd['points_cost']}
💎 当前积分：{user['points']}
━━━━━━━━━━━━━━━━━━━━━

确认兑换吗？"""
                
                markup = types.InlineKeyboardMarkup(row_width=1)
                btn1 = types.InlineKeyboardButton("✅ 确认兑换", callback_data=f"confirm_exchange_{text}")
                btn2 = types.InlineKeyboardButton("🔙 返回兑换中心", callback_data="exchange_center")
                markup.add(btn1, btn2)
            
            bot.send_message(chat_id, msg, reply_markup=markup)
        return
    
    # ========== 默认：发送欢迎消息 ==========
    send_welcome(chat_id, user_id)

# ==================== 发送订单页面 ====================
def send_vip_order_page(chat_id, user_id):
    user_state[user_id] = {'waiting': 'vip_order'}
    
    markup = types.InlineKeyboardMarkup()
    btn = types.InlineKeyboardButton("🔙 返回首页", callback_data="back_home")
    markup.add(btn)
    bot.send_photo(chat_id, ORDER_IMAGE, caption=ORDER_MSG, reply_markup=markup)

def send_wechat_order_page(chat_id, user_id):
    user_state[user_id] = {'waiting': 'wechat_order'}
    
    markup = types.InlineKeyboardMarkup()
    btn = types.InlineKeyboardButton("🔙 返回积分中心", callback_data="points_center")
    markup.add(btn)
    bot.send_photo(chat_id, WECHAT_ORDER_IMAGE, caption=WECHAT_ORDER_MSG, reply_markup=markup)

def send_alipay_order_page(chat_id, user_id):
    user_state[user_id] = {'waiting': 'alipay_order'}
    
    markup = types.InlineKeyboardMarkup()
    btn = types.InlineKeyboardButton("🔙 返回积分中心", callback_data="points_center")
    markup.add(btn)
    bot.send_photo(chat_id, ALIPAY_ORDER_IMAGE, caption=ALIPAY_ORDER_MSG, reply_markup=markup)

# ==================== 回调处理 ====================
@bot.callback_query_handler(func=lambda call: call.data == "admin_panel")
def cb_admin_panel(call):
    if call.from_user.id != ADMIN_ID:
        return
    bot.answer_callback_query(call.id)
    user_state[call.from_user.id] = {}
    send_admin_panel(call.message.chat.id)

@bot.callback_query_handler(func=lambda call: call.data == "channel_library")
def cb_channel_library(call):
    if call.from_user.id != ADMIN_ID:
        return
    bot.answer_callback_query(call.id)
    user_state[call.from_user.id] = {}
    send_channel_library(call.message.chat.id)

@bot.callback_query_handler(func=lambda call: call.data == "add_command")
def cb_add_command(call):
    if call.from_user.id != ADMIN_ID:
        return
    bot.answer_callback_query(call.id)
    
    user_state[call.from_user.id] = {'admin_step': 'waiting_command_name'}
    
    msg = """➕ 添加新命令

━━━━━━━━━━━━━━━━━━━━━
📌 请输入命令名称

支持：中文、英文、数字
示例：VIP资源、资料包1
━━━━━━━━━━━━━━━━━━━━━"""
    
    markup = types.InlineKeyboardMarkup()
    btn = types.InlineKeyboardButton("❌ 取消", callback_data="channel_library")
    markup.add(btn)
    bot.send_message(call.message.chat.id, msg, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data == "delete_command_menu")
def cb_delete_command_menu(call):
    if call.from_user.id != ADMIN_ID:
        return
    bot.answer_callback_query(call.id)
    
    commands = get_all_commands()
    
    if not commands:
        bot.send_message(call.message.chat.id, "📭 暂无命令可删除")
        send_channel_library(call.message.chat.id)
        return
    
    user_state[call.from_user.id] = {'admin_step': 'waiting_delete_name'}
    
    msg = "🗑️ 删除命令\n\n━━━━━━━━━━━━━━━━━━━━━\n📋 已有命令：\n"
    
    for cmd in commands:
        msg += f"• {cmd['command_name']}\n"
    
    msg += "\n请输入要删除的命令名称"
    
    markup = types.InlineKeyboardMarkup()
    btn = types.InlineKeyboardButton("❌ 取消", callback_data="channel_library")
    markup.add(btn)
    bot.send_message(call.message.chat.id, msg, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data == "start_verify")
def cb_start_verify(call):
    user_id = call.from_user.id
    if is_vip_locked(user_id):
        bot.answer_callback_query(call.id, "⏳ 请等待冷却时间结束", show_alert=True)
        return
    bot.answer_callback_query(call.id)
    
    markup = types.InlineKeyboardMarkup()
    btn = types.InlineKeyboardButton("💳 我已付款，验证订单", callback_data="paid_verify")
    markup.add(btn)
    bot.send_photo(call.message.chat.id, VIP_IMAGE, caption=VIP_MSG, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data == "paid_verify")
def cb_paid_verify(call):
    user_id = call.from_user.id
    if is_vip_locked(user_id):
        bot.answer_callback_query(call.id, "⏳ 请等待冷却时间结束", show_alert=True)
        return
    bot.answer_callback_query(call.id)
    send_vip_order_page(call.message.chat.id, user_id)

@bot.callback_query_handler(func=lambda call: call.data == "points_center")
def cb_points_center(call):
    bot.answer_callback_query(call.id)
    user_state[call.from_user.id] = {}
    send_points_center(call.message.chat.id, call.from_user.id)

@bot.callback_query_handler(func=lambda call: call.data == "exchange_center")
def cb_exchange_center(call):
    bot.answer_callback_query(call.id)
    user_state[call.from_user.id] = {}
    send_exchange_center(call.message.chat.id, call.from_user.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("exchange_"))
def cb_exchange_item(call):
    command_name = call.data[9:]
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    
    cmd = get_command(command_name)
    if not cmd:
        bot.answer_callback_query(call.id, "❌ 该内容不存在", show_alert=True)
        return
    
    user = get_user(user_id)
    purchased = has_purchased(user_id, command_name)
    
    bot.answer_callback_query(call.id)
    
    if purchased:
        sent_message_ids = []
        
        for link_info in cmd['message_links']:
            try:
                sent = bot.copy_message(
                    chat_id=chat_id,
                    from_chat_id=link_info['channel_id'],
                    message_id=link_info['message_id'],
                    protect_content=True
                )
                sent_message_ids.append(sent.message_id)
            except:
                pass
        
        if sent_message_ids:
            delete_messages_later(chat_id, sent_message_ids, user_id)
        
        markup = types.InlineKeyboardMarkup()
        btn = types.InlineKeyboardButton("🎁 返回兑换中心", callback_data="exchange_center")
        markup.add(btn)
        hint_msg = bot.send_message(chat_id, "✅ 内容已发送\n\n⏰ 消息将在20分钟后自动删除\n💡 已兑换内容可随时重新获取", reply_markup=markup)
        sent_message_ids.append(hint_msg.message_id)
    else:
        if user['points'] < cmd['points_cost']:
            msg = f"""🎁 兑换内容：{command_name}

━━━━━━━━━━━━━━━━━━━━━
💰 需要积分：{cmd['points_cost']}
💎 当前积分：{user['points']}
━━━━━━━━━━━━━━━━━━━━━

❌ 积分不足，请先充值"""
            
            markup = types.InlineKeyboardMarkup(row_width=1)
            btn1 = types.InlineKeyboardButton("💳 去充值", callback_data="recharge")
            btn2 = types.InlineKeyboardButton("🔙 返回兑换中心", callback_data="exchange_center")
            markup.add(btn1, btn2)
        else:
            msg = f"""🎁 兑换内容：{command_name}

━━━━━━━━━━━━━━━━━━━━━
💰 需要积分：{cmd['points_cost']}
💎 当前积分：{user['points']}
━━━━━━━━━━━━━━━━━━━━━

确认兑换吗？"""
            
            markup = types.InlineKeyboardMarkup(row_width=1)
            btn1 = types.InlineKeyboardButton("✅ 确认兑换", callback_data=f"confirm_exchange_{command_name}")
            btn2 = types.InlineKeyboardButton("🔙 返回兑换中心", callback_data="exchange_center")
            markup.add(btn1, btn2)
        
        bot.send_message(chat_id, msg, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("confirm_exchange_"))
def cb_confirm_exchange(call):
    command_name = call.data[16:]
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    
    cmd = get_command(command_name)
    if not cmd:
        bot.answer_callback_query(call.id, "❌ 该内容不存在", show_alert=True)
        return
    
    user = get_user(user_id)
    
    if user['points'] < cmd['points_cost']:
        bot.answer_callback_query(call.id, "❌ 积分不足", show_alert=True)
        return
    
    deduct_points(user_id, cmd['points_cost'])
    add_purchase(user_id, command_name)
    
    bot.answer_callback_query(call.id, "✅ 兑换成功！")
    
    sent_message_ids = []
    
    for link_info in cmd['message_links']:
        try:
            sent = bot.copy_message(
                chat_id=chat_id,
                from_chat_id=link_info['channel_id'],
                message_id=link_info['message_id'],
                protect_content=True
            )
            sent_message_ids.append(sent.message_id)
        except:
            pass
    
    if sent_message_ids:
        delete_messages_later(chat_id, sent_message_ids, user_id)
    
    new_user = get_user(user_id)
    markup = types.InlineKeyboardMarkup()
    btn = types.InlineKeyboardButton("🎁 返回兑换中心", callback_data="exchange_center")
    markup.add(btn)
    hint_msg = bot.send_message(chat_id, f"""✅ 兑换成功！

💰 消耗积分：{cmd['points_cost']}
💎 剩余积分：{new_user['points']}

⏰ 消息将在20分钟后自动删除
💡 已兑换内容可随时重新获取（无需再次付费）""", reply_markup=markup)
    sent_message_ids.append(hint_msg.message_id)

@bot.callback_query_handler(func=lambda call: call.data == "checkin")
def cb_checkin(call):
    user_id = call.from_user.id
    user = get_user(user_id)
    today = date.today()
    
    if user['last_checkin'] == today:
        bot.answer_callback_query(call.id, "❌ 今日已签到，明天再来！", show_alert=True)
        return
    
    points = random.randint(3, 8)
    add_points(user_id, points)
    update_user(user_id, last_checkin=today)
    
    bot.answer_callback_query(call.id)
    
    new_user = get_user(user_id)
    msg = f"""🎉 签到成功！

💰 获得积分：+{points}
💎 当前积分：{new_user['points']}

明天继续签到获取更多积分！"""
    
    markup = types.InlineKeyboardMarkup()
    btn = types.InlineKeyboardButton("🔙 返回积分中心", callback_data="points_center")
    markup.add(btn)
    bot.send_message(call.message.chat.id, msg, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data == "recharge")
def cb_recharge(call):
    bot.answer_callback_query(call.id)
    send_recharge_menu(call.message.chat.id, call.from_user.id)

@bot.callback_query_handler(func=lambda call: call.data == "wechat_pay")
def cb_wechat_pay(call):
    user_id = call.from_user.id
    user = get_user(user_id)
    
    if user['wechat_used']:
        bot.answer_callback_query(call.id, "❌ 微信充值已使用过", show_alert=True)
        return
    if is_wechat_locked(user_id):
        bot.answer_callback_query(call.id, "⏳ 请等待冷却时间结束", show_alert=True)
        return
    
    bot.answer_callback_query(call.id)
    
    markup = types.InlineKeyboardMarkup()
    btn1 = types.InlineKeyboardButton("✅ 我已支付，开始验证", callback_data="wechat_verify")
    btn2 = types.InlineKeyboardButton("🔙 返回充值页面", callback_data="recharge")
    markup.add(btn1)
    markup.add(btn2)
    bot.send_photo(call.message.chat.id, WECHAT_PAY_IMAGE, caption=WECHAT_PAY_MSG, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data == "wechat_verify")
def cb_wechat_verify(call):
    user_id = call.from_user.id
    user = get_user(user_id)
    
    if user['wechat_used']:
        bot.answer_callback_query(call.id, "❌ 微信充值已使用过", show_alert=True)
        return
    if is_wechat_locked(user_id):
        bot.answer_callback_query(call.id, "⏳ 请等待冷却时间结束", show_alert=True)
        return
    
    bot.answer_callback_query(call.id)
    send_wechat_order_page(call.message.chat.id, user_id)

@bot.callback_query_handler(func=lambda call: call.data == "alipay_pay")
def cb_alipay_pay(call):
    user_id = call.from_user.id
    user = get_user(user_id)
    
    if user['alipay_used']:
        bot.answer_callback_query(call.id, "❌ 支付宝充值已使用过", show_alert=True)
        return
    if is_alipay_locked(user_id):
        bot.answer_callback_query(call.id, "⏳ 请等待冷却时间结束", show_alert=True)
        return
    
    bot.answer_callback_query(call.id)
    
    markup = types.InlineKeyboardMarkup()
    btn1 = types.InlineKeyboardButton("✅ 我已支付，开始验证", callback_data="alipay_verify")
    btn2 = types.InlineKeyboardButton("🔙 返回充值页面", callback_data="recharge")
    markup.add(btn1)
    markup.add(btn2)
    bot.send_photo(call.message.chat.id, ALIPAY_PAY_IMAGE, caption=ALIPAY_PAY_MSG, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data == "alipay_verify")
def cb_alipay_verify(call):
    user_id = call.from_user.id
    user = get_user(user_id)
    
    if user['alipay_used']:
        bot.answer_callback_query(call.id, "❌ 支付宝充值已使用过", show_alert=True)
        return
    if is_alipay_locked(user_id):
        bot.answer_callback_query(call.id, "⏳ 请等待冷却时间结束", show_alert=True)
        return
    
    bot.answer_callback_query(call.id)
    send_alipay_order_page(call.message.chat.id, user_id)

@bot.callback_query_handler(func=lambda call: call.data == "back_home")
def cb_back_home(call):
    bot.answer_callback_query(call.id)
    user_state[call.from_user.id] = {}
    send_welcome(call.message.chat.id, call.from_user.id)

@bot.callback_query_handler(func=lambda call: call.data == "locked")
def cb_locked(call):
    bot.answer_callback_query(call.id, "⏳ 请等待冷却时间结束后重试", show_alert=True)

@bot.callback_query_handler(func=lambda call: call.data == "used")
def cb_used(call):
    bot.answer_callback_query(call.id, "❌ 该充值方式已使用，每种方式仅限1次", show_alert=True)

@bot.callback_query_handler(func=lambda call: call.data == "get_file_id")
def cb_get_file_id(call):
    if call.from_user.id != ADMIN_ID:
        return
    bot.answer_callback_query(call.id)
    bot.send_message(call.message.chat.id, "📤 请发送任意文件/图片/视频，我会返回 File ID")

# ==================== 启动 ====================
init_db()
bot.infinity_polling()
