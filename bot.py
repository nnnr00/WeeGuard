# ------------------------------------------------------------
#  bot.py
#  ------------------------------------------------------------
#  This file contains
#   • a Telegram bot (python‑telegram‑bot)
#   • a FastAPI web‑application that serves the MoonTag pages
#   • an APScheduler job that creates two random 10‑character keys
#     every day at 10:00 Asia/Shanghai
#   • all database models (Neon PostgreSQL)
#   • the original admin menu (file‑id storage) – unchanged
#   • the new “开业活动” flow, key‑exchange and point‑awarding logic
# ------------------------------------------------------------

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
    Column,
    DateTime,
    Enum,
    Integer,
    String,
    Text,
    Boolean,
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

# ------------------------------------------------------------
#  1️⃣  Load environment variables
# ------------------------------------------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS_RAW = os.getenv("ADMIN_ID", "")
DATABASE_URL = os.getenv("DATABASE_URL")
SITE_DOMAIN = os.getenv("DOMAIN")               # e.g. https://my‑app.onrailway.app
TIMEZONE = tz.gettz("Asia/Shanghai")

if not (BOT_TOKEN and ADMIN_IDS_RAW and DATABASE_URL and SITE_DOMAIN):
    raise RuntimeError(
        "Missing one of BOT_TOKEN / ADMIN_ID / DATABASE_URL / DOMAIN environment variables"
    )
ADMIN_IDS = [int(x) for x in ADMIN_IDS_RAW.split(",") if x.strip().isdigit()]

# ------------------------------------------------------------
#  2️⃣  SQLAlchemy models (Neon)
# ------------------------------------------------------------
Base = declarative_base()


class FileIDRecord(Base):
    __tablename__ = "file_ids"

    id = Column(Integer, primary_key=True)
    file_id = Column(Text, nullable=False, unique=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class UserAdUsage(Base):
    """
    One row per user per calendar day.
    Stores how many rewarded ads have already been shown and how many points
    have been granted on that day.
    """
    __tablename__ = "user_ad_usage"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, index=True, nullable=False)
    usage_date = Column(DateTime, nullable=False)
    ads_watched_today = Column(Integer, default=0, nullable=False)
    points_granted_today = Column(Integer, default=0, nullable=False)


class SecretKey(Base):
    """
    Stores the two “secret” keys that are generated every day.
    Only one row per secret_type (key1 / key2) is active at a time.
    """
    __tablename__ = "secret_keys"
    __table_args__ = (UniqueConstraint("secret_type", name="uq_secret_type"),)

    id = Column(Integer, primary_key=True)
    secret_type = Column(
        Enum("key1", "key2", name="secret_type_enum"), nullable=False
    )
    secret_value = Column(Text, nullable=False, unique=True)
    is_active = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AdminLink(Base):
    """
    Stores the two full URLs that the admin supplies via /my.
    The URL is only a container – the real “secret” is the last path segment.
    """
    __tablename__ = "admin_links"

    id = Column(Integer, primary_key=True)
    link_type = Column(Enum("key1", "key2", name="link_type_enum"), nullable=False)
    url = Column(Text, nullable=False)
    is_active = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class UserKeyUsage(Base):
    """
    Tracks whether a particular secret key (key1 / key2) has already been
    claimed by a given user on the current calendar day.
    """
    __tablename__ = "user_key_usage"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, index=True, nullable=False)
    secret_type = Column(Enum("key1", "key2", name="secret_type_enum"), nullable=False)
    usage_date = Column(DateTime, nullable=False)

    __table_args__ = (UniqueConstraint("user_id", "secret_type", name="uq_user_type"),)


# ------------------------------------------------------------
#  3️⃣  Async engine & session factory
# ------------------------------------------------------------
engine: AsyncEngine = create_async_engine(
    DATABASE_URL, echo=False, future=True
)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_async_session() -> AsyncSession:
    """Yield a new AsyncSession – used with `async with`."""
    async with AsyncSessionLocal() as session:
        yield session


# ------------------------------------------------------------
#  4️⃣  Helper functions – DB CRUD
# ------------------------------------------------------------
async def store_file_id(session: AsyncSession, fid: str) -> None:
    """Insert a file_id only if it does not exist yet."""
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
    today_start = datetime.now(TIMEZONE).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    result = await session.execute(
        """
        SELECT *
        FROM user_ad_usage
        WHERE user_id = :uid
          AND usage_date::date = :today
        """,
        {"uid": user_id, "today": today_start},
    )
    row = result.fetchone()
    return row[0] if row else None


async def upsert_user_usage(
    session: AsyncSession,
    user_id: int,
    points: int,
    reward_source: str = "rewarded_ad",
) -> None:
    """
    Increment the daily counter for the user.
    If a row does not exist yet, create it.
    """
    today_start = datetime.now(TIMEZONE).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    existing = await session.execute(
        """
        SELECT * FROM user_ad_usage
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


async def generate_random_string(length: int = 10) -> str:
    """Return a random string of the given length using letters and digits."""
    alphabet = string.ascii_letters + string.digits
    return "".join(random.choice(alphabet) for _ in range(length))


async def store_today_secrets(session: AsyncSession) -> None:
    """
    Delete any previously active secrets and insert two brand‑new 10‑character
    secrets (key1 and key2). They are marked as active.
    """
    # Deactivate previous ones
    await session.execute("UPDATE secret_keys SET is_active = FALSE")
    # Generate new secrets
    key1 = await generate_random_string()
    key2 = await generate_random_string()
    # Insert new rows
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


# ------------------------------------------------------------
#  5️⃣  FastAPI application (static files + API endpoints)
# ------------------------------------------------------------
fastapi_app = FastAPI()
fastapi_app.mount(
    "/static",
    StaticFiles(
        directory=os.path.join(os.path.dirname(__file__), "doc")
    ),
    name="static",
)


@fastapi_app.get("/", response_class=HTMLResponse)
async def serve_root_page() -> str:
    """The very first page that the user sees when opening the web‑app."""
    return """
    <html lang="zh-CN">
      <head><meta charset="UTF-8"><title>MoonTag 入口</title></head>
      <body style="text-align:center;margin-top:30px;">
        <div style="margin-bottom:15px;color:#555;">
          正在跳转至奖励视频页面，请稍候…
        </div>
        <script>
          // 1️⃣ 打开 MoonTag 包裹的 rewarded‑ad
          const adUrl = 'https://otieu.com/4/10489957';
          window.location.href = adUrl;

          // 2️⃣ 3 秒后再跳转到真实的密钥链接（会在 admin 命令里更新）
          setTimeout(() => {
            window.location.href = '/key_input.html';
          }, 3000);
        </script>
      </body>
    </html>
    """


@fastapi_app.get("/activity_center", response_class=HTMLResponse)
async def serve_activity_center_page() -> str:
    """
    This URL is opened from the “开业活动” inline button.
    It shows the description, a button “按钮二：获取密钥”, and later
    leads to the key‑input page.
    """
    return """
    <!DOCTYPE html>
    <html lang="zh-CN">
    <head>
      <meta charset="UTF-8">
      <title>活动中心 – 开业庆典</title>
      <style>
        body {font-family:Arial,sans-serif; text-align:center; margin-top:30px;}
        .box {display:inline-block; padding:12px 20px; margin:10px; border:1px solid #888;
              border-radius:6px; background:#f9f9f9;}
        button {padding:10px 18px; margin:5px; cursor:pointer;}
        .counter {font-weight:bold; color:#d00;}
      </style>
    </head>
    <body>
      <div class="box">
        每天可以通过夸克网盘获取密钥。<br>
        需要三秒的跳转，请耐心等候。<br>
        看到文件名后请保存网盘、重命名文件、复制文件名并发送给机器人。<br>
        <span class="counter">（已使用 0/2 次今日）</span>
      </div>

      <button id="btn_get_key" class="box">按钮二：获取密钥</button>

      <script>
        const btn = document.getElementById('btn_get_key');
        btn.onclick = async () => {
          // 显示说明并提供“开始获取密钥”按钮
          const html = \`<div class="box">
            按下下面的按钮即可观看 rewarded 视频并获得积分。\\(0/2\\)已使用\`
            </div>\`;
          alert(html);
          // navigate to the key‑input page
          window.location.href = '/key_input.html';
        };
      </script>
    </body>
    </html>
    """


@fastapi_app.get("/key_input", response_class=HTMLResponse)
async def serve_key_input_page() -> str:
    """Simple page that contains an input field for the secret."""
    return """
    <!DOCTYPE html>
    <html lang="zh-CN">
    <head>
      <meta charset="UTF-8">
      <title>输入密钥</title>
      <style>
        body {font-family:Arial,sans-serif; text-align:center; margin-top:30px;}
        input {width:200px; padding:6px; font-size:14px;}
        button {margin-top:10px; padding:6px 12px;}
        .msg {margin-top:10px; color:#006600;}
      </style>
    </head>
    <body>
      <h2>请粘贴密钥并点击「提交」</h2>
      <input id="keyInput" placeholder="例如：A1b2C3d4E5">
      <br>
      <button onclick="sendKey()">提交</button>
      <div class="msg" id="msgArea"></div>

      <script>
        async function sendKey() {
          const key = document.getElementById('keyInput').value.trim();
          if (!key) { document.getElementById('msgArea').innerText='请输入密钥'; return; }
          const resp = await fetch('/validate_key', {
            method:'POST',
            headers:{'Content-Type':'application/json'},
            body:JSON.stringify({secret:key})
          });
          const data = await resp.json();
          document.getElementById('msgArea').innerText = data.message;
          if (data.status === 'accepted') {
            alert('恭喜！你获得了 ' + data.points + ' 积分');
            // 更新计数器（这里直接刷新页面以简化演示）
            location.reload();
          }
        }
      </script>
    </body>
    </html>
    """


class RewardRequest(BaseModel):
    secret: str   # the 10‑character key that the user pastes


@fastapi_app.post("/validate_key", status_code=status.HTTP_200_OK)
async def validate_key_endpoint(request: Request, payload: RewardRequest) -> JSONResponse:
    """
    Called by the key‑input page after the user submits a secret.
    The logic:
      • The secret must match today's key1 or key2.
      • The user must not have used that secret type already today.
      • 1st secret → 8 points, 2nd secret → 6 points.
      • If the secret is valid, points are added and the usage flag is set.
    """
    user_id = request.headers.get("X-Telegram-User-Id")
    if not user_id:
        raise HTTPException(status_code=400, detail="Missing Telegram user id header")
    user_id = int(user_id)

    async with AsyncSessionLocal() as session:
        # fetch today's active secrets
        result = await session.execute(
            "SELECT secret_type, secret_value FROM secret_keys WHERE is_active = TRUE"
        )
        active = result.fetchall()
        secret_map: Dict[str, str] = {row[0]: row[1] for row in active}
        if not secret_map:
            return JSONResponse(
                content={"status": "rejected", "message": "今日密钥尚未生成"},
                status_code=403,
            )

        # find which secret_type (if any) matches the payload
        matched_type: Optional[str] = None
        for stype, svalue in secret_map.items():
            if payload.secret == svalue:
                matched_type = stype
                break

        if not matched_type:
            return JSONResponse(
                content={"status": "rejected", "message": "密钥不匹配或已失效"},
                status_code=403,
            )

        # check if the user has already used this secret_type today
        usage_row = await session.execute(
            """
            SELECT * FROM user_key_usage
            WHERE user_id = :uid
              AND secret_type = :stype
              AND usage_date::date = :today
            """,
            {"uid": user_id, "stype": matched_type, "today": datetime.now(TIMEZONE).replace(
                hour=0, minute=0, second=0, microsecond=0
            )},
        )
        if usage_row.scalar():
            return JSONResponse(
                content={"status": "rejected", "message": "今日已使用过该密钥"},
                status_code=403,
            )

        # award points
        points_to_add = 8 if matched_type == "key1" else 6

        await upsert_user_usage(session, user_id, points_to_add, reward_source="key_claim")
        # mark usage
        usage_record = UserKeyUsage(
            user_id=user_id,
            secret_type=matched_type,
            usage_date=datetime.now(TIMEZONE),
        )
        session.add(usage_record)
        await session.commit()

        return JSONResponse(
            content={"status": "accepted", "points": points_to_add},
            status_code=200,
        )


# ------------------------------------------------------------
# 6️⃣  Telegram‑bot handlers (original admin + new MoonTag flow)
# ------------------------------------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ------------------------------------------------------------
# 6.1  /start – three inline buttons
# ------------------------------------------------------------
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    The very first message the user sees.
    Three buttons:
      • 开始验证
      • 查看积分
      • 开业活动   (opens the activity‑center page)
    """
    keyboard = [
        [
            InlineKeyboardButton(
                text="开始验证",
                callback_data="button_start_verification"
            ),
            InlineKeyboardButton(
                text="查看积分",
                callback_data="button_show_points"
            ),
            InlineKeyboardButton(
                text="开业活动",
                url=f"{SITE_DOMAIN}/activity_center"
            ),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "欢迎使用本机器人！请选择下面的功能：",
        reply_markup=reply_markup
    )


# ------------------------------------------------------------
# 6.2  /admin – unchanged admin menu (file‑id storage)
# ------------------------------------------------------------
async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("❌ 您不是管理员")
        return

    keyboard = [
        [
            InlineKeyboardButton("📥 保存 File ID", callback_data="admin_menu_save"),
            InlineKeyboardButton("📂 查看 & 删除", callback_data="admin_menu_list"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "🛠️ 管理后台已打开，请点按钮",
        reply_markup=reply_markup
    )


# ------------------------------------------------------------
# 6.3  Save‑file‑id flow (unchanged)
# ------------------------------------------------------------
async def cb_save_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "请发送一张图片（Telegram 会返回其 file_id）"
    )
    context.user_data["awaiting_file"] = True


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Store the received photo's file_id into Neon."""
    if not context.user_data.get("awaiting_file"):
        return
    # highest‑resolution version
    photo = update.message.photo[-1]
    file_id = photo.file_id

    async with AsyncSessionLocal() as session:
        await store_file_id(session, file_id)

    await update.message.reply_text(
        f"✅ 文件已保存\n`{file_id}`",
        parse_mode="Markdown"
    )
    context.user_data.pop("awaiting_file", None)


async def admin_menu_list_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the list of stored file_ids and allow deletion."""
    query = update.callback_query
    await query.answer()
    async with AsyncSessionLocal() as session:
        ids = await retrieve_all_file_ids(session)

    if not ids:
        await query.edit_message_text("📂 暂无已保存的 File ID")
        return

    recent = ids[:8]

    rows = []
    for fid in recent:
        short = fid if len(fid) <= 12 else fid[:12] + "..."
        rows.append([InlineKeyboardButton(f"{short}", callback_data=f"del_{fid}")])
    # Pad up to 5 rows
    while len(rows) < 5:
        rows.append([InlineKeyboardButton("⬜️", callback_data="noop")])

    rows.append([InlineKeyboardButton("❌ 取消", callback_data="noop")])
    reply_markup = InlineKeyboardMarkup(rows)

    await query.edit_message_text(
        "📂 请选择要删除的记录（会要求二次确认）",
        reply_markup=reply_markup
    )


async def admin_menu_delete_confirmation_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Second‑level confirmation before deletion."""
    query = update.callback_query
    await query.answer()
    fid = query.data.split("_", 1)[1]   # format: del_<file_id>

    confirm_kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    text="✅ 确认删除",
                    callback_data=f"confirm_del_{fid}"
                ),
                InlineKeyboardButton(
                    text="❎ 取消",
                    callback_data="noop"
                ),
            ]
        ]
    )
    await query.edit_message_text(
        f"⚠️ 确定要删除以下记录吗？\n`{fid}`",
        parse_mode="Markdown",
        reply_markup=confirm_kb
    )


async def confirm_deletion_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Execute the deletion and return to the main admin menu."""
    query = update.callback_query
    await query.answer()
    fid = query.data.split("_", 1)[1]   # format: confirm_del_<file_id>

    async with AsyncSessionLocal() as session:
        await delete_file_id(session, fid)

    await query.edit_message_text(f"✅ 已删除 `{fid}`", parse_mode="Markdown")

    main_kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📥 保存 File ID", callback_data="admin_menu_save"),
                InlineKeyboardButton("📂 查看 & 删除", callback_data="admin_menu_list"),
            ]
        ]
    )
    await context.bot.send_message(
        chat_id=query.message.chat.id,
        text="🛠️ 管理后台已重新打开，请继续操作",
        reply_markup=main_kb
    )


# ------------------------------------------------------------
# 6.4  Button‑press callbacks that belong to the *new* MoonTag flow
# ------------------------------------------------------------
async def handle_start_verification_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Placeholder for the “开始验证” button – currently does nothing."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("此功能尚未实现，敬请期待！")


async def handle_show_points_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Placeholder for the “查看积分” button – currently does nothing."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("积分查询功能正在开发中，稍后加入！")


# ------------------------------------------------------------
# 6.5  /my – admin‑only key‑link manager
# ------------------------------------------------------------
async def my_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Admin command with three behaviours:
      1️⃣ If the command is issued alone → reply with today’s two secret values.
      2️⃣ If the user has previously entered the “awaiting_key_input” state,
          we store the first or second link accordingly.
      3️⃣ State machine:
          • /my  → “请输入密钥一链接”
          • next message → store as key1 link
          • next message → “请输入密钥二链接”
          • next message → store as key2 link and finish.
    """
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("❌ 只有管理员可以使用此命令")
        return

    # --------------------------------------------------------
    #  State handling – store in user_data
    # --------------------------------------------------------
    state = context.user_data.get("my_state")
    text = update.message.text.strip()
    if not text:
        await update.message.reply_text(
            "⚠️ 请在 /my 之后发送完整的文字（链接）"
        )
        return

    if state == "awaiting_key1":
        # store key1 link
        async with AsyncSessionLocal() as session:
            # extract the last path segment – that will be the secret value
            from urllib.parse import urlparse
            parsed = urlparse(text)
            secret_part = parsed.path.rstrip("/").split("/")[-1]
            # store or replace
            await session.execute(
                "DELETE FROM admin_links WHERE link_type = 'key1'"
            )
            await session.execute(
                """
                INSERT INTO admin_links (link_type, url, is_active)
                VALUES ('key1', :url, TRUE)
                """,
                {"url": text, "now": datetime.utcnow()},
            )
            await session.commit()
        await update.message.reply_text(
            "密钥一链接已保存。为您准备第二个链接：请输入密钥二链接"
        )
        context.user_data["my_state"] = "awaiting_key2"
        return

    if state == "awaiting_key2":
        async with AsyncSessionLocal() as session:
            from urllib.parse import urlparse
            parsed = urlparse(text)
            secret_part = parsed.path.rstrip("/").split("/")[-1]
            await session.execute(
                "DELETE FROM admin_links WHERE link_type = 'key2'"
            )
            await session.execute(
                """
                INSERT INTO admin_links (link_type, url, is_active)
                VALUES ('key2', :url, TRUE)
                """,
                {"url": text, "now": datetime.utcnow()},
            )
            await session.commit()
        await update.message.reply_text("密钥二链接已保存，绑定完成。")
        context.user_data.pop("my_state")
        return

    # --------------------------------------------------------
    #  Default handling – either view current keys or start a new round
    # --------------------------------------------------------
    if state is None:
        # No ongoing state → treat this /my as a “view / start new round”
        context.user_data["my_state"] = "awaiting_key1"
        await update.message.reply_text("请输入密钥一链接")
        return

    # Fallback – just reply with the current links
    async with AsyncSessionLocal() as session:
        rows = await session.execute(
            """
            SELECT link_type, url FROM admin_links WHERE is_active = TRUE
            """
        )
        links = {row[0]: row[1] for row in rows}
    if not links:
        await update.message.reply_text("暂无已绑定的密钥链接。")
    else:
        formatted = "\n".join([f"{ltype}: {links[ltype]}" for ltype in sorted(links.keys())])
        await update.message.reply_text("当前已绑定的密钥链接：\n" + formatted)


# ------------------------------------------------------------
# 6.6  Register every handler with the Application
# ------------------------------------------------------------
def register_handlers(app: Application) -> None:
    # Basic commands
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CommandHandler("my", my_command))

    # Placeholder buttons from /start
    app.add_handler(CallbackQueryHandler(handle_start_verification_button, pattern="^button_start_verification$"))
    app.add_handler(CallbackQueryHandler(handle_show_points_button, pattern="^button_show_points$"))

    # Admin‑menu callbacks (unchanged)
    app.add_handler(CallbackQueryHandler(cb_save_button, pattern="^admin_menu_save$"))
    app.add_handler(MessageHandler(filters.PHOTO & filters.UpdateContext(user_data={"awaiting_file": True}), handle_photo))
    app.add_handler(CallbackQueryHandler(admin_menu_list_button, pattern="^admin_menu_list$"))
    app.add_handler(CallbackQueryHandler(admin_menu_delete_confirmation_button, pattern="^del_"))
    app.add_handler(CallbackQueryHandler(confirm_deletion_button, pattern="^confirm_del_"))

    # MoonTag‑specific callbacks
    app.add_handler(CallbackQueryHandler(handle_start_verification_button, pattern="^menu_start_verification$"))
    app.add_handler(CallbackQueryHandler(handle_show_points_button, pattern="^menu_show_points$"))

    # Admin‑only secret‑link manager
    app.add_handler(CallbackQueryHandler(my_command, pattern="^admin_menu_"))


# ------------------------------------------------------------
# 7️⃣  Scheduler – generate new secrets every day at 10:00 Asia/Shanghai
# ------------------------------------------------------------
from apscheduler.schedulers.asyncio import AsyncIOScheduler

scheduler = AsyncIOScheduler()
scheduler.add_job(
    func=store_today_secrets,
    trigger="cron",
    hour=10,
    minute=0,
    timezone="Asia/Shanghai",
    id="daily_secret_generator",
)
scheduler.start()


# ------------------------------------------------------------
# 8️⃣  Main entry point – run both the Telegram bot and FastAPI
# ------------------------------------------------------------
async def main() -> None:
    """
    Starts:
      • the Telegram bot (polling)
      • the FastAPI server (uvicorn) on port 8000
    Both run forever until an unhandled exception occurs.
    """
    # Register all handlers before building the Application object
    register_handlers(app=Application.builder().token(BOT_TOKEN).build())

    # ----------------------------------------------------
    # 8.1  Start FastAPI via uvicorn
    # ----------------------------------------------------
    import uvicorn

    uvicorn_config = uvicorn.Config(fastapi_app, host="0.0.0.0", port=8000)
    server = uvicorn.Server(uvicorn_config)

    # ----------------------------------------------------
    # 8.2  Run both coroutines concurrently
    # ----------------------------------------------------
    bot_task = asyncio.create_task(app.run_polling())
    server_task = asyncio.create_task(server.serve())

    await asyncio.gather(bot_task, server_task)


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())# ------------------------------------------------------------
#  bot.py
#  ------------------------------------------------------------
#  This file contains
#   • a Telegram bot (python‑telegram‑bot)
#   • a FastAPI web‑application that serves the MoonTag pages
#   • an APScheduler job that creates two random 10‑character keys
#     every day at 10:00 Asia/Shanghai
#   • all database models (Neon PostgreSQL)
#   • the original admin menu (file‑id storage) – unchanged
#   • the new “开业活动” flow, key‑exchange and point‑awarding logic
# ------------------------------------------------------------

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
    Column,
    DateTime,
    Enum,
    Integer,
    String,
    Text,
    Boolean,
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

# ------------------------------------------------------------
#  1️⃣  Load environment variables
# ------------------------------------------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS_RAW = os.getenv("ADMIN_ID", "")
DATABASE_URL = os.getenv("DATABASE_URL")
SITE_DOMAIN = os.getenv("DOMAIN")               # e.g. https://my‑app.onrailway.app
TIMEZONE = tz.gettz("Asia/Shanghai")

if not (BOT_TOKEN and ADMIN_IDS_RAW and DATABASE_URL and SITE_DOMAIN):
    raise RuntimeError(
        "Missing one of BOT_TOKEN / ADMIN_ID / DATABASE_URL / DOMAIN environment variables"
    )
ADMIN_IDS = [int(x) for x in ADMIN_IDS_RAW.split(",") if x.strip().isdigit()]

# ------------------------------------------------------------
#  2️⃣  SQLAlchemy models (Neon)
# ------------------------------------------------------------
Base = declarative_base()


class FileIDRecord(Base):
    __tablename__ = "file_ids"

    id = Column(Integer, primary_key=True)
    file_id = Column(Text, nullable=False, unique=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class UserAdUsage(Base):
    """
    One row per user per calendar day.
    Stores how many rewarded ads have already been shown and how many points
    have been granted on that day.
    """
    __tablename__ = "user_ad_usage"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, index=True, nullable=False)
    usage_date = Column(DateTime, nullable=False)
    ads_watched_today = Column(Integer, default=0, nullable=False)
    points_granted_today = Column(Integer, default=0, nullable=False)


class SecretKey(Base):
    """
    Stores the two “secret” keys that are generated every day.
    Only one row per secret_type (key1 / key2) is active at a time.
    """
    __tablename__ = "secret_keys"
    __table_args__ = (UniqueConstraint("secret_type", name="uq_secret_type"),)

    id = Column(Integer, primary_key=True)
    secret_type = Column(
        Enum("key1", "key2", name="secret_type_enum"), nullable=False
    )
    secret_value = Column(Text, nullable=False, unique=True)
    is_active = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AdminLink(Base):
    """
    Stores the two full URLs that the admin supplies via /my.
    The URL is only a container – the real “secret” is the last path segment.
    """
    __tablename__ = "admin_links"

    id = Column(Integer, primary_key=True)
    link_type = Column(Enum("key1", "key2", name="link_type_enum"), nullable=False)
    url = Column(Text, nullable=False)
    is_active = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class UserKeyUsage(Base):
    """
    Tracks whether a particular secret key (key1 / key2) has already been
    claimed by a given user on the current calendar day.
    """
    __tablename__ = "user_key_usage"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, index=True, nullable=False)
    secret_type = Column(Enum("key1", "key2", name="secret_type_enum"), nullable=False)
    usage_date = Column(DateTime, nullable=False)

    __table_args__ = (UniqueConstraint("user_id", "secret_type", name="uq_user_type"),)


# ------------------------------------------------------------
#  3️⃣  Async engine & session factory
# ------------------------------------------------------------
engine: AsyncEngine = create_async_engine(
    DATABASE_URL, echo=False, future=True
)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_async_session() -> AsyncSession:
    """Yield a new AsyncSession – used with `async with`."""
    async with AsyncSessionLocal() as session:
        yield session


# ------------------------------------------------------------
#  4️⃣  Helper functions – DB CRUD
# ------------------------------------------------------------
async def store_file_id(session: AsyncSession, fid: str) -> None:
    """Insert a file_id only if it does not exist yet."""
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
    today_start = datetime.now(TIMEZONE).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    result = await session.execute(
        """
        SELECT *
        FROM user_ad_usage
        WHERE user_id = :uid
          AND usage_date::date = :today
        """,
        {"uid": user_id, "today": today_start},
    )
    row = result.fetchone()
    return row[0] if row else None


async def upsert_user_usage(
    session: AsyncSession,
    user_id: int,
    points: int,
    reward_source: str = "rewarded_ad",
) -> None:
    """
    Increment the daily counter for the user.
    If a row does not exist yet, create it.
    """
    today_start = datetime.now(TIMEZONE).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    existing = await session.execute(
        """
        SELECT * FROM user_ad_usage
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


async def generate_random_string(length: int = 10) -> str:
    """Return a random string of the given length using letters and digits."""
    alphabet = string.ascii_letters + string.digits
    return "".join(random.choice(alphabet) for _ in range(length))


async def store_today_secrets(session: AsyncSession) -> None:
    """
    Delete any previously active secrets and insert two brand‑new 10‑character
    secrets (key1 and key2). They are marked as active.
    """
    # Deactivate previous ones
    await session.execute("UPDATE secret_keys SET is_active = FALSE")
    # Generate new secrets
    key1 = await generate_random_string()
    key2 = await generate_random_string()
    # Insert new rows
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


# ------------------------------------------------------------
#  5️⃣  FastAPI application (static files + API endpoints)
# ------------------------------------------------------------
fastapi_app = FastAPI()
fastapi_app.mount(
    "/static",
    StaticFiles(
        directory=os.path.join(os.path.dirname(__file__), "doc")
    ),
    name="static",
)


@fastapi_app.get("/", response_class=HTMLResponse)
async def serve_root_page() -> str:
    """The very first page that the user sees when opening the web‑app."""
    return """
    <html lang="zh-CN">
      <head><meta charset="UTF-8"><title>MoonTag 入口</title></head>
      <body style="text-align:center;margin-top:30px;">
        <div style="margin-bottom:15px;color:#555;">
          正在跳转至奖励视频页面，请稍候…
        </div>
        <script>
          // 1️⃣ 打开 MoonTag 包裹的 rewarded‑ad
          const adUrl = 'https://otieu.com/4/10489957';
          window.location.href = adUrl;

          // 2️⃣ 3 秒后再跳转到真实的密钥链接（会在 admin 命令里更新）
          setTimeout(() => {
            window.location.href = '/key_input.html';
          }, 3000);
        </script>
      </body>
    </html>
    """


@fastapi_app.get("/activity_center", response_class=HTMLResponse)
async def serve_activity_center_page() -> str:
    """
    This URL is opened from the “开业活动” inline button.
    It shows the description, a button “按钮二：获取密钥”, and later
    leads to the key‑input page.
    """
    return """
    <!DOCTYPE html>
    <html lang="zh-CN">
    <head>
      <meta charset="UTF-8">
      <title>活动中心 – 开业庆典</title>
      <style>
        body {font-family:Arial,sans-serif; text-align:center; margin-top:30px;}
        .box {display:inline-block; padding:12px 20px; margin:10px; border:1px solid #888;
              border-radius:6px; background:#f9f9f9;}
        button {padding:10px 18px; margin:5px; cursor:pointer;}
        .counter {font-weight:bold; color:#d00;}
      </style>
    </head>
    <body>
      <div class="box">
        每天可以通过夸克网盘获取密钥。<br>
        需要三秒的跳转，请耐心等候。<br>
        看到文件名后请保存网盘、重命名文件、复制文件名并发送给机器人。<br>
        <span class="counter">（已使用 0/2 次今日）</span>
      </div>

      <button id="btn_get_key" class="box">按钮二：获取密钥</button>

      <script>
        const btn = document.getElementById('btn_get_key');
        btn.onclick = async () => {
          // 显示说明并提供“开始获取密钥”按钮
          const html = \`<div class="box">
            按下下面的按钮即可观看 rewarded 视频并获得积分。\\(0/2\\)已使用\`
            </div>\`;
          alert(html);
          // navigate to the key‑input page
          window.location.href = '/key_input.html';
        };
      </script>
    </body>
    </html>
    """


@fastapi_app.get("/key_input", response_class=HTMLResponse)
async def serve_key_input_page() -> str:
    """Simple page that contains an input field for the secret."""
    return """
    <!DOCTYPE html>
    <html lang="zh-CN">
    <head>
      <meta charset="UTF-8">
      <title>输入密钥</title>
      <style>
        body {font-family:Arial,sans-serif; text-align:center; margin-top:30px;}
        input {width:200px; padding:6px; font-size:14px;}
        button {margin-top:10px; padding:6px 12px;}
        .msg {margin-top:10px; color:#006600;}
      </style>
    </head>
    <body>
      <h2>请粘贴密钥并点击「提交」</h2>
      <input id="keyInput" placeholder="例如：A1b2C3d4E5">
      <br>
      <button onclick="sendKey()">提交</button>
      <div class="msg" id="msgArea"></div>

      <script>
        async function sendKey() {
          const key = document.getElementById('keyInput').value.trim();
          if (!key) { document.getElementById('msgArea').innerText='请输入密钥'; return; }
          const resp = await fetch('/validate_key', {
            method:'POST',
            headers:{'Content-Type':'application/json'},
            body:JSON.stringify({secret:key})
          });
          const data = await resp.json();
          document.getElementById('msgArea').innerText = data.message;
          if (data.status === 'accepted') {
            alert('恭喜！你获得了 ' + data.points + ' 积分');
            // 更新计数器（这里直接刷新页面以简化演示）
            location.reload();
          }
        }
      </script>
    </body>
    </html>
    """


class RewardRequest(BaseModel):
    secret: str   # the 10‑character key that the user pastes


@fastapi_app.post("/validate_key", status_code=status.HTTP_200_OK)
async def validate_key_endpoint(request: Request, payload: RewardRequest) -> JSONResponse:
    """
    Called by the key‑input page after the user submits a secret.
    The logic:
      • The secret must match today's key1 or key2.
      • The user must not have used that secret type already today.
      • 1st secret → 8 points, 2nd secret → 6 points.
      • If the secret is valid, points are added and the usage flag is set.
    """
    user_id = request.headers.get("X-Telegram-User-Id")
    if not user_id:
        raise HTTPException(status_code=400, detail="Missing Telegram user id header")
    user_id = int(user_id)

    async with AsyncSessionLocal() as session:
        # fetch today's active secrets
        result = await session.execute(
            "SELECT secret_type, secret_value FROM secret_keys WHERE is_active = TRUE"
        )
        active = result.fetchall()
        secret_map: Dict[str, str] = {row[0]: row[1] for row in active}
        if not secret_map:
            return JSONResponse(
                content={"status": "rejected", "message": "今日密钥尚未生成"},
                status_code=403,
            )

        # find which secret_type (if any) matches the payload
        matched_type: Optional[str] = None
        for stype, svalue in secret_map.items():
            if payload.secret == svalue:
                matched_type = stype
                break

        if not matched_type:
            return JSONResponse(
                content={"status": "rejected", "message": "密钥不匹配或已失效"},
                status_code=403,
            )

        # check if the user has already used this secret_type today
        usage_row = await session.execute(
            """
            SELECT * FROM user_key_usage
            WHERE user_id = :uid
              AND secret_type = :stype
              AND usage_date::date = :today
            """,
            {"uid": user_id, "stype": matched_type, "today": datetime.now(TIMEZONE).replace(
                hour=0, minute=0, second=0, microsecond=0
            )},
        )
        if usage_row.scalar():
            return JSONResponse(
                content={"status": "rejected", "message": "今日已使用过该密钥"},
                status_code=403,
            )

        # award points
        points_to_add = 8 if matched_type == "key1" else 6

        await upsert_user_usage(session, user_id, points_to_add, reward_source="key_claim")
        # mark usage
        usage_record = UserKeyUsage(
            user_id=user_id,
            secret_type=matched_type,
            usage_date=datetime.now(TIMEZONE),
        )
        session.add(usage_record)
        await session.commit()

        return JSONResponse(
            content={"status": "accepted", "points": points_to_add},
            status_code=200,
        )


# ------------------------------------------------------------
# 6️⃣  Telegram‑bot handlers (original admin + new MoonTag flow)
# ------------------------------------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ------------------------------------------------------------
# 6.1  /start – three inline buttons
# ------------------------------------------------------------
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    The very first message the user sees.
    Three buttons:
      • 开始验证
      • 查看积分
      • 开业活动   (opens the activity‑center page)
    """
    keyboard = [
        [
            InlineKeyboardButton(
                text="开始验证",
                callback_data="button_start_verification"
            ),
            InlineKeyboardButton(
                text="查看积分",
                callback_data="button_show_points"
            ),
            InlineKeyboardButton(
                text="开业活动",
                url=f"{SITE_DOMAIN}/activity_center"
            ),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "欢迎使用本机器人！请选择下面的功能：",
        reply_markup=reply_markup
    )


# ------------------------------------------------------------
# 6.2  /admin – unchanged admin menu (file‑id storage)
# ------------------------------------------------------------
async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("❌ 您不是管理员")
        return

    keyboard = [
        [
            InlineKeyboardButton("📥 保存 File ID", callback_data="admin_menu_save"),
            InlineKeyboardButton("📂 查看 & 删除", callback_data="admin_menu_list"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "🛠️ 管理后台已打开，请点按钮",
        reply_markup=reply_markup
    )


# ------------------------------------------------------------
# 6.3  Save‑file‑id flow (unchanged)
# ------------------------------------------------------------
async def cb_save_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "请发送一张图片（Telegram 会返回其 file_id）"
    )
    context.user_data["awaiting_file"] = True


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Store the received photo's file_id into Neon."""
    if not context.user_data.get("awaiting_file"):
        return
    # highest‑resolution version
    photo = update.message.photo[-1]
    file_id = photo.file_id

    async with AsyncSessionLocal() as session:
        await store_file_id(session, file_id)

    await update.message.reply_text(
        f"✅ 文件已保存\n`{file_id}`",
        parse_mode="Markdown"
    )
    context.user_data.pop("awaiting_file", None)


async def admin_menu_list_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the list of stored file_ids and allow deletion."""
    query = update.callback_query
    await query.answer()
    async with AsyncSessionLocal() as session:
        ids = await retrieve_all_file_ids(session)

    if not ids:
        await query.edit_message_text("📂 暂无已保存的 File ID")
        return

    recent = ids[:8]

    rows = []
    for fid in recent:
        short = fid if len(fid) <= 12 else fid[:12] + "..."
        rows.append([InlineKeyboardButton(f"{short}", callback_data=f"del_{fid}")])
    # Pad up to 5 rows
    while len(rows) < 5:
        rows.append([InlineKeyboardButton("⬜️", callback_data="noop")])

    rows.append([InlineKeyboardButton("❌ 取消", callback_data="noop")])
    reply_markup = InlineKeyboardMarkup(rows)

    await query.edit_message_text(
        "📂 请选择要删除的记录（会要求二次确认）",
        reply_markup=reply_markup
    )


async def admin_menu_delete_confirmation_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Second‑level confirmation before deletion."""
    query = update.callback_query
    await query.answer()
    fid = query.data.split("_", 1)[1]   # format: del_<file_id>

    confirm_kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    text="✅ 确认删除",
                    callback_data=f"confirm_del_{fid}"
                ),
                InlineKeyboardButton(
                    text="❎ 取消",
                    callback_data="noop"
                ),
            ]
        ]
    )
    await query.edit_message_text(
        f"⚠️ 确定要删除以下记录吗？\n`{fid}`",
        parse_mode="Markdown",
        reply_markup=confirm_kb
    )


async def confirm_deletion_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Execute the deletion and return to the main admin menu."""
    query = update.callback_query
    await query.answer()
    fid = query.data.split("_", 1)[1]   # format: confirm_del_<file_id>

    async with AsyncSessionLocal() as session:
        await delete_file_id(session, fid)

    await query.edit_message_text(f"✅ 已删除 `{fid}`", parse_mode="Markdown")

    main_kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📥 保存 File ID", callback_data="admin_menu_save"),
                InlineKeyboardButton("📂 查看 & 删除", callback_data="admin_menu_list"),
            ]
        ]
    )
    await context.bot.send_message(
        chat_id=query.message.chat.id,
        text="🛠️ 管理后台已重新打开，请继续操作",
        reply_markup=main_kb
    )


# ------------------------------------------------------------
# 6.4  Button‑press callbacks that belong to the *new* MoonTag flow
# ------------------------------------------------------------
async def handle_start_verification_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Placeholder for the “开始验证” button – currently does nothing."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("此功能尚未实现，敬请期待！")


async def handle_show_points_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Placeholder for the “查看积分” button – currently does nothing."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("积分查询功能正在开发中，稍后加入！")


# ------------------------------------------------------------
# 6.5  /my – admin‑only key‑link manager
# ------------------------------------------------------------
async def my_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Admin command with three behaviours:
      1️⃣ If the command is issued alone → reply with today’s two secret values.
      2️⃣ If the user has previously entered the “awaiting_key_input” state,
          we store the first or second link accordingly.
      3️⃣ State machine:
          • /my  → “请输入密钥一链接”
          • next message → store as key1 link
          • next message → “请输入密钥二链接”
          • next message → store as key2 link and finish.
    """
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("❌ 只有管理员可以使用此命令")
        return

    # --------------------------------------------------------
    #  State handling – store in user_data
    # --------------------------------------------------------
    state = context.user_data.get("my_state")
    text = update.message.text.strip()
    if not text:
        await update.message.reply_text(
            "⚠️ 请在 /my 之后发送完整的文字（链接）"
        )
        return

    if state == "awaiting_key1":
        # store key1 link
        async with AsyncSessionLocal() as session:
            # extract the last path segment – that will be the secret value
            from urllib.parse import urlparse
            parsed = urlparse(text)
            secret_part = parsed.path.rstrip("/").split("/")[-1]
            # store or replace
            await session.execute(
                "DELETE FROM admin_links WHERE link_type = 'key1'"
            )
            await session.execute(
                """
                INSERT INTO admin_links (link_type, url, is_active)
                VALUES ('key1', :url, TRUE)
                """,
                {"url": text, "now": datetime.utcnow()},
            )
            await session.commit()
        await update.message.reply_text(
            "密钥一链接已保存。为您准备第二个链接：请输入密钥二链接"
        )
        context.user_data["my_state"] = "awaiting_key2"
        return

    if state == "awaiting_key2":
        async with AsyncSessionLocal() as session:
            from urllib.parse import urlparse
            parsed = urlparse(text)
            secret_part = parsed.path.rstrip("/").split("/")[-1]
            await session.execute(
                "DELETE FROM admin_links WHERE link_type = 'key2'"
            )
            await session.execute(
                """
                INSERT INTO admin_links (link_type, url, is_active)
                VALUES ('key2', :url, TRUE)
                """,
                {"url": text, "now": datetime.utcnow()},
            )
            await session.commit()
        await update.message.reply_text("密钥二链接已保存，绑定完成。")
        context.user_data.pop("my_state")
        return

    # --------------------------------------------------------
    #  Default handling – either view current keys or start a new round
    # --------------------------------------------------------
    if state is None:
        # No ongoing state → treat this /my as a “view / start new round”
        context.user_data["my_state"] = "awaiting_key1"
        await update.message.reply_text("请输入密钥一链接")
        return

    # Fallback – just reply with the current links
    async with AsyncSessionLocal() as session:
        rows = await session.execute(
            """
            SELECT link_type, url FROM admin_links WHERE is_active = TRUE
            """
        )
        links = {row[0]: row[1] for row in rows}
    if not links:
        await update.message.reply_text("暂无已绑定的密钥链接。")
    else:
        formatted = "\n".join([f"{ltype}: {links[ltype]}" for ltype in sorted(links.keys())])
        await update.message.reply_text("当前已绑定的密钥链接：\n" + formatted)


# ------------------------------------------------------------
# 6.6  Register every handler with the Application
# ------------------------------------------------------------
def register_handlers(app: Application) -> None:
    # Basic commands
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CommandHandler("my", my_command))

    # Placeholder buttons from /start
    app.add_handler(CallbackQueryHandler(handle_start_verification_button, pattern="^button_start_verification$"))
    app.add_handler(CallbackQueryHandler(handle_show_points_button, pattern="^button_show_points$"))

    # Admin‑menu callbacks (unchanged)
    app.add_handler(CallbackQueryHandler(cb_save_button, pattern="^admin_menu_save$"))
    app.add_handler(MessageHandler(filters.PHOTO & filters.UpdateContext(user_data={"awaiting_file": True}), handle_photo))
    app.add_handler(CallbackQueryHandler(admin_menu_list_button, pattern="^admin_menu_list$"))
    app.add_handler(CallbackQueryHandler(admin_menu_delete_confirmation_button, pattern="^del_"))
    app.add_handler(CallbackQueryHandler(confirm_deletion_button, pattern="^confirm_del_"))

    # MoonTag‑specific callbacks
    app.add_handler(CallbackQueryHandler(handle_start_verification_button, pattern="^menu_start_verification$"))
    app.add_handler(CallbackQueryHandler(handle_show_points_button, pattern="^menu_show_points$"))

    # Admin‑only secret‑link manager
    app.add_handler(CallbackQueryHandler(my_command, pattern="^admin_menu_"))


# ------------------------------------------------------------
# 7️⃣  Scheduler – generate new secrets every day at 10:00 Asia/Shanghai
# ------------------------------------------------------------
from apscheduler.schedulers.asyncio import AsyncIOScheduler

scheduler = AsyncIOScheduler()
scheduler.add_job(
    func=store_today_secrets,
    trigger="cron",
    hour=10,
    minute=0,
    timezone="Asia/Shanghai",
    id="daily_secret_generator",
)
scheduler.start()


# ------------------------------------------------------------
# 8️⃣  Main entry point – run both the Telegram bot and FastAPI
# ------------------------------------------------------------
async def main() -> None:
    """
    Starts:
      • the Telegram bot (polling)
      • the FastAPI server (uvicorn) on port 8000
    Both run forever until an unhandled exception occurs.
    """
    # Register all handlers before building the Application object
    register_handlers(app=Application.builder().token(BOT_TOKEN).build())

    # ----------------------------------------------------
    # 8.1  Start FastAPI via uvicorn
    # ----------------------------------------------------
    import uvicorn

    uvicorn_config = uvicorn.Config(fastapi_app, host="0.0.0.0", port=8000)
    server = uvicorn.Server(uvicorn_config)

    # ----------------------------------------------------
    # 8.2  Run both coroutines concurrently
    # ----------------------------------------------------
    bot_task = asyncio.create_task(app.run_polling())
    server_task = asyncio.create_task(server.serve())

    await asyncio.gather(bot_task, server_task)


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
