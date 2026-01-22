import os
import logging
import asyncio
import threading
from datetime import datetime
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
WEBAPP_URL = os.getenv('WEBAPP_URL', 'https://你的用户名.github.io/你的仓库名')  # GitHub Pages URL

# 初始化数据库
db = Database(DATABASE_URL)

# Telegram Bot 应用实例
bot_app = None


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
    
    keyboard = [
        [InlineKeyboardButton(f"🎬 看视频赚积分 ({watch_count}/3)", callback_data="watch_ad_info")],
        [InlineKeyboardButton("🔙 返回首页", callback_data="back_to_start")],
    ]
    
    await update.message.reply_text(
        "🎉 <b>开业活动中心</b>\n\n"
        "欢迎参与我们的开业活动！\n"
        "完成任务即可获得丰厚积分奖励！",
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
        # 确保用户存在于数据库
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
        
        keyboard = [
            [InlineKeyboardButton(f"🎬 看视频赚积分 ({watch_count}/3)", callback_data="watch_ad_info")],
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
    
    # ==================== 管理员后台 ====================
    
    # 返回后台
    elif data == "back_to_admin":
        if not is_admin(user_id):
            await query.edit_message_text("⛔ 您没有管理员权限")
            return
        
        context.user_data['waiting_for_image'] = False
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
    bot_app.add_handler(CallbackQueryHandler(button_handler))
    bot_app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    
    # 错误处理
    bot_app.add_error_handler(error_handler)
    
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
