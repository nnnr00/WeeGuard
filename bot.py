import os
import logging
import asyncio
import threading
from datetime import datetime, timedelta
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import (
    Application, 
    CommandHandler, 
    MessageHandler, 
    CallbackQueryHandler, 
    filters, 
    ContextTypes
)

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz

from database import Database

# 日志配置
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', 
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# 环境变量
BOT_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_ID = int(os.getenv('ADMIN_ID', '0'))
DATABASE_URL = os.getenv('DATABASE_URL')
WEBAPP_URL = os.getenv('WEBAPP_URL', 'https://你的用户名.github.io/你的仓库名')

# 北京时区
BEIJING_TZ = pytz.timezone('Asia/Shanghai')

# 初始化数据库
db = Database(DATABASE_URL)

# Telegram Bot 应用实例
bot_app = None

# APScheduler 调度器
scheduler = AsyncIOScheduler(timezone=BEIJING_TZ)


# ==================== FastAPI 部分 ====================

class AdCallbackRequest(BaseModel):
    token: str


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI 生命周期管理"""
    # 启动时
    db.init_tables()
    logger.info("FastAPI started")
    yield
    # 关闭时
    logger.info("FastAPI shutting down")


# 创建 FastAPI 应用
api = FastAPI(title="Telegram Bot API", lifespan=lifespan)

# CORS 配置
api.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@api.get("/")
async def root():
    """健康检查"""
    return {"status": "ok", "message": "Telegram Bot API is running"}


@api.get("/api/ad/token/{user_id}")
async def get_ad_token(user_id: int):
    """获取广告观看令牌"""
    try:
        # 检查用户是否还能观看广告
        if not db.can_watch_ad(user_id):
            return JSONResponse(
                status_code=400,
                content={"success": False, "message": "今日观看次数已用完"}
            )
        
        # 生成令牌
        token = db.generate_ad_token(user_id)
        
        if token:
            return {"success": True, "token": token}
        else:
            return JSONResponse(
                status_code=500,
                content={"success": False, "message": "生成令牌失败"}
            )
    except Exception as e:
        logger.error(f"Error generating token: {e}")
        return JSONResponse(
            status_code=500,
            content={"success": False, "message": str(e)}
        )


@api.post("/api/ad/callback")
async def ad_callback(request: AdCallbackRequest):
    """广告观看完成回调"""
    try:
        token = request.token
        
        # 验证令牌
        is_valid, user_id = db.verify_and_use_token(token)
        
        if not is_valid:
            return JSONResponse(
                status_code=400,
                content={"success": False, "message": "无效或已过期的令牌"}
            )
        
        # 记录观看并发放积分
        success, points, count = db.record_ad_watch(user_id)
        
        if success:
            # 清理过期令牌
            db.cleanup_expired_tokens()
            
            return {
                "success": True, 
                "points": points, 
                "watch_count": count,
                "message": f"恭喜获得 {points} 积分！"
            }
        else:
            return JSONResponse(
                status_code=400,
                content={"success": False, "message": "今日观看次数已用完"}
            )
    except Exception as e:
        logger.error(f"Error in ad callback: {e}")
        return JSONResponse(
            status_code=500,
            content={"success": False, "message": str(e)}
        )


@api.get("/api/user/{user_id}/points")
async def get_user_points(user_id: int):
    """获取用户积分"""
    try:
        points = db.get_user_points(user_id)
        watch_count = db.get_ad_watch_count_today(user_id)
        return {
            "success": True,
            "points": points,
            "ad_watch_count": watch_count,
            "ad_watch_remaining": 3 - watch_count
        }
    except Exception as e:
        logger.error(f"Error getting user points: {e}")
        return JSONResponse(
            status_code=500,
            content={"success": False, "message": str(e)}
        )


@api.get("/api/secret/link/{link_num}")
async def get_secret_link(link_num: int):
    """获取密钥链接"""
    try:
        secrets_data = db.get_daily_secrets()
        
        if not secrets_data:
            return JSONResponse(
                status_code=404,
                content={"success": False, "message": "密钥未生成"}
            )
        
        if link_num == 1:
            link = secrets_data.get('link1')
            updated = secrets_data.get('link1_updated', False)
        else:
            link = secrets_data.get('link2')
            updated = secrets_data.get('link2_updated', False)
        
        if not updated or not link:
            return JSONResponse(
                status_code=404,
                content={"success": False, "message": "链接未设置"}
            )
        
        return {"success": True, "link": link}
    except Exception as e:
        logger.error(f"Error getting secret link: {e}")
        return JSONResponse(
            status_code=500,
            content={"success": False, "message": str(e)}
        )


# ==================== Telegram Bot 部分 ====================

def is_admin(user_id: int) -> bool:
    """检查是否为管理员"""
    return user_id == ADMIN_ID


def get_start_keyboard():
    """获取首页键盘"""
    keyboard = [
        [InlineKeyboardButton("✅ 开始验证", callback_data="start_verify")],
        [InlineKeyboardButton("💰 积分中心", callback_data="points_center")],
        [InlineKeyboardButton("🎉 开业活动", callback_data="activity_center")],
    ]
    return InlineKeyboardMarkup(keyboard)


def get_admin_keyboard():
    """获取管理员后台键盘"""
    keyboard = [
        [InlineKeyboardButton("📷 获取图片 File ID", callback_data="get_file_id")],
        [InlineKeyboardButton("🗂 查看已保存的图片", callback_data="view_images")],
    ]
    return InlineKeyboardMarkup(keyboard)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /start 命令"""
    user = update.effective_user
    
    # 确保用户存在于数据库
    db.get_or_create_user(user.id, user.username)
    
    welcome_text = (
        f"👋 欢迎使用机器人，{user.first_name}！\n\n"
        "请选择以下功能："
    )
    await update.message.reply_text(
        welcome_text,
        reply_markup=get_start_keyboard()
    )


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /admin 命令"""
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text("⛔ 您没有管理员权限")
        return
    
    # 清除等待状态
    context.user_data['waiting_for_image'] = False
    context.user_data['waiting_for_link1'] = False
    context.user_data['waiting_for_link2'] = False
    
    await update.message.reply_text(
        "🔧 <b>管理员后台</b>\n\n请选择功能：",
        reply_markup=get_admin_keyboard(),
        parse_mode='HTML'
    )


async def id_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /id 命令 - 快捷获取File ID"""
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text("⛔ 您没有管理员权限")
        return
    
    context.user_data['waiting_for_image'] = True
    
    keyboard = [[InlineKeyboardButton("❌ 取消", callback_data="back_to_admin")]]
    
    await update.message.reply_text(
        "📷 <b>获取图片 File ID</b>\n\n"
        "请发送一张图片，我将获取它的 File ID 并保存",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='HTML'
    )


async def jf_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /jf 命令 - 积分中心"""
    user = update.effective_user
    
    # 确保用户存在
    db.get_or_create_user(user.id, user.username)
    
    points = db.get_user_points(user.id)
    signed_today = db.check_signed_today(user.id)
    
    sign_btn_text = "✅ 今日已签到" if signed_today else "📅 每日签到"
    
    keyboard = [
        [InlineKeyboardButton(sign_btn_text, callback_data="do_sign_in")],
        [InlineKeyboardButton("🔙 返回首页", callback_data="back_to_start")],
    ]
    
    await update.message.reply_text(
        f"💰 <b>积分中心</b>\n\n"
        f"👤 用户：{user.first_name}\n"
        f"💎 当前积分：<b>{points}</b>\n\n"
        f"────────────\n"
        f"📌 签到规则：\n"
        f"• 首次签到：10 积分\n"
        f"• 每日签到：3-8 积分（随机）",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='HTML'
    )


async def hd_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /hd 命令 - 活动中心"""
    user = update.effective_user
    
    # 确保用户存在
    db.get_or_create_user(user.id, user.username)
    
    watch_count = db.get_ad_watch_count_today(user.id)
    click_count = db.get_user_redirect_clicks_today(user.id)
    
    keyboard = [
        [InlineKeyboardButton(f"🎬 看视频赚积分 ({watch_count}/3)", callback_data="watch_ad_info")],
        [InlineKeyboardButton(f"📦 网盘密钥福利 ({click_count}/2)", callback_data="secret_key_info")],
        [InlineKeyboardButton("🔙 返回首页", callback_data="back_to_start")],
    ]
    
    await update.message.reply_text(
        "🎉 <b>开业活动中心</b>\n\n"
        "欢迎参与我们的开业活动！\n"
        "完成任务即可获得丰厚积分奖励！",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='HTML'
    )


async def my_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /my 命令 - 管理员查看/更换密钥"""
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text("⛔ 您没有管理员权限")
        return
    
    # 检查是否在北京时间10点后
    if not db.is_after_10am_beijing():
        now = db.get_beijing_now()
        await update.message.reply_text(
            f"⏰ <b>时间未到</b>\n\n"
            f"当前北京时间：{now.strftime('%H:%M:%S')}\n\n"
            f"请在 <b>10:00</b> 之后再操作密钥链接。",
            parse_mode='HTML'
        )
        return
    
    # 确保当天密钥存在
    secrets_data = db.get_daily_secrets()
    if not secrets_data:
        secrets_data = db.create_daily_secrets()
    
    link1_status = "✅ 已设置" if secrets_data.get('link1_updated') else "❌ 未设置"
    link2_status = "✅ 已设置" if secrets_data.get('link2_updated') else "❌ 未设置"
    
    keyboard = [
        [InlineKeyboardButton("🔗 设置密钥1链接", callback_data="set_link1")],
        [InlineKeyboardButton("🔗 设置密钥2链接", callback_data="set_link2")],
        [InlineKeyboardButton("🔙 返回后台", callback_data="back_to_admin")],
    ]
    
    await update.message.reply_text(
        f"🔑 <b>今日密钥管理</b>\n\n"
        f"📅 密钥日期：{secrets_data['secret_date']}\n\n"
        f"────────────\n"
        f"🔐 <b>密钥1</b>（8积分）：\n"
        f"<code>{secrets_data['secret1']}</code>\n"
        f"链接状态：{link1_status}\n\n"
        f"🔐 <b>密钥2</b>（6积分）：\n"
        f"<code>{secrets_data['secret2']}</code>\n"
        f"链接状态：{link2_status}\n"
        f"────────────\n\n"
        f"⏰ 密钥每日北京时间 10:00 自动更新",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='HTML'
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理按钮回调"""
    query = update.callback_query
    await query.answer()
    
    user = query.from_user
    user_id = user.id
    data = query.data
    
    # ==================== 首页相关 ====================
    
    # 返回首页
    if data == "back_to_start":
        # 清除等待状态
        context.user_data['waiting_for_image'] = False
        context.user_data['waiting_for_link1'] = False
        context.user_data['waiting_for_link2'] = False
        context.user_data['waiting_for_secret'] = False
        
        db.get_or_create_user(user_id, user.username)
        
        await query.edit_message_text(
            f"👋 欢迎使用机器人，{user.first_name}！\n\n"
            "请选择以下功能：",
            reply_markup=get_start_keyboard()
        )
    
    # 开始验证
    elif data == "start_verify":
        keyboard = [[InlineKeyboardButton("🔙 返回首页", callback_data="back_to_start")]]
        await query.edit_message_text(
            "✅ <b>验证功能</b>\n\n"
            "此功能开发中...",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='HTML'
        )
    
    # ==================== 积分中心 ====================
    
    # 积分中心
    elif data == "points_center":
        db.get_or_create_user(user_id, user.username)
        
        points = db.get_user_points(user_id)
        signed_today = db.check_signed_today(user_id)
        
        sign_btn_text = "✅ 今日已签到" if signed_today else "📅 每日签到"
        
        keyboard = [
            [InlineKeyboardButton(sign_btn_text, callback_data="do_sign_in")],
            [InlineKeyboardButton("🔙 返回首页", callback_data="back_to_start")],
        ]
        
        await query.edit_message_text(
            f"💰 <b>积分中心</b>\n\n"
            f"👤 用户：{user.first_name}\n"
            f"💎 当前积分：<b>{points}</b>\n\n"
            f"────────────\n"
            f"📌 签到规则：\n"
            f"• 首次签到：10 积分\n"
            f"• 每日签到：3-8 积分（随机）",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='HTML'
        )
    
    # 签到
    elif data == "do_sign_in":
        db.get_or_create_user(user_id, user.username)
        
        if db.check_signed_today(user_id):
            await query.answer("今日已签到！明天再来吧~", show_alert=True)
            return
        
        success, points_earned, is_first = db.do_sign_in(user_id)
        
        if success:
            total_points = db.get_user_points(user_id)
            
            if is_first:
                msg = f"🎉 首次签到成功！\n\n获得 <b>{points_earned}</b> 积分"
            else:
                msg = f"✅ 签到成功！\n\n获得 <b>{points_earned}</b> 积分"
            
            keyboard = [
                [InlineKeyboardButton("✅ 今日已签到", callback_data="do_sign_in")],
                [InlineKeyboardButton("🔙 返回首页", callback_data="back_to_start")],
            ]
            
            await query.edit_message_text(
                f"💰 <b>积分中心</b>\n\n"
                f"👤 用户：{user.first_name}\n"
                f"💎 当前积分：<b>{total_points}</b>\n\n"
                f"────────────\n"
                f"{msg}\n\n"
                f"────────────\n"
                f"📌 签到规则：\n"
                f"• 首次签到：10 积分\n"
                f"• 每日签到：3-8 积分（随机）",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='HTML'
            )
        else:
            await query.answer("签到失败，请稍后重试", show_alert=True)
    
    # ==================== 活动中心 ====================
    
    # 活动中心
    elif data == "activity_center":
        db.get_or_create_user(user_id, user.username)
        
        watch_count = db.get_ad_watch_count_today(user_id)
        click_count = db.get_user_redirect_clicks_today(user_id)
        
        keyboard = [
            [InlineKeyboardButton(f"🎬 看视频赚积分 ({watch_count}/3)", callback_data="watch_ad_info")],
            [InlineKeyboardButton(f"📦 网盘密钥福利 ({click_count}/2)", callback_data="secret_key_info")],
            [InlineKeyboardButton("🔙 返回首页", callback_data="back_to_start")],
        ]
        
        await query.edit_message_text(
            "🎉 <b>开业活动中心</b>\n\n"
            "欢迎参与我们的开业活动！\n"
            "完成任务即可获得丰厚积分奖励！",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='HTML'
        )
    
    # 看视频赚积分信息页
    elif data == "watch_ad_info":
        db.get_or_create_user(user_id, user.username)
        
        watch_count = db.get_ad_watch_count_today(user_id)
        remaining = 3 - watch_count
        
        if remaining <= 0:
            keyboard = [
                [InlineKeyboardButton("🔙 返回活动中心", callback_data="activity_center")],
            ]
            
            await query.edit_message_text(
                "🎬 <b>看视频赚积分</b>\n\n"
                "❌ 今日观看次数已用完\n\n"
                "明天再来吧！（北京时间0点重置）",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='HTML'
            )
        else:
            # 生成观看链接
            watch_url = f"{WEBAPP_URL}/docs/watch.html?user_id={user_id}"
            
            # 计算下次奖励
            if watch_count == 0:
                next_reward = "10 积分"
            elif watch_count == 1:
                next_reward = "6 积分"
            else:
                next_reward = "3-10 积分（随机）"
            
            keyboard = [
                [InlineKeyboardButton("▶️ 开始观看", url=watch_url)],
                [InlineKeyboardButton("🔄 刷新状态", callback_data="watch_ad_info")],
                [InlineKeyboardButton("🔙 返回活动中心", callback_data="activity_center")],
            ]
            
            await query.edit_message_text(
                f"🎬 <b>看视频赚积分</b>\n\n"
                f"📺 观看视频广告即可获得积分奖励！\n\n"
                f"────────────\n"
                f"📊 今日进度：{watch_count}/3\n"
                f"🎁 下次奖励：{next_reward}\n"
                f"────────────\n\n"
                f"📌 奖励规则：\n"
                f"• 第1次观看：10 积分\n"
                f"• 第2次观看：6 积分\n"
                f"• 第3次观看：3-10 积分（随机）\n\n"
                f"⏰ 每日北京时间 0:00 重置",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='HTML'
            )
    
    # ==================== 网盘密钥福利 ====================
    
    # 密钥信息页
    elif data == "secret_key_info":
        db.get_or_create_user(user_id, user.username)
        
        click_count = db.get_user_redirect_clicks_today(user_id)
        claimed_secrets = db.get_user_claimed_secrets_today(user_id)
        
        # 检查链接是否已设置
        links_ready = db.are_links_ready()
        
        if click_count >= 2:
            keyboard = [
                [InlineKeyboardButton("🔙 返回活动中心", callback_data="activity_center")],
            ]
            
            await query.edit_message_text(
                "📦 <b>网盘密钥福利</b>\n\n"
                "❌ 今日获取次数已用完\n\n"
                "⏰ 明日 <b>上午 10:00</b> 重置后可继续获取",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='HTML'
            )
        elif not links_ready:
            keyboard = [
                [InlineKeyboardButton("🔄 刷新状态", callback_data="secret_key_info")],
                [InlineKeyboardButton("🔙 返回活动中心", callback_data="activity_center")],
            ]
            
            await query.edit_message_text(
                "📦 <b>网盘密钥福利</b>\n\n"
                "⏳ 请等待管理员更换新密钥链接\n\n"
                "管理员每日 10:00 更新链接，请稍后再试",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='HTML'
            )
        else:
            # 计算下次奖励
            if click_count == 0:
                next_reward = "8 积分"
            else:
                next_reward = "6 积分"
            
            keyboard = [
                [InlineKeyboardButton("🔑 开始获取密钥", callback_data="start_get_secret")],
                [InlineKeyboardButton("🔙 返回活动中心", callback_data="activity_center")],
            ]
            
            await query.edit_message_text(
                f"📦 <b>网盘密钥福利</b>\n\n"
                f"通过夸克网盘获取隐藏密钥，输入即可领取积分！\n\n"
                f"────────────\n"
                f"📊 今日进度：{click_count}/2\n"
                f"🎁 下次奖励：{next_reward}\n"
                f"────────────\n\n"
                f"📌 <b>获取步骤：</b>\n"
                f"1️⃣ 点击「开始获取密钥」按钮\n"
                f"2️⃣ 等待 3 秒自动跳转到网盘页面\n"
                f"3️⃣ 保存文件到网盘，查看文件名\n"
                f"4️⃣ 复制文件名中的密钥\n"
                f"5️⃣ 返回机器人发送密钥领取积分\n\n"
                f"────────────\n"
                f"📌 <b>奖励规则：</b>\n"
                f"• 第1次密钥：8 积分\n"
                f"• 第2次密钥：6 积分\n\n"
                f"⏰ 每日北京时间 <b>10:00</b> 重置",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='HTML'
            )
    
    # 开始获取密钥
    elif data == "start_get_secret":
        db.get_or_create_user(user_id, user.username)
        
        click_count = db.get_user_redirect_clicks_today(user_id)
        
        if click_count >= 2:
            await query.answer("今日获取次数已用完", show_alert=True)
            return
        
        # 检查链接是否已设置
        if not db.are_links_ready():
            await query.answer("请等待管理员更换新密钥链接", show_alert=True)
            return
        
        # 记录点击
        new_count = db.record_redirect_click(user_id)
        
        # 根据是第几次点击决定使用哪个中转页面
        if new_count == 1:
            redirect_url = f"{WEBAPP_URL}/docs/redirect1.html"
        else:
            redirect_url = f"{WEBAPP_URL}/docs/redirect2.html"
        
        # 设置等待密钥输入状态
        context.user_data['waiting_for_secret'] = True
        
        keyboard = [
            [InlineKeyboardButton("🔗 前往获取密钥", url=redirect_url)],
            [InlineKeyboardButton("📝 我已获取，输入密钥", callback_data="input_secret")],
            [InlineKeyboardButton("🔙 返回活动中心", callback_data="activity_center")],
        ]
        
        await query.edit_message_text(
            f"📦 <b>获取密钥 - 第 {new_count} 次</b>\n\n"
            f"请点击下方按钮前往获取密钥\n\n"
            f"⚠️ <b>注意事项：</b>\n"
            f"• 页面将先跳转广告（约3秒）\n"
            f"• 然后自动跳转到网盘页面\n"
            f"• 保存文件后查看文件名即为密钥\n\n"
            f"获取密钥后，直接在聊天框发送密钥即可领取积分！",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='HTML'
        )
    
    # 输入密钥
    elif data == "input_secret":
        context.user_data['waiting_for_secret'] = True
        
        keyboard = [
            [InlineKeyboardButton("🔙 返回活动中心", callback_data="activity_center")],
        ]
        
        await query.edit_message_text(
            "📝 <b>输入密钥</b>\n\n"
            "请在聊天框中直接发送您获取的密钥\n\n"
            "密钥格式：12位字母+数字组合",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='HTML'
        )
    
    # ==================== 管理员后台 ====================
    
    # 返回后台
    elif data == "back_to_admin":
        if not is_admin(user_id):
            await query.edit_message_text("⛔ 您没有管理员权限")
            return
        
        context.user_data['waiting_for_image'] = False
        context.user_data['waiting_for_link1'] = False
        context.user_data['waiting_for_link2'] = False
        
        await query.edit_message_text(
            "🔧 <b>管理员后台</b>\n\n请选择功能：",
            reply_markup=get_admin_keyboard(),
            parse_mode='HTML'
        )
    
    # 获取File ID
    elif data == "get_file_id":
        if not is_admin(user_id):
            await query.edit_message_text("⛔ 您没有管理员权限")
            return
        
        context.user_data['waiting_for_image'] = True
        keyboard = [[InlineKeyboardButton("❌ 取消", callback_data="back_to_admin")]]
        
        await query.edit_message_text(
            "📷 <b>获取图片 File ID</b>\n\n"
            "请发送一张图片，我将获取它的 File ID 并保存",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='HTML'
        )
    
    # 查看已保存图片列表
    elif data == "view_images":
        if not is_admin(user_id):
            await query.edit_message_text("⛔ 您没有管理员权限")
            return
        
        images = db.get_all_images()
        
        if not images:
            keyboard = [[InlineKeyboardButton("🔙 返回后台", callback_data="back_to_admin")]]
            await query.edit_message_text(
                "📭 <b>暂无保存的图片</b>\n\n"
                "使用「获取图片 File ID」功能添加图片",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='HTML'
            )
        else:
            keyboard = []
            for img in images:
                short_id = img['file_id'][:25] + "..."
                btn_text = f"🖼 #{img['id']} | {short_id}"
                keyboard.append([
                    InlineKeyboardButton(btn_text, callback_data=f"detail_{img['id']}")
                ])
            
            keyboard.append([InlineKeyboardButton("🔙 返回后台", callback_data="back_to_admin")])
            
            await query.edit_message_text(
                f"🗂 <b>已保存的图片</b>（共 {len(images)} 张）\n\n"
                "点击查看详情：",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='HTML'
            )
    
    # 查看图片详情
    elif data.startswith("detail_"):
        if not is_admin(user_id):
            await query.edit_message_text("⛔ 您没有管理员权限")
            return
        
        image_id = int(data.replace("detail_", ""))
        image = db.get_image_by_id(image_id)
        
        if image:
            keyboard = [
                [InlineKeyboardButton("🗑 删除此图片", callback_data=f"confirm_del_{image_id}")],
                [InlineKeyboardButton("🔙 返回列表", callback_data="view_images")],
            ]
            
            await query.edit_message_text(
                f"🖼 <b>图片详情</b>\n\n"
                f"📌 ID: <code>{image['id']}</code>\n\n"
                f"📎 File ID:\n<code>{image['file_id']}</code>\n\n"
                f"🕐 保存时间: {image['created_at'].strftime('%Y-%m-%d %H:%M:%S')}",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='HTML'
            )
        else:
            await query.edit_message_text(
                "❌ 图片不存在",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 返回列表", callback_data="view_images")
                ]]),
                parse_mode='HTML'
            )
    
    # 确认删除
    elif data.startswith("confirm_del_"):
        if not is_admin(user_id):
            await query.edit_message_text("⛔ 您没有管理员权限")
            return
        
        image_id = int(data.replace("confirm_del_", ""))
        
        keyboard = [
            [
                InlineKeyboardButton("✅ 确认删除", callback_data=f"delete_{image_id}"),
                InlineKeyboardButton("❌ 取消", callback_data=f"detail_{image_id}")
            ],
        ]
        
        await query.edit_message_text(
            f"⚠️ <b>确认删除</b>\n\n"
            f"确定要删除图片 <b>#{image_id}</b> 吗？\n\n"
            f"此操作不可撤销！",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='HTML'
        )
    
    # 执行删除
    elif data.startswith("delete_"):
        if not is_admin(user_id):
            await query.edit_message_text("⛔ 您没有管理员权限")
            return
        
        image_id = int(data.replace("delete_", ""))
        
        success = db.delete_image(image_id)
        
        if success:
            await query.edit_message_text(
                f"✅ <b>删除成功</b>\n\n"
                f"图片 #{image_id} 已删除",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 返回后台", callback_data="back_to_admin")
                ]]),
                parse_mode='HTML'
            )
        else:
            await query.edit_message_text(
                "❌ 删除失败",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 返回后台", callback_data="back_to_admin")
                ]]),
                parse_mode='HTML'
            )
    
    # ==================== 设置密钥链接（管理员）====================
    
    # 设置密钥1链接
    elif data == "set_link1":
        if not is_admin(user_id):
            await query.edit_message_text("⛔ 您没有管理员权限")
            return
        
        context.user_data['waiting_for_link1'] = True
        context.user_data['waiting_for_link2'] = False
        
        keyboard = [[InlineKeyboardButton("❌ 取消", callback_data="cancel_set_link")]]
        
        await query.edit_message_text(
            "🔗 <b>设置密钥1链接</b>\n\n"
            "请发送密钥1的夸克网盘链接：",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='HTML'
        )
    
    # 设置密钥2链接
    elif data == "set_link2":
        if not is_admin(user_id):
            await query.edit_message_text("⛔ 您没有管理员权限")
            return
        
        context.user_data['waiting_for_link1'] = False
        context.user_data['waiting_for_link2'] = True
        
        keyboard = [[InlineKeyboardButton("❌ 取消", callback_data="cancel_set_link")]]
        
        await query.edit_message_text(
            "🔗 <b>设置密钥2链接</b>\n\n"
            "请发送密钥2的夸克网盘链接：",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='HTML'
        )
    
    # 取消设置链接
    elif data == "cancel_set_link":
        if not is_admin(user_id):
            return
        
        context.user_data['waiting_for_link1'] = False
        context.user_data['waiting_for_link2'] = False
        
        # 返回密钥管理页面
        secrets_data = db.get_daily_secrets()
        if not secrets_data:
            secrets_data = db.create_daily_secrets()
        
        link1_status = "✅ 已设置" if secrets_data.get('link1_updated') else "❌ 未设置"
        link2_status = "✅ 已设置" if secrets_data.get('link2_updated') else "❌ 未设置"
        
        keyboard = [
            [InlineKeyboardButton("🔗 设置密钥1链接", callback_data="set_link1")],
            [InlineKeyboardButton("🔗 设置密钥2链接", callback_data="set_link2")],
            [InlineKeyboardButton("🔙 返回后台", callback_data="back_to_admin")],
        ]
        
        await query.edit_message_text(
            f"🔑 <b>今日密钥管理</b>\n\n"
            f"📅 密钥日期：{secrets_data['secret_date']}\n\n"
            f"────────────\n"
            f"🔐 <b>密钥1</b>（8积分）：\n"
            f"<code>{secrets_data['secret1']}</code>\n"
            f"链接状态：{link1_status}\n\n"
            f"🔐 <b>密钥2</b>（6积分）：\n"
            f"<code>{secrets_data['secret2']}</code>\n"
            f"链接状态：{link2_status}\n"
            f"────────────\n\n"
            f"⏰ 密钥每日北京时间 10:00 自动更新",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='HTML'
        )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理文本消息"""
    user = update.effective_user
    user_id = user.id
    text = update.message.text.strip()
    
    # ==================== 管理员：设置密钥链接 ====================
    
    if is_admin(user_id):
        # 设置密钥1链接
        if context.user_data.get('waiting_for_link1'):
            context.user_data['waiting_for_link1'] = False
            
            success = db.update_secret_link(1, text)
            
            if success:
                await update.message.reply_text(
                    "✅ <b>密钥1链接设置成功！</b>\n\n"
                    "请继续设置密钥2链接，或使用 /my 查看详情",
                    parse_mode='HTML'
                )
            else:
                await update.message.reply_text("❌ 设置失败，请重试")
            return
        
        # 设置密钥2链接
        if context.user_data.get('waiting_for_link2'):
            context.user_data['waiting_for_link2'] = False
            
            success = db.update_secret_link(2, text)
            
            if success:
                await update.message.reply_text(
                    "✅ <b>密钥2链接设置成功！</b>\n\n"
                    "所有链接已设置完毕，用户现在可以获取密钥了！\n\n"
                    "使用 /my 查看详情",
                    parse_mode='HTML'
                )
            else:
                await update.message.reply_text("❌ 设置失败，请重试")
            return
    
    # ==================== 用户：输入密钥 ====================
    
    if context.user_data.get('waiting_for_secret') or len(text) == 12:
        # 确保用户存在
        db.get_or_create_user(user_id, user.username)
        
        # 验证密钥
        is_valid, secret_type, points = db.verify_secret(text)
        
        if is_valid:
            # 尝试领取
            success, message = db.claim_secret(user_id, secret_type, points)
            
            context.user_data['waiting_for_secret'] = False
            
            if success:
                total_points = db.get_user_points(user_id)
                
                keyboard = [[InlineKeyboardButton("🔙 返回活动中心", callback_data="activity_center")]]
                
                await update.message.reply_text(
                    f"🎉 <b>领取成功！</b>\n\n"
                    f"✅ 密钥验证通过\n"
                    f"💎 获得积分：<b>+{points}</b>\n"
                    f"💰 当前总积分：<b>{total_points}</b>\n\n"
                    f"感谢参与活动！",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode='HTML'
                )
            else:
                keyboard = [[InlineKeyboardButton("🔙 返回活动中心", callback_data="activity_center")]]
                
                await update.message.reply_text(
                    f"⚠️ <b>领取失败</b>\n\n"
                    f"{message}",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode='HTML'
                )
        else:
            # 密钥无效
            if context.user_data.get('waiting_for_secret'):
                keyboard = [[InlineKeyboardButton("🔙 返回活动中心", callback_data="activity_center")]]
                
                await update.message.reply_text(
                    "❌ <b>密钥无效</b>\n\n"
                    "请确认您输入的密钥是否正确，或该密钥已过期。\n\n"
                    "密钥每日北京时间 10:00 更新",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode='HTML'
                )


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理图片消息"""
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        return
    
    # 检查是否在等待图片
    if not context.user_data.get('waiting_for_image'):
        return
    
    # 获取最高质量的图片
    photo = update.message.photo[-1]
    file_id = photo.file_id
    
    # 保存到数据库
    saved_id = db.save_image(file_id)
    
    # 重置等待状态
    context.user_data['waiting_for_image'] = False
    
    keyboard = [[InlineKeyboardButton("🔙 返回后台", callback_data="back_to_admin")]]
    
    await update.message.reply_text(
        f"✅ <b>保存成功</b>\n\n"
        f"📌 ID: <code>{saved_id}</code>\n\n"
        f"📎 File ID:\n<code>{file_id}</code>",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='HTML'
    )


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """错误处理"""
    logger.error(f"Error: {context.error}")


# ==================== 定时任务 ====================

async def daily_secret_update():
    """每日密钥更新任务 - 北京时间10:00执行"""
    try:
        logger.info("Starting daily secret update...")
        
        # 生成新的每日密钥
        today = db.get_beijing_today()
        secrets_data = db.create_daily_secrets(today)
        
        if secrets_data and bot_app:
            # 发送通知给管理员
            message = (
                f"🔔 <b>每日密钥已更新</b>\n\n"
                f"📅 日期：{secrets_data['secret_date']}\n\n"
                f"────────────\n"
                f"🔐 <b>密钥1</b>（8积分）：\n"
                f"<code>{secrets_data['secret1']}</code>\n\n"
                f"🔐 <b>密钥2</b>（6积分）：\n"
                f"<code>{secrets_data['secret2']}</code>\n"
                f"────────────\n\n"
                f"⚠️ 请使用 /my 命令设置今日密钥链接"
            )
            
            await bot_app.bot.send_message(
                chat_id=ADMIN_ID,
                text=message,
                parse_mode='HTML'
            )
            
            logger.info(f"Daily secrets created and admin notified: {secrets_data['secret1']}, {secrets_data['secret2']}")
    except Exception as e:
        logger.error(f"Error in daily secret update: {e}")


def run_bot():
    """运行 Telegram Bot"""
    global bot_app
    
    # 初始化数据库表
    db.init_tables()
    
    # 创建应用
    bot_app = Application.builder().token(BOT_TOKEN).build()
    
    # 添加处理器
    bot_app.add_handler(CommandHandler("start", start))
    bot_app.add_handler(CommandHandler("admin", admin_command))
    bot_app.add_handler(CommandHandler("id", id_command))
    bot_app.add_handler(CommandHandler("jf", jf_command))
    bot_app.add_handler(CommandHandler("hd", hd_command))
    bot_app.add_handler(CommandHandler("my", my_command))
    bot_app.add_handler(CallbackQueryHandler(button_handler))
    bot_app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # 错误处理
    bot_app.add_error_handler(error_handler)
    
    # 配置定时任务
    scheduler.add_job(
        daily_secret_update,
        CronTrigger(hour=10, minute=0, timezone=BEIJING_TZ),
        id='daily_secret_update',
        replace_existing=True
    )
    
    # 启动调度器
    scheduler.start()
    logger.info("APScheduler started - Daily secret update scheduled at 10:00 Beijing time")
    
    # 启动机器人
    logger.info("Telegram Bot is starting...")
    bot_app.run_polling(allowed_updates=Update.ALL_TYPES)


def run_api():
    """运行 FastAPI"""
    port = int(os.getenv('PORT', 8080))
    uvicorn.run(api, host="0.0.0.0", port=port)


if __name__ == '__main__':
    # 在后台线程运行 FastAPI
    api_thread = threading.Thread(target=run_api, daemon=True)
    api_thread.start()
    
    # 在主线程运行 Telegram Bot
    run_bot()
