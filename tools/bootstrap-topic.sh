#!/usr/bin/env bash
# Cold-start ingest for a topic.
#
#   tools/bootstrap-topic.sh <topic-name>
#
# Launches an interactive claude session with a playbook prompt that:
#   1. Pulls SCM repo content (issues, PRs/MRs, wiki) when SCM_REPO is set
#   2. Pulls chat history from the enabled channels
#   3. Asks the owner for Google Doc URLs / local files / other sources
#   4. Reads all of it and drafts the initial memory/*.md content
#   5. Asks the owner to review and commit
#
# The topic stays in `bootstrapping` state throughout, so inbound mentions get
# a canned "still ingesting" reply instead of silence.
#
# Channel resolution, token materialization and .mcp.json all come from
# run-topic.sh --print-argv. This script used to carry its own copy of that
# logic, and the copy rotted: it still answered the development-channels
# confirmation once per enabled channel, months after run-topic.sh learned that
# the CLI shows a single combined prompt and the extra keystrokes land in the
# chat input as a user message. One prepare stage, one place to fix.
set -euo pipefail

TOPIC_NAME="${1:?usage: bootstrap-topic.sh <topic-name>}"
TOPIC_ROOT="${TOPIC_ROOT:-$HOME/workspace}"
TOPIC_DIR="$TOPIC_ROOT/topic-${TOPIC_NAME}"
SEED_DIR="$(cd "$(dirname "$0")/.." && pwd)"

[ -d "$TOPIC_DIR" ] || { echo "error: $TOPIC_DIR not found" >&2; exit 1; }

# Mark the topic as bootstrapping, with an ETA the canned reply can quote.
python3 - "$TOPIC_DIR/ingest_state.json" <<'PY'
import datetime, json, pathlib, sys
p = pathlib.Path(sys.argv[1])
try:
    state = json.loads(p.read_text(encoding="utf-8"))
except (OSError, ValueError):
    state = {}
state["state"] = "bootstrapping"
state["eta"] = (datetime.datetime.now()
                + datetime.timedelta(hours=2)).isoformat(timespec="seconds")
p.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
print("state → bootstrapping")
PY

set -a
# shellcheck disable=SC1091  # per-topic file, not present at lint time
source "$TOPIC_DIR/bot.env"
set +a
unset ANTHROPIC_API_KEY  # force subscription auth

# Prepare the topic and take the argv from the one place that knows how.
# AGENTPORT_NO_RESUME: cold start must be a fresh conversation — resuming would
# drop this playbook into the middle of an existing one.
mapfile -t CLAUDE_ARGV < <(AGENTPORT_NO_RESUME=1 "$SEED_DIR/tools/run-topic.sh" "$TOPIC_NAME" --print-argv)
[ "${#CLAUDE_ARGV[@]}" -gt 0 ] || { echo "run-topic.sh produced no argv" >&2; exit 1; }

# Which channels ended up enabled, read back off the argv rather than
# re-deriving it from bot.env. The prompt below changes shape per channel, and
# a second derivation is a second thing that can disagree.
SLACK_ENABLED=0; DISCORD_ENABLED=0; LINE_ENABLED=0
for a in "${CLAUDE_ARGV[@]}"; do
  case "$a" in
    server:slack)   SLACK_ENABLED=1 ;;
    server:discord) DISCORD_ENABLED=1 ;;
    server:line)    LINE_ENABLED=1 ;;
  esac
done

# The gate fails closed, and no adapter has written .current-role yet — say who
# this is, or cold start cannot write a single memory file.
export AGENTPORT_ROLE=owner

# Cold-start prompt — guides Claude to systematically ingest each source.
SCM_INGEST="${SEED_DIR}/core/ingest/github_ingest.py"
case "${SCM_TYPE:-github}" in
  gitlab) SCM_INGEST="${SEED_DIR}/core/ingest/gitlab_ingest.py" ;;
  github|"") SCM_INGEST="${SEED_DIR}/core/ingest/github_ingest.py" ;;
  *) echo "warning: unknown SCM_TYPE=${SCM_TYPE}; defaulting to github" >&2 ;;
esac

# Per-channel prompt fragments — only enabled channels appear in the playbook.
SLACK_STAGE="（跳過 — 這個 topic 沒啟用 Slack）"
if [ "$SLACK_ENABLED" = "1" ]; then
  SLACK_STAGE="問題 1：'哪些 Slack channel 跟 ${TOPIC_NAME} 強相關？給我 channel name / id（多個用逗號）'
- 等回答，逐個跑 ${SEED_DIR}/core/ingest/slack_ingest.py channel-history <id> --days 90
- 每個 channel 吸完，回報「拿到 N 則訊息、看起來討論什麼」
- 全部跑完，問問題 2

問題 2：'topic 在 Slack 上有沒有特定關鍵字（例 \"QA\"、\"test plan\"）？跨 channel 搜，回 keyword 或 'no''
- 是就 slack_ingest.py search '<keyword>'
- 否就跳"
fi

DISCORD_STAGE="（跳過 — 這個 topic 沒啟用 Discord）"
if [ "$DISCORD_ENABLED" = "1" ]; then
  DISCORD_STAGE="問 owner：'Discord 上哪些頻道跟 ${TOPIC_NAME} 相關？（頻道要先在 .discord-state/access.json 的 groups 登記過 bot 才看得到）'
- 逐個用 mcp__discord__fetch_messages(chat_id, limit=100) 拉近期歷史
- 摘要寫進 sources/local/discord-<channel>-<date>.md，回報「拿到 N 則、討論什麼」"
fi

CHANNEL_AUTH_CHECK=""
if [ "$SLACK_ENABLED" = "1" ]; then
  CHANNEL_AUTH_CHECK="跑 'curl -s -H \"Authorization: Bearer \$SLACK_BOT_TOKEN\" https://slack.com/api/auth.test' 驗證 Slack token 仍有效，預期 ok=true。"
fi
if [ "$DISCORD_ENABLED" = "1" ]; then
  CHANNEL_AUTH_CHECK="${CHANNEL_AUTH_CHECK}跑 'curl -s -H \"Authorization: Bot \$DISCORD_BOT_TOKEN\" https://discord.com/api/v10/users/@me' 驗證 Discord token 仍有效，預期回傳含 bot id 的 JSON。"
fi

PROMPT="你是 ${TOPIC_NAME} 的 topic expert，正在進行 cold-start ingest。

# 互動原則（最重要）
1 一次只問 owner「一個問題」，等回答再問下一個。「絕不」一次倒一大串 plan
2 每問完一輪，立刻動手做（call ingest script、寫檔），不要 dump 計畫等 owner 同意
3 owner 答「不知道 / 跳過 / 都沒有」也是合法答案，記下來繼續往下走
4 每個來源 ingest 完，回報「拿了什麼」「來源 link」「初步觀察」，再問下一個

# 當前 context（不要再問 owner）
- Topic name: ${TOPIC_NAME}
- Memory repo: ${TOPIC_DIR}（已是 git repo）
- SCM repo: $([ -n "${SCM_REPO:-}" ] && echo "${SCM_REPO} (type=${SCM_TYPE:-github})" || echo "(none — non-repo topic，跳過 SCM 階段)")
- 啟用頻道: $([ "$SLACK_ENABLED" = "1" ] && echo -n "Slack ")$([ "$DISCORD_ENABLED" = "1" ] && echo -n "Discord")
- Ingest scripts 在 ${SEED_DIR}/core/ingest/（github_ingest.py / gitlab_ingest.py / drive_ingest.py / slack_ingest.py）
- Ingest scripts 用 PEP 723 self-contained 格式（uv run --script shebang），「絕不」用 \`python3 <path>\` 呼叫 — 那會缺 dep。直接跑：\`${SEED_DIR}/core/ingest/github_ingest.py repo-list <owner/repo>\`
- 共用 service tokens 在 ~/.claude/secrets/agentport.env（已載入）
- Owner id: Slack=${OWNER_SLACK_USER_ID:-(未設)} / Discord=${OWNER_DISCORD_USER_ID:-(未設)}

# 互動腳本（一問一答）
按照下面順序，「每次只發一個問題」。owner 回完，你回報吸收結果再問下一個。

## SCM 階段
$(if [ -n "${SCM_REPO:-}" ]; then
    echo "問 owner：'要不要先吸 ${SCM_REPO} 的 repo-list？'。等回答，是就跑 ${SCM_INGEST} repo-list ${SCM_REPO}，列出 top issues / PRs / discussions 給 owner 看，問哪幾個重要要深 ingest。"
  else
    echo "（跳過 — SCM_REPO 未設）"
  fi)

## Slack 階段
${SLACK_STAGE}

## Discord 階段
${DISCORD_STAGE}

## 文件階段
問：'有相關 Google Doc / Drive 文件、或本地檔案嗎？貼連結或路徑（多個就一行一個）。沒有回 'no''
- Google Doc 逐個 ${SEED_DIR}/core/ingest/drive_ingest.py doc <id>
- 本地檔案 copy 進 sources/local/ 後用 Read 摘要
- 每個吸完，回報「標題 + 簡短摘要」

## People 階段
問：'誰是 ${TOPIC_NAME} 的核心利害關係人？列 handle（@xxx）或姓名，每行一個，至少包括 owner、主要 contributor、外部 stakeholder。沒有就回 'just me''
- 對每個人，問 owner 「他對此 topic 的 RACI 角色 / 專長是什麼」
- 寫進 memory/people.md（先丟初稿問 owner 對不對）

## 整理階段（不要再問，主動做）
吸完所有來源，動手寫 memory/*.md 的「初稿」：
- memory/background.md — 為什麼 ${TOPIC_NAME} 存在 + glossary（領域術語）
- memory/current_understanding.md — 目前共識
- memory/decisions.md — 拍板決策（要追溯到具體 source）
- memory/open_questions.md — 未解問題 + 已識別風險（標 [Q] / [R]）
- memory/commitments.md — 對外承諾
- memory/people.md — 已從 People 階段填完（這裡只需確認）

寫完每個 section，丟 diff 給 owner 看，問「這段 OK 嗎 / 哪裡要改」。owner 確認後該 section commit 一次（commit message: 'cold-start: <section> initial draft')。

## 自我驗證階段（在進結束報告前必須做）

寫完所有 section 後，先「不」要結束。執行 self-test，確認這個 agent 確實能用：

1 跑 \`ls -la memory/\` 列出 6 個 section 檔，確認每個都「非空」且有 commit 過。如果某個 section 是空檔，回去填或標 '(尚未有內容)'。
2 跑 \`cat ingest_state.json\`，確認 state='ready'。如果不是，把它改成 ready 再 commit。
3 ${CHANNEL_AUTH_CHECK}
4 試一個 sample Q&A — 從你剛寫的 current_understanding.md 挑一個明顯命題（例 'X 是什麼' / 'Y 由誰負責'），自己用 read-memory 找答案 + 套 CLAUDE.md 'Source citation rules' 內聯規格 emit 引用區塊（這是模擬 agent 被 mention 後會做的事）。owner 看了能接受，再進下一步。
5 跑 'git log --oneline -10' 確認 commit 數量符合預期（至少 5-7 個 commit：每 section 一個 + ingest + state-ready）

任何一步失敗或結果不對 → 回頭修。Self-test 通過才往下走。

## 結束
全 6 個 section 都 commit 完 + self-test 通過：
- 把 ${TOPIC_DIR}/ingest_state.json 的 state 改成 'ready'，加 commit（如果 self-test 步驟 2 已做就跳）
- 對 owner 報告以下「setup 完成」訊息（中文 + 列點，包含這 3 段）：

  ### 1. setup 已完成 ✅ + self-test 結果
  逐項列：
  - 建好的 git repo URL、頻道 app（Slack app / Discord bot）
  - commit 數量 + 最新 5 個 commit 訊息
  - memory section 各長多大（行數）
  - sources 數量
  - self-test 5 項全部 PASS（如果有 fail 列出來）
  - 剛剛模擬的 Q&A 是否合理（owner 確認過再標 PASS）

  ### 2. 跑 smoke test + 起 long-lived 互動 session（透過 systemd）
  bot.env 已預設 SKIP_PERMS=1（create-topic.sh seed），不需手動加。
  新架構：不用 claude -p（2026-06-15 後算 Pool 2 計費）。systemd user
  service 包 per-topic tmux server 包 long-lived interactive claude
  session；頻道 plugin 用 MCP notification 餵訊息進來。

  \`\`\`
  # smoke test（memory size、state、啟用頻道的 token/plugin 檢查、DM owner）
  tools/smoke-test-topic.sh ${TOPIC_NAME}

  # 一次性 install systemd unit（每台機器一次）
  bash deploy/install.sh

  # enable + start topic agent service
  systemctl --user enable --now topic-agent@${TOPIC_NAME}.service

  # attach to TUI（per-topic tmux server，所以要透過 wrapper）
  tools/attach-topic.sh ${TOPIC_NAME}
  \`\`\`
  頻道端把 bot 拉進相關 channel（Slack /invite；Discord 在 access.json groups 登記），然後 @ 試水溫。Smoke 任一步 fail 先修再 launch。

  ### 4. 「中控台」用法（重要）
  「不要」關掉這個 bootstrap session — 它就是 owner 控制台。同一個 Claude TUI 帶 CLAUDE.md + memory + skills 全部 context，你可以繼續：
   - 直接讀 / 編 memory（owner 直寫）
   - 「list pending」/ 「approve <file>」處理 contributor 提案
   - 「ingest <url>」吸新來源
   - ad-hoc 查詢

  之後重新進中控台用：
  \`\`\`
  ${SEED_DIR}/tools/console-topic.sh ${TOPIC_NAME}
  \`\`\`
  跟 bootstrap 共用 memory + skills，不會 re-run ingest playbook。

  ### 5. 角色 + Approve 流程（給 owner 看的提醒）
  - 你是 owner（Slack=${OWNER_SLACK_USER_ID:-未啟用} / Discord=${OWNER_DISCORD_USER_ID:-未啟用}）。任何其他人都是 contributor。
  - Owner @bot：所有事都可做（直寫 memory、approve、ingest、刪除）
  - Contributor @bot：只能查詢 / 整理 thread / 提交 propose-memory-update（會進 pending/，不直接動 memory）
  - 強制機制：頻道 plugin 把 role 寫進 .current-role，PreToolUse hook 讀它阻擋 contributor 寫 memory/，純 code-level 阻擋
  - 收到 contributor 提案：bot 會通知你，你回「@bot approve <pending-file>」或「@bot reject <pending-file> <理由>」

# 寫 memory 的紀律（絕不違反）
- 每段必須引「具體連結」（GitHub/GitLab URL、Doc URL、頻道 permalink、sources/ 路徑...）
- 沒夠強來源就標 [來源單一未確認] 或別寫
- 衝突要列出來問 owner，不自己選邊
- 用中文寫 memory（與 owner 一致）

# 開工
現在直接發第一個問題給 owner，不要 echo 整個 plan。"

# Interactive Claude session — cold start needs back-and-forth with the
# owner (which channels, which docs, conflict adjudication, etc.). We run
# the topic dir as the CLAUDE.md / cwd so the agent loads its own persona,
# and pass the cold-start playbook as the first user prompt.
#

PROMPT_FILE="$(mktemp -t "bootstrap-prompt-${TOPIC_NAME}.XXXXXX")"
trap 'rm -f "$PROMPT_FILE"' EXIT
printf '%s' "$PROMPT" > "$PROMPT_FILE"

# Cold start is owner-driven and runs a lot of tool calls (ingest scripts, git,
# file writes, channel reads); prompting on each one stops the flow dead. The
# argv from run-topic.sh already carries the permission mode chosen by
# SKIP_PERMS in bot.env.

# --dangerously-load-development-channels raises one interactive confirmation,
# combined across every enabled channel. Answer it exactly once: a second "1"
# lands in the chat input and gets submitted as a user message.
if [ -n "${TMUX:-}" ] && \
   { [ "$SLACK_ENABLED" = 1 ] || [ "$DISCORD_ENABLED" = 1 ] || [ "$LINE_ENABLED" = 1 ]; }; then
  (
    sleep 5
    tmux send-keys -t "$(tmux display-message -p '#S')" "1" Enter 2>/dev/null || true
  ) &
fi

cd "$TOPIC_DIR"
exec claude "${CLAUDE_ARGV[@]}" "$(cat "$PROMPT_FILE")"
