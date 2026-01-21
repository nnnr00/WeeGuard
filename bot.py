# ======================================================================
#  bot.py – 完整、已修正的 Railway 部署版（已移除所有 psycopg2 痕跡）
# ======================================================================

# ---------------------------- 1️⃣ 基础 import ----------------------------
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
    Boolean,               # 必須保留
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
    create_async_engine,   # <-- 只用此函式，會自動使用 asyncpg
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
DOMAIN = os.getenv("DOMAIN")               # 必須是完整 https:// URL

# 下面的 URL 直接使用 Railway 提供的 Neon 連線字串
if not (BOT_TOKEN and ADMIN_IDS_RAW and DATABASE_URL and DOMAIN):
    raise RuntimeError(
        "Missing one of BOT_TOKEN / ADMIN_ID / DATABASE_URL / DOMAIN environment variables"
    )
ADMIN_IDS = [int(x) for x in ADMIN_IDS_RAW.split(",") if x.strip().isdigit()]

# ---------------------------- 3️⃣ SQLAlchemy 基礎 ----------------------------
Base = declarative_base()


# ---------- 3.1 表模型（保持原有） ----------
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
    is_active = Column(Boolean, default=False, nullable=False)   # 必須 Boolean
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
    is_active = Column(Boolean, default=False, nullable=False)   # 必須 Boolean
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


# ---------------------------- 4️⃣ 异步 Engine ----------------------------
engine: AsyncEngine = create_async_engine(
    DATABASE_URL,          # <-- 直接交給 create_async_engine，它會自動偵測 asyncpg
    echo=False,
    future=True
)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_async_session() -> AsyncSession:
    """Yield an AsyncSession for convenient `async with` usage."""
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
    return [row[0] for row in result.fetchall()]


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
    reward_source: str = "rewarded_ad",
) -> None:
    today_start = datetime.now(tz.gettz("Asia/Shanghai")).replace(
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
    alphabet = string.ascii_letters + string.digits
    return "".join(random.choice(alphabet) for _ in range(length))


# ---------------------------- 6️⃣ 今日密鑰生成 & 私聊 ----------------------------
async def store_today_secrets(session: AsyncSession, bot) -> None:
    """每天 10:00 生成 new key1 / key2，並私聊管理員。"""
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
            msg = (
                f"🔔 **今日密钥已更新**（{datetime.now(tz.gettz('Asia/Shanghai')):%Y-%m-%d %H:%M} )\n"
                f"密钥一（8积分）: `{key1}`\n"
                f"密钥二（6积分）: `{key2}`"
            )
            await bot.send_message(chat_id=admin_id, text=msg, parse_mode="Markdown")
        except Exception as e:
            logging.warning(f"Failed to PM admin {admin_id}: {e}")


# ---------------------------- 7️⃣ 每日计数重置 ----------------------------
async def reset_video_counter_daily(session: AsyncSession) -> None:
    await session.execute("DELETE FROM video_view_usage")
    await session.commit()
    logging.info("Daily video view counter reset.")


async def reset_explanation_counter_daily(session: AsyncSession) -> None:
    await session.execute("DELETE FROM explanation_view_usage")
    await session.commit()
    logging.info("Daily explanation view counter reset.")


# ---------------------------- 8️⃣ FastAPI ----------------------------
fastapi_app = FastAPI()
fastapi_app.mount(
    "/static",
    StaticFiles(
        directory=os.path.join(os.path.dirname(__file__), "doc")
    ),
    name="static",
)


# ---------- 8.1 首页（直接跳转） ----------
@fastapi_app.get("/", response_class=HTMLResponse)
async def serve_root_page() -> str:
    """首页直接跳转到奖励视频，3 秒后回到 /hd（活动中心）。"""
    return f"""
    <html lang="zh-CN"><head><meta charset="UTF-8"><title>MoonTag 入口</title></head>
    <body style="text-align:center;margin-top:30px;">
      <div style="margin-bottom:15px;color:#555;">
        正在跳转至奖励视频页面，请稍候…
      </div>
      <script>
        window.location.href = '{AD_AD_URL}';
        setTimeout(()=>{{window.location.href = '/hd';}}, 3000);
      </script>
    </body></html>
    """


# ---------- 8.2 活动中心页面（/hd） ----------
@fastapi_app.get("/hd", response_class=HTMLResponse)
async def serve_hd_page(request: Request) -> str:
    """活动中心页面，包含按钮一（0/3）和按钮二（0/2）以及计数刷新脚本。"""
    # ---- 读取當前计数 ----
    async def _fetch_counters():
        uid = request.headers.get("X-Telegram-User-Id")
        uid = int(uid) if uid else 0
        async with AsyncSessionLocal() as session:
            video_row = await session.execute(
                """
                SELECT COUNT(*) FROM video_view_usage
                WHERE user_id = :uid AND usage_date::date = CURRENT_DATE
                """,
                {"uid": uid},
            )
            video_used = video_row.scalar() or 0

            explain_row = await session.execute(
                """
                SELECT COUNT(*) FROM explanation_view_usage
                WHERE user_id = :uid AND usage_date::date = CURRENT_DATE
                """,
                {"uid": uid},
            )
            explain_used = explain_row.scalar() or 0
        return {"video_used": video_used, "explain_used": explain_used}

    # ---- 读取已绑定的 admin 链接 ----
    async def _fetch_links():
        async with AsyncSessionLocal() as session:
            rows = await session.execute(
                "SELECT link_type, url FROM admin_links WHERE is_active = TRUE"
            )
            return {row[0]: row[1] for row in rows}

    # ---- HTML（纯字符串，使用 .format() 注入 AD_AD_URL） ----
    html = """
    <!DOCTYPE html>
    <html lang="zh-CN">
    <head>
      <meta charset="UTF-8"><title>活动中心 – 开业庆典</title>
      <style>
        body{{font-family:Arial,sans-serif;text-align:center;margin-top:30px;}}
        .box{{display:inline-block;padding:12px 20px;margin:10px;border:1px solid #888;
               border-radius:6px;background:#f9f9f9;}}
        .counter{{font-weight:bold;color:#d00;}}
        button{{padding:10px 18px;margin:5px;cursor:pointer;}}
      </style>
    </head>
    <body>
      <div class="box">
        观看视频可获得积分，每日最多 3 次，已观看 <span id="videoCounter"
        class="counter">(0/3)</span> 次。&#13;
        说明页面每日可点击 2 次，已点击 <span id="explainCounter"
        class="counter">(0/2)</span> 次。
      </div>

      <div class="box"><button id="btn_video">按钮一：观看视频获取积分</button></div>
      <div class="box"><button id="btn_explain">按钮二：查看说明</button></div>

      <script>
        async function loadCounters(){
          const r = await fetch('/current_counters');
          const d = await r.json();
          document.getElementById('videoCounter').innerText = `$(d.video_used)/(3)`;
          document.getElementById('explainCounter').innerText = `$(d.explain_used)/(2)`;
        }
        loadCounters();

        async function fetchLinks(){
          const r = await fetch('/active_admin_links');
          const d = await r.json();
          return d;
        }

        // 按钮一 – 观看视频（3 秒后打开奖励视频）
        document.getElementById('btn_video').onclick = async () => {{
          const used = await fetch('/current_counters').then(r=>r.json()).then(d=>d.video_used);
          if (used >= 3){
            alert('已达今日观看上限，请明天再来');
            return;
          }
          setTimeout(()=>{{window.location.href = '{AD_AD_URL}';}}, 3000);
        }};

        // 按钮二 – 查看说明（3 秒后打开说明页）
        document.getElementById('btn_explain').onclick = async () => {{
          const links = await fetchLinks();
          if (!links.key1 || !links.key2){
            alert('请等待管理员更换新密钥链接');
            return;
          }
          setTimeout(()=>{{window.location.href = '/explanation_page.html';}}, 3000);
        }};
      </script>
    </body></html>
    """.format(AD_AD_URL=AD_AD_URL)

    return html


# ---------- 8.3 说明页面（/explanation_page.html） ----------
@fastapi_app.get("/explanation_page.html", response_class=HTMLResponse)
async def serve_explanation_page() -> str:
    """说明页面，展示获取密钥的完整步骤并计数（0/2）。"""
    return """
    <!DOCTYPE html>
    <html lang="zh-CN">
    <head>
      <meta charset="UTF-8"><title>密钥获取说明</title>
      <style>
        body{{font-family:Arial,sans-serif;text-align:center;margin-top:30px;}}
        .box{{display:inline-block;padding:12px 20px;margin:10px;border:1px solid #888;
               border-radius:6px;background:#f9f9f9;}}
        .counter{{font-weight:bold;color:#d00;}}
      </style>
    </head>
    <body>
      <div class="box">
        <strong>获取密钥的完整步骤：</strong><br>
        1️⃣ 打开管理员绑定的网盘链接，文件名即为密钥。<br>
        2️⃣ 将文件下载后保存到夸克网盘。<br>
        3️⃣ 为文件重新命名（建议使用英文或数字），<br>
           然后复制 **新文件名** 并在此页面粘贴发送给机器人。<br>
        4️⃣ 机器人会返回积分（首次 8，第二次 6），并在成功后给出提示。
      </div>

      <div class="counter">（已使用 0/2 次今日）</div>

      <script>
        async function refreshCounter(){
          const r = await fetch('/explanation_counter');
          const d = await r.json();
          document.querySelector('.counter').innerText = \`已使用 ${d.used}/2 次今日\`;
        }
        refreshCounter();

        // 5 秒后自动返回活动中心（可自行修改）
        setTimeout(()=>{{window.location.href = '/hd';}}, 5000);
      </script>
    </body></html>
    """


# ---------- 8.4 计数 API ----------
@fastapi_app.get("/current_counters", response_model=Dict[str, int])
async def current_counters(request: Request):
    """前端轮询获取：视频观看次数（0/3）和说明点击次数（0/2）。"""
    uid = request.headers.get("X-Telegram-User-Id")
    uid = int(uid) if uid else 0
    async with AsyncSessionLocal() as session:
        video_row = await session.execute(
            """
            SELECT COUNT(*) FROM video_view_usage
            WHERE user_id = :uid AND usage_date::date = CURRENT_DATE
            """,
            {"uid": uid},
        )
        video_used = video_row.scalar() or 0

        explain_row = await session.execute(
            """
            SELECT COUNT(*) FROM explanation_view_usage
            WHERE user_id = :uid AND usage_date::date = CURRENT_DATE
            """,
            {"uid": uid},
        )
        explain_used = explain_row.scalar() or 0
    return {"video_used": video_used, "explain_used": explain_used}


# ---------- 8.5 管理员已绑定的链接 ----------
@fastapi_app.get("/active_admin_links", response_model=Dict[str, str])
async def active_admin_links():
    """返回当前活跃的 key1 / key2 URL（若不存在返回空字典）。"""
    async with AsyncSessionLocal() as session:
        rows = await session.execute(
            "SELECT link_type, url FROM admin_links WHERE is_active = TRUE"
        )
        return {row[0]: row[1] for row in rows}


# ---------- 8.6 说明页面计数 ----------
@fastapi_app.get("/explanation_counter", response_model=Dict[str, int])
async def explanation_counter(request: Request):
    """返回当前用户今日对说明页面的点击次数（0、1、2）。"""
    uid = request.headers.get("X-Telegram-User-Id")
    uid = int(uid) if uid else 0
    async with AsyncSessionLocal() as session:
        row = await session.execute(
            """
            SELECT COUNT(*) FROM explanation_view_usage
            WHERE user_id = :uid AND usage_date::date = CURRENT_DATE
            """,
            {"uid": uid},
        )
        return {"used": row.scalar() or 0}


# ---------- 8.7 记录说明页面点击 ----------
@fastapi_app.post("/record_explanation_click", status_code=status.HTTP_200_OK)
async def record_explanation_click(request: Request):
    """在用户成功打开说明页面后记录一次点击（用于计数）。"""
    uid = request.headers.get("X-Telegram-User-Id")
    uid = int(uid) if uid else 0
    async with AsyncSessionLocal() as session:
        usage = await session.execute(
            """
            INSERT INTO explanation_view_usage (user_id, usage_date)
            VALUES (:uid, CURRENT_TIMESTAMP)
            ON CONFLICT (user_id, usage_date) DO NOTHING
            RETURNING id
            """,
            {"uid": uid},
        )
        if usage.scalar():
            await session.commit()
    return {"status": "recorded"}


# ---------- 8.8 奖励视频校验（原 rewarded_ad 逻辑） ----------
class RewardRequest(BaseModel):
    secret: str   # 用户粘贴的密钥


@fastapi_app.post("/validate_key", status_code=status.HTTP_200_OK)
async def validate_key_endpoint(request: Request, payload: RewardRequest) -> JSONResponse:
    """
    1️⃣ 取出当前活跃的密钥（key1、key2）  
    2️⃣ 与用户提交的 secret 匹配  
    3️⃣ 若已使用则直接拒绝；否则授予 8（key1）/6（key2）积分  
    """
    user_id = request.headers.get("X-Telegram-User-Id")
    if not user_id:
        raise HTTPException(status_code=400, detail="Missing Telegram user id header")
    user_id = int(user_id)

    async with AsyncSessionLocal() as session:
        # 取出当前活跃的密钥
        result = await session.execute(
            "SELECT secret_type, secret_value FROM secret_keys WHERE is_active = TRUE"
        )
        active = {row[0]: row[1] for row in result.fetchall()}
        if not active:
            return JSONResponse(
                content={"status": "rejected", "message": "今日密钥尚未生成"},
                status_code=403,
            )

        # 匹配
        matched_type: Optional[str] = None
        for stype, svalue in active.items():
            if payload.secret == svalue:
                matched_type = stype
                break
        if not matched_type:
            return JSONResponse(
                content={"status": "rejected", "message": "密钥不匹配或已失效"},
                status_code=403,
            )

        # 检查是否已使用
        usage_row = await session.execute(
            """
            SELECT * FROM user_key_usage
            WHERE user_id = :uid
              AND secret_type = :stype
              AND usage_date::date = :today
            """,
            {"uid": user_id, "stype": matched_type,
             "today": datetime.now(tz.gettz("Asia/Shanghai")).replace(
                 hour=0, minute=0, second=0, microsecond=0)},
        )
        if usage_row.scalar():
            return JSONResponse(
                content={"status": "rejected", "message": "今日已使用过该密钥"},
                status_code=403,
            )

        # 积分
        points_to_add = 8 if matched_type == "key1" else 6

        # 记录使用
        usage_record = UserKeyUsage(
            user_id=user_id,
            secret_type=matched_type,
            usage_date=datetime.now(tz.gettz("Asia/Shanghai")),
        )
        session.add(usage_record)

        # 同时把这笔积分写入原有的 ad_usage 表（保持原有计数逻辑）
        await upsert_user_usage(session, user_id, points_to_add, reward_source="key_claim")
        await session.commit()

        return JSONResponse(
            content={"status": "accepted", "points": points_to_add},
            status_code=200,
        )


# ---------------------------- 9️⃣ Telegram Bot 处理 ----------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/start → 三个按钮"""
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
                url=f"{DOMAIN}/hd"
            ),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "欢迎使用本机器人！请选择下面的功能：",
        reply_markup=reply_markup
    )


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """仅管理员可见的后台入口（文件 ID 保存/删除）。"""
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


# ---------- 9.1 保存 file_id ----------
async def cb_save_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "请发送一张图片（Telegram 会返回其 file_id）"
    )
    context.user_data["awaiting_file"] = True


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """保存用户发送的照片 file_id"""
    if not context.user_data.get("awaiting_file"):
        return
    photo = update.message.photo[-1]  # 最高分辨率
    file_id = photo.file_id

    async with AsyncSessionLocal() as session:
        await store_file_id(session, file_id)

    await update.message.reply_text(
        f"✅ 文件已保存\n`{file_id}`",
        parse_mode="Markdown"
    )
    context.user_data.pop("awaiting_file", None)


# ---------- 9.2 删除 file_id ----------
async def admin_menu_list_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
    while len(rows) < 5:
        rows.append([InlineKeyboardButton("⬜️", callback_data="noop")])
    rows.append([InlineKeyboardButton("❌ 取消", callback_data="noop")])
    reply_markup = InlineKeyboardMarkup(rows)

    await query.edit_message_text(
        "📂 请选择要删除的记录（会要求二次确认）",
        reply_markup=reply_markup
    )


async def admin_menu_delete_confirmation_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    fid = query.data.split("_", 1)[1]   # shape: del_<file_id>

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
    query = update.callback_query
    await query.answer()
    fid = query.data.split("_", 1)[1]   # shape: confirm_del_<file_id>

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


# ---------- 9.3 其他占位按钮 ----------
async def handle_start_verification_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("此功能尚未实现，敬请期待！")


async def handle_show_points_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("积分查询功能正在开发中，稍后加入！")


# ---------- 9.4 /my 命令（密钥管理） ----------
async def my_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /my 的完整行为：
      • 第一次 → “请输入密钥一链接”
      • 输入链接 → 保存为 key1（8积分）
      • 再次发送 /my → “请输入密钥二链接”
      • 输入链接 → 保存为 key2（6积分）
      • 任何时刻单独发送 /my（不带状态） → 私聊管理员当前 key1、key2 与对应积分
    """
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("❌ 只有管理员可以使用此命令")
        return

    state = context.user_data.get("my_state")
    text = update.message.text.strip()

    # -------- 状态机 ----------
    if state == "awaiting_key1":
        async with AsyncSessionLocal() as session:
            from urllib.parse import urlparse
            parsed = urlparse(text)
            secret_part = parsed.path.rstrip("/").split("/")[-1]
            await session.execute(
                "DELETE FROM admin_links WHERE link_type = 'key1'"
            )
            await session.execute(
                "INSERT INTO admin_links (link_type, url, is_active) VALUES ('key1', :url, TRUE)",
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
                "INSERT INTO admin_links (link_type, url, is_active) VALUES ('key2', :url, TRUE)",
                {"url": text, "now": datetime.utcnow()},
            )
            await session.commit()
        await update.message.reply_text("密钥二链接已保存，绑定完成。")
        context.user_data.pop("my_state")
        return

    # 默认情况（第一次或状态已清除）
    if state is None:
        context.user_data["my_state"] = "awaiting_key1"
        await update.message.reply_text("请输入密钥一链接")
        return

    # 若状态不匹配，直接返回已绑定的链接信息
    async with AsyncSessionLocal() as session:
        rows = await session.execute(
            "SELECT link_type, url FROM admin_links WHERE is_active = TRUE"
        )
        links = {row[0]: row[1] for row in rows}
    if not links:
        await update.message.reply_text("暂无已绑定的密钥链接。")
    else:
        formatted = "\n".join([f"{ltype}: {links[ltype]}" for ltype in sorted(links.keys())])
        await update.message.reply_text("当前已绑定的密钥链接：\n" + formatted)


# ---------------------------- 10️⃣ 注册所有 Handler ----------------------------
def register_handlers(app: Application) -> None:
    # 基础指令
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CommandHandler("my", my_command))

    # 管理员文件 ID 相关回调
    app.add_handler(CallbackQueryHandler(cb_save_button, pattern="^admin_menu_save$"))
    app.add_handler(MessageHandler(filters.PHOTO & filters.UpdateContext(user_data={"awaiting_file": True}), handle_photo))
    app.add_handler(CallbackQueryHandler(admin_menu_list_button, pattern="^admin_menu_list$"))
    app.add_handler(CallbackQueryHandler(admin_menu_delete_confirmation_button, pattern="^del_"))
    app.add_handler(CallbackQueryHandler(confirm_deletion_button, pattern="^confirm_del_"))

    # 位置占位按钮
    app.add_handler(CallbackQueryHandler(handle_start_verification_button, pattern="^button_start_verification$"))
    app.add_handler(CallbackQueryHandler(handle_show_points_button, pattern="^button_show_points$"))

    # 兼容旧回调（如果有）
    app.add_handler(CallbackQueryHandler(handle_start_verification_button, pattern="^menu_start_verification$"))
    app.add_handler(CallbackQueryHandler(handle_show_points_button, pattern="^menu_show_points$"))


# ---------------------------- 11️⃣ Scheduler（每日任务） ----------------------------
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import asyncio

scheduler = AsyncIOScheduler()


def start_scheduler(app: Application):
    """
    注册两个每日任务：
      • 0:00（Asia/Shanghai） → 重置视频计数（0/3）
      • 10:00（Asia/Shanghai） → 重置说明计数并生成新密钥（并私聊管理员）
    """
    scheduler.add_job(
        func=reset_video_counter_daily,
        trigger="cron",
        hour=0,
        minute=0,
        timezone="Asia/Shanghai",
        id="reset_video",
        args=[AsyncSessionLocal],
    )
    scheduler.add_job(
        func=lambda: asyncio.create_task(
            store_today_secrets(AsyncSessionLocal(), app.bot)
        ),
        trigger="cron",
        hour=10,
        minute=0,
        timezone="Asia/Shanghai",
        id="generate_secrets",
        args=[AsyncSessionLocal],
    )
    scheduler.start()


# ---------------------------- 12️⃣ 主入口 ----------------------------
async def main() -> None:
    """
    程序入口：
      1️⃣ 创建 Telegram Application 并注册所有处理器
      2️⃣ 启动 APScheduler（需要把当前的 telegram_app 传进去，以便私聊）
      3️⃣ 通过 uvicorn 同时运行 FastAPI（端口 8000）
    """
    # ① Telegram Bot
    telegram_app = Application.builder().token(BOT_TOKEN).build()
    register_handlers(telegram_app)

    # ② Scheduler（需要把 telegram_app 传进去，以便在私聊里使用 bot 对象）
    start_scheduler(telegram_app)

    # ③ FastAPI + uvicorn
    import uvicorn

    uvicorn_config = uvicorn.Config(fastapi_app, host="0.0.0.0", port=8000)
    server = uvicorn.Server(uvicorn_config)

    # 并发运行 Bot（polling） 与 FastAPI
    bot_task = asyncio.create_task(telegram_app.run_polling())
    server_task = asyncio.create_task(server.serve())
    await asyncio.gather(bot_task, server_task)


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
