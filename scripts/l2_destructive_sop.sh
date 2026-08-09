#!/usr/bin/env bash
# l2_destructive_sop.sh — [8/7 L2 P1 SOP] L2 hygiene dry-run → 真跑安全流程
#
# 设计动机: hygiene destructive 路径有 audit_undo 兜底, 但需要人工 gate, 不能 cron 自动 destructive.
# 本脚本 5 步流程:
#   1. dry-run 看 candidate 列表 + audit_log 写入 'proposed'
#   2. 主人 review proposals (本脚本只输出 stats + sample)
#   3. 主人 confirm  (输入 y/N)
#   4. destructive 真跑 (confirm_destructive=True), 走 audit_log applied 路径
#   5. 跑后显示 applied 数, 如异常可走 memory_audit_undo 单条恢复
#
# 用法:
#   ./l2_destructive_sop.sh                # 默认 hygiene pass, dry-run 先跑
#   ./l2_destructive_sop.sh --auto-yes     # 跳过 confirm prompt (脚本/CI 用, 主人 NOT 推荐)
#   ./l2_destructive_sop.sh --pass decay   # decay pass 单独跑
#
# 安全保证:
#   - dry-run 阶段不传 confirm_destructive, mnelo 强制只数不改
#   - 真跑阶段要 stdin 输入 'y' 才执行
#   - 任何阶段异常都 echo 最后 applied audit_log id, 主人可走 undo
#
# 反模式:
#   - 不要给本脚本传 --auto-yes 然后塞 cron — 这等于把 destructive 自动跑
#   - 不要传 --confirm-destructive=... 这种 flag — 那是 run_maintenance 参数, 不在本脚本
#
# 相关:
#   - Memory.run_maintenance(passes, dry_run, confirm_destructive): memory.py:1868
#   - Memory.audit_undo(audit_id): memory.py:1841
#   - run_hygiene.py wrapper (本仓 scripts/) — 本脚本就是给它的 CLI 包装

set -euo pipefail

REPO_ROOT="/Users/apple/.hermes/memory"
# [8/9 review B5 fix 2] python 路径走 env 链, 不硬编码 macOS 路径.
# env 顺序: MNELO_VENV_PY (主人 override) > system python3 (Linux/cron 兜底)
VENV_PY="${MNELO_VENV_PY:-$(command -v python3)}"
WRAPPER="$REPO_ROOT/scripts/run_hygiene.py"
DB="$REPO_ROOT/memory.db"
LOG_DIR="$REPO_ROOT/logs"

mkdir -p "$LOG_DIR"

# 配色 (输出更可读)
RED='\033[0;31m'
GRN='\033[0;32m'
YEL='\033[1;33m'
BLU='\033[0;34m'
NC='\033[0m'

log() { echo -e "${BLU}[$(date +%H:%M:%S)]${NC} $*"; }
warn() { echo -e "${YEL}[WARN]${NC} $*" >&2; }
err() { echo -e "${RED}[ERR]${NC} $*" >&2; }
ok() { echo -e "${GRN}[OK]${NC} $*"; }

AUTO_YES=false
PASSES="hygiene"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --auto-yes) AUTO_YES=true; shift ;;
        --pass) PASSES="$2"; shift 2 ;;
        *) err "unknown arg: $1"; exit 2 ;;
    esac
done

if [[ ! -f "$DB" ]]; then
    err "DB not found: $DB"; exit 1
fi

# [1] 检查 mnelo MCP 是否在跑 — hygiene 跟 mcp_server 抢 zvec LOCK, 必须用 usearch backend
# (run_hygiene.py 已强制 MNELO_MEMORY_SEARCH_BACKEND=usearch, 这里只检查 MCP 在不在)
if ! curl -sf --max-time 3 http://127.0.0.1:8086/health >/dev/null 2>&1; then
    warn "mnelo MCP 不在 8086 — hygiene 仍可跑 (走 usearch backend), 但 mcp 不可达 = 主人在 DB 写期间调 recall 会卡"
fi

# [2] Dry-run 阶段 — 输出 candidate 统计 + sample
log "=== [1/5] DRY-RUN hygiene pass ($PASSES) ==="
DRY_LOG="$LOG_DIR/l2-sop.dry-run.$(date +%Y%m%d_%H%M%S).json"
log "输出 → $DRY_LOG"

# [8/9 review B5 fix 1] dry-run 跟 destructive 共用同一 PASSES, 不要默认 hygiene.
# (原代码 DRY_RESULT 没传 passes, 永远跑 default 'hygiene', 不能反映 destructive 实际行为)
DRY_RESULT=$(
    cd "$REPO_ROOT" && \
    MNELO_HOME="${MNELO_HOME:-$HOME/.hermes}" \
    MNELO_MEMORY_SEARCH_BACKEND=usearch \
    MNELO_DRY_RUN_PASSES="$PASSES" \
    "$VENV_PY" "$WRAPPER"
) || {
    err "dry-run 失败. 重跑加 stderr 看错误"; exit 1
}

echo "$DRY_RESULT" | tee "$DRY_LOG" >/dev/null

# 解析 stats
APPLIED=$(echo "$DRY_RESULT" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('applied', '?'))")
SKIPPED=$(echo "$DRY_RESULT" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('skipped', '?'))")
FAILED=$(echo "$DRY_RESULT" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('failed', '?'))")
PROPOSAL_COUNT=$(echo "$DRY_RESULT" | python3 -c "
import json, sys
d = json.load(sys.stdin)
proposals = d.get('proposals', {})
total = sum(len(v) for v in proposals.values() if isinstance(v, list))
print(total)
" 2>/dev/null || echo "?")

log "dry-run 结果: applied=$APPLIED skipped=$SKIPPED failed=$FAILED proposals=$PROPOSAL_COUNT"

# [3] 如果没 proposal, 直接退出
if [[ "$PROPOSAL_COUNT" == "0" ]]; then
    ok "无 candidate, 无需真跑. 退出."
    exit 0
fi

# [4] Confirm gate
log "=== [2/5] CONFIRM 真跑 ==="
if [[ "$AUTO_YES" == "true" ]]; then
    warn "--auto-yes 模式: 跳过 prompt 直接跑 (cron/CI 用, 主人 NOT 推荐)"
else
    echo
    echo -e "${YEL}⚠️  destructive hygiene 会真改 ${PROPOSAL_COUNT} 个 chunk/relation/vector.${NC}"
    echo -e "${YEL}   所有改动可走 memory_audit_undo(<audit_id>) 恢复.${NC}"
    echo -e "${YEL}   建议: 先 dry-run 看 sample proposals 确认无误.${NC}"
    echo
    read -r -p "$(echo -e ${RED}确认 destructive 真跑? 输入 'y' 继续, 其他取消: ${NC})" ans
    if [[ "$ans" != "y" ]]; then
        log "取消. dry-run 已保留在 $DRY_LOG."
        exit 0
    fi
fi

# [5] 真跑 — confirm_destructive=True
log "=== [3/5] DESTRUCTIVE 真跑 ==="
REAL_LOG="$LOG_DIR/l2-sop.destructive.$(date +%Y%m%d_%H%M%S).json"

# [8/9 review B5 fix 3] PASSES / DB_PATH 走 env var, 不嵌进 python -c 字符串.
# 原代码 '$PASSES' '$DB' 直接字符串拼接, 注入面 (e.g. --pass "foo']);import os;os.system('rm -rf /');p=['").
REAL_RESULT=$(
    cd "$REPO_ROOT" && \
    MNELO_HOME="${MNELO_HOME:-$HOME/.hermes}" \
    MNELO_MEMORY_SEARCH_BACKEND=usearch \
    MNELO_DESTRUCTIVE_PASSES="$PASSES" \
    MNELO_DB_PATH="$DB" \
    "$VENV_PY" -c "
import sys
import os
sys.path.insert(0, '$REPO_ROOT')
sys.path.insert(0, '$REPO_ROOT/scripts')
os.environ.setdefault('MNELO_MEMORY_SEARCH_BACKEND', 'usearch')
from memory import Memory
m = Memory(db_path=__import__('pathlib').Path(os.environ['MNELO_DB_PATH']))
import json
# passes 从 env 解析, 逗号分隔. 之前嵌 '$PASSES' 替换是 shell injection surface.
passes_raw = os.environ.get('MNELO_DESTRUCTIVE_PASSES', 'hygiene')
passes = [p.strip() for p in passes_raw.split(',') if p.strip()]
result = m.run_maintenance(
    passes=passes,
    dry_run=False,
    confirm_destructive=True,
)
print(json.dumps(result, indent=2, ensure_ascii=False))
" 2>&1
) || {
    err "destructive 跑失败. 见上面 stderr"
    err "如果部分 chunk 已 applied, 走 undo: memory_audit_undo(<id>) 查最近 audit_log"
    exit 1
}

echo "$REAL_RESULT" | tee "$REAL_LOG" >/dev/null

REAL_APPLIED=$(echo "$REAL_RESULT" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('applied', '?'))")
REAL_FAILED=$(echo "$REAL_RESULT" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('failed', '?'))")

log "真跑结果: applied=$REAL_APPLIED failed=$REAL_FAILED"
log "完整 JSON: $REAL_LOG"

# [6] 跑后建议
log "=== [4/5] 跑后检查 ==="
log "查最近 applied audit: sqlite3 $DB \"SELECT id, action_type, ref_id, created_at FROM audit_log WHERE pass_name='hygiene' AND status='applied' ORDER BY id DESC LIMIT 10;\""
log "如需 undo:  m.audit_undo(<audit_id>) 走 MCP 或 python"

log "=== [5/5] DONE ==="
ok "L2 destructive 流程完成"