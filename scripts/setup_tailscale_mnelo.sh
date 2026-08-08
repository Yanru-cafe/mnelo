#!/usr/bin/env bash
# setup_tailscale_mnelo.sh — [8/8 Tailscale multi-agent] 已有 mnelo 装 Tailscale 多机
#
# 主人 8/8 拍板 mnelo 改成 multi-agent 远程调用. 单机本地用户跑这一脚本,
# 5 步把 mnelo mcp_server 改成 Tailscale mesh 多机共享:
#   1. 验证 Tailscale daemon 在跑
#   2. 拿 macbook 的 Tailscale IP (100.64.0.0/10)
#   3. 备份 + 改 ~/Library/LaunchAgents/ai.mnelo.mcp.plist --host 0.0.0.0
#   4. launchctl unload + load (重启 plist)
#   5. smoke test: curl 127.0.0.1 + Tailscale IP /health
#
# 已知前置:
#   - mnelo 已 install.sh (默认 127.0.0.1, 没 multi-agent) 或新建装
#   - Tailscale 已在 mesh (macOS: tailscale up, 或 macOS App Store Tailscale.app)
#   - 主人已 mentor 燕如 / 别的 agent 加到 Tailscale mesh + token 共享
#
# 安全保证:
#   - 0.0.0.0 bind 不等于暴露公网 — mcp_server 白名单只接 loopback + 100.64.0.0/10
#     (commit 3e538de, 33 tests 绿). LAN / 公网 / IPv6 仍拒绝.
#   - plist 备份保留 .bak.YYYYMMDD-pre-tailscale (走 customization hygiene)
#
# 用法:
#   bash scripts/setup_tailscale_mnelo.sh                # 默认 mnelo plist 路径
#   PLIST_DST=/path/to/ai.mnelo.mcp.plist bash ...       # 自定义 plist
#   MNELO_TOKEN=$(cat ~/.config/mnelo/auth_token) bash ...  # 验证用 Bearer token

set -euo pipefail

# ---- 配置 ----
PLIST_DST="${PLIST_DST:-$HOME/Library/LaunchAgents/ai.mnelo.mcp.plist}"
BACKUP_SUFFIX="$(date +%Y%m%d)-pre-tailscale"
TOKEN_FILE="${MNELO_TOKEN_FILE:-$HOME/.config/mnelo/auth_token}"
TAILSCALE_HOST_RE="^100\.(6[4-9]|[7-9][0-9]|1[0-1][0-9]|12[0-7])\."

# ---- 颜色 ----
if [ -t 1 ]; then
    RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
else
    RED=''; GREEN=''; YELLOW=''; NC=''
fi
log()  { echo -e "[setup-tailscale] $*"; }
ok()   { echo -e "[setup-tailscale] ${GREEN}OK${NC}: $*"; }
warn() { echo -e "[setup-tailscale] ${YELLOW}WARN${NC}: $*"; }
err()  { echo -e "[setup-tailscale] ${RED}ERROR${NC}: $*" >&2; }

# ---- Step 1: Tailscale daemon 在跑吗? ----
log "Step 1/5: 验证 Tailscale daemon..."
if ! command -v tailscale >/dev/null 2>&1; then
    err "tailscale CLI 不存在. macOS: brew install --cask tailscale, 或 App Store 装 Tailscale.app"
    exit 1
fi
if ! tailscale status >/dev/null 2>&1; then
    err "tailscale daemon 没跑. 启 tailscale: tailscale up 或打开 Tailscale.app"
    exit 1
fi
ok "tailscale daemon 在跑"

# ---- Step 2: 拿 Tailscale IP (100.64.0.0/10) ----
log "Step 2/5: 拿 Tailscale IP..."
TAILSCALE_IP="$(tailscale ip -4 2>/dev/null | head -1 | tr -d '[:space:]')"
if [ -z "$TAILSCALE_IP" ] || ! [[ "$TAILSCALE_IP" =~ $TAILSCALE_HOST_RE ]]; then
    err "无法拿 Tailscale IP (got '$TAILSCALE_IP'). 确认 tailscale up 跑通"
    exit 1
fi
ok "Tailscale IP: $TAILSCALE_IP"

# ---- Step 3: 改 plist --host 0.0.0.0 ----
log "Step 3/5: 改 plist --host → 0.0.0.0..."
if [ ! -f "$PLIST_DST" ]; then
    err "plist 不存在: $PLIST_DST. 先跑 install.sh (或指定 PLIST_DST)"
    exit 1
fi

CURRENT_HOST="$(plutil -extract ProgramArguments.5 raw "$PLIST_DST" 2>/dev/null || echo '')"
if [ "$CURRENT_HOST" = "0.0.0.0" ]; then
    ok "plist --host 已是 0.0.0.0 (skip 重写)"
elif [ "$CURRENT_HOST" = "127.0.0.1" ]; then
    cp "$PLIST_DST" "$PLIST_DST.bak.$BACKUP_SUFFIX"
    ok "备份: $PLIST_DST.bak.$BACKUP_SUFFIX"
    sed -i '' 's|<string>127.0.0.1</string>|<string>0.0.0.0</string>|' "$PLIST_DST"
    # 验证替换成功
    NEW_HOST="$(plutil -extract ProgramArguments.5 raw "$PLIST_DST" 2>/dev/null || echo '')"
    if [ "$NEW_HOST" != "0.0.0.0" ]; then
        err "plist sed 替换失败 (expected 0.0.0.0, got '$NEW_HOST'). 手动改 plist"
        exit 1
    fi
    ok "plist --host 改成 0.0.0.0"
else
    err "plist --host 现状 '$CURRENT_HOST' 异常 (期望 127.0.0.1 或 0.0.0.0). 手动改 plist"
    exit 1
fi

# ---- Step 4: reload plist (KeepAlive 重启) ----
log "Step 4/5: reload plist (KeepAlive 自动重启)..."
if launchctl unload "$PLIST_DST" 2>/dev/null; then
    ok "unload 成功"
else
    warn "unload 失败 (可能 plist 没在跑, 初始跑)"
fi
if launchctl load "$PLIST_DST" 2>&1; then
    ok "load 成功"
else
    err "load 失败. 手动跑: launchctl load $PLIST_DST"
    exit 1
fi

# ---- Step 5: smoke test 127.0.0.1 + Tailscale IP ----
log "Step 5/5: smoke test /health..."
sleep 2  # 留 2 秒 mcp_server 启动
HEALTH_LOCAL="$(curl -sS -o /dev/null -w "%{http_code}" http://127.0.0.1:8086/health 2>/dev/null || echo '000')"
HEALTH_TAILSCALE="$(curl -sS -o /dev/null -w "%{http_code}" "http://$TAILSCALE_IP:8086/health" 2>/dev/null || echo '000')"

log "  127.0.0.1:8086/health → $HEALTH_LOCAL"
log "  $TAILSCALE_IP:8086/health → $HEALTH_TAILSCALE"

if [ "$HEALTH_LOCAL" != "200" ]; then
    err "本地 /health 失败 (期望 200). 看日志: tail -f $HOME/.hermes/logs/mnelo.mcp.error.log"
    exit 1
fi
if [ "$HEALTH_TAILSCALE" != "200" ]; then
    err "Tailscale IP /health 失败 (期望 200). 看 mcp_server 是否真绑 0.0.0.0"
    exit 1
fi
ok "两个端点都 200"

# ---- Bonus: Bearer auth smoke test (可选) ----
if [ -f "$TOKEN_FILE" ]; then
    TOKEN="$(cat "$TOKEN_FILE" | tr -d '[:space:]')"
    log "Bonus: Bearer auth smoke test..."
    AUTH_RESULT="$(curl -sS -X POST "http://$TAILSCALE_IP:8086/mcp" \
        -H "Authorization: Bearer $TOKEN" \
        -H "Content-Type: application/json" \
        -H "Accept: application/json, text/event-stream" \
        -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"setup_tailscale_smoke","version":"1.0"}}}' \
        2>/dev/null | head -3 || echo 'FAIL')"
    if echo "$AUTH_RESULT" | grep -q '"serverInfo"'; then
        ok "Bearer auth + initialize 200 (serverInfo 拿到)"
    else
        warn "Bearer auth smoke test 失败 (但 /health 通). 燕如侧可能 401"
        log "  first 200 chars: ${AUTH_RESULT:0:200}"
    fi
fi

ok "✅ setup 完成"
log "主人 macbook mnelo mcp_server 现在 Tailscale mesh 共享"
log "  燕如 / 别的 agent: 用 scripts/mnelo_remote_client.py 调"
log "  还原 loopback: cp $PLIST_DST.bak.$BACKUP_SUFFIX $PLIST_DST && launchctl unload $PLIST_DST && launchctl load $PLIST_DST"
