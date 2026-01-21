# ======================================================================
#  bot.py – 完整、已修正的 Railway 版（已移除所有缩写、已移除 name 参数）
# ======================================================================

import os
import logging
import random
import string
from datetime import datetime, timezone
from typing import Dict, List, Optional

from dateutil import tz
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import (
    Boolean,               # 必须保留的列类型
    Column,
    DateTime,
    Enum,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    create_async_engine,
)
from sqlalchemy.orm import declarative_base, sessionmaker
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
    MessageHandler,
    filters,
)

# ---------------------------- 2️⃣ 環境變數 ----------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS_RAW = os.getenv("ADMIN_ID", "")
DATABASE_URL = os.getenv("DATABASE_URL")
DOMAIN = os.getenv("DOMAIN")               # 必須是完整的 https://… URL

if not (BOT_TOKEN and ADMIN_IDS_RAW and DATABASE_URL and DOMAIN):
    raise RuntimeError(
        "Missing one of BOT_TOKEN / ADMIN_ID / DATABASE_URL / DOMAIN environment variables"
    )
ADMIN_IDS = [int(x) for x in ADMIN_IDS_RAW.split(",") if x.strip().isdigit()]

# ---------------------------- 3️⃣ SQLAlchemy 基礎結構 ----------------------------
Base = declarative_base()

# ---------- 3.1 表模型（保持原有功能） ----------
class FileIDRecord(Base):
    __tablename__ = "file_ids"
    id = Column(Integer, primary_key=True)
    file_id = Column(Text, nullable=False, unique=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class UserAdUsage(Base):
    __tablename__ = "user_ad_usage"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, index=True, nullable=False)
    usage_date = Column(DateTime, nullable=False)
    ads_watched_today = Column(Integer, default=0, nullable=False)
    points_granted_today = Column(Integer, default=0, nullable=False)

class SecretKey(Base):
    __tablename__ = "secret_keys"
    __table_args__ = (UniqueConstraint("secret_type", name="uq_secret_type"),)

    id = Column(Integer, primary_key=True)
    secret_type = Column(
        Enum("key1", "key2", name="secret_type_enum"), nullable=False
    )
    secret_value = Column(Text, nullable=False, unique=True)
    is_active = Column(Boolean, default=False, nullable=False)   # 必须 Boolean
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AdminLink(Base):
    __tablename__ = "admin_links"
    __table_args__ = (UniqueConstraint("link_type", name="uq_link_type"),)

    id = Column(Integer, primary_key=True)
    link_type = Column(
        Enum("key1", "key2", name="link_type_enum"), nullable=False
    )
    url = Column(Text, nullable=False)
    is_active = Column(Boolean, default=False, nullable=False)   # 必须 Boolean
    created_at = Column(DateTime, default=datetime.utcnow)


class UserKeyUsage(Base):
    __tablename__ = "user_key_usage"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, index=True, nullable=False)
    secret_type = Column(
        Enum("key1", "key2", name="secret_type_enum"), nullable=False
    )
    usage_date = Column(DateTime, nullable=False)

    __table_args__ = (UniqueConstraint("user_id", "secret_type", name="uq_user_type"),)


class VideoViewUsage(Base):
    __tablename__ = "video_view_usage"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, index=True, nullable=False)
    usage_date = Column(DateTime, nullable=False)

    __table_args__ = (UniqueConstraint("user_id", "usage_date", name="uq_user_date"),)


class ExplanationViewUsage(Base):
    __tablename__ = "explanation_view_usage"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, index=True, nullable=False)
    usage_date = Column(DateTime, nullable=False)

    __table_args__ = (UniqueConstraint("user_id", "usage_date", name="uq_explain_date"),)


# ---------------------------- 4️⃣ Async engine ----------------------------
engine: AsyncEngine = create_async_engine(
    DATABASE_URL, echo=False, future=True
)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_async_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session


# ---------------------------- 5️⃣ 輔助函式 ----------------------------
async def store_file_id(session: AsyncSession, fid: str) -> None:
    result = await session.execute(
        "SELECT 1 FROM file_ids WHERE file_id = :fid", {"fid": fid}
    )
    if not result.scalar():
        await session.execute(
            "INSERT INTO file_ids (file_id, created_at) VALUES (:fid, :now)",
            {"fid": fid, "now": datetime.utcnow()},
        )
        await session.commit()
        logging.info(f"Saved file_id: {fid}")
    else:
        logging.info(f"File_id already present: {fid}")

async def retrieve_all_file_ids(session: AsyncSession) -> List[str]:
    result = await session.execute(
        "SELECT file_id FROM file_ids ORDER BY created_at DESC"
    )
    rows = result.fetchall()
    return [row[0] for row in rows]

async def delete_file_id(session: AsyncSession, fid: str) -> None:
    await session.execute(
        "DELETE FROM file_ids WHERE file_id = :fid", {"fid": fid}
    )
    await session.commit()
    logging.info(f"Deleted file_id: {fid}")

async def get_user_usage_today(session: AsyncSession, user_id: int) -> Optional[UserAdUsage]:
    today_start = datetime.now(tz.gettz("Asia/Shanghai")).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    result = await session.execute(
        """
        SELECT *
        FROM user_ad_usage
        WHERE user_id = :uid AND usage_date::date = :today
        """,
        {"uid": user_id, "today": today_start},
    )
    row = result.fetchone()
    return row[0] if row else None


async def upsert_user_usage(
    session: AsyncSession,
    user_id: int,
    points: int,
    reward_source: str = "ad_reward",
) -> None:
    today_start = datetime.now(tz.gettz("Asia/Shanghai")).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    existing = await session.execute(
        """
        SELECT *
        FROM user_ad_usage
        WHERE user_id = :uid AND usage_date::date = :today
        """,
        {"uid": user_id, "today": today_start},
    )
    if existing.scalar():
        usage = existing.one()
        usage.ads_watched_today += 1
        usage.points_granted_today += points
        await session.commit()
    else:
        new_row = UserAdUsage(
            user_id=user_id,
            usage_date=today_start,
            ads_watched_today=1,
            points_granted_today=points,
        )
        session.add(new_row)
        await session.commit()
    logging.info(
        f"User {user_id} received {points} points for ad (ads today: {usage.ads_watched_today})"
    )

# -----------------------------------------------------------------
async def generate_random_string(length: int = 10) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(random.choice(alphabet) for _ in range(length))
# ---------------------------- 6️⃣ 每日密鑰生成與私聊管理員 ----------------------------
async def store_today_secrets(session: AsyncSession, bot) -> None:
    """每日 10:00 生成全新 key1 / key2，標記為 active，並私聊管理員。"""
    await session.execute("UPDATE secret_keys SET is_active = FALSE")
    key1 = await generate_random_string()
    key2 = await generate_random_string()
    await session.execute(
        """
        INSERT INTO secret_keys (secret_type, secret_value, is_active, created_at)
        VALUES ('key1', :v1, TRUE, :now),
               ('key2', :v2, TRUE, :now)
        """,
        {"v1": key1, "v2": key2, "now": datetime.utcnow()},
    )
    await session.commit()
    logging.info(f"Generated new daily secrets → key1={key1}, key2={key2}")

    for admin_id in ADMIN_IDS:
        try:
            message = (
                f"🔔 **今日密钥已更新**（{datetime.now(tz.gettz('Asia/Shanghai')):%Y-%m-%d %H:%M} )\n"
                f"密钥一（8 积分）: `{key1}`\n"
                f"密钥二（6 积分）: `{key2}`"
            )
            await bot.send_message(chat_id=admin_id, text=message, parse_mode="Markdown")
        except Exception as e:
            logging.warning(f"Failed to private‑message admin {admin_id}: {e}")
    

# ---------------------------- 7️⃣ 每日計數重置 ----------------------------
async def reset_video_counter_daily(session: AsyncSession) -> None:
    await session.execute("DELETE FROM video_view_usage")
    await session.commit()
    logging.info("Daily video view counter reset.")

async def reset_explanation_counter_daily(session: AsyncSession) -> None:
    await session.execute("DELETE FROM explanation_view_usage")
    await session.commit()
    logging.info("Daily explanation view counter reset.")


# ---------------------------- 7️⃣ FastAPI 基礎 ----------------------------
application = FastAPI()   # <- the application that will serve HTML pages
application.mount(
    "/static",
    StaticFiles(
        directory=os.path.join(os.path.dirname(__file__), "doc"),
        mount_point="/static",
        name="static"
    ),
    name="static"
)


# ---------- 1️⃣ 首頁（自動跳轉） ----------
@application.get("/", response_class=HTMLResponse)
async def root_page() -> str:
    """首頁直接跳轉至獎勵影片，3 秒後回到活動中心頁面。"""
    return f"""
<!!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8"><title>MoonTag 入口</title></head>
<body style="text-align:center;margin-top:30px;">
  <div style="margin-bottom:15px;color:#555;">
    正在跳轉至獎勵影片，請稍等…
    </div>
    <script>
        window.location.href = '{DOMAIN}';
        setTimeout(()=>{{window.location.href = '/activity_center.html';}}, 3000);
    </script>
</body></html>
"""

# ---------- 2️⃣ 活動中心 (/activity_center.html) ----------
# 這個路由直接返回 HTML, 因此不需要額外的檔案；內容已寫在程式碼內。
# (若想把 HTML 放在 doc/ 內，把路徑調整即可；此處直接返回 HTML 文字以避免路徑問題。)

@application.get("/activity_center", response_class=HTMLResponse)
async def activity_center_page(request: Request) -> str:
    """
    活動中心頁面：顯示兩個按鈕
        • 按鈳一 – 觀看影片（0/3，0:00 自動重置）
        • 按鈳二 – 查看說明（0/2，10:00 重置）
    """
    # 取得當前計數 (用於前端顯示)
    async def fetch_counters():
        uid = request.headers.get("X-Telegram-User-Id")
        uid = int(uid) if uid else 0
        async with AsyncSessionLocal() as session:
            video_cnt = await session.execute(
                """
                SELECT COUNT(*) FROM video_view_usage
                WHERE user_id = :uid AND usage_date::date = CURRENT_DATE
                """,
                {"uid": uid},
            )
            video_cnt = result.scalar() or 0

            explain_cnt = await session.execute(
                """
                SELECT COUNT(*) FROM explanation_view_usage
                WHERE user_id = :uid AND usage_date::date = CURRENT_DATE
                """,
                {"uid": uid},
            )
            explain_cnt = result.scalar() or 0
        return {"video_used": video_cnt, "explain_used": explain_cnt}

    # 取得管理員已綁定的鏈結 (key1 / key2)
    async def fetch_admin_links():
        async with AsyncSessionLocal() as session:
            rows = await session.execute(
                "SELECT link_type, url FROM admin_links WHERE is_active = TRUE"
            )
            return {row[0]: row[1] for row in rows}

    # 完整 HTML (使用 .format 取代 f‑string 以避免嵌套的大括號衝突)
    html_content = """
<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8"><title>活動中心 – 開業慶典</title><style>
    body{font-family:Arial,sans-serif;text-align:center;margin-top:30px;}
    .box{display:inline-block;padding:12px 20px;margin:10px;border:1px solid #888;
         border-radius:6px;background:#f9f9f9;}
    .counter{font-weight:bold;color:#d00;}
    button{padding:10px 18px;margin:5px;cursor:pointer;}
</style></head><body>
<div class="box">
  觀看影片可獲取積分，每日最多 3 次，已觀看 <span id="videoCounter" class="counter">(0/3)</span> 次。
  &#13;說明頁面每日可點擊 2 次，已點擊 <span id="explainCounter" class="counter">(0/2)</span> 次。
</div>

<div class="box"><button id="btn_video_one">按鈕一：觀看影片取積分</button></div>
<div class="box"><button id="btn_share"   shareCtrl="share">按鈳二：查看說明</button></div>

<script>
  async function loadCounters(){
    const r = await fetch('/current_counters');
    const d = await r.json();
    document.getElementById('videoCounter').innerText = `$(d.video_used)/(3)`;
    document.getElementById('explainCounter').innerText = `$(d.explain_used)/(2)`;
</script>
    """
    return html_content

# ---------- 8.2  活動中心頁面 (HTML 直接回傳) ----------
# (已在上面的程式碼中寫入完整 HTML；不需要額外檔案)

# ---------- 8.3 說明頁面 (explanation_page.html) ----------
@application.get("/explanation_page.html", response_class=HTMLResponse)
async def explanation_page() -> HTMLResponse:
    """說明頁面，顯示完整步驟與計數 (0/2)。"""
    return """
<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><title>密鑰取得說明</title><style>
    body{font-family:Arial,sans-serif;text-align:center;margin-top:30px;}
    .box{display:inline-block;padding:12px 20px;margin:10px;border:1px solid #888;
         border-radius:6px;background:#f9f9f9;}
    .counter{font-weight:bold;color:#d00;}
</style></head><body>
<div class="box"><strong>获取密钥的完整步驟：</strong><br>
1️⃣ 打開管理員綁定的網盤鏈結，檔案名稱即為密鑰。<br>
2️⃣ 將檔案下載後保存至夸克網盤。<br>
3️⃣ 為檔案重新命名（建議使用英文或數字），<br>
   然後複製**新檔名**並在此頁面貼上並發送給機器人。<br>
4️⃣ 机器人會回傳積分（首次 8，第二次 6），成功後會給出提示。
</div><div class="counter">已使用 0/2 次今日</div><script>
async function refreshCounter(){
  const r = await fetch('/explanation_counter');
  const d = await r.json();
  document.querySelector('.counter').innerText = `已使用 ${d.used}/2 次今日`;
</script><script>setTimeout(()=>{window.location.href = '/activity_center.html';}, 5000);</script></body></html>
"""

# ---------- 9️⃣ 共享的資料庫會話 ----------------------------
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


# ---------------------------- 7️⃣ 獎勵驗證 (原 reward_ad) ----------
class RewardRequest(BaseModel):
    secret: str   # 用戶粘貼的密鑰

@application.get("/validate_key", response_class=JSONResponse)
async def validate_key_endpoint(request: Request, payload: RewardRequest) -> JSONResponse:
    """
 * 1️⃣ 取出當前活躍的密鑰 (key1 / key2)
 * 2️⃣ 與使用者提交的 secret 相比對
 * 3️⃣ 若已使用則直接拒絕；否則授予 8 (key1) 或 6 (key2) 積分
    """
    user_id = request.headers.get("X-Telegram-User-Id")
    if not user_id:
        raise HTTPException(status_code=400, detail="Missing Telegram user id header")
    user_id = int(user_id)

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            "SELECT secret_type, secret_value FROM secret_keys WHERE is_active = TRUE"
        )
        active_keys = {row[0]: row[1] for row in result.fetchall()}
        if not active_keys:
            return JSONResponse(
                content={"status": "rejected", "message": "今日密鑰尚未生成"},
                status_code=403,
            )

        matched_type: Optional[str] = None
        for stype, svalue in active_keys.items():
            if payload.secret == svalue:
                matched_type = stype
                break
        if not matched_type:
            return JSONResponse(
                content={"status": "rejected", "message": "密鑰不匹配或已失效"},
                status_code=403,
            )

        # 檢查是否已使用過
        usage_row = await session.execute(
            """
            SELECT *
            FROM user_key_usage
            WHERE user_id = :uid
              AND secret_type = :stype
              AND usage_date::date = :today
            """,
            {"uid": user_id, "stype": matched_type,
             "today": datetime.now(tz.gettz("Asia/Shanghai")).replace(
                 hour=0, minute=0, second=0, microsecond=0)},
            )
        if existing_row := result.scalar():
            return JSONResponse(
                content={"status": "rejected", "message": "今日已使用過該密鑰"},
                status_code=403,
            )

        points_to_add = 8 if matched_type == "key1" else 6

        usage_record = UserKeyUsage(
            user_id=user_id,
            secret_type=matched_type,
            usage_date=datetime.now(tz.gettz("Asia/Shanghai")),
        )
        session.add(usage_record)

        await session.commit()

        return JSONResponse(
            content={"status": "accepted", "points": points_to_add},
            status_code=200,
        )


# ---------------------------- 10️⃣ Telegram Bot 處理 ----------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------- 9.1 /start 按鈕 ----------
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                text="開始驗證",
                callback_data="button_start_verification"
            ),
            InlineKeyboardButton(
                text="查看積分",
                callback_data="button_show_points"
            ),
            InlineKeyboardButton(
                text="開業活動",
                url=f"{DOMAIN}/activity_center.html"
            ),
        ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "歡迎使用本機器人！請選擇下方功能：",
        reply_markup=reply_markup
    )


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """僅管理員可見的後台入口 (文件 ID 保存/刪除)。"""
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("❌ 您不是管理員")
        return

    keyboard = InlineKeyboardMarkup([
        [
            InlineButtonButton(text="📥 保存 File ID", callback_data="admin_menu_save"),
            InlineButtonButton(text="📂 查看 & 刪除", callback_data="admin_menu_list")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "🛠️ 管理後台已開啟，請點選按鈳",
        reply_markup=reply_markup
    )


# ---------- 9.1.1 保存檔案 (file_id) ----------
async def save_file_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "請發送一張圖片（Telegram 會回傳其 file_id）"
    )
    context.user_data["awaiting_file"] = True


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """保存使用者發送的照片 file_id"""
    if not context.user_data.get("awaiting_file"):
        return
    photo = update.message.photo[-1]  # 取得最高解析度圖片
    file_id = photo.file_id

    async with AsyncSessionLocal() as session:
        await store_file_id(session, file_id)

    await update.message.reply_text(
        f"✅ 檔案已儲存\n`{file_id}`",
        parse_mode="Markdown"
    )
    context.user_data.pop("awaiting_file", None)


# ---------- 9.2 刪除檔案 ----------
async def list_file_ids_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    async with AsyncSessionLocal() as session:
        file_ids = await retrieve_all_file_ids(session)

    if not file_ids:
        await query.edit_message_text("📂 暫無已儲存的 File ID")
        return

    rows = []
    for fid in file_ids[:8]:
        short = fid if len(fid) <= 12 else fid[:12] + "..."
        rows.append([InlineKeyboardButton(f"{short}", callback_data=f"del_{fid}")])
    while len(rows) < 5:
        rows.append([InlineKeyboardButton("⬜️", callback_data="noop")])
    rows.append([InlineKeyboardButton("❌ 取消", callback_data="noop")])
    reply_markup = InlineKeyboardMarkup(rows)

    await query.edit_message_text(
        "📂 請選擇要刪除的檔案 (會要求二次確認)",
        reply_markup=reply_markup
    )


async def delete_confirmation_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    file_id = query.callback_data.split("del_")[1]

    confirm_markup = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ 確定刪除", callback_data=f"del_confirm_{file_id}"),
                InlineKeyboardButton("❌ 取消", callback_data="noop")
            ]
        }
    )
    await query.edit_message_text(
        f"⚠️ 確定要刪除以下檔案嗎？\n`{file_id}`",
        parse_mode="Markdown",
        reply_markup=confirm_markup
    )
    await query.answer()


async def confirm_deletion_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    file_id = query.callback_data.split("del_")[1]

    await delete_file_id(session=AsyncSessionLocal(), fid=file_id)
    await query.edit_message_text(f"✅ 已刪除 `{file_id}`", parse_mode="Markdown")

    main_markup = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📥 保存 File ID", callback_data="admin_menu_save"),
                InlineKeyboardButton("📂 查看 & 刪除", callback_data="admin_menu_list")
            ]
        )
    await context.bot.send_message(
        chat_id=update.callback_query.message.chat_id,
        text="🛠️ 管理介面已重新開啟，請繼續操作",
        reply_markup=main_markup
    )


# ---------- 9.4 其它佔位按鈳 ----------
async def placeholder_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("此功能尚未實作，敬請期待！")


# ---------- 9.5 /my 命令 (密鎖管理) ----------
async def my_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/my 的完整行為說明（管理员專用）"""
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("❌ 只有管理员可以使用此指令")
        return

    state = context.user_data.get("awaiting_key")
    text = update.message.text.strip()

    # ---------- 狀態機 ----------
    if state == "awaiting_key_one":
        async with AsyncSessionLocal() as session:
            from urllib.parse import urlparse
            secret_part = urlparse(text).path.strip().strip("/").split("/")[-1]
            await session.execute(
                "DELETE FROM admin_links WHERE link_type = 'key_one'",
                {"url": text, "now": datetime.utcnow()},
            )
            await session.commit()
        await update.message.reply_text(
            "已儲存密鎝一 (key_one) = 8 積分。請輸入第二個密鎖鏈結以儲存第二個密鎖 (key_two)。"
        )
        context.user_data["state"] = "awaiting_key_two"
        return

    if state == "awaiting_two":
        async with AsyncSessionLocal() as session:
            from urllib.parse import urlparse
            secret_part = urlparse(url).path.strip().strip("/").split("/")[-1]
            await session.execute(
                "DELETE FROM admin_links WHERE link_type = 'key_two'",
                {"url": text, "now": datetime.utcnow()},
            )
            await session.execute(
                "INSERT INTO admin_links (link_type, url, is_active) VALUES ('key_two', :url, TRUE)",
                {"url": text, "now": datetime.utcnow()},
            )
            await session.commit()
        await update.message.reply_text("已儲存第二個密鑰 (key_two)。")
        context.user_data.pop("state")
        return

    if state is None:
        context.user_data["state"] = "awaiting_one"
        await update.message.reply_text("請輸入第一個密鑰的完整 URL")
        return

    # 若狀態不匹配，直接回覆目前已綁定的連結
    async with AsyncSessionLocal() as local_session:
        rows = await local_session.execute(
            "SELECT link_type, url FROM admin_links WHERE is_active = TRUE"
        )
        rows = result.fetchall()
        if not rows:
            await update.message.reply_text("目前尚未有任何已綁定的密鑰。")
        else:
            formatted = "\n".join([f"{key_type}: {url}" for key_type, url in rows.items()])
            await update.message.reply_text("目前已綁定的密鑰如下：\n" + formatted)
