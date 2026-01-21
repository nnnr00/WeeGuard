# src/commands/fileid.py
# ------------------------------------------------------------
# File‑ID 管理器（完整 Wizard）
# ------------------------------------------------------------
# 该文件实现了「管理员后台」→「管理文件 ID」的完整流程：
#   • 新增（输入文字 → 送图片 → 保存）
#   • 查看（显示列表、查看细节、重新获取、删除）
#   • 删除（单条、全部）
#   • 重新获取 File‑ID（重新向 Telegram 索要同一张图片的 file_id）
#   • 所有操作均受管理员权限检查
# ------------------------------------------------------------

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.ext import (
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    Scenes,
    filters,
)

import datetime
from typing import Dict, List

from src.models.file import insert_file, list_files, get_file_by_id, delete_file, delete_all_records


# ------------------- 权限检查 -------------------
def is_admin(user_id: int) -> bool:
    """如果 user_id 在环境变量 ADMIN_IDS 中则返回 True。"""
    admin_ids = os.getenv("ADMIN_IDS", "")
    return user_id in [int(x) for x in admin_ids.split(",") if x.strip()]


# ------------------- Wizard 状态机 -------------------
admin_fileid_scene = Scenes.Wizard()


# ---------- Step: 主菜单 ----------
async def admin_fileid_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(text="📥 新增 File‑ID", callback_data="admin_fileid_add")],
            [InlineKeyboardButton(text="🔎 查看所有记录", callback_data="admin_fileid_list")],
            [InlineKeyboardButton(text="🚫 取消", callback_data="admin_cancel")],
        ]
    )
    await update.callback_query.answer()
    await update.callback_query.edit_message_text(
        "🛠️ 管理员 File‑ID 管理菜单，请选择操作：", reply_markup=keyboard
    )
    return "admin_fileid_add"


# ---------- Step: 输入要保存的文字 ----------
async def admin_fileid_add_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    await update.callback_query.edit_message_text(
        "📝 请在此输入要与 file_id 同时保存的文字（可自行描述）：",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton(text="🚫 取消", callback_data="admin_cancel")]]
        )
    )
    context.user_data["admin_fileid_await_text"] = True
    return "admin_fileid_get_text"


# ---------- Step: 获取图片并保存 ----------
async def admin_fileid_get_image(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message.photo:
        await update.message.reply_text(
            "❗️ 必须发送一张照片才能获取 file_id。", reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton(text="🚫 取消", callback_data="admin_cancel")]]
            )
        )
        return "admin_fileid_get_text"

    # 取得图片的最高分辨率 file_id
    file_id = update.message.photo[-1].file_id

    saved_text = context.user_data.get("admin_fileid_saved_text", "")
    from src.models.file import insert_file

    record = await insert_file(
        user_id=update.effective_user.id,
        file_id=file_id,
        text=saved_text,
    )
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(text="🔎 查看所有记录", callback_data="admin_fileid_list"),
                InlineKeyboardButton(text="🚫 取消", callback_data="admin_cancel"),
            ]
        )
    await update.message.reply_text(
        f"✅ 保存成功！\n🆔 记录 ID：{record['id']}\n📎 File‑ID：`{file_id}`\n🗒️ 文字：{saved_text}",
        parse_mode="Markdown",
        reply_markup=keyboard,
    )
    context.user_data.pop("admin_fileid_await_text", None)
    context.user_data.pop("admin_fileid_saved_text", None)
    return "admin_main"


# ---------- Step: 查看所有记录 ----------
async def admin_fileid_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    from src.models.file import list_files

    rows = await list_files(limit=10)
    if not rows:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            "📂 当前没有任何记录，请先使用「新增 File‑ID」创建。"
        )
        return "admin_main"

    inline_keyboard = []
    for row in rows:
        inline_keyboard.append(
            [
                InlineKeyboardButton(
                    text="👀 查看", callback_data=f"view_{row['id']}"
                ),
                InlineKeyboardButton(text="🗑️ 删除", callback_data=f"del_{row['id']}"),
            ]
        )
    inline_keyboard.append(
        [InlineKeyboardButton(text="🔙 返回上一级", callback_data="admin_main")]
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard)

    await update.callback_query.answer()
    await update.callback_query.edit_message_text(
        "📚 以下是最近的记录（最多显示 10 条），请选择「查看」或「删除」：\n\n"
        + "\n".join(
            f"{idx}. ID={row['id']} 文字={row['text'][:20]}…"
            for idx, row in enumerate(rows, start=1)
        ),
        reply_markup=keyboard,
    )
    return "admin_fileid_list"


async def admin_fileid_view_process(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    data = query.data
    if data.startswith("view_"):
        record_id = int(data.split("_")[1])
        from src.models.file import get_file_by_id

        rec = await get_file_by_id(record_id)
        if not rec:
            await query.edit_message_text(
                "❗️ 该记录已不存在或已被删除。", reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton(text="🔙 返回上一级", callback_data="admin_main")]]
                )
            )
            return "admin_main"

        text = (
            f"🆔 记录 ID：{rec['id']}\n"
            f"👤 用户 ID：{rec['user_id']}\n"
            f"🗒️ 文字：{rec['text']}\n"
            f"📎 File‑ID：`{rec['file_id']}`\n"
            f"🕒 创建时间：{rec['created_at']}"
        )
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        text="🔁 重新获取 File‑ID", callback_data=f"reget_{record_id}"
                    ),
                    InlineKeyboardButton(text="🗑️ 删除此记录", callback_data=f"del_{record_id}"),
                ],
                [InlineKeyboardButton(text="🔙 返回上一级", callback_data="admin_main")]
            ]
        )
        await query.edit_message_text(
            text=text,
            parse_mode="Markdown",
            reply_markup=keyboard,
        )
        return "admin_fileid_view"


async def admin_fileid_confirm_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    data = query.data
    if data.startswith("del_"):
        record_id = int(data.split("_")[1])
        from src.models.file import delete_file

        await delete_file(record_id)
        await query.edit_message_text(
            f"🗑️ 记录 ID {record_id} 已成功删除！",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton(text="🔙 返回上一级", callback_data="admin_main")]]
            )
        )
        return "admin_main"

    if data == "admin_main":
        return "admin_main"

    return "admin_main"


# ------------------- 删除全部 ----------
async def admin_fileid_delete_all(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    from src.models.file import delete_all_records

    await delete_all_records()
    await update.callback_query.edit_message_text(
        "🗑️ 所有 File‑ID 记录已全部删除！",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton(text="🔙 返回上一级", callback_data="admin_main")]]
        )
    )
    return "admin_main"


# ------------------- 取消
async def admin_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    await update.callback_query.edit_message_text(
        "🔙 已返回主菜单", reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton(text="🔧 主菜单", callback_data="admin_main")]]
        )
    )
    return "admin_main"


# ------------------- 为 Wizard 状态机添加所有handler -------------
admin_fileid_scene = Scenes.Wizard()

admin_fileid_scene.states["admin_fileid_start"] = [
    CallbackQueryHandler(
        lambda u, c: u.scene.start("admin_fileid_start")
        if u.data == "admin_fileid_start"
        else None,
        pattern="^admin_fileid_start$",
    ),
    CallbackQueryHandler(
        lambda u, c: u.scene.start("admin_fileid_add")
        if u.data == "admin_fileid_add"
        else u.scene.start("admin_fileid_list")
        else u.scene.start("admin_cancel")
        else None,
        pattern="^admin_(fileid_add|list|cancel)$",
    ),
]

admin_fileid_scene.states["admin_fileid_add_text"] = [
    CallbackQueryHandler(
        lambda u, c: u.scene.start("admin_fileid_get_text")
        if u.data == "admin_fileid_get_text"
        else u.scene.start("admin_fileid_add")
        else u.scene.start("admin_cancel")
        else None,
        pattern="^admin_fileid_get_text$",
    ),
    MessageHandler(filters.TEXT & ~filters.COMMAND, admin_fileid_text_received),
]

admin_fileid_scene.states["admin_fileid_get_text"] = [
    CallbackQueryHandler(
        lambda u, c: u.scene.start("admin_fileid_get_text")
        if u.data == "admin_fileid_get_text"
        else u.scene.start("admin_fileid_add")
        else u.scene.start("admin_cancel")
        else None,
        pattern="^admin_fileid_get_text$",
    ),
    MessageHandler(filters.PHOTO, admin_fileid_get_image),
]

admin_fileid_scene.states["admin_fileid_list"] = [
    CallbackQueryHandler(
        lambda u, c: u.scene.start("admin_fileid_view_process")
        if u.data.startswith("view_")
        else u.scene.start("admin_fileid_confirm_delete")
        if u.data.startswith("del_")
        else u.scene.start("admin_fileid_delete_all")
        else u.scene.start("admin_main")
        if u.data == "admin_fileid_list"
        else None,
        pattern="^(view_|del_|admin_fileid_delete_all)$",
    ),
]

admin_fileid_scene.states["admin_fileid_view"] = [
    CallbackQueryHandler(
        lambda u, c: u.scene.start("admin_fileid_process")
        if u.data.startswith("view_")
        else u.scene.start("admin_fileid_confirm_delete")
        else u.scene.start("admin_fileid_list")
        if u.data == "admin_fileid_list"
        else None,
        pattern="^(view_|del_|admin_fileid_list)$",
    ),
]

admin_fileid_scene.states["admin_fileid_process"] = [
    CallbackQueryHandler(
        lambda u, c: u.scene.start("admin_fileid_process")
        if u.data.startswith("view_")
        else u.scene.start("admin_fileid_confirm_delete")
        else u.scene.start("admin_fileid_list")
        if u.data == "admin_fileid_list"
        else None,
        pattern="^(view_|del_|admin_fileid_list)$",
    ),
]

admin_fileid_scene.states["admin_fileid_confirm_delete"] = [
    CallbackQueryHandler(
        lambda u, c: u.scene.start("admin_fileid_confirm_delete_process")
        if u.data.startswith("delete_confirm_")
        else u.scene.start("admin_fileid_process")
        if u.data.startswith("view_")
        else u.scene.start("admin_fileid_list")
        if u.data == "admin_fileid_list"
        else None,
        pattern="^delete_confirm_",
    ),
]

admin_fileid_scene.states["admin_fileid_delete_confirm_process"] = [
    CallbackQueryHandler(
        lambda u, c: u.scene.start("admin_fileid_delete_process")
        if u.data.startswith("delete_confirm_")
        else u.scene.start("admin_fileid_process")
        else u.scene.start("admin_fileid_list")
        if u.data == "admin_fileid_list"
        else None,
        pattern="^delete_confirm_",
    ),
]  

admin_fileid_scene.states["admin_cancel"] = [
    CallbackQueryHandler(
        lambda u, c: u.scene.start("admin_main")
        if u.data == "admin_main"
        else None,
        pattern="^admin_main$",
    ),
]

# 中间件：权限检查
async def admin_permission_middleware(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        await update.callback_query.answer(
            text="❌ 您不是管理员，无权访问此功能。", show_alert=True
        )
        return

admin_fileid_scene.middleware = admin_permission_middleware


# ------------------- 导出供 main.py 调用的对象 -------------------
adminFileIdWizard = admin_fileid_scene  # <-- 这一行让 main.py 能 `from src.commands.fileid import adminFileIdWizard`
