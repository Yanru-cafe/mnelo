#!/usr/bin/env bash
# mnelo/install.sh — one-command install for local-first memory layer
#
# 用法 (在刚 clone 出来的 mnelo 目录里跑):
#   bash scripts/install.sh                    # 默认装到 ~/.hermes/memory
#   LIVE_ROOT=~/.mnelo bash scripts/install.sh # 装到新位置
#
# 步骤:
#   1. 检查 Python 3.9+ / git / curl
#   2. 创建 venv (如果没)
#   3. pip install -r requirements.txt
#   4. (可选) 下载 bge-small-zh-v1.5 模型 (~92 MB, 避免首次 recall 冷启动)
#   5. python scripts/init_db.py
#   6. 装 plist (macOS launchd) + launchctl load
#   7. 跑 health_check.py 验证
#
# 设计原则:
#   - idempotent: 可重复跑, 已装的步骤会跳过
#   - 失败早退 (set -euo pipefail)
#   - 文件权限 0600/0700 (P0 安全, 防其他 user 读 KG / schema / config)
#
set -euo pipefail

umask 077  # [P0-1] 默认 0600/0700, 防止其他本地 user 读 mnelo 数据

# ---- 配置 ----
LIVE_ROOT="${LIVE_ROOT:-$HOME/.hermes/memory}"
MNELO_HOME="$(dirname "$LIVE_ROOT")"
PLIST_LABEL="ai.mnelo.mcp"
PLIST_SRC="$(cd "$(dirname "$0")/.." && pwd)/scripts/launchd/${PLIST_LABEL}.plist"
PLIST_DST="$HOME/Library/LaunchAgents/${PLIST_LABEL}.plist"
VENV_DIR="$LIVE_ROOT/.venv"
PY_BIN="${PY_BIN:-python3}"

# ---- 颜色 (CI 环境无 TTY 时降级) ----
if [ -t 1 ]; then
    RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
else
    RED=''; GREEN=''; YELLOW=''; NC=''
fi
log()  { echo -e "[install] $*"; }
warn() { echo -e "[install] ${YELLOW}WARN${NC}: $*"; }
err()  { echo -e "[install] ${RED}ERROR${NC}: $*" >&2; }
ok()   { echo -e "[install] ${GREEN}OK${NC}: $*"; }

# ---- 1. 依赖检查 ----
log "检查依赖..."
command -v "$PY_BIN" >/dev/null 2>&1 || { err "需要 $PY_BIN (Python 3.9+)"; exit 1; }
PY_VERSION="$($PY_BIN -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
log "Python $PY_VERSION"
command -v git >/dev/null 2>&1 || { err "需要 git"; exit 1; }

# ---- 2. 创建 LIVE_ROOT ----
log "准备 live 目录: $LIVE_ROOT"
mkdir -p "$LIVE_ROOT/api" "$LIVE_ROOT/scripts" "$LIVE_ROOT/logs"
chmod 700 "$LIVE_ROOT"

# ---- 3. 复制 repo 文件到 live ----
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
log "同步 repo → live (top-level .py/.sql/.sh)..."
for f in "$REPO_ROOT"/*.py "$REPO_ROOT"/*.sql "$REPO_ROOT"/*.sh; do
    [ -f "$f" ] || continue
    base="$(basename "$f")"
    [ "$base" = "install.sh" ] && continue  # 不复制自己
    cp "$f" "$LIVE_ROOT/$base"
    chmod 600 "$LIVE_ROOT/$base"
done

# ---- 4. 复制 api/ + scripts/ ----
if [ -d "$REPO_ROOT/api" ]; then
    log "复制 api/ ..."
    cp -r "$REPO_ROOT/api/." "$LIVE_ROOT/api/"
    find "$LIVE_ROOT/api" -type f -name "*.py" -exec chmod 600 {} \;
fi
log "复制 scripts/ ..."
cp "$REPO_ROOT/scripts/"*.py "$LIVE_ROOT/scripts/" 2>/dev/null || true
chmod 600 "$LIVE_ROOT/scripts/"*.py 2>/dev/null || true

# ---- 5. venv ----
if [ ! -d "$VENV_DIR" ]; then
    log "创建 venv: $VENV_DIR"
    "$PY_BIN" -m venv "$VENV_DIR"
fi
VENV_PY="$VENV_DIR/bin/python3"
log "venv python: $VENV_PY"

# ---- 6. pip install ----
log "pip install -r requirements.txt ..."
"$VENV_PY" -m pip install --quiet --upgrade pip
"$VENV_PY" -m pip install --quiet -r "$REPO_ROOT/requirements.txt"

# ---- 7. init_db ----
if [ ! -f "$LIVE_ROOT/memory.db" ]; then
    log "初始化数据库..."
    "$VENV_PY" "$LIVE_ROOT/scripts/init_db.py"
    ok "数据库已建: $LIVE_ROOT/memory.db"
else
    log "数据库已存在, 跳过 init_db (想重置就 rm memory.db 再跑)"
fi

# ---- 8. (可选) 预下载 embedder 模型 ----
if [ "${SKIP_MODEL_DOWNLOAD:-0}" != "1" ]; then
    log "预下载 bge-small-zh 模型 (~92 MB, 避免首次 recall 冷启动)..."
    "$VENV_PY" -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-zh-v1.5')" 2>&1 | tail -3 || \
        warn "模型预下载失败, 首次 recall 时会按需下载"
else
    log "跳过模型下载 (SKIP_MODEL_DOWNLOAD=1)"
fi

# ---- 9. auth token ----
TOKEN_FILE="$HOME/.config/mnelo/auth_token"
if [ ! -f "$TOKEN_FILE" ]; then
    log "生成 auth token: $TOKEN_FILE"
    mkdir -p "$(dirname "$TOKEN_FILE")"
    chmod 700 "$(dirname "$TOKEN_FILE")"
    "$VENV_PY" -c "import secrets; print(secrets.token_urlsafe(48))" > "$TOKEN_FILE"
    chmod 600 "$TOKEN_FILE"
    ok "token 已生成"
else
    log "token 已存在: $TOKEN_FILE"
fi

# ---- 10. plist (macOS) ----
if [ "$(uname -s)" = "Darwin" ]; then
    if [ -f "$PLIST_SRC" ]; then
        log "装 launchd plist: $PLIST_DST"
        mkdir -p "$(dirname "$PLIST_DST")"
        # 替换 plist 里的 LIVE_ROOT / VENV_PY 占位符
        sed -e "s|__LIVE_ROOT__|$LIVE_ROOT|g" \
            -e "s|__VENV_PY__|$VENV_PY|g" \
            -e "s|__VENV_DIR__|$VENV_DIR|g" \
            -e "s|__MNELO_HOME__|$MNELO_HOME|g" \
            "$PLIST_SRC" > "$PLIST_DST"
        chmod 644 "$PLIST_DST"

        # 先 unload (如果已存在) 再 load, 防 duplicate
        launchctl unload "$PLIST_DST" 2>/dev/null || true
        launchctl load "$PLIST_DST"
        ok "plist 已装 + launchd load 成功"
        log "日志: tail -f $MNELO_HOME/logs/mnelo.mcp.log"
    else
        warn "plist 模板不存在: $PLIST_SRC (跳过 launchd 装)"
    fi
else
    log "非 macOS, 跳过 launchd (手动跑: MNELO_HOME=$MNELO_HOME $VENV_PY $LIVE_ROOT/mcp_server.py --transport sse)"
fi

# ---- 11. health check ----
log "跑 health_check.py 验证..."
"$VENV_PY" "$LIVE_ROOT/scripts/health_check.py" || warn "health_check 失败, 但 install 已完成"

# ---- 12. backup 配置 (TASKS_BACKUP_RESTORE A4) ----
if [ -t 0 ] && [ "${MNELO_MEMORY_BACKUP_SKIP_PROMPT:-0}" != "1" ]; then
    log "配置自动备份..."
    read -r -p "启用定期备份? [Y/n] " enable_backup
    case "$enable_backup" in
        [Nn]*)
            ok "跳过备份配置"
            ;;
        *)
            echo "  快照位置:"
            echo "    1) ~/.hermes/memory/snapshots/                       (本地, 不出机器)"
            echo "    2) ~/macbot-memory/work/mnelo-snapshots/             (dr-backup 自动 rsync → NAS, 不推 GitHub)"
            echo "    3) ~/macbot-memory/work/mnelo-snapshots/             (dr-backup 自动 rsync + git push → GitHub)"
            echo "       ⚠️  GitHub 仓库 **必须是 PRIVATE** — mnelo.db 含 PII, 推 public = 泄露"
            echo "    注: 选项 2 / 3 写到同一路径, 差异在 dr-backup.sh 配没配 git push"
            if ! gh repo view chinesewebman/macbot-memory --json visibility 2>/dev/null | grep -q PRIVATE; then
                warn "无法确认 macbot-memory 仓库是 private. 主人请手动验证后再启选 3."
            fi
            echo "    4) /path/to/custom/                                  (自定义)"
            read -r -p "  Choose [1/2/3/4]: " loc_choice
            case "$loc_choice" in
                1) SNAP_DIR="$HOME/.hermes/memory/snapshots" ;;
                2|"") SNAP_DIR="$HOME/macbot-memory/work/mnelo-snapshots" ;;
                3) SNAP_DIR="$HOME/macbot-memory/work/mnelo-snapshots" ;;
                *) SNAP_DIR="$loc_choice" ;;
            esac

            read -r -p "保留最近多少份全备? (老的自动删) [30]: " ret_count
            RETENTION="${ret_count:-30}"
            if ! [[ "$RETENTION" =~ ^[0-9]+$ ]] || [ "$RETENTION" -lt 1 ]; then
                RETENTION=30
            fi

            # 写 config.toml [backup]
            CONFIG_TOML="$LIVE_ROOT/config.toml"
            [ -f "$CONFIG_TOML" ] || cp "$REPO_ROOT/config.toml.example" "$CONFIG_TOML"
            chmod 600 "$CONFIG_TOML"
            # 删旧 [backup] section (idempotent)
            sed -i.bak '/^\[backup\]/,/^retention = /d' "$CONFIG_TOML" 2>/dev/null || true
            cat >> "$CONFIG_TOML" <<TOML

[backup]
enabled = true
snapshot_dir = "$SNAP_DIR"
schedule = "wed+sun"
retention = $RETENTION
TOML
            chmod 600 "$CONFIG_TOML"
            ok "backup config 已写: snapshot_dir=$SNAP_DIR retention=$RETENTION"

            # 装 backup plist (macOS)
            if [ "$(uname -s)" = "Darwin" ]; then
                BACKUP_PLIST_SRC="$REPO_ROOT/scripts/launchd/ai.mnelo.backup.plist"
                BACKUP_PLIST_DST="$HOME/Library/LaunchAgents/ai.mnelo.backup.plist"
                if [ -f "$BACKUP_PLIST_SRC" ]; then
                    sed -e "s|__LIVE_ROOT__|$LIVE_ROOT|g" \
                        -e "s|__VENV_PY__|$VENV_PY|g" \
                        -e "s|__MNELO_HOME__|$MNELO_HOME|g" \
                        "$BACKUP_PLIST_SRC" > "$BACKUP_PLIST_DST"
                    chmod 644 "$BACKUP_PLIST_DST"
                    launchctl unload "$BACKUP_PLIST_DST" 2>/dev/null || true
                    launchctl load "$BACKUP_PLIST_DST"
                    ok "backup plist 已装 (周三+周日 03:00 自动跑)"
                    log "备份日志: tail -f $MNELO_HOME/logs/mnelo.backup.log"
                else
                    warn "backup plist 模板不存在: $BACKUP_PLIST_SRC (跳过)"
                fi
            fi
            ;;
    esac
else
    log "非交互模式 / 跳过备份询问 (设 MNELO_MEMORY_BACKUP_SKIP_PROMPT=0 强制 prompt)"
fi

ok "✅ install 完成"
log "测试:"
log "  echo '{\"jsonrpc\":\"2.0\",\"method\":\"initialize\",\"params\":{\"protocolVersion\":\"2024-11-05\",\"capabilities\":{},\"clientInfo\":{\"name\":\"test\",\"version\":\"1.0\"}},\"id\":1}' | $VENV_PY $LIVE_ROOT/mcp_server.py --transport stdio"