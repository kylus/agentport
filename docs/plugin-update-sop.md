# Plugin Update SOP

Slack plugin（`claude-code-slack-channel`）有新版本時的標準處理流程。

## 觸發條件

每天凌晨 02:00 的 nightly sync 會自動檢查。有新版本時，bot 會發 Slack DM 給你：

> ⚠️ Slack plugin 有新版本需要更新
> 分支: `feat/owner-role-hook`
> 落後: N 個 commit

---

## 更新步驟（一行搞定）

```bash
cd ~/cc-project/agentport && tools/update-plugin.sh
```

這個指令會：
1. `git pull --rebase` plugin repo
2. `bun install` 更新依賴
3. `systemctl --user restart topic-agent@*.service` 重啟所有 topic agent
4. 自動確認 `--dangerously-load-development-channels` 對話框
5. 驗證 bun plugin 有重新啟動

---

## 驗證方法

更新後在 Slack 頻道發 `@<bot-name> ping`，確認有 👀 反應且有回覆。

如果沒反應：

```bash
# 看 TUI 目前狀態
<your-attach-command> <topic>   # e.g. tmux attach -t topic-<topic>
# Ctrl-b d 離開

# 看 service log
journalctl --user -u topic-agent@<topic-name>.service -n 20 --no-pager
```

---

## 手動更新（如果 update-plugin.sh 失敗）

```bash
# 1. 更新 plugin
cd ~/workspace/claude-code-slack-channel
git pull --rebase origin feat/owner-role-hook
bun install

# 2. 重啟 agent
export XDG_RUNTIME_DIR=/run/user/$(id -u)
systemctl --user restart topic-agent@<topic-name>.service

# 3. 確認 --dangerously-load-development-channels 對話框（5 秒後自動）
# 或手動：<your-attach-command> <topic>   # e.g. tmux attach -t topic-<topic> → 按 1 Enter
```

---

## 版本落後過多的症狀

| 症狀 | 原因 | 修法 |
|------|------|------|
| Bot 沒有 👀 reaction | plugin MCP 協定不相容，bun 啟動後馬上崩 | 執行 `tools/update-plugin.sh` |
| `bun` 不在 `ps aux` 裡 | 同上 | 同上 |
| Bot 回覆但功能異常 | 部分 API 行為改變 | 更新後觀察 |
