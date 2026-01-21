# src/commands/fileid.py
# ------------------------------------------------------------
# 這個檔案實作「File‑ID」管理功能的完整 Wizard
# ------------------------------------------------------------
# 主要功能：
# 1. 透過管理員後台的「按鈕二」進入
# 2. 輸入文字 → 送送照片 → 取得 Telegram file_id
# 3. 把 (user_id, file_id, text) 存入 resources/file_records 表
# 4. 顯示紀錄、重新取得 file_id、或刪除紀錄
# 5. 所有操作均受管理員權限限制
# ------------------------------------------------------------


from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    Scenes,
    filters,
)

# ------------------------------------------------------------
# 1️⃣ 取得管理員 ID（同 admin.py 完全相同的檢查）
# ------------------------------------------------------------
import os

def get_admin_ids() -> list:
    raw = os.getenv("ADMIN_IDS", "")
    return [int(x) for x in raw.split(",") if x.strip() != ""]

ADMIN_IDS = get_admin_ids()


def is_admin(user_id: int) -> bool:
    """若使用者 id 在 ADMIN_IDS 中則回傳 True，否則 False。"""
    return user_id in ADMIN_IDS


# ------------------------------------------------------------
# 2️⃣ Wizard – 完整的新增 / 查看 / 刪除 步驟
# ------------------------------------------------------------
async def admin_fileid_scene() -> Scenes.WizardScene:
    """
    這個 Wizard 包含以下狀態碼：
      • `admin_fileid_start` – 顯示「新增」或「查看」的主選單
      • `admin_fileid_add_text` – 等待管理員輸入文字說明
      • `admin_fileid_get_image` – 等待管理員發送圖片以取得 file_id
      • `admin_fileid_list` – 顯示目前所有紀錄（最多 10 條），每筆配有「查看」與「刪除」按鈕
      • `admin_fileid_view` – 點擊「查看」後顯示詳細資訊與「重新取得」或「刪除」選項
      • `admin_fileid_confirm_delete` – 確認刪除該筆紀錄
      • `admin_fileid_cancel` – 任意時候按「取消」回到主選單
    Wizard 只能由管理員進入（使用 `is_admin` 檢查）。
    """
    wizard = Scenes.Wizard()

    # ------------------------------------------------------------
    # Step A : admin_fileid_start – 主選單
    # ------------------------------------------------------------
    async def admin_fileid_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """顯示管理員可執行的主要選項"""
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        text="📥 新增 File‑ID", callback_data="admin_fileid_add"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🔎 查看所有紀錄", callback_data="admin_fileid_list"
                    )
                ],
                [
                    InlineKeyboardButton(text="🚫 取消", callback_data="admin_fileid_cancel")
                ],
            ]
        )
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            "🛠️ 管理員 File‑ID 管理選單，請選擇要執行的操作：", reply_markup=keyboard
        )
        return "admin_fileid_add"

    # ------------------------------------------------------------
    # Step B : admin_fileid_add_text – 輸入要保存的文字
    # ------------------------------------------------------------
    async def admin_fileid_add_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """要求管理員輸入要與 file_id 同時保存的文字說明"""
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            "📝 請在下方輸入要與此 file_id 一起保存的文字（可自行描述）",
            reply_markup=InlineKeyboardMarkup([[{text: "🚫 取消", callback_data="admin_fileid_cancel"}]]),
        )
        # 暫存「等待文字」的狀態
        context.user_data["admin_fileid_await_text"] = True
        return "admin_fileid_add_text"

    # ------------------------------------------------------------
    # Step C : admin_fileid_get_image – 等待發送圖片以取得 file_id
    # ------------------------------------------------------------
    async def admin_fileid_get_image(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """接收照片、取得 file_id，然後存入 DB"""
        if not update.message.photo:
            await update.callback_query.edit_message_text(
                "❗️ 必須發送一張圖片才能繼續，請重新嘗試。",
                reply_markup=InlineKeyboardMarkup([[{text: "🚫 取消", callback_data="admin_fileid_cancel"}]]),
            )
            return "admin_fileid_get_image"

        # 取得圖片的 file_id（Telegram 會把多張大小依代號排列，最後一張即最大解析度）
        file_id = update.message.photo[-1].file_id

        # 從 user_data 取出先前保存的文字（如果有 إن.save_text）
        saved_text = context.user_data.get("admin_fileid_saved_text", "")
        # 呼叫 models/file.py 中的 insert_file 完成存檔
        from ..models.file import insert_file

        record = await insert_file(
            user_id=update.effective_user.id,
            file_id=file_id,
            text=saved_text,
        )
        # 回傳成功訊息並提供下一步選擇
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        text="🔎 查看所有紀錄", callback_data="admin_fileid_list"
                    )
                ],
                [
                    InlineKeyboardButton(text="🚫 取消", callback_data="admin_fileid_cancel")
                ],
            ]
        )
        await update.callback_query.edit_message_text(
            f"✅ 保存成功！\n🆔 紀錄 ID：{record['id']}\n📎 File‑ID：`{file_id}`\n🗒️ 文字：{saved_text}",
            parse_mode="Markdown",
            reply_markup=keyboard,
        )
        # 清理暫存資料，結束 Wizard
        context.user_data.pop("admin_fileid_await_text", None)
        context.user_data.pop("admin_fileid_saved_text", None)
        return "admin_fileid_start"

    # ------------------------------------------------------------
    # Step D : admin_fileid_list – 顯示目前所有紀錄（最多 10 條）
    # ------------------------------------------------------------
    async def admin_fileid_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """從 DB 拉出最近 10 筆紀錄，顯示並提供「查看」與「刪除」按鈕"""
        from ..models.file import list_files

        rows = await list_files(limit=10)
        if not rows:
            await update.callback_query.answer()
            await update.callback_query.edit_message_text(
                "📂 暫時沒有任何紀錄，請先使用「新增 File‑ID」來建立。"
            )
            return "admin_fileid_start"

        # 準備顯示的文字（只顯示 id 與文字前 20 個字符）
        lines = []
        for idx, rec in enumerate(rows, start=1):
            lines.append(f"{idx}. ID={rec['id']} 文字={rec['text'][:20]}…")
        # 為每筆紀錄新增「查看」和「刪除」按鈕（使用回調 data 格式 `view_<id>`、`del_<id>`）
        inline_keyboard = []
        for rec in rows:
            inline_keyboard.append(
                [
                    InlineKeyboardButton(
                        text="👀 查看",
                        callback_data=f"view_{rec['id']}",
                    ),
                    InlineKeyboardButton(
                        text="🗑️ 刪除",
                        callback_data=f"del_{rec['id']}",
                    ),
                ]
            )
        inline_keyboard.append(
            [InlineKeyboardButton(text="🔙 返回上一層", callback_data="admin_fileid_start")]
        )
        keyboard = InlineKeyboardMarkup(inline_keyboard)

        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            "📚 以下是目前最近的紀錄（最多顯示 10 條），請點選 '查看' 或 '刪除'：\n\n"
            + "\n".join(lines),
            reply_markup=keyboard,
        )
        return "admin_fileid_list"

    # ------------------------------------------------------------
    # Step E : admin_fileid_view – 點擊「查看」後顯示詳細資訊
    # ------------------------------------------------------------
    async def admin_fileid_view(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """根據回調 data 中的 id（如 view_123）顯示該筆紀錄的完整內容"""
        # 解析回調 data
        data = update.callback_query.data
        if not data.startswith("view_"):
            return "admin_fileid_start"
        record_id = int(data.split("_")[1])

        # 從 DB 取出該筆紀錄
        from ..models.file import get_file_by_id

        rec = await get_file_by_id(record_id)
        if not rec:
            await update.callback_query.answer()
            await update.callback_query.edit_message_text("❗️ 該紀錄已不存在或已被刪除。")
            return "admin_fileid_start"

        # 準備顯示的訊息
        text = (
            f"🆔 **紀錄 ID** ：{rec['id']}\n"
            f"👤 **使用者 ID** ：{rec['user_id']}\n"
            f"🔠 **文字說明** ：{rec['text']}\n"
            f"📎 **File‑ID** ：`{rec['file_id']}`\n"
            f"🕒 **建立時間** ：{rec['created_at']}"
        )
        # 為這筆紀錄加上「重新取得」與「刪除」按鈕
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        text="🔁 重新取得 File‑ID", callback_data=f"reget_{rec['id']}"
                    ),
                    InlineKeyboardButton(text="🗑️ 刪除此紀錄", callback_data=f"del_{rec['id']}"),
                ],
                [
                    InlineKeyboardButton(text="🔙 返回上一層", callback_data="admin_fileid_list")
                ],
            ]
        )
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(parse_mode="Markdown", text=text, reply_markup=keyboard)
        return "admin_fileid_view"

    # ------------------------------------------------------------
    # Step F : admin_fileid_confirm_delete – 刪除確認或執行刪除
    # ------------------------------------------------------------
    async def admin_fileid_confirm_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """收到 `del_<id>` 回調，先確認再執行刪除"""
        data = update.callback_query.data
        if not data.startswith("del_"):
            return "admin_fileid_start"
        record_id = int(data.split("_")[1])

        # 彈出確認對話框
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            f"⚠️ 確定要刪除 **ID 為 {record_id} 的紀錄嗎？此操作無法復原。",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            text="✅ 確定刪除", callback_data=f"delete_confirm_{record_id}"
                        )
                    ],
                    InlineKeyboardButton(text="🚫 前往取消", callback_data="admin_fileid_list"),
                ]
            ),
        )
        return "admin_fileid_delete_confirm"

    async def admin_fileid_delete_confirm_process(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """真正執行刪除操作"""
        data = update.callback_query.data
        if not data.startswith("delete_confirm_"):
            return "admin_fileid_start"
        record_id = int(data.split("_")[1])

        # 呼叫 models/file.py 的 delete_file 函式
        from ..models.file import delete_file

        await delete_file(record_id)
        await update.callback_query.edit_message_text(
            f"🗑️ 紀錄 ID {record_id} 已成功刪除！", reply_markup=InlineKeyboardMarkup([[{text: "🔙 返回上一層", callback_data="admin_fileid_list"}]]))
        return "admin_fileid_list"

    # ------------------------------------------------------------
    # Step G : admin_fileid_reget – 重新取得 File‑ID（重新下載同一張圖）
    # ------------------------------------------------------------
    async def admin_fileid_reget(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """根據回調 data `reget_<id>` 重新向 Telegram 服務請求同一張圖的 file_id"""
        data = update.callback_query.data
        if not data.startswith("reget_"):
            return "admin_fileid_start"
        record_id = int(data.split("_")[1])

        # 從 DB 取出該紀錄的 file_id 與文字
        from ..models.file import get_file_by_id

        rec = await get_file_by_id(record_id)
        if not rec:
            await update.callback_query.answer()
            await update.callback_query.edit_message_text("❗️ 該紀錄已消失，無法重新取得。")
            return "admin_fileid_start"

        # 使用 Telegram Bot 的 `download_file` 或直接透過 `get_file` 取得 file_object
        # 然後把 file_id 重新發送給使用者（簡單回覆即可）
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            f"📎 該筆紀錄的原始 File‑ID 為 `{rec['file_id']}`\n（若需要重新下載圖片，請在聊天框重新上傳圖片）",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[{text: "🔙 返回上一層", callback_data="admin_fileid_list"}]]),
        )
        return "admin_fileid_start"

    # ------------------------------------------------------------
    # Step H : admin_fileid_delete_all – 一鍵清空全部紀錄（管理員專用）
    # ------------------------------------------------------------
    async def admin_fileid_delete_all(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """在「查看所有紀錄」的列表中，點擊「刪除全部」按鈕時呼叫此函式"""
        from ..models.file import delete_all_records

        await delete_all_records()
        await update.callback_query.edit_message_text(
            "🗑️ 所有 File‑ID 紀錄已全部刪除！", reply_markup=InlineKeyboardMarkup([[{text: "🔙 返回主選單", callback_data="admin_fileid_start"}]]))
        return "admin_fileid_start"

    # ------------------------------------------------------------
    # 8️⃣ 把所有 step 加入 Wizard 狀態機
    # ------------------------------------------------------------
    wizard = Scenes.Wizard()

    wizard.states["admin_fileid_start"] = [
        CallbackQueryHandler(
            lambda u, c: u.scene.start("admin_fileid_start") if u.data == "admin_fileid_start" else None,
            pattern="^admin_fileid_start$",
        ),
        CallbackQueryHandler(
            lambda u, c: u.scene.start("admin_fileid_add_text") if u.data == "admin_fileid_add"
            else u.scene.start("admin_fileid_list") if u.data == "admin_fileid_list"
            else u.scene.start("admin_fileid_cancel") if u.data == "admin_fileid_cancel"
            else None,
            pattern="^admin_fileid_(add|list|cancel)$",
        ),
    ]

    wizard.states["admin_fileid_add_text"] = [
        CallbackQueryHandler(
            lambda u, c: u.scene.start("admin_fileid_get_image") if u.data == "admin_fileid_get_image" else None,
            pattern="^admin_fileid_get_image$",
        ),
        MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: u.scene.start("admin_fileid_get_image")),
    ]

    wizard.states["admin_fileid_get_image"] = [
        CallbackQueryHandler(
            lambda u, c: u.scene.start("admin_fileid_add_text") if u.data == "admin_fileid_add_text" else None,
            pattern="^admin_fileid_add_text$",
        ),
        MessageHandler(filters.PHOTO, lambda u, c: u.scene.start("admin_fileid_get_image")),
    ]

    wizard.states["admin_fileid_list"] = [
        CallbackQueryHandler(
            lambda u, c: u.scene.start("admin_fileid_view") if u.data.startswith("view_") else
                         u.scene.start("admin_fileid_confirm_delete") if u.data.startswith("del_") else
                         u.scene.start("admin_fileid_delete_all") if u.data == "admin_fileid_delete_all" else
                         u.scene.start("admin_fileid_start") if u.data == "admin_fileid_start" else None,
            pattern="^(view_|del_|delete_confirm_|admin_fileid_delete_all)$",
        ),
    ]

    wizard.states["admin_fileid_view"] = [
        CallbackQueryHandler(
            lambda u, c: u.scene.start("admin_fileid_reget") if u.data.startswith("reget_") else
                         u.scene.start("admin_fileid_confirm_delete") if u.data.startswith("del_") else
                         u.scene.start("admin_fileid_list") if u.data == "admin_fileid_list" else None,
            pattern="^(reget_|del_|admin_fileid_list)$",
        ),
    ]

    wizard.states["admin_fileid_delete_confirm"] = [
        CallbackQueryHandler(
            lambda u, c: admin_fileid_delete_confirm_process(u, c) if u.data.startswith("delete_confirm_") else None,
            pattern="^delete_confirm_$",
        ),
        CallbackQueryHandler(
            lambda u, c: u.scene.start("admin_fileid_list") if u.data == "admin_fileid_list" else None,
            pattern="^admin_fileid_list$",
        ),
    ]

    wizard.states["admin_fileid_reget"] = [
        CallbackQueryHandler(
            lambda u, c: u.scene.start("admin_fileid_view") if u.data.startswith("reget_") else None,
            pattern="^reget_$",
        ),
        CallbackQueryHandler(
            lambda u, c: u.scene.start("admin_fileid_list") if u.data == "admin_fileid_list" else None,
            pattern="^admin_fileid_list$",
        ),
    ]

    wizard.states["admin_fileid_cancel"] = [
        CallbackQueryHandler(
            lambda u, c: u.scene.start("admin_fileid_start") if u.data == "admin_fileid_start" else None,
            pattern="^admin_fileid_start$",
        ),
    ]

    # ------------------------------------------------------------
    # 9️⃣ 為整個 Wizard 加上權限檢查的 middleware
    # ------------------------------------------------------------
    async def admin_permission_middleware(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """所有與 admin_fileid_ 開頭的回調都必須先檢查是否為管理員"""
        if not is_admin(update.effective_user.id):
            await update.callback_query.answer(
                text="❌ 您不是管理員，沒有此功能的存取權限。", show_alert=True
            )
            return

    wizard.middleware = admin_permission_middleware

    return wizard


# ------------------------------------------------------------
# 10️⃣ 匯出供 main.py 使用的物件
# ------------------------------------------------------------
adminFileIdWizard = admin_fileid_scene()  # 這是一個已完成配置的 Scenes.Wizard 實例
