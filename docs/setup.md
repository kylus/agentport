# 一次性安裝（每台機器一次）

## 1. Python deps (uv)

If you don't have `uv` yet:

```bash
curl -LsSf https://astral.sh/uv/install.sh -o /tmp/uv-install.sh
bash /tmp/uv-install.sh
export PATH="$HOME/.local/bin:$PATH"   # add to ~/.bashrc / ~/.zshrc for permanence
```

Then sync deps into a project-local venv (`.venv/` at repo root):

```bash
cd <agentport-checkout>
uv sync
```

All `uv run …` / `uv tool run …` invocations later in this doc auto-activate the venv; no `source .venv/bin/activate` needed.

To verify no known vulnerabilities:

```bash
uv tool run pip-audit --requirement <(uv export --no-hashes --format requirements-txt) --disable-pip --no-deps
```

## 2. Bun + 頻道 plugin（至少一個）

兩個頻道都跑在 bun 上：

```bash
curl -fsSL https://bun.sh/install | bash
```

**Discord**（個人環境推薦——不需要 workspace 管理權，只要一個 Discord 帳號 + 一個自己的 server）：

```bash
gh repo clone <your-fork>/claude-plugins-official ~/workspace/claude-plugins-official
cd ~/workspace/claude-plugins-official && git checkout feat/owner-role-hook
cd external_plugins/discord && bun install --frozen-lockfile
```

**Slack**（需要一個你能建 app 的 workspace）：

```bash
curl -fsSL https://downloads.slack-edge.com/slack-cli/install.sh | bash
slack login           # browser 開起來授權，選 workspace
slack auth list       # 確認登入成功

gh repo clone <your-fork>/claude-code-slack-channel ~/workspace/claude-code-slack-channel
cd ~/workspace/claude-code-slack-channel && git checkout feat/owner-role-hook
bun install --frozen-lockfile
```

## 3. Claude Code

確認已登入訂閱（不要用 ANTHROPIC_API_KEY）：

```bash
# 把可能殘留的 API key 從 settings.json 拿掉
python3 -c "import json; p='$HOME/.claude/settings.json'; s=json.load(open(p)); s.get('env',{}).pop('ANTHROPIC_API_KEY',None); json.dump(s,open(p,'w'),indent=2)"

# 用訂閱登入
claude login
```

## 4. 共用 secrets

建立 `~/.claude/secrets/agentport.env`（**不要進版控**）。全部選用——只填你會用到的來源：

```bash
mkdir -p ~/.claude/secrets
cat > ~/.claude/secrets/agentport.env <<'EOF'
# GitHub auth — TWO options, pick one:
#  (a) Leave GITHUB_TOKEN unset and run `gh auth login` once. github_ingest.py
#      falls back to `gh auth token`. Simplest.
#  (b) PAT (scopes: repo if private; or fine-grained 'Read' on Issues/PR/Discussions/Contents)
#      https://github.com/settings/tokens
#      GITHUB_TOKEN=ghp_...

# GitLab personal access token (read_api + read_repository) — only if you
# have topics whose source-of-truth lives on GitLab.
# https://gitlab.com/-/user_settings/personal_access_tokens
# GITLAB_URL=https://gitlab.com
# GITLAB_TOKEN=glpat-...

# Google Drive — choose ONE auth path (only if you ingest Google Docs):
# (a) Service account (recommended): create at https://console.cloud.google.com → IAM → Service Accounts
#     and share each Drive doc/folder with the service account email.
# GOOGLE_SERVICE_ACCOUNT_JSON=/home/$USER/.claude/secrets/google-sa.json
# (b) OAuth refresh token (alternative)
# GOOGLE_OAUTH_CLIENT_ID=...
# GOOGLE_OAUTH_CLIENT_SECRET=...
# GOOGLE_OAUTH_REFRESH_TOKEN=...

# Slack user token — only if Slack channel enabled AND you want ingest of
# search + private channel history beyond what the bot token can see.
# SLACK_INGEST_TOKEN=xoxp-...
EOF
chmod 600 ~/.claude/secrets/agentport.env
```

# 每個新 topic 的流程

```bash
cd <agentport-checkout>

# 0. （可選）引導式一條龍：檢查依賴、選頻道、建 topic、provision、驗證
tools/onboard-topic.sh

# ——或手動逐步——

# 1. Create skeleton
tools/create-topic.sh <topic-name> [<github-repo-url>] [<owner>/<repo>]

# 2. Provision 至少一個頻道
tools/provision-discord-app.sh <topic-name>
# 跟著 stdout 提示在 Developer Portal 建 app + bot、開 Message Content
# Intent、貼 token、貼你自己的 Discord user id、點 invite URL 把 bot
# 拉進你的 server

tools/provision-slack-app.sh <topic-name>
# 跟著 stdout 提示完成 Install + 拿 token，填到 bot.env

# run-topic.sh 每次啟動自動偵測 bot.env 裡哪些頻道有 token，只載那些

# 3. Push topic repo to GitHub (optional but recommended)
gh repo create <you>/topic-<topic-name> --private
cd ~/workspace/topic-<topic-name>
git remote add origin git@github.com:<you>/topic-<topic-name>.git
git push -u origin main

# 4. Launch listener — preferred path is systemd user service (see deploy/README.md)
bash deploy/install.sh
systemctl --user enable --now topic-agent@<topic-name>.service
tools/attach-topic.sh <topic-name>   # live TUI (each topic uses its own tmux server)
```

新建 topic 預設 `ingest_state.state = "ready"`，agent 直接活著、可接 @mention。
還沒記憶之前對問題回 `[無來源]` 軟拒絕（PRD §3.3）— 不是 broken，是合法狀態。

**Discord 提醒**：DM 開箱即用（owner 已在 allowFrom），但「伺服器頻道」要在
`.discord-state/access.json` 的 `groups` 登記過該 channel id 才收得到訊息
（沒登記 = 靜默 drop，連 👀 ack 都沒有）：

```json
"groups": {"<channel_id>": {"allowFrom": ["<owner_id>"], "requireMention": true}}
```

memory 累積的路徑：

- ad-hoc：頻道對 bot 說 `@bot ingest <url>`，owner 餵一個 source 它吸一個
- 一次性大量 ingest（owner 有 30-40 分鐘 + 想得清楚 scope 時才跑）：

```bash
tools/bootstrap-topic.sh <topic-name>
```

cold-start 模式期間 agent 對頻道 mention 回「正在吸收歷史」直到完成。

- 自動同步：編輯 topic 的 `sync.json`——`session_digest.keywords` 填 regex
  fragments（從 Claude Code session 紀錄撈 topic 相關片段）、`repo_sync` 填要
  定期快照的 repo + 檔案清單。nightly timer（`deploy/systemd/`）會跑
  `tools/sync-session-memory.sh` 自動吸收。

# 升級 Slack scopes（已部署的 topic；Slack-only）

`templates/slack-app-manifest.yml` 是 source of truth。當你改 manifest（加
scope、改 event subscriptions），新建的 topic 直接吃到新版本，但
已存在的 topic agent 的 live Slack app 還停在舊 scope — Slack 不會回頭比對。

`tools/migrate-app-scopes.sh <topic-name>` 把已部署的 app 拉到跟 template
對齊。流程：

1. `apps.manifest.export` 抓 live manifest
2. 跟 template 比，列出缺的 scope（只加不刪）
3. `apps.manifest.update` 推回去
4. 印 Reinstall URL（**必走**：scope 改了 OAuth token 要 reissue，
   舊 token 在 reinstall 那刻被 revoke）
5. 你貼新 bot token 進 stdin，腳本 auth.test 驗過後寫回 `bot.env`
6. `systemctl --user restart topic-agent@<topic>.service` 接新 token

如果你想跳過 token 互動步驟（要自己 handle bot.env），第 5 步 prompt 留空
Enter 過去就好。

```bash
tools/migrate-app-scopes.sh <topic-name>
```

# Troubleshooting

常見錯誤 + 修法都收在主 README 的 [FAQ](../README.md#faq)：

- `systemctl --user` 噴 "Failed to connect to bus" → FAQ #6（XDG_RUNTIME_DIR）
- Service 起來 10 秒就掛掉、log 寫 "command not found" → FAQ #1 + systemd PATH 不吃 bashrc，要在 unit 內 `Environment=PATH=...`
- Bot 在頻道叫不動 → FAQ #1（gate/access.json 檢查順序）
- Slack bot 看不到 private channel → 確認 `groups:read` scope 有給；scope 改了用 FAQ #4 的 migrate
- claude TUI crash 了沒重起 → FAQ #5
- bootstrap 跑到一半中斷想接著做 → claude `--resume` 接舊 session
- Discord bot 沒回訊息 → FAQ #16（intent 沒開 / 沒共享 server / access.json 的 allowFrom 或 groups 沒登記）

# 平日操作

- @bot 在已啟用頻道提問 → bot 回覆（Slack streaming / Discord 一次性），引來源、標信心
- Owner 直接 @bot 說「把 X 寫進記憶 + 來源 Y」→ bot 走 owner 路徑直接 commit
- Contributor @bot 說同樣的話 → bot 走 propose 路徑進 pending/
- Owner @bot 「list pending」/ 「approve <file>」/ 「reject <file>」
- Owner @bot 「ingest <url>」吸收新來源
- 隨時 `cd ~/workspace/topic-<name> && git log -- memory/<section>.md` 看 section 歷史

# 維運

- tmux session 掛了：`systemctl --user restart topic-agent@<name>.service`
- 修 skill：在 agentport/ 改 + push → 所有 topic 立刻拿到（symlink）
- 加新 source 類型：在 core/ingest/ 加 script + 改 skills/ingest-source/SKILL.md
- Slack bot token rotation：去 Slack admin reissue + 改 bot.env + 重啟 listener
- Discord bot token rotation：Developer Portal → Bot → Reset Token + 改 bot.env 的 `DISCORD_BOT_TOKEN` + 重啟 listener
