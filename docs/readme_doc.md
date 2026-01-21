# VIP 中转 Bot 使用文档

## 1. 部署步骤
1. 把本仓库克隆或直接复制以下文件到 Railway 项目根目录：  
   - `bot.py`  
   - `requirements.txt`  
   - `runtime.txt`  
   - `doc/`（其中的 `readme_doc.md`、`hd_page.html`、`mid_page.html`）  
2. 在 Railway → **Settings → Variables** 中填写：  
   | 变量 | 示例 | 说明 |
   |------|------|------|
   | `BOT_TOKEN` | `123456:ABC-DEF...` | 你的 Telegram Bot Token |
   | `DATABASE_URL` | `postgresql+asyncpg://user:pwd@host:5432/dbname` | Neon PostgreSQL 连接串 |
   | `ADMIN_IDS` | `987654321,123456789` | 管理员 Telegram ID（逗号分隔，可留空） |
   | `REPLY_WEBHOOK_URL` | `https://my-bot.railway.app` | Railway 为你分配的根域名（**不要自行修改**） |
3. 在 **Build & Deployment** 页面把 **Start Command** 设为 `python bot.py` 并 **Deploy**。  
4. 部署成功后机器人会自动上线，发送 `/start` 即可看到欢迎页。

## 2. 常用指令
| 指令 | 功能 |
|------|------|
| `/start` | 显示欢迎页，含 **开始验证**、**积分**、**开业活动**（含获取密钥）四个按钮 |
| `/balance` | 查看余额 |
| `/deposit <amount>` | 存入金额 |
| `/withdraw <amount>` | 提取金额 |
| `/jf` | 直接打开积分页面（可每日签到） |
| `/admin` | 进入管理员后台（仅限管理员） |
| `/my` | **查看今日密钥**；`/my <新链接1> <新链接2>` 更新「获取密钥」按钮跳转的 Quark 链接 |
| **直接发送密钥字符串** | 若完整匹配当天生成的密钥（10 位大小写字母+数字），即可获得 8 积分或 6 积分（只能使用一次） |

## 3. “开业活动” 页面
- **按钮一**：观看视频 → 观看完毕后自动调用 `/reward` 并获得积分（10 → 6 → 3‑10 随机）  
- **按钮二**：获取密钥 → 3 秒后自动跳转到管理员在 `/my` 中设置的 Quark 直链；若管理员尚未设置，按钮会显示 **“⏳ 请等待管理员更换链接”**  

## 4. 密钥生成规则
- 每天 **北京时间 10:00** 自动生成两段 **10 位随机字符**（大小写字母+数字）  
  - `token_one` → 价值 **8 积分**  
  - `token_two` → 价值 **6 积分**  
- 旧的密钥在使用后会被标记为已使用，次日 10:00 自动被**丢弃**并生成新的两个密钥。  
- 用户只要把对应的 **完整字符串** 发送给机器人即可领取积分。  

## 5. 管理员操作
- 使用 `/my` 可以**无限次**查看今天的密钥。  
- 如果想更换「获取密钥」按钮跳转的 Quark 链接，只需在 `/my` 后面跟上两条完整的 Quark 分享链接，例如：  
