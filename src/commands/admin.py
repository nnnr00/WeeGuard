# src/commands/admin.py
# ------------------------------------------------------------
# 管理員後台 (admin) 相關功能
# ------------------------------------------------------------
# 包含：
#   • /admin 按鈕 UI (管理員專用)
#   • file‑id 相關的 Wizard (新增、查看、刪除)
#   • 只允許設定過的 Telegram user_id 為管理員
# ------------------------------------------------------------


from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    Scenes,
    filters,
)

# ------------------------------------------------------------
# 1️⃣ 取得管理員 ID 列表 (從環境變數 ADMIN_IDS 讀取)
# ------------------------------------------------------------
import os

def get_admin_ids() -> List[int]:
    """
    從環境變數 ADMIN_IDS 讀取管理員 Telegram user_id。
    如果變數未設定或為空字串，則返回空列表。
    """
    raw = os.getenv("ADMIN_IDS", "")
    return [int(x) for x in raw.split(",") if x.strip() != ""]


ADMIN_IDS = get_admin_ids()


# ------------------------------------------------------------
# 2️⃣ 判斷當前使用者是否為管理員的 Helper
# ------------------------------------------------------------
def is_admin(user_id: int) -> bool:
    """返回 True 表示此 user_id 列在 ADMIN_IDS 之中。"""
    return user_id in ADMIN_IDS


# ------------------------------------------------------------
# 3️⃣ 文字Wizard – 用於「新增 / 查看 / 刪除」File‑ID
# ------------------------------------------------------------
async def admin_scene() -> Scenes.WizardScene:
    """
    這個 Wizard 包含 4 個步驟：
      1. 顯示主選單（只會出現一次）
      2. 「新增」按鈕 → 輸入文字 → 輸入圖片 → 保存到 DB
      3. 「查看」按鈕 → 顯示列表 → 點擊 ID → 可再次取得或刪除
      4. 「刪除」按鈕 → 確認刪除 → 從 DB 移除
    Wizard 只能由管理員進入（在 /admin 命令中檢查 is_admin）。
    """
    wizard = Scenes.Wizard()

    # ------------------- Step 0 : 進入 admin 主選單 -------------------
    wizard.states["admin_main"] = [
        CommandHandler("admin", lambda u, c: u.scene.start("admin_main")),
        CallbackQueryHandler(
            # 這裡的回調只處理「管理文件 ID」這個按鈕
            lambda u, c: u.scene.start("admin_fileid") if u.data == "admin_fileid" else None,
            pattern="^admin_",
        ),
    ]

    # ------------------- Step 1 : 顯示主選單 -------------------
    async def admin_main_enter(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """進入 admin 主選單，顯示三個功能按鈕"""
        query = update.callback_query
        if query:
            await query.answer()  # 把「loading」狀態消失

        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        text="📁 管理文件 ID", callback_data="admin_fileid"
                    )
                ],
                [
                    InlineKeyboardButton(text="🔎 查看所有紀錄", callback_data="admin_view"),
                ],
                [
                    InlineKeyboardButton(text="❌ 重置權限設定", callback_data="admin_reset")
                ],
            ]
        )
        await query.edit_message_text(
            "🛠️ 管理員後台已開啟，請選擇操作：", reply_markup=keyboard
        )
        return "admin_main"

    # ------------------- Step 2 : 新增 File‑ID -------------------
    async def admin_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """開始新增流程：要求使用者輸入文字說明"""
        await update.message.reply_text(
            "📝 請輸入要保存的文字（例如「活動宣傳文案」）"
        )
        return "admin_add_text"

    async def admin_add_text_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """收到使用者文字後，進入「請發送圖片」 단계"""
        text = update.message.text
        # 把文字暫存在 user_data 中
        context.user_data["admin_add_text"] = text
        await update.message.reply_text(
            "🖼️ 現在請發送一張圖片（單張或多張均可）以取得其 file_id",
            reply_markup=InlineKeyboardMarkup([[{text: "🚫 取消", callback_data="admin_cancel"}]]),
        )
        return "admin_add_image"

    async def admin_add_image_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """取得圖片的 file_id，並呼叫 insert_file 存入 DB"""
        if not update.message.photo:
            await update.message.reply_text("❗️ 我們需要一張圖片才能繼續")
            return "admin_add_image"

        # Telegram 會把所有照片大小依代號給出，我們只取最小的 (at 0)
        file_id = update.message.photo[-1].file_id

        # 從 user_data 取出先前保存的文字
        saved_text = context.user_data.get("admin_add_text", "")
        # 假設已在 src/models/file.py 中寫好 insert_file 函式
        from ..models.file import insert_file

        record = await insert_file(
            user_id=update.effective_user.id,
            file_id=file_id,
            text=saved_text,
        )
        # 把新建立的紀錄 ID 再回傳給使用者
        await update.message.reply_text(
            f"✅ 保存成功！\n🆔 紀錄 ID：{record['id']}\n📎 File‑ID：`{file_id}`\n🗒️ 文字：{saved_text}",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[{text: "🔙 返回主選單", callback_data="admin_main"}]]),
        )
        # 清理暫存資料，結束此 Wizard
        context.user_data.pop("admin_add_text", None)
        return "admin_main"

    # ------------------- Step 3 : 查看紀錄（列表） -------------------
    async def admin_view_enter(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """列出所有 File‑ID 紀錄（最多顯示 10 條）"""
        from ..models.file import list_files

        rows = await list_files(limit=10)
        if not rows:
            await update.message.reply_text("📂 暫無任何紀錄。")
            return "admin_main"

        lines = []
        for idx, rec in enumerate(rows, start=1):
            lines.append(f"{idx}. ID={rec['id']} 文字={rec['text'][:15]}…")
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton(text="🔙 返回主選單", callback_data="admin_main")]]
        )
        await update.message.reply_text(
            "📚 以下是當前存檔的最高 10 筆紀錄：\n\n" + "\n".join(lines),
            reply_markup=keyboard,
        )
        return "admin_main"

    # ------------------- Step 4 : 刪除確認與執行 -------------------
    async def admin_delete_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """顯示要刪除的紀錄 ID，讓管理員確認"""
        # 直接從 DB 取出所有 ID（使用 list_files 只取 id 欄位）
        from ..models.file import list_files

        rows = await list_files(limit=100)  # 取全部
        if not rows:
            await update.message.reply_text("📂 沒有可刪除的紀錄。")
            return "admin_main"

        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        text="🗑️ 刪除全部", callback_data="admin_delete_all"
                    )
                ]
            ]
        )
        await update.message.reply_text(
            "⚠️ 以下是目前所有紀錄的 ID（點擊「刪除全部」將會一次清空所有紀錄）：",
            reply_markup=keyboard,
        )
        return "admin_main"

    async def admin_delete_all_process(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """真正執行刪除全部紀錄的動作"""
        from ..models.file import delete_all_records  # 需要自行在 models/file.py 裡實作

        await delete_all_records()
        await update.message.reply_text(
            "🗑️ 所有紀錄已全部刪除！", reply_markup=InlineKeyboardMarkup([[{text: "🔙 返回主選單", callback_data="admin_main"}]]))
        return "admin_main"

    # ------------------- Step 5 : 取消 -------------------
    async def admin_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """使用者按下「取消」時返回主選單"""
        await update.message.reply_text(
            "🔙 已返回主選單", reply_markup=InlineKeyboardMarkup([[{text: "🔧 主選單", callback_data="admin_main"}]]))
        return "admin_main"

    # ------------------------------------------------------------
    # 6️⃣ 把所有 step 加入Wizard的狀態機
    # ------------------------------------------------------------
    wizard.states["admin_main"] = [
        CommandHandler("admin", lambda u, c: u.scene.start("admin_main")),
        CallbackQueryHandler(
            lambda u, c: u.scene.start("admin_main") if u.data == "admin_main" else None,
            pattern="^admin_main$",
        ),
    ]

    wizard.states["admin_add_text"] = [
        CommandHandler("admin", lambda u, c: u.scene.start("admin_main")),
        CallbackQueryHandler(
            lambda u, c: u.scene.start("admin_add_image") if u.data == "admin_add_image" else None,
            pattern="^admin_add_image$",
        ),
        MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_text_received),
    ]

    wizard.states["admin_add_image"] = [
        CommandHandler("admin", lambda u, c: u.scene.start("admin_main")),
        CallbackQueryHandler(
            lambda u, c: u.scene.start("admin_main") if u.data == "admin_cancel" else None,
            pattern="^admin_cancel$",
        ),
        MessageHandler(filters.PHOTO, admin_add_image_received),
    ]

    wizard.states["admin_view"] = [
        CommandHandler("admin", lambda u, c: u.scene.start("admin_main")),
        CallbackQueryHandler(
            lambda u, c: u.scene.start("admin_main") if u.data == "admin_main" else None,
            pattern="^admin_main$",
        ),
    ]

    wizard.states["admin_delete"] = [
        CommandHandler("admin", lambda u, c: u.scene.start("admin_main")),
        CallbackQueryHandler(
            lambda u, c: u.scene.start("admin_delete_confirm") if u.data == "admin_delete_confirm" else None,
            pattern="^admin_delete_confirm$",
        ),
        CallbackQueryHandler(
            lambda u, c: admin_delete_all_process(u, c) if u.data == "admin_delete_all" else None,
            pattern="^admin_delete_all$",
        ),
        CallbackQueryHandler(
            lambda u, c: u.scene.start("admin_main") if u.data == "admin_main" else None,
            pattern="^admin_main$",
        ),
    ]

    wizard.states["admin_cancel"] = [
        CommandHandler("admin", lambda u, c: u.scene.start("admin_main")),
        CallbackQueryHandler(
            lambda u, c: u.scene.start("admin_main") if u.data == "admin_main" else None,
            pattern="^admin_main$",
        ),
    ]

    # ------------------------------------------------------------
    # 7️⃣ 進入檢查是否為管理員的 Middleware
    # ------------------------------------------------------------
    async def admin_permission_middleware(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """所有 admin 相關的回調與指令，必須先檢查使用者是否為 admin。"""
        if not is_admin(update.effective_user.id):
            await update.callback_query.answer(
                text="❌ 您不是管理員，沒有此功能的存取權限。", show_alert=True
            )
            # 防止繼續進入 Wizard，直接返回
            return

    # 把 middleware掛到整個 Wizard 上
    wizard.middleware = admin_permission_middleware

    return wizard


# ------------------------------------------------------------
# 8️⃣ 匯出供 main.py 使用的物件
# ------------------------------------------------------------
adminWizard = admin_scene()  # 這是一個已經完成配置的 Scenes.Wizard 實例
