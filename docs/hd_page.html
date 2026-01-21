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

# -------------------- 1️⃣ 读取环境变量 --------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS_RAW = os.getenv("ADMIN_ID", "")
DATABASE_URL = os.getenv("DATABASE_URL")
DOMAIN = os.getenv("DOMAIN")                     # <-- <<<--- 需要自行替换为你的 Railway 公开域名
AD_AD_URL = "https://otieu.com/4/10489957"       # <-- <<<--- 奖励视频直链（保持不变）
EXPLANATION_URL = "https://otieu.com/4/10489994" # <-- <<<--- 密钥说明页直链（保持不变）

TIMEZONE = tz.gettz("Asia/Shanghai")

if not (BOT_TOKEN and ADMIN_IDS_RAW and DATABASE_URL and DOMAIN):
    raise RuntimeError(
        "Missing one of BOT_TOKEN / ADMIN_ID / DATABASE_URL / DOMAIN environment variables"
    )
ADMIN_IDS = [int(x) for x in ADMIN_IDS_RAW.split(",") if x.strip().isdigit()]

# -------------------- 2️⃣ SQLAlchemy模型 --------------------
Base = declarative_base()


class FileIDRecord(Base):
    __tablename__ = "file_ids"
    id = Column(Integer, primary_key=True)
    file_id = Column(Text, nullable=False, unique=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class UserAdUsage(Base):
    """Rewarded‑ad 观看计数（原有功能）"""
    __tablename__ = "user_ad_usage"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, index=True, nullable=False)
    usage_date = Column(DateTime, nullable=False)
    ads_watched_today = Column(Integer, default=0, nullable=False)
    points_granted_today = Column(Integer, default=0, nullable=False)


class SecretKey(Base):
    """每天生成的两个 10 位密钥（key1、key2）"""
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
    """管理员通过 /my 提供的完整 URL（最后一段即为密钥）"""
    __tablename__ = "admin_links"
    __table_args__ = (UniqueConstraint("link_type", name="uq_link_type"),)

    id = Column(Integer, primary_key=True)
    link_type = Column(
        Enum("key1", "key2", name="link_type_enum"), nullable=False
    )
    url = Column(Text, nullable=False)
    is_active = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class UserKeyUsage(Base):
    """记录用户是否已使用当天的 key1 / key2"""
    __tablename__ = "user_key_usage"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, index=True, nullable=False)
    secret_type = Column(
        Enum("key1", "key2", name="secret_type_enum"), nullable=False
    )
    usage_date = Column(DateTime, nullable=False)

    __table_args__ = (UniqueConstraint("user_id", "secret_type", name="uq_user_type"),)


# ----------------- 视频观看计数（新功能） -----------------
class VideoViewUsage(Base):
    """记录用户当天观看奖励视频的次数（上限 3）"""
    __tablename__ = "video_view_usage"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, index=True, nullable=False)
    usage_date = Column(DateTime, nullable=False)

    __table_args__ = (UniqueConstraint("user_id", "usage_date", name="uq_user_date"),)


# -------------------- 3️⃣ 异步 Engine & Session --------------------
engine: AsyncEngine = create_async_engine(
    DATABASE_URL, echo=False, future=True
)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_async_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session


# -------------------- 4️⃣ 数据库助手（CRUD） --------------------
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
    alphabet = string.ascii_letters + string.digits
    return "".join(random.choice(alphabet) for _ in range(length))


async def store_today_secrets(session: AsyncSession, context) -> None:
    """每天 10:00 生成两个新密钥，旧密钥失效，并私聊管理员"""
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

    # 私聊管理员
    for admin_id in ADMIN_IDS:
        try:
            msg = (
                f"🔔 **今日密钥已更新**（{datetime.now(TIMEZONE).strftime('%Y-%m-%d %H:%M')})\\n"
                f"密钥一（8积分）: `{key1}`\\n"
                f"密钥二（6积分）: `{key2}`"
            )
            await context.bot.send_message(chat_id=admin_id, text=msg, parse_mode="Markdown")
        except Exception as e:
            logging.warning(f"Failed to PM admin {admin_id}: {e}")


async def reset_video_counter_daily(session: AsyncSession) -> None:
    """每天 0:00 重置视频观看计数（0/3）"""
    await session.execute("DELETE FROM video_view_usage")
    await session.commit()
    logging.info("Daily video view counter has been reset.")


async def reset_explanation_counter_daily(session: AsyncSession) -> None:
    """每天 10:00 重置说明页面计数（0/2）"""
    await session.execute("DELETE FROM explanation_view_usage")
    await session.commit()
    logging.info("Daily explanation view counter has been reset.")


# -------------------- 5️⃣ FastAPI --------------------
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
    """首页 – 自动跳转到 /hd 页面"""
    return """
    <html lang="zh-CN"><head><meta charset="UTF-8"><title>MoonTag 入口</title></head>
    <body style="text-align:center;margin-top:30px;">
      <div style="margin-bottom:15px;color:#555;">
        正在跳转至奖励视频页面，请稍候…
      </div>
      <script>
        // 直接打开奖励视频
        window.location.href = '{AD_AD_URL}';
        // 3 秒后再回到活动中心页面
        setTimeout(()=>{{window.location.href = '/hd';}},3000);
      </script>
    </body></html>
    """.format(AD_AD_URL=AD_AD_URL)


# ---------- 5.1 /hd 页面（活动中心） ----------
@fastapi_app.get("/hd", response_class=HTMLResponse)
async def serve_hd_page(request: Request) -> str:
    """
    活动中心页面，展示两个按钮：
      1️⃣ 观看视频获取积分（计数 0/3，每天 0:00 重置）
      2️⃣ 查看说明（计数 0/2，每天 10:00 重置）
    前端会向后端请求计数信息并实时刷新。
    """
    # 读取当前视频计数与说明计数的接口
    async def _fetch_counters():
        uid = request.headers.get("X-Telegram-User-Id")
        uid = int(uid) if uid else 0
        async with AsyncSessionLocal() as session:
            # 视频计数
            video_row = await session.execute(
                """
                SELECT COUNT(*) FROM video_view_usage
                WHERE user_id = :uid
                  AND usage_date::date = CURRENT_DATE
                """,
                {"uid": uid},
            )
            video_used = video_row.scalar() or 0

            # 说明计数
            explain_row = await session.execute(
                """
                SELECT COUNT(*) FROM explanation_view_usage
                WHERE user_id = :uid
                  AND usage_date::date = CURRENT_DATE
                """,
                {"uid": uid},
            )
            explain_used = explain_row.scalar() or 0
        return {"video_used": video_used, "explain_used": explain_used}

    # 这里直接返回 HTML，JS 会在页面加载后调用 /current_counters 获取最新计数
    return f'''
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
        // 读取当前计数
        async function loadCounters(){
          const resp = await fetch('/current_counters');
          const data = await resp.json();
          document.getElementById('videoCounter').innerText = `$(data.video_used)/(3)`;
          document.getElementById('explainCounter').innerText = `$(data.explain_used)/(2)`;
        }
        loadCounters();

        // 读取后端链接（用于按钮二）
        async function fetchLinks(){
          const r = await fetch('/active_admin_links');
          const d = await r.json();
          return d;
        }

        // 按钮一 – 观看视频（只有未满 3 次才可点）
        document.getElementById('btn_video').onclick = async () => {
          const used = await fetch('/current_counters').then(r=>r.json()).then(d=>d.video_used);
          if (used >= 3){
            alert('已达今日观看上限，请明天再来');
            return;
          }
          // 3 秒后打开奖励视频
          setTimeout(()=>{window.location.href = '{AD_AD_URL}';}, 3000);
        };

        // 按钮二 – 查看说明（进入说明页面）
        document.getElementById('btn_explain').onclick = async () => {{
          const links = await fetchLinks();
          // 若管理员尚未绑定密钥链接，则提示等待
          if (!links.key1 || !links.key2){
            alert('请等待管理员更换新密钥链接');
            return;
          }
          // 3 秒后跳转到说明页面
          setTimeout(()=>{{window.location.href = '/explanation_page.html';}}, 3000);
        }};
      </script>
    </body></html>
    '''.format(AD_AD_URL=AD_AD_URL)


# ---------- 5.2 说明页面（/explanation_page.html） ----------
@fastapi_app.get("/explanation_page.html", response_class=HTMLResponse)
async def serve_explanation_page() -> str:
    """
    说明页面，展示使用步骤：
      1. 通过夸克网盘获取密钥文件
      2. 看到文件名后请保存、重命名、复制文件名
      3. 把文件名发送给机器人即可获得积分
    同时计数 0/2，每天 10:00 重置。
    """
    return """
    <!DOCTYPE html>
    <html lang="zh-CN">
    <head>
      <meta charset="UTF-8"><title>说明页面</title>
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
        <strong>获取密钥的完整步骤：</strong><br>
        1️⃣ 打开网盘链接（管理员已绑定的链接），文件名即为密钥。<br>
        2️⃣ 将文件下载后保存到你的夸克网盘。<br>
        3️⃣ 为文件重新命名（建议使用英文或数字），<br>
           然后复制 **新文件名** 并在此页面粘贴发送给机器人。<br>
        4️⃣ 机器人会返回积分（首次 8，第二次 6），并在成功后给予提示。
      </div>

      <div class="box counter">（已使用 0/2 次今日）</div>

      <script>
        // 计数刷新（每次打开页面后向后端请求最新计数）
        async function refreshCounter(){
          const r = await fetch('/explanation_counter');
          const d = await r.json();
          document.querySelector('.box.counter').innerText = `已使用 ${d.used}/2 次今日`;
        }
        refreshCounter();

        // 3 秒后自动回到活动中心（可自行修改）
        setTimeout(()=>{{window.location.href = '/hd';}}, 5000);
      </script>
    </body></html>
    """


# ---------- 5.3 返回计数的 API ----------
@fastapi_app.get("/current_counters", response_model=Dict[str, int])
async def current_counters(request: Request):
    """
    前端定时器会轮询此接口，获取当前用户的视频观看次数与说明页面点击次数。
    """
    uid = request.headers.get("X-Telegram-User-Id")
    uid = int(uid) if uid else 0
    async with AsyncSessionLocal() as session:
        # 视频观看次数
        video_row = await session.execute(
            """
            SELECT COUNT(*) FROM video_view_usage
            WHERE user_id = :uid
              AND usage_date::date = CURRENT_DATE
            """,
            {"uid": uid},
        )
        video_used = video_row.scalar() or 0

        # 说明页面次数
        explain_row = await session.execute(
            """
            SELECT COUNT(*) FROM explanation_view_usage
            WHERE user_id = :uid
              AND usage_date::date = CURRENT_DATE
            """,
            {"uid": uid},
        )
        explain_used = explain_row.scalar() or 0

    return {"video_used": video_used, "explain_used": explain_used}


# ---------- 5.4 管理员已绑定的链接 ----------
@fastapi_app.get("/active_admin_links", response_model=Dict[str, str])
async def active_admin_links():
    """返回当前活跃的 key1 / key2 URL（若不存在返回空字典）"""
    async with AsyncSessionLocal() as session:
        rows = await session.execute(
            "SELECT link_type, url FROM admin_links WHERE is_active = TRUE"
        )
        return {row[0]: row[1] for row in rows}


# ---------- 5.5 说明页面计数 ----------
@fastapi_app.get("/explanation_counter", response_model=Dict[str, int])
async def explanation_counter(request: Request):
    """返回当前用户今日对说明页面的点击次数（0、1、2）"""
    uid = request.headers.get("X-Telegram-User-Id")
    uid = int(uid) if uid else 0
    async with AsyncSessionLocal() as session:
        row = await session.execute(
            """
            SELECT COUNT(*) FROM explanation_view_usage
            WHERE user_id = :uid
              AND usage_date::date = CURRENT_DATE
            """,
            {"uid": uid},
        )
        return {"used": row.scalar() or 0}


# ---------- 5.6 记录说明页面的点击 ----------
@fastapi_app.post("/record_explanation_click", status_code=status.HTTP_200_OK)
async def record_explanation_click(request: Request):
    """后端会在用户成功点击「按钮二」后收到一次调用，用于计数"""
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


# -------------------- 6️⃣ 奖励视频验证（与原有 rewarded_ad 保持一致） --------------------
class RewardRequest(BaseModel):
    secret: str   # 用户粘贴的密钥


@fastapi_app.post("/validate_key", status_code=status.HTTP_200_OK)
async def validate_key_endpoint(request: Request, payload: RewardRequest) -> JSONResponse:
    """
    与之前的流程相同：校验密钥、检查是否已使用、若符合则给 8/6 积分。
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

        # 找到匹配的 secret_type
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

        # 检查是否已使用该密钥
        usage_row = await session.execute(
            """
            SELECT * FROM user_key_usage
            WHERE user_id = :uid
              AND secret_type = :stype
              AND usage_date::date = :today
            """,
            {"uid": user_id, "stype": matched_type,
             "today": datetime.now(TIMEZONE).replace(
                 hour=0, minute=0, second=0, microsecond=0)},
        )
        if usage_row.scalar():
            return JSONResponse(
                content={"status": "rejected", "message": "今日已使用过该密钥"},
                status_code=403,
            )

        # 计算积分
        points_to_add = 8 if matched_type == "key1" else 6

        # 记录使用
        usage_record = UserKeyUsage(
            user_id=user_id,
            secret_type=matched_type,
            usage_date=datetime.now(TIMEZONE),
        )
        session.add(usage_record)

        # 同样使用原有的积分写入函数（与 rewarded‑ad 相同的计数方式）
        await upsert_user_usage(session, user_id, points_to_add, reward_source="key_claim")
        await session.commit()

        return JSONResponse(
            content={"status": "accepted", "points": points_to_add},
            status_code=200,
        )


# -------------------- 7️⃣ 注册 Telegram Bot 处理器 --------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /start – 三个按钮：
      • 开始验证
      • 查看积分
      • 开业活动（打开 /hd 页面）
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
    """/admin – 仅管理员可见的后台入口（保持原有文件‑ID 功能）"""
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


# ---------- 7.1 保存 file_id ----------
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


# ---------- 7.2 删除 file_id ----------
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
    fid = query.data.split("_", 1)[1]  # format: del_<file_id>

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
    fid = query.data.split("_", 1)[1]  # format: confirm_del_<file_id>

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


# ---------- 7.3 其他占位按钮 ----------
async def handle_start_verification_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("此功能尚未实现，敬请期待！")


async def handle_show_points_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("积分查询功能正在开发中，稍后加入！")


# ---------- 7.4 /my 命令（管理员设置/查看密钥） ----------
async def my_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /my 的行为：
      • 第一次发送 → “请输入密钥一链接”
      • 发送链接 → 保存为 key1（8积分）
      • 再次发送 /my → “请输入密钥二链接”
      • 发送链接 → 保存为 key2（6积分）
      • 任何时候单独发送 /my（不带状态） → 私聊管理员当前的 key1、key2 与对应积分
    """
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("❌ 只有管理员可以使用此命令")
        return

    state = context.user_data.get("my_state")
    text = update.message.text.strip()

    # -----------------------------------------------------------------
    # 状态机：awaiting_key1 → awaiting_key2 → None
    # -----------------------------------------------------------------
    if state == "awaiting_key1":
        async with AsyncSessionLocal() as session:
            # 把链接的最后一段当作密钥保存
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

    # -----------------------------------------------------------------
    # 其它情况：直接 /my 或未开始状态
    # -----------------------------------------------------------------
    if state is None:
        context.user_data["my_state"] = "awaiting_key1"
        await update.message.reply_text("请输入密钥一链接")
        return

    # 如果以上都不匹配，直接返回当前已绑定的链接信息
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


# -------------------- 8️⃣ Scheduler（每日任务） --------------------
from apscheduler.schedulers.asyncio import AsyncIOScheduler

scheduler = AsyncIOScheduler()


def start_scheduler(app: Application):
    """
    在创建 Application 后调用此函数，为两个每日任务注册：
      1️⃣ 0:00（北京时间） → 重置视频观看计数（0/3）
      2️⃣ 10:00（北京时间） → 重置说明页面计数（0/2） 并生成新密钥
    """
    # 重置视频计数
    scheduler.add_job(
        func=reset_video_counter_daily,
        trigger="cron",
        hour=0,
        minute=0,
        timezone="Asia/Shanghai",
        id="reset_video",
        args=[AsyncSessionLocal],
    )
    # 重置说明计数并生成新密钥
    scheduler.add_job(
        func=lambda: asyncio.create_task(store_today_secrets(AsyncSessionLocal(), app.bot)),
        trigger="cron",
        hour=10,
        minute=0,
        timezone="Asia/Shanghai",
        id="generate_secrets",
        args=[AsyncSessionLocal],
    )
    scheduler.start()


# -------------------- 9️⃣ 注册所有 Handler --------------------
def register_handlers(app: Application) -> None:
    # 基础指令
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CommandHandler("my", my_command))

    # 位置回调（原有 admin 功能）
    app.add_handler(CallbackQueryHandler(cb_save_button, pattern="^admin_menu_save$"))
    app.add_handler(MessageHandler(filters.PHOTO & filters.UpdateContext(user_data={"awaiting_file": True}), handle_photo))
    app.add_handler(CallbackQueryHandler(admin_menu_list_button, pattern="^admin_menu_list$"))
    app.add_handler(CallbackQueryHandler(admin_menu_delete_confirmation_button, pattern="^del_"))
    app.add_handler(CallbackQueryHandler(confirm_deletion_button, pattern="^confirm_del_"))

    # 占位按钮
    app.add_handler(CallbackQueryHandler(handle_start_verification_button, pattern="^button_start_verification$"))
    app.add_handler(CallbackQueryHandler(handle_show_points_button, pattern="^button_show_points$"))

    # 新增的按钮（活动中心的两个按钮）在前端页面里已经绑定了 JS，这里不需要额外的回调。

    # 其它可能的回调（如文件保存后的提示）
    app.add_handler(CallbackQueryHandler(handle_start_verification_button, pattern="^menu_start_verification$"))
    app.add_handler(CallbackQueryHandler(handle_show_points_button, pattern="^menu_show_points$"))


# -------------------- 10️⃣ 主入口 --------------------
async def main() -> None:
    """
    程序入口：启动 Telegram Bot（轮询） + FastAPI（uvicorn） + APScheduler。
    """
    # 先创建 Application（用于注册所有 Telegram handlers）
    telegram_app = Application.builder().token(BOT_TOKEN).build()
    register_handlers(telegram_app)

    # 启动 APScheduler，需要把当前的 telegram_app 传进去
    start_scheduler(telegram_app)

    # 同时启动 FastAPI 服务器
    import uvicorn

    uvicorn_config = uvicorn.Config(fastapi_app, host="0.0.0.0", port=8000)
    server = uvicorn.Server(uvicorn_config)

    # 并发运行两个服务
    bot_task = asyncio.create_task(telegram_app.run_polling())
    server_task = asyncio.create_task(server.serve())
    await asyncio.gather(bot_task, server_task)


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
