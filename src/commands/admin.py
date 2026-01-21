# src/commands/admin.py
# ------------------------------------------------------------
# 管理员后台（/admin）完整实现
# ------------------------------------------------------------
# 只负责 UI 与状态机，不涉及任何数据库读写（这些交给
# `src/models/file.py` 与 `src/models/point.py`）。
# ------------------------------------------------------------

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.ext import (
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    Scenes,
    filters,
)

import os
from typing import List

# ------------------- 管理员 ID 列表 -------------------
def get_admin_ids() -> List[int]:
    """从环境变量 ADMIN_IDS 读取管理员 Telegram user_id 列表。"""
    raw = os.getenv("ADMIN_IDS", "")
    return [int(x) for x in raw.split(",") if x.strip() != ""]

ADMIN_IDS = get_admin_ids()


def is_admin(user_id: int) -> bool:
    """若 user_id 在 ADMIN_IDS 中则返回 True，否则 False。"""
    return user_id in ADMIN_IDS


# ------------------- 统一的权限检查 -------------------
async def admin_permission_middleware(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """所有 admin 相关的回调都必须先检查是否是管理员。"""
    if not is_admin(update.effective_user.id):
        await update.callback_query.answer(
            text="❌ 您不是管理员，无权访问此功能。", show_alert=True
        )
        return


# ------------------- Admin 场景（Wizard） -------------------
admin_scene = Scenes.Wizard()


# ---------- Step 0: 进入管理员主菜单 ----------
async def admin_main_enter(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    text="📁 管理文件 ID", callback_data="admin_fileid"
                )
            ],
            [
                InlineKeyboardButton(text="🔎 查看所有记录", callback_data="admin_view"),
                InlineKeyboardButton(text="❌ 重置权限设置", callback_data="admin_reset"),
            ],
        ]
    )
    await update.callback_query.answer()
    await update.callback_query.edit_message_text(
        "🛠️ 管理员后台已打开，请选择操作：", reply_markup=keyboard
    )
    return "admin_main"


# ---------- Step 1: 管理文件 ID ----------
async def admin_fileid_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(text="📥 新增 File‑ID", callback_data="admin_fileid_add")]
        ]
    )
    await update.callback_query.answer()
    await update.callback_query.edit_message_text(
        "📁 请选择要执行的操作：", reply_markup=keyboard
    )
    return "admin_fileid_add"


# ---------- Step 2: 输入要保存的文字 ----------
async def admin_fileid_add_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    await update.callback_query.edit_message_text(
        "📝 请在此输入要与 file_id 同时保存的文字（可以是描述性文字）：",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton(text="🚫 取消", callback_data="admin_cancel")]]
        )
    )
    # 保存文字到 user_data 以备后续使用
    context.user_data["admin_fileid_await_text"] = True
    return "admin_fileid_add_text"


async def admin_fileid_text_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message.photo:
        await update.message.reply_text(
            "❗️ 必须发送一张照片以获取 file_id。", reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton(text="🚫 取消", callback_data="admin_cancel")]]
            )
        )
        return "admin_fileid_add_text"

    # 取得图片的最清晰文件 (Telegram 会把图片按分辨率排序)
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
                InlineKeyboardButton(text="🔎 查看所有记录", callback_data="admin_fileid_view"),
                InlineKeyboardButton(text="🚫 取消", callback_data="admin_cancel"),
            ]
        )
    await update.message.reply_text(
        f"✅ 保存成功！\n🆔 记录 ID：{record['id']}\n📎 File‑ID：`{file_id}`\n🗒️ 文字：{saved_text}",
        parse_mode="Markdown",
        reply_markup=keyboard,
    )
    # 清理临时数据并返回主菜单
    context.user_data.pop("admin_fileid_await_text", None)
    context.user_data.pop("admin_fileid_saved_text", None)
    return "admin_main"


# ---------- Step 3: 查看所有记录 ----------
async def admin_fileid_view_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    from src.models.file import list_files

    rows = await list_files(limit=10)
    if not rows:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            "📂 当前没有任何记录，请先使用「新增 File‑ID」创建。"
        )
        return "admin_main"

    # 为每条记录生成「查看」和「删除」按钮
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
    return "admin_fileid_view"


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


async def admin_fileid_delete_all(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """在「查看所有记录」页面点击「删除全部」时调用。"""
    from src.models.file import delete_all_records

    await delete_all_records()
    await update.callback_query.edit_message_text(
        "🗑️ 所有 File‑ID 记录已全部删除！",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton(text="🔙 返回上一级", callback_data="admin_main")]]
        )
    )
    return "admin_main"


# ------------------- 中间件：统一权限检查 -------------------
async def admin_permission_middleware(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """所有 admin 相关的回调都必须先通过权限检查。"""
    if not is_admin(update.effective_user.id):
        await update.callback_query.answer(
            text="❌ 您不是管理员，无权访问此功能。", show_alert=True
        )
        return


# ------------------- 把所有 Step 加入 Wizard 状态机 -------------------
admin_scene = Scenes.Wizard()

# 主菜单
admin_scene.states["admin_main"] = [
    CallbackQueryHandler(
        lambda u, c: u.scene.start("admin_main")
        if u.data == "admin_main"
        else None,
        pattern="^admin_main$",
    ),
    CallbackQueryHandler(
        lambda u, c: u.scene.start("admin_fileid_add")
        if u.data == "admin_fileid_add"
        else u.scene.start("admin_fileid_view")
        if u.data == "admin_fileid_view"
        else u.scene.start("admin_main")
        if u.data == "admin_cancel"
        else None,
        pattern="^admin_(fileid_add|view|cancel|reset)$",
    ),
]

# 新增 File‑ID 文本输入
admin_scene.states["admin_fileid_add_text"] = [
    CallbackQueryHandler(
        lambda u, c: u.scene.start("admin_fileid_get_text")
        if u.data == "admin_fileid_get_text"
        else u.scene.start("admin_fileid_add")
        if u.data == "admin_fileid_add"
        else u.scene.start("admin_main")
        if u.data == "admin_cancel"
        else None,
        pattern="^admin_(fileid_add|cancel)$",
    ),
    MessageHandler(filters.TEXT & ~filters.COMMAND, admin_fileid_text_received),
]

# 获取图片并保存
admin_scene.states["admin_fileid_get_text"] = [
    CallbackQueryHandler(
        lambda u, c: u.scene.start("admin_fileid_get_text")
        if u.data == "admin_fileid_get_text"
        else u.scene.start("admin_main")
        if u.data == "admin_cancel"
        else None,
        pattern="^admin_fileid_get_text$",
    ),
    MessageHandler(filters.PHOTO, admin_fileid_text_received),
]

# 查看记录页面
admin_scene.states["admin_fileid_view"] = [
    CallbackQueryHandler(
        lambda u, c: u.scene.start("admin_fileid_process")
        if u.data.startswith("view_")
        else u.scene.start("admin_fileid_confirm_delete")
        if u.data.startswith("del_")
        else u.scene.start("admin_fileid_view")
        if u.data == "admin_fileid_view"
        else u.scene.start("admin_main")
        if u.data == "admin_main"
        else None,
        pattern="^(view_|del_|admin_fileid_view|admin_main)$",
    ),
]

# 处理「查看」后的细节页面
admin_scene.states["admin_fileid_process"] = [
    CallbackQueryHandler(
        lambda u, c: u.scene.start("admin_fileid_process")
        if u.data.startswith("view_")
        else u.scene.start("admin_fileid_confirm_delete")
        if u.data.startswith("del_")
        else u.scene.start("admin_fileid_view")
        if u.data == "admin_fileid_view"
        else u.scene.start("admin_main")
        if u.data == "admin_main"
        else None,
        pattern="^(view_|del_|admin_fileid_view|admin_main)$",
    ),
]

# 删除确认页面
admin_scene.states["admin_fileid_confirm_delete"] = [
    CallbackQueryHandler(
        lambda u, c: u.scene.start("admin_fileid_confirm_delete_process")
        if u.data.startswith("delete_confirm_")
        else u.scene.start("admin_fileid_confirm_delete")
        if u.data == "admin_fileid_confirm_delete"
        else u.scene.start("admin_fileid_confirm_delete")
        if u.data == "admin_fileid_confirm_delete"
        else u.scene.start("admin_main")
        if u.data == "admin_main"
        else None,
        pattern="^delete_confirm_",
    ),
]

# 删除实际执行
admin_scene.states["admin_fileid_delete_confirm_process"] = [
    CallbackQueryHandler(
        lambda u, c: u.scene.start("admin_fileid_confirm_delete_process")
        if u.data.startswith("delete_confirm_")
        else u.scene.start("admin_fileid_process")
        if u.data.startswith("view_")
        else u.scene.start("admin_fileid_view")
        if u.data == "admin_fileid_process"
        else u.scene.start("admin_main")
        if u.data == "admin_main"
        else None,
        pattern="^delete_confirm_",
    ),
]

# 删除全部
admin_scene.states["admin_fileid_delete_all"] = [
    CallbackQueryHandler(
        lambda u, c: u.scene.start("admin_fileid_delete_all")
        if u.data == "admin_fileid_delete_all"
        else u.scene.start("admin_main")
        if u.data == "admin_main"
        else None,
        pattern="^admin_fileid_delete_all$",
    ),
]

# 取消
admin_scene.states["admin_cancel"] = [
    CallbackQueryHandler(
        lambda u, c: u.scene.start("admin_main")
        if u.data == "admin_main"
        else None,
        pattern="^admin_main$",
    ),
]

# 将中间件挂到整个 Wizard
admin_scene.middleware = admin_permission_middleware


# ------------------- 导出供 main.py 调用的对象 -------------------
adminWizard = admin_scene  # 这一行让 main.py 能 `from src.commands.admin import adminWizard`
