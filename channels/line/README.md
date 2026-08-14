# LINE channel plugin

agentport 的第三個 channel：LINE Messaging API。與 Slack/Discord plugin 同一套
合約（MCP stdio server、`notifications/claude/channel` inbound、`reply` tool、
access.json gate、role hook），差別在傳輸層 — LINE 沒有長連線 gateway，改由
本 plugin 內嵌的 loopback HTTP webhook listener 接收事件。

## Runtime

Bun（`bun run server.ts`；TypeScript 直接跑，不編譯）。`bun.lock` 由
**Bun 1.3.14** 產生，是文字格式所以 diff 看得懂。

要動相依就用 Bun：`npm install` 會產生 `package-lock.json`，兩份 lockfile 並存
等於沒有 lockfile——之後誰裝到哪一份取決於他手邊有什麼工具。CI 用
`bun install --frozen-lockfile` 擋住 lockfile 與 package.json 不同步。

## 需求

- LINE Official Account + Messaging API channel（secret + long-lived token）
- 公網 HTTPS 端點：任何 TLS 反代（caddy/nginx/lucky/cloudflared）把
  `https://<domain>/line/webhook` 轉到 `127.0.0.1:${LINE_WEBHOOK_PORT}`（預設 18789）
- 在 LINE Developers Console（或 PUT /v2/bot/channel/webhook/endpoint）登記該 URL

## 設定

bot.env（per topic）：

    LINE_CHANNEL_SECRET=...
    LINE_CHANNEL_ACCESS_TOKEN=...
    OWNER_LINE_USER_ID=U...
    # LINE_WEBHOOK_PORT=18789

your launcher 會 materialize `.line-state/.env` + 種 access.json（owner 進
allowFrom）。群組要另外在 `.line-state/access.json` 的 `groups` 登記
（key = groupId，`requireMention` 預設 true；LINE 的 mention 偵測用
message.mention.mentionees[].isSelf）。

## 注意

- replyToken 免費但 ~1 分鐘內單次有效；逾時自動 fallback 到 push API，
  push 消耗 OA 每月訊息額度 — 回覆合併成一則，別碎碎念。
- Bot 只能收自己所在聊天室的訊息；沒有歷史查詢 API（無 fetch_messages）。
- webhook listener 只綁 loopback；反代會把整個網域的流量都轉過來，
  非 webhook path 一律 404，webhook 一律驗 X-Line-Signature。
