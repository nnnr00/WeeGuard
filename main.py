# -*- coding: utf-8 -*-
import logging
import json
import os
from telegram import Update, ForceReply
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# 开启日志
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# 用来保存自定义命令的简单 json 文件
DB_FILE = "custom_commands.json"

# 加载自定义命令
def load_custom_commands():
    if os.path.exists(DB_FILE):
        with open(DB_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}  # {"/hello": "你好呀！", "/pic": {"type": "photo", "file_id": "..."}}

# 保存自定义命令
def save_custom_commands(data):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

custom_commands = load_custom_commands()

# 欢迎语 + 进入订单号输入状态
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    welcome = (
        "✨ 欢迎使用订单查询机器人！\n\n"
        "请直接回复您的订单号，我会帮您快速查询～\n"
    )
    await update.message.reply_html(
        welcome,
        reply_markup=ForceReply(selective=True)
    )
    # 把用户状态标记为“等待订单号”
    context.user_data["awaiting_order"] = True

# 处理所有文字消息
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    user_id = update.effective_user.id

    # 1. 优先检查是否命中自定义命令（以 / 开头）
    if text.startswith("/"):
        cmd = text.split()[0].lower()
        if cmd in custom_commands:
            reply = custom_commands[cmd]
            if isinstance(reply, str):
                await update.message.reply_text(reply, parse_mode="HTML")
            elif reply["type"] == "photo":
                await update.message.reply_photo(reply["file_id"], caption=reply.get("caption", ""))
            elif reply["type"] == "document":
                await update.message.reply_document(reply["file_id"], caption=reply.get("caption", ""))
            return

    # 2. 如果正在等待订单号，或者消息以 20260 开头，就走订单识别逻辑
    awaiting = context.user_data.get("awaiting_order", False)
    if awaiting or text.startswith("20260"):
        context.user_data["awaiting_order"] = False  # 清除状态

        if text.startswith("20260") and len(text) >= 10:  # 你可以自己调整长度要求
            await update.message.reply_text(
                f"✅ 订单号 <code>{text}</code> 已识别！\n\n"
                "正在为您查询，请稍等……\n"
                "（这里可以接数据库或 API 查询）",
                parse_mode="HTML"
            )
            # TODO: 在这里调用你的订单查询接口
            # query_result = query_order(text)
            # await update.message.reply_text(query_result)
        else:
            await update.message.reply_text(
                "❌ 订单号格式不正确，请检查后重新输入\n"
                reply_markup=ForceReply(selective=True)
            )
            context.user_data["awaiting_order"] = True
        return

    # 3. 其他普通消息（可选）
    await update.message.reply_text("请发送您的订单号～")

# ==================== 自定义命令管理（仅管理员可用） ====================
ADMIN_ID = 1480512549  # ←←← 把这里改成你自己的 Telegram ID !!!

async def addcmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("🚫 你无权使用此命令")
        return

    try:
        _, cmd, *reply_parts = context.args
        reply_text = " ".join(reply_parts)
        if not cmd.startswith("/"):
            cmd = "/" + cmd
        custom_commands[cmd.lower()] = reply_text
        save_custom_commands(custom_commands)
        await update.message.reply_text(f"✅ 已添加命令 {cmd} → {reply_text}")
    except:
        await update.message.reply_text(
            "用法：/addcmd /命令 回复内容\n"
            "示例：/addcmd /hello 欢迎光临！"
        )

# 支持添加图片/文件命令
async def addmedia(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    await update.message.reply_text(
        "请回复这条消息，发送你要设置的图片或文件，然后在下一条消息输入命令名\n"
        "例如回复这句后发图 → 再发文字：/banner"
    )
    context.user_data["waiting_media"] = True

async def handle_media_for_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID or not context.user_data.get("waiting_media"):
        return

    if update.message.photo:
        file_id = update.message.photo[-1].file_id
        media_type = "photo"
    elif update.message.document:
        file_id = update.message.document.file_id
        media_type = "document"
    else:
        await update.message.reply_text("请发送图片或文件")
        return

    context.user_data["pending_media"] = {"type": media_type, "file_id": file_id}
    context.user_data["waiting_media"] = False
    await update.message.reply_text("媒体已接收！现在请发送命令名（例如 /banner）")

async def handle_cmd_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID or "pending_media" not in context.user_data:
        return

    cmd = update.message.text.strip()
    if not cmd.startswith("/"):
        cmd = "/" + cmd

    media_info = context.user_data.pop("pending_media")
    media_info["caption"] = update.message.caption or ""
    custom_commands[cmd.lower()] = media_info
    save_custom_commands(custom_commands)
    await update.message.reply_text(f"✅ 已设置 {cmd} 为媒体回复")

async def delcmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    if not context.args:
        await update.message.reply_text("用法：/delcmd /命令")
        return
    cmd = context.args[0].lower()
    if cmd in custom_commands:
        del custom_commands[cmd]
        save_custom_commands(custom_commands)
        await update.message.reply_text(f"已删除 {cmd}")
    else:
        await update.message.reply_text("命令不存在")

async def listcmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    if not custom_commands:
        await update.message.reply_text("暂无自定义命令")
        return
    lines = []
    for cmd, val in custom_commands.items():
        if isinstance(val, str):
            preview = val[:30] + "..." if len(val) > 30 else val
            lines.append(f"{cmd} → {preview}")
        else:
            lines.append(f"{cmd} → [{val['type']}]")
    await update.message.reply_text("\n".join(lines))

# ==================== 主函数 ====================
def main():
    # 把下面这行换成你自己的 Bot Token
    TOKEN = "8515162052:AAFyZu2oKv9CjgtKaA0nQHc-PydLRaV5BZI"

    application = Application.builder().token(TOKEN).build()

    # 命令
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("addcmd", addcmd))
    application.add_handler(CommandHandler("addmedia", addmedia))
    application.add_handler(CommandHandler("delcmd", delcmd))
    application.add_handler(CommandHandler("listcmd", listcmd))

    # 媒体 → 命令名 流程
    application.add_handler(MessageHandler(filters.PHOTO | filters.Document, handle_media_for_cmd))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_cmd_name), group=1)

    # 普通消息处理（订单号识别 + 自定义命令）
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # 动态加载自定义命令（用户发 /xxx 时也会触发）
    for cmd in custom_commands.keys():
        if cmd.startswith("/"):
            application.add_handler(CommandHandler(cmd[1:], handle_message, filters=None))

    print("机器人已启动！")
    application.run_polling()

if __name__ == "__main__":
    main()
def main():
    from telegram import Bot
    from telegram.ext import Application
    import asyncio

    TOKEN = "8515162052:AAFyZu2oKv9CjgtKaA0nQHc-PydLRaV5BZI"   # ← 再次确认这里对了

    async def test():
        bot = Bot(TOKEN)
        me = await bot.get_me()
        print("机器人启动成功！用户名：", me.username)
        await bot.send_message(chat_id=你的ID, text="我活了！测试成功")  # ← 改成你的数字ID

    asyncio.run(test())

if __name__ == "__main__":
    main()
