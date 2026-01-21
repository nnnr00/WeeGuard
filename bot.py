import os
import logging
import asyncio
import threading
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import (
    Application, 
    CommandHandler, 
    CallbackQueryHandler, 
    MessageHandler, 
    filters,
    ContextTypes,
    ConversationHandler
)
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import uvicorn
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz
from database import (
    init_db, 
    save_file_id, 
    get_all_file_ids, 
    delete_file_id, 
    get_file_by_id,
    get_or_create_user,
    get_user_points,
    check_and_do_checkin,
    get_user_info,
    get_ad_watch_count,
    generate_ad_token,
    verify_ad_token,
    get_token_user_id,
    check_duplicate_ip,
    get_today_keys,
    create_new_daily_keys,
    update_key_link,
    get_key_links,
    get_user_key_claim_count,
    claim_key,
    check_keys_ready,
    is_after_10am_beijing,
    get_beijing_datetime,
    get_next_key_reset_time,
    check_user_claimed_key
)

# 配置日志
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# 环境变量
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://你的用户名.github.io/你的仓库名")

# 北京时区
BEIJING_TZ = pytz.timezone('Asia/Shanghai')

# 会话状态
WAITING_FOR_PHOTO = 1
WAITING_FOR_KEY_INPUT = 2
WAITING_FOR_KEY1_LINK = 3
WAITING_FOR_KEY2_LINK = 4

# 用户状态存储
user_states = {}

# Telegram 应用实例（全局）
telegram_app = None

# ==================== FastAPI 后端 ====================

app = FastAPI(title="Telegram Bot API")

# CORS 配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
async def root():
    """健康检查"""
    return {"status": "ok", "message": "Telegram Bot API is running"}

@app.get("/api/token/{user_id}")
async def get_token(user_id: int):
    """获取广告验证令牌"""
    try:
        token = generate_ad_token(user_id)
        return {"success": True, "token": token}
    except Exception as e:
        logger.error(f"生成令牌失败: {e}")
        raise HTTPException(status_code=500, detail="生成令牌失败")

@app.post("/api/verify")
async def verify_ad(request: Request):
    """验证广告观看并发放积分"""
    try:
        data = await request.json()
        token = data.get("token")
        
        if not token:
            return JSONResponse(
                status_code=400,
                content={"success": False, "message": "缺少验证令牌"}
            )
        
        # 获取客户端信息用于防作弊
        ip_address = request.client.host
        user_agent = request.headers.get("user-agent", "")
        
        # 获取用户ID
        user_id = get_token_user_id(token)
        
        if not user_id:
            return JSONResponse(
                status_code=400,
                content={"success": False, "message": "无效的令牌"}
            )
        
        # 检查IP是否可疑
        if check_duplicate_ip(user_id, ip_address):
            logger.warning(f"可疑IP检测: user_id={user_id}, ip={ip_address}")
            return JSONResponse(
                status_code=400,
                content={"success": False, "message": "检测到异常行为，请稍后再试"}
            )
        
        # 验证并发放积分
        success, points, message = verify_ad_token(token, ip_address, user_agent)
        
        if success:
            # 获取最新积分和观看次数
            current_points = get_user_points(user_id)
            watch_count = get_ad_watch_count(user_id)
            
            return {
                "success": True,
                "points_earned": points,
                "total_points": current_points,
                "watch_count": watch_count,
                "message": message
            }
        else:
            return JSONResponse(
                status_code=400,
                content={"success": False, "message": message}
            )
    
    except Exception as e:
        logger.error(f"验证广告失败: {e}")
        raise HTTPException(status_code=500, detail="验证失败")

@app.get("/api/user/{user_id}")
async def get_user(user_id: int):
    """获取用户信息"""
    try:
        user = get_user_info(user_id)
        if user:
            watch_count = get_ad_watch_count(user_id)
            return {
                "success": True,
                "user_id": user_id,
                "points": user['points'],
                "watch_count": watch_count,
                "max_watch": 3
            }
        else:
            return JSONResponse(
                status_code=404,
                content={"success": False, "message": "用户不存在"}
            )
    except Exception as e:
        logger.error(f"获取用户信息失败: {e}")
        raise HTTPException(status_code=500, detail="获取用户信息失败")

@app.get("/api/key-links")
async def get_current_key_links():
    """获取当前密钥链接"""
    try:
        key1_link, key2_link = get_key_links()
        return {
            "success": True,
            "key1_link": key1_link,
            "key2_link": key2_link
        }
    except Exception as e:
        logger.error(f"获取密钥链接失败: {e}")
        raise HTTPException(status_code=500, detail="获取密钥链接失败")

# ==================== Telegram Bot ====================

def is_admin(user_id: int) -> bool:
    """检查是否是管理员"""
    return user_id == ADMIN_ID

def get_start_keyboard():
    """首页主键盘"""
    keyboard = [
        [InlineKeyboardButton("✅ 开始验证", callback_data="start_verify")],
        [InlineKeyboardButton("💰 积分中心", callback_data="points_center")],
        [InlineKeyboardButton("🎉 开业活动", callback_data="activity_center")],
    ]
    return InlineKeyboardMarkup(keyboard)

def get_points_keyboard():
    """积分中心键盘"""
    keyboard = [
        [InlineKeyboardButton("📅 每日签到", callback_data="daily_checkin")],
        [InlineKeyboardButton("◀️ 返回首页", callback_data="back_to_start")],
    ]
    return InlineKeyboardMarkup(keyboard)

def get_activity_keyboard(user_id: int):
    """活动中心键盘"""
    watch_count = get_ad_watch_count(user_id)
    key_count = get_user_key_claim_count(user_id)
    
    keyboard = [
        [InlineKeyboardButton(f"🎬 看视频得积分 ({watch_count}/3)", callback_data="watch_ad")],
        [InlineKeyboardButton(f"🔑 夸克宝箱密钥 ({key_count}/2)", callback_data="key_activity")],
        [InlineKeyboardButton("◀️ 返回首页", callback_data="back_to_start")],
    ]
    return InlineKeyboardMarkup(keyboard)

def get_watch_ad_keyboard(user_id: int, token: str):
    """观看广告键盘"""
    watch_url = f"{WEBAPP_URL}?user_id={user_id}&token={token}"
    
    keyboard = [
        [InlineKeyboardButton("▶️ 开始观看", url=watch_url)],
        [InlineKeyboardButton("🔄 刷新状态", callback_data="refresh_ad_status")],
        [InlineKeyboardButton("◀️ 返回活动中心", callback_data="activity_center")],
    ]
    return InlineKeyboardMarkup(keyboard)

def get_key_activity_keyboard(user_id: int):
    """密钥活动键盘"""
    key_count = get_user_key_claim_count(user_id)
    claimed_key1 = check_user_claimed_key(user_id, "key1")
    claimed_key2 = check_user_claimed_key(user_id, "key2")
    
    keyboard = []
    
    # 检查密钥是否就绪
    keys_ready, _ = check_keys_ready()
    
    if not claimed_key1:
        if keys_ready:
            keyboard.append([InlineKeyboardButton("🔑 获取密钥一 (+8积分)", callback_data="get_key_1")])
        else:
            keyboard.append([InlineKeyboardButton("⏳ 密钥一 (等待更新)", callback_data="key_not_ready")])
    else:
        keyboard.append([InlineKeyboardButton("✅ 密钥一 (已领取)", callback_data="key_already_claimed")])
    
    if not claimed_key2:
        if keys_ready:
            keyboard.append([InlineKeyboardButton("🔑 获取密钥二 (+6积分)", callback_data="get_key_2")])
        else:
            keyboard.append([InlineKeyboardButton("⏳ 密钥二 (等待更新)", callback_data="key_not_ready")])
    else:
        keyboard.append([InlineKeyboardButton("✅ 密钥二 (已领取)", callback_data="key_already_claimed")])
    
    keyboard.append([InlineKeyboardButton("◀️ 返回活动中心", callback_data="activity_center")])
    
    return InlineKeyboardMarkup(keyboard)

def get_admin_keyboard():
    """管理员后台主键盘"""
    keyboard = [
        [InlineKeyboardButton("🖼 获取图片 File ID", callback_data="get_file_id")],
        [InlineKeyboardButton("📂 查看已保存的图片", callback_data="view_saved")],
    ]
    return InlineKeyboardMarkup(keyboard)

def get_back_keyboard():
    """返回按钮"""
    keyboard = [[InlineKeyboardButton("◀️ 返回后台", callback_data="back_to_admin")]]
    return InlineKeyboardMarkup(keyboard)

def get_back_to_points_keyboard():
    """返回积分中心按钮"""
    keyboard = [[InlineKeyboardButton("◀️ 返回积分中心", callback_data="points_center")]]
    return InlineKeyboardMarkup(keyboard)

def get_back_to_activity_keyboard():
    """返回活动中心按钮"""
    keyboard = [[InlineKeyboardButton("◀️ 返回活动中心", callback_data="activity_center")]]
    return InlineKeyboardMarkup(keyboard)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """启动命令"""
    user = update.effective_user
    
    # 获取或创建用户
    get_or_create_user(user.id, user.username)
    
    # 清除用户状态
    if user.id in user_states:
        del user_states[user.id]
    
    await update.message.reply_text(
        f"👋 欢迎使用机器人，{user.first_name}！\n\n"
        f"请选择您需要的功能：",
        reply_markup=get_start_keyboard()
    )

async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """管理员后台"""
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text("⛔ 您没有管理员权限")
        return
    
    await update.message.reply_text(
        "🔐 **管理员后台**\n\n"
        "请选择功能：",
        reply_markup=get_admin_keyboard(),
        parse_mode="Markdown"
    )

async def jf_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/jf 命令 - 积分中心"""
    user = update.effective_user
    
    # 获取或创建用户
    get_or_create_user(user.id, user.username)
    
    # 获取用户积分
    points = get_user_points(user.id)
    
    await update.message.reply_text(
        f"💰 **积分中心**\n\n"
        f"👤 用户：{user.first_name}\n"
        f"💎 当前积分：**{points}**\n\n"
        f"请选择操作：",
        reply_markup=get_points_keyboard(),
        parse_mode="Markdown"
    )

async def hd_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/hd 命令 - 活动中心"""
    user = update.effective_user
    
    # 获取或创建用户
    get_or_create_user(user.id, user.username)
    
    # 获取用户观看次数
    watch_count = get_ad_watch_count(user.id)
    key_count = get_user_key_claim_count(user.id)
    points = get_user_points(user.id)
    
    await update.message.reply_text(
        f"🎉 **活动中心**\n\n"
        f"👤 用户：{user.first_name}\n"
        f"💎 当前积分：**{points}**\n\n"
        f"🎁 开业活动进行中！\n"
        f"📺 视频观看：{watch_count}/3\n"
        f"🔑 密钥领取：{key_count}/2\n\n"
        f"请选择活动：",
        reply_markup=get_activity_keyboard(user.id),
        parse_mode="Markdown"
    )

async def my_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/my 命令 - 管理员查看/更换密钥"""
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text("⛔ 您没有管理员权限")
        return
    
    # 检查是否在10点之后
    if not is_after_10am_beijing():
        next_reset = get_next_key_reset_time()
        await update.message.reply_text(
            f"⏰ **请在北京时间 10:00 后再试**\n\n"
            f"下次更新时间：{next_reset.strftime('%Y-%m-%d %H:%M:%S')} (北京时间)\n\n"
            f"💡 密钥每天北京时间上午 10:00 自动更新",
            parse_mode="Markdown"
        )
        return
    
    # 获取今日密钥
    keys = get_today_keys()
    
    if not keys:
        # 创建新密钥
        keys = create_new_daily_keys()
    
    key1_link = keys.get('key1_link') or "未设置"
    key2_link = keys.get('key2_link') or "未设置"
    
    await update.message.reply_text(
        f"🔐 **今日密钥管理**\n\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"🔑 **密钥一** (+8积分)\n"
        f"`{keys['key1']}`\n\n"
        f"🔑 **密钥二** (+6积分)\n"
        f"`{keys['key2']}`\n\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"🔗 **密钥一链接**：\n{key1_link}\n\n"
        f"🔗 **密钥二链接**：\n{key2_link}\n\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"📅 更新时间：{keys['created_at'].strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        f"💡 回复 `1` 更换密钥一链接\n"
        f"💡 回复 `2` 更换密钥二链接",
        parse_mode="Markdown"
    )
    
    # 设置管理员状态
    user_states[user_id] = "waiting_for_key_choice"

async def id_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/id 命令 - 获取图片 File ID"""
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text("⛔ 您没有管理员权限")
        return ConversationHandler.END
    
    await update.message.reply_text(
        "🖼 **获取图片 File ID**\n\n"
        "请发送一张图片，我将返回它的 File ID\n\n"
        "发送 /cancel 取消操作",
        parse_mode="Markdown",
        reply_markup=get_back_keyboard()
    )
    return WAITING_FOR_PHOTO

async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理文本消息"""
    user = update.effective_user
    user_id = user.id
    text = update.message.text.strip()
    
    # 检查管理员状态
    if is_admin(user_id) and user_id in user_states:
        state = user_states[user_id]
        
        if state == "waiting_for_key_choice":
            if text == "1":
                user_states[user_id] = "waiting_for_key1_link"
                await update.message.reply_text(
                    "🔗 **请输入密钥一的跳转链接**\n\n"
                    "这是用户获取密钥一时跳转到的网盘链接\n\n"
                    "💡 发送 /cancel 取消操作",
                    parse_mode="Markdown"
                )
                return
            elif text == "2":
                user_states[user_id] = "waiting_for_key2_link"
                await update.message.reply_text(
                    "🔗 **请输入密钥二的跳转链接**\n\n"
                    "这是用户获取密钥二时跳转到的网盘链接\n\n"
                    "💡 发送 /cancel 取消操作",
                    parse_mode="Markdown"
                )
                return
            else:
                del user_states[user_id]
        
        elif state == "waiting_for_key1_link":
            update_key_link("key1", text)
            del user_states[user_id]
            await update.message.reply_text(
                f"✅ **密钥一链接绑定完成！**\n\n"
                f"🔗 链接：{text}\n\n"
                f"💡 使用 /my 查看当前密钥状态",
                parse_mode="Markdown"
            )
            return
        
        elif state == "waiting_for_key2_link":
            update_key_link("key2", text)
            del user_states[user_id]
            await update.message.reply_text(
                f"✅ **密钥二链接绑定完成！**\n\n"
                f"🔗 链接：{text}\n\n"
                f"💡 使用 /my 查看当前密钥状态",
                parse_mode="Markdown"
            )
            return
    
    # 检查用户是否在等待输入密钥
    if user_id in user_states and user_states[user_id] == "waiting_for_key":
        # 尝试验证密钥
        success, points, message, key_type = claim_key(user_id, text, user.username)
        
        if success:
            current_points = get_user_points(user_id)
            key_count = get_user_key_claim_count(user_id)
            
            await update.message.reply_text(
                f"{message}\n\n"
                f"💎 当前总积分：**{current_points}**\n"
                f"🔑 今日已领取：{key_count}/2",
                parse_mode="Markdown",
                reply_markup=get_back_to_activity_keyboard()
            )
        else:
            await update.message.reply_text(
                f"{message}\n\n"
                f"💡 请检查密钥是否正确，或返回活动中心重新获取",
                parse_mode="Markdown",
                reply_markup=get_back_to_activity_keyboard()
            )
        
        # 清除状态
        del user_states[user_id]
        return
    
    # 尝试作为密钥验证（用户可能直接发送密钥）
    keys = get_today_keys()
    if keys and (text == keys['key1'] or text == keys['key2']):
        success, points, message, key_type = claim_key(user_id, text, user.username)
        
        if success:
            current_points = get_user_points(user_id)
            key_count = get_user_key_claim_count(user_id)
            
            await update.message.reply_text(
                f"{message}\n\n"
                f"💎 当前总积分：**{current_points}**\n"
                f"🔑 今日已领取：{key_count}/2",
                parse_mode="Markdown",
                reply_markup=get_back_to_activity_keyboard()
            )
        else:
            await update.message.reply_text(
                message,
                parse_mode="Markdown",
                reply_markup=get_back_to_activity_keyboard()
            )
        return

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理按钮回调"""
    query = update.callback_query
    await query.answer()
    
    user = update.effective_user
    user_id = user.id
    data = query.data
    
    # 清除之前的状态
    if user_id in user_states and not data.startswith("get_key"):
        if user_states[user_id] not in ["waiting_for_key1_link", "waiting_for_key2_link", "waiting_for_key_choice"]:
            del user_states[user_id]
    
    # ==================== 首页相关 ====================
    
    # 返回首页
    if data == "back_to_start":
        if user_id in user_states:
            del user_states[user_id]
        
        await query.edit_message_text(
            f"👋 欢迎使用机器人，{user.first_name}！\n\n"
            f"请选择您需要的功能：",
            reply_markup=get_start_keyboard()
        )
        return ConversationHandler.END
    
    # 开始验证
    elif data == "start_verify":
        await query.edit_message_text(
            "✅ **开始验证**\n\n"
            "验证功能开发中...",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("◀️ 返回首页", callback_data="back_to_start")]
            ])
        )
        return ConversationHandler.END
    
    # ==================== 积分中心相关 ====================
    
    # 积分中心
    elif data == "points_center":
        # 获取或创建用户
        get_or_create_user(user_id, user.username)
        
        # 获取用户积分
        points = get_user_points(user_id)
        
        await query.edit_message_text(
            f"💰 **积分中心**\n\n"
            f"👤 用户：{user.first_name}\n"
            f"💎 当前积分：**{points}**\n\n"
            f"请选择操作：",
            reply_markup=get_points_keyboard(),
            parse_mode="Markdown"
        )
        return ConversationHandler.END
    
    # 每日签到
    elif data == "daily_checkin":
        success, points_earned, message, is_first = check_and_do_checkin(user_id, user.username)
        
        # 获取最新积分
        current_points = get_user_points(user_id)
        
        if success:
            if is_first:
                text = (
                    f"🎉 **首次签到成功！**\n\n"
                    f"🎁 恭喜获得首次签到奖励：**+{points_earned}** 积分\n"
                    f"💎 当前总积分：**{current_points}**\n\n"
                    f"💡 每日签到可获得 3-8 随机积分哦！"
                )
            else:
                text = (
                    f"✅ **签到成功！**\n\n"
                    f"🎁 获得积分：**+{points_earned}**\n"
                    f"💎 当前总积分：**{current_points}**\n\n"
                    f"💡 明天继续签到可获得更多积分！"
                )
        else:
            text = (
                f"⏰ **{message}**\n\n"
                f"💎 当前总积分：**{current_points}**\n\n"
                f"💡 每天可签到一次，明天再来吧！"
            )
        
        await query.edit_message_text(
            text,
            parse_mode="Markdown",
            reply_markup=get_back_to_points_keyboard()
        )
        return ConversationHandler.END
    
    # ==================== 活动中心相关 ====================
    
    # 活动中心
    elif data == "activity_center":
        # 获取或创建用户
        get_or_create_user(user_id, user.username)
        
        # 获取用户观看次数
        watch_count = get_ad_watch_count(user_id)
        key_count = get_user_key_claim_count(user_id)
        points = get_user_points(user_id)
        
        await query.edit_message_text(
            f"🎉 **活动中心**\n\n"
            f"👤 用户：{user.first_name}\n"
            f"💎 当前积分：**{points}**\n\n"
            f"🎁 开业活动进行中！\n"
            f"📺 视频观看：{watch_count}/3\n"
            f"🔑 密钥领取：{key_count}/2\n\n"
            f"请选择活动：",
            reply_markup=get_activity_keyboard(user_id),
            parse_mode="Markdown"
        )
        return ConversationHandler.END
    
    # 看视频得积分
    elif data == "watch_ad":
        watch_count = get_ad_watch_count(user_id)
        
        if watch_count >= 3:
            await query.edit_message_text(
                "⏰ **今日次数已用完**\n\n"
                "您今天已经观看了 3 次视频\n"
                "每天北京时间 0:00 重置次数\n\n"
                "💡 明天再来吧！",
                parse_mode="Markdown",
                reply_markup=get_back_to_activity_keyboard()
            )
            return ConversationHandler.END
        
        # 生成验证令牌
        token = generate_ad_token(user_id)
        
        # 计算本次可获得积分
        next_count = watch_count + 1
        if next_count == 1:
            points_preview = "10"
        elif next_count == 2:
            points_preview = "6"
        else:
            points_preview = "3-10 随机"
        
        await query.edit_message_text(
            f"🎬 **看视频得积分**\n\n"
            f"📺 今日观看次数：{watch_count}/3\n"
            f"🎁 本次可获得：**{points_preview}** 积分\n\n"
            f"📋 **活动规则：**\n"
            f"• 第 1 次观看：获得 10 积分\n"
            f"• 第 2 次观看：获得 6 积分\n"
            f"• 第 3 次观看：获得 3-10 随机积分\n"
            f"• 每天北京时间 0:00 重置次数\n\n"
            f"⚠️ 请完整观看视频，中途退出无法获得积分\n\n"
            f"点击下方按钮开始观看：",
            parse_mode="Markdown",
            reply_markup=get_watch_ad_keyboard(user_id, token)
        )
        return ConversationHandler.END
    
    # 刷新广告状态
    elif data == "refresh_ad_status":
        watch_count = get_ad_watch_count(user_id)
        points = get_user_points(user_id)
        
        if watch_count >= 3:
            await query.edit_message_text(
                f"✅ **今日任务已完成！**\n\n"
                f"📺 观看次数：{watch_count}/3\n"
                f"💎 当前积分：**{points}**\n\n"
                f"💡 明天再来获取更多积分吧！",
                parse_mode="Markdown",
                reply_markup=get_back_to_activity_keyboard()
            )
        else:
            # 生成新令牌
            token = generate_ad_token(user_id)
            
            next_count = watch_count + 1
            if next_count == 1:
                points_preview = "10"
            elif next_count == 2:
                points_preview = "6"
            else:
                points_preview = "3-10 随机"
            
            await query.edit_message_text(
                f"🔄 **状态已刷新**\n\n"
                f"📺 今日观看次数：{watch_count}/3\n"
                f"💎 当前积分：**{points}**\n"
                f"🎁 下次可获得：**{points_preview}** 积分\n\n"
                f"点击下方按钮继续观看：",
                parse_mode="Markdown",
                reply_markup=get_watch_ad_keyboard(user_id, token)
            )
        
        return ConversationHandler.END
    
    # ==================== 密钥活动相关 ====================
    
    # 密钥活动入口
    elif data == "key_activity":
        key_count = get_user_key_claim_count(user_id)
        points = get_user_points(user_id)
        next_reset = get_next_key_reset_time()
        
        await query.edit_message_text(
            f"🔑 **夸克宝箱密钥**\n\n"
            f"💎 当前积分：**{points}**\n"
            f"📊 今日领取：{key_count}/2\n\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"📋 **活动说明**\n\n"
            f"1️⃣ 点击下方按钮获取密钥\n"
            f"2️⃣ 页面跳转中请耐心等待 3 秒\n"
            f"3️⃣ 看到夸克网盘后，保存文件\n"
            f"4️⃣ 重命名文件，复制文件名\n"
            f"5️⃣ 将密钥发送给机器人领取积分\n\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"🎁 **积分奖励**\n\n"
            f"• 密钥一：**+8** 积分\n"
            f"• 密钥二：**+6** 积分\n\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"⏰ 重置时间：每天北京时间 10:00\n"
            f"📅 下次重置：{next_reset.strftime('%m-%d %H:%M')}\n\n"
            f"请选择要获取的密钥：",
            parse_mode="Markdown",
            reply_markup=get_key_activity_keyboard(user_id)
        )
        return ConversationHandler.END
    
    # 密钥未就绪
    elif data == "key_not_ready":
        await query.answer("⏳ 请等待管理员更换新密钥链接", show_alert=True)
        return ConversationHandler.END
    
    # 密钥已领取
    elif data == "key_already_claimed":
        await query.answer("✅ 您已领取过此密钥，请勿重复领取", show_alert=True)
        return ConversationHandler.END
    
    # 获取密钥一
    elif data == "get_key_1":
        # 检查是否已领取
        if check_user_claimed_key(user_id, "key1"):
            await query.answer("✅ 您已领取过密钥一，请勿重复领取", show_alert=True)
            return ConversationHandler.END
        
        # 检查密钥是否就绪
        keys_ready, msg = check_keys_ready()
        if not keys_ready:
            await query.answer("⏳ 请等待管理员更换新密钥链接", show_alert=True)
            return ConversationHandler.END
        
        # 获取密钥链接
        key1_link, _ = get_key_links()
        
        # 构建中转页面URL
        redirect_url = f"{WEBAPP_URL}/redirect1.html?target={key1_link}"
        
        keyboard = [
            [InlineKeyboardButton("🚀 开始获取密钥一", url=redirect_url)],
            [InlineKeyboardButton("📝 我已获取，输入密钥", callback_data="input_key")],
            [InlineKeyboardButton("◀️ 返回", callback_data="key_activity")]
        ]
        
        await query.edit_message_text(
            f"🔑 **获取密钥一**\n\n"
            f"🎁 领取奖励：**+8 积分**\n\n"
            f"📋 **获取步骤：**\n\n"
            f"1️⃣ 点击「开始获取密钥一」按钮\n"
            f"2️⃣ 等待 3 秒自动跳转到夸克网盘\n"
            f"3️⃣ 保存文件到自己的网盘\n"
            f"4️⃣ 重命名文件，复制新文件名\n"
            f"5️⃣ 返回这里点击「输入密钥」\n"
            f"6️⃣ 将密钥发送给我领取积分\n\n"
            f"⚠️ 密钥每天 10:00 更新，请及时领取！",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return ConversationHandler.END
    
    # 获取密钥二
    elif data == "get_key_2":
        # 检查是否已领取
        if check_user_claimed_key(user_id, "key2"):
            await query.answer("✅ 您已领取过密钥二，请勿重复领取", show_alert=True)
            return ConversationHandler.END
        
        # 检查密钥是否就绪
        keys_ready, msg = check_keys_ready()
        if not keys_ready:
            await query.answer("⏳ 请等待管理员更换新密钥链接", show_alert=True)
            return ConversationHandler.END
        
        # 获取密钥链接
        _, key2_link = get_key_links()
        
        # 构建中转页面URL
        redirect_url = f"{WEBAPP_URL}/redirect2.html?target={key2_link}"
        
        keyboard = [
            [InlineKeyboardButton("🚀 开始获取密钥二", url=redirect_url)],
            [InlineKeyboardButton("📝 我已获取，输入密钥", callback_data="input_key")],
            [InlineKeyboardButton("◀️ 返回", callback_data="key_activity")]
        ]
        
        await query.edit_message_text(
            f"🔑 **获取密钥二**\n\n"
            f"🎁 领取奖励：**+6 积分**\n\n"
            f"📋 **获取步骤：**\n\n"
            f"1️⃣ 点击「开始获取密钥二」按钮\n"
            f"2️⃣ 等待 3 秒自动跳转到夸克网盘\n"
            f"3️⃣ 保存文件到自己的网盘\n"
            f"4️⃣ 重命名文件，复制新文件名\n"
            f"5️⃣ 返回这里点击「输入密钥」\n"
            f"6️⃣ 将密钥发送给我领取积分\n\n"
            f"⚠️ 密钥每天 10:00 更新，请及时领取！",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return ConversationHandler.END
    
    # 输入密钥
    elif data == "input_key":
        user_states[user_id] = "waiting_for_key"
        
        await query.edit_message_text(
            f"📝 **请输入密钥**\n\n"
            f"请将您从夸克网盘获取的密钥发送给我\n\n"
            f"💡 密钥格式：12位字母数字组合\n"
            f"💡 例如：`aBcD1234EfGh`\n\n"
            f"⚠️ 请确保密钥正确，每个密钥只能领取一次",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("◀️ 返回活动中心", callback_data="activity_center")]
            ])
        )
        return ConversationHandler.END
    
    # ==================== 管理员后台相关 ====================
    
    # 返回管理员后台
    if data == "back_to_admin":
        if not is_admin(user_id):
            await query.edit_message_text("⛔ 您没有管理员权限")
            return ConversationHandler.END
        
        await query.edit_message_text(
            "🔐 **管理员后台**\n\n"
            "请选择功能：",
            reply_markup=get_admin_keyboard(),
            parse_mode="Markdown"
        )
        return ConversationHandler.END
    
    # 获取图片 File ID
    elif data == "get_file_id":
        if not is_admin(user_id):
            await query.edit_message_text("⛔ 您没有管理员权限")
            return ConversationHandler.END
        
        await query.edit_message_text(
            "🖼 **获取图片 File ID**\n\n"
            "请发送一张图片，我将返回它的 File ID",
            parse_mode="Markdown",
            reply_markup=get_back_keyboard()
        )
        return WAITING_FOR_PHOTO
    
    # 查看已保存的图片
    elif data == "view_saved":
        if not is_admin(user_id):
            await query.edit_message_text("⛔ 您没有管理员权限")
            return ConversationHandler.END
        
        records = get_all_file_ids()
        
        if not records:
            await query.edit_message_text(
                "📂 **已保存的图片**\n\n"
                "暂无保存的图片记录",
                parse_mode="Markdown",
                reply_markup=get_back_keyboard()
            )
            return ConversationHandler.END
        
        keyboard = []
        for record in records[:10]:  # 最多显示10条
            btn_text = f"🖼 #{record['id']} - {record['created_at'].strftime('%m/%d %H:%M')}"
            keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"view_{record['id']}")])
        
        keyboard.append([InlineKeyboardButton("◀️ 返回后台", callback_data="back_to_admin")])
        
        await query.edit_message_text(
            "📂 **已保存的图片**\n\n"
            "点击查看详情，可进行删除操作：",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return ConversationHandler.END
    
    # 查看单条记录
    elif data.startswith("view_"):
        if not is_admin(user_id):
            await query.edit_message_text("⛔ 您没有管理员权限")
            return ConversationHandler.END
        
        record_id = int(data.split("_")[1])
        record = get_file_by_id(record_id)
        
        if not record:
            await query.edit_message_text(
                "❌ 记录不存在",
                reply_markup=get_back_keyboard()
            )
            return ConversationHandler.END
        
        keyboard = [
            [InlineKeyboardButton("🗑 删除此记录", callback_data=f"confirm_delete_{record_id}")],
            [InlineKeyboardButton("◀️ 返回列表", callback_data="view_saved")],
            [InlineKeyboardButton("🏠 返回后台", callback_data="back_to_admin")]
        ]
        
        # 发送图片预览
        try:
            await query.message.reply_photo(
                photo=record['file_id'],
                caption=f"📋 **记录 #{record['id']}**\n\n"
                        f"🆔 File ID:\n`{record['file_id']}`\n\n"
                        f"📅 保存时间: {record['created_at'].strftime('%Y-%m-%d %H:%M:%S')}",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            await query.message.delete()
        except Exception as e:
            await query.edit_message_text(
                f"📋 **记录 #{record['id']}**\n\n"
                f"🆔 File ID:\n`{record['file_id']}`\n\n"
                f"📅 保存时间: {record['created_at'].strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                f"⚠️ 图片预览失败",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        
        return ConversationHandler.END
    
    # 确认删除
    elif data.startswith("confirm_delete_"):
        if not is_admin(user_id):
            await query.edit_message_text("⛔ 您没有管理员权限")
            return ConversationHandler.END
        
        record_id = int
