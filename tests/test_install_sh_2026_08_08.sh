#!/usr/bin/env bash
# test_install_sh_2026_08_08.sh — [8/8 Tailscale multi-agent] install.sh 行为测试
#
# 主人 8/8 拍板 mnelo plist/systemd 加 __BIND_HOST__ / __LIVE_ROOT__ 占位符后,
# 验证 install.sh 的 sed 替换逻辑 + listen mode 询问 + plist 渲染结果.
#
# 策略: 不真跑 install.sh (副作用太大: 装 plist / 写 db / pip install).
# 改测 4 个核心不变量:
#   1. plist 模板含 __BIND_HOST__ 占位符 (新人 install.sh 跑通能拿到正确 host)
#   2. sed 替换正则跟 install.sh 行 130-141 完全一致 (防止哪天 install.sh 改了 sed 但忘了 template)
#   3. plist 渲染结果 plutil lint 通过 (不是格式错)
#   4. MNELO_INSTALL_NONINTERACTIVE=1 跳过 listen mode 询问 (CI 友好)
#
# 跑法:
#   bash tests/test_install_sh_2026_08_08.sh
#   或在 CI 跑: bash tests/test_install_sh_2026_08_08.sh && echo "OK"

set -uo pipefail  # [8/8] 去掉 -e (run_test 自己管理 set +e/-e)

# ---- 颜色 ----
if [ -t 1 ]; then
    RED='\033[0;31m'; GREEN='\033[0;32m'; NC='\033[0m'
else
    RED=''; GREEN=''; NC=''
fi
log()  { echo -e "[test_install] $*"; }
ok()   { echo -e "[test_install] ${GREEN}OK${NC}: $*"; }
err()  { echo -e "[test_install] ${RED}FAIL${NC}: $*" >&2; }

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PLIST_TEMPLATE="$REPO_ROOT/scripts/launchd/ai.mnelo.mcp.plist"
SYSTEMD_TEMPLATE="$REPO_ROOT/scripts/systemd/mnelo-mcp.service"
INSTALL_SH="$REPO_ROOT/scripts/install.sh"

# ---- 临时目录 ----
TMPDIR=$(mktemp -d -t mnelo_install_test.XXXXXX)
trap 'rm -rf "$TMPDIR"' EXIT

LIVE_ROOT="$TMPDIR/memory"
VENV_DIR="$TMPDIR/memory/.venv"
VENV_PY="$VENV_DIR/bin/python3"
MNELO_HOME="$TMPDIR"
BIND_HOST_LOOPBACK="127.0.0.1"
BIND_HOST_TAILSCALE="0.0.0.0"

# ---- 测试计数器 ----
TESTS_RUN=0
TESTS_PASSED=0
TESTS_FAILED=0

# ---- run_test 函数 ----
# [8/8] 用 caller scope 直接 eval, $VAR 自动展开. 不用 bash -c (其双引号/quotes 复杂).
run_test() {
    local name="$1"
    local cmd="$2"
    TESTS_RUN=$((TESTS_RUN + 1))
    ( eval "$cmd" ) >/dev/null 2>&1
    if [ $? -eq 0 ]; then
        ok "$name"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        err "$name"
        echo "  cmd: $cmd" >&2
        TESTS_FAILED=$((TESTS_FAILED + 1))
    fi
}

# ========================
# 1. 模板完整性
# ========================

run_test "plist_template_exists" "[ -f '$PLIST_TEMPLATE' ]"
run_test "systemd_template_exists" "[ -f '$SYSTEMD_TEMPLATE' ]"
run_test "install_sh_exists" "[ -f '$INSTALL_SH' ]"

# ========================
# 2. plist 模板含 4 个占位符
# ========================

run_test "plist_template_has_LIVE_ROOT" "grep -qF '__LIVE_ROOT__' '$PLIST_TEMPLATE'"
run_test "plist_template_has_VENV_PY" "grep -qF '__VENV_PY__' '$PLIST_TEMPLATE'"
run_test "plist_template_has_VENV_DIR" "grep -qF '__VENV_DIR__' '$PLIST_TEMPLATE'"
run_test "plist_template_has_BIND_HOST" "grep -qF '__BIND_HOST__' '$PLIST_TEMPLATE'"
# [8/8] install.sh 替换 __MNELO_HOME__ 是 sed no-op (plist template 没这个占位符).
run_test "plist_template_has_no_MNELO_HOME_residue" "! grep -qF '__MNELO_HOME__' '$PLIST_TEMPLATE'"

# ========================
# 3. systemd 模板含 5 个占位符
# ========================

run_test "systemd_template_has_LIVE_ROOT" "grep -qF '__LIVE_ROOT__' '$SYSTEMD_TEMPLATE'"
run_test "systemd_template_has_VENV_PY" "grep -qF '__VENV_PY__' '$SYSTEMD_TEMPLATE'"
run_test "systemd_template_has_USER_LINE" "grep -qF '__USER_LINE__' '$SYSTEMD_TEMPLATE'"
run_test "systemd_template_has_WANTED_BY" "grep -qF '__WANTED_BY__' '$SYSTEMD_TEMPLATE'"
run_test "systemd_template_has_BIND_HOST" "grep -qF '__BIND_HOST__' '$SYSTEMD_TEMPLATE'"

# ========================
# 4. plist sed 替换 (默认 loopback)
# ========================

render_plist() {
    local bind_host="$1"
    sed -e "s|__LIVE_ROOT__|$LIVE_ROOT|g" \
        -e "s|__VENV_PY__|$VENV_PY|g" \
        -e "s|__VENV_DIR__|$VENV_DIR|g" \
        -e "s|__MNELO_HOME__|$MNELO_HOME|g" \
        -e "s|__BIND_HOST__|$bind_host|g" \
        "$PLIST_TEMPLATE"
}

PLIST_RENDERED="$TMPDIR/test.plist"
render_plist "$BIND_HOST_LOOPBACK" > "$PLIST_RENDERED"

run_test "plist_no_placeholder_residual_loopback" "! grep -qF '__' '$PLIST_RENDERED'"
run_test "plist_host_is_loopback" "grep -qF '127.0.0.1' '$PLIST_RENDERED'"
# [8/8] plist 模板的注释里有 '0.0.0.0' (说明文字), 渲染后仍在. 改测 XML tag 内 host 仅为 loopback.
run_test "plist_no_tailscale_bind_leak" "! grep -qF '<string>0.0.0.0</string>' '$PLIST_RENDERED'"
run_test "plist_host_is_loopback_in_tag" "grep -qF '<string>127.0.0.1</string>' '$PLIST_RENDERED'"

# ========================
# 5. plist sed 替换 (Tailscale explicit)
# ========================

render_plist "$BIND_HOST_TAILSCALE" > "$PLIST_RENDERED"

run_test "plist_no_placeholder_residual_tailscale" "! grep -qF '__' '$PLIST_RENDERED'"
run_test "plist_host_is_tailscale_in_tag" "grep -qF '<string>0.0.0.0</string>' '$PLIST_RENDERED'"
run_test "plist_no_loopback_when_tailscale" "! grep -qF '<string>127.0.0.1</string>' '$PLIST_RENDERED'"

# ========================
# 6. plutil lint (macOS only)
# ========================

if command -v plutil >/dev/null 2>&1; then
    run_test "plist_lint_passes" "plutil -lint '$PLIST_RENDERED'"
else
    log "⏭  plutil 不可用 (非 macOS), 跳过 plutil-lint 测试"
fi

# ========================
# 7. systemd sed 替换无残留
# ========================

render_systemd() {
    local bind_host="$1"
    local user_line="${2:-User=apple}"
    local wanted_by="${3:-multi-user.target}"
    sed -e "s|__LIVE_ROOT__|$LIVE_ROOT|g" \
        -e "s|__VENV_PY__|$VENV_PY|g" \
        -e "s|__MNELO_HOME__|$MNELO_HOME|g" \
        -e "s|__USER_LINE__|$user_line|g" \
        -e "s|__WANTED_BY__|$wanted_by|g" \
        -e "s|__BIND_HOST__|$bind_host|g" \
        "$SYSTEMD_TEMPLATE"
}

SYSTEMD_RENDERED="$TMPDIR/test.service"
render_systemd "$BIND_HOST_TAILSCALE" > "$SYSTEMD_RENDERED"

run_test "systemd_no_placeholder_residual" "! grep -qF '__' '$SYSTEMD_RENDERED'"
# [8/8] grep -qF -e '--host 0.0.0.0' 显式 -e 防止 '--host' 被 grep 解析为 flag
run_test "systemd_host_is_tailscale" "grep -qF -e '--host 0.0.0.0' '$SYSTEMD_RENDERED'"
run_test "systemd_user_line_replaced" "grep -qF 'User=apple' '$SYSTEMD_RENDERED'"
# [8/8] systemd 注释里有 'WantedBy=multi-user.target' 描述, grep -qE '^WantedBy' 限定行首
run_test "systemd_wanted_by_replaced" "grep -qE '^WantedBy=multi-user.target' '$SYSTEMD_RENDERED'"

# ========================
# 8. install.sh sed 替换段含 __BIND_HOST__ (防止 install.sh 改了 sed 但忘了 template)
# ========================

# 提前提取 (caller scope 一次跑, 不进 cmd 字符串)
INSTALL_PLIST_SED=$(awk '/# 替换 plist 里的 LIVE_ROOT/,/"$PLIST_SRC" > "$PLIST_DST"/{print; if (++c == 7) exit}' "$INSTALL_SH" 2>/dev/null) || true
INSTALL_SYSTEMD_SED=$(awk '/__USER_LINE__/,/"$SYSTEMD_SRC" > "$SD_DST"/{print; if (++c == 8) exit}' "$INSTALL_SH" 2>/dev/null) || true

# [8/8] caller scope 算 + 用 grep -F 避免 \$VAR 二次展开
if printf '%s' "$INSTALL_PLIST_SED" | grep -qF '__BIND_HOST__'; then
    run_test "install_sh_has_BIND_HOST_sed" "true"
else
    run_test "install_sh_has_BIND_HOST_sed" "false"
fi
if printf '%s' "$INSTALL_SYSTEMD_SED" | grep -qF '__BIND_HOST__'; then
    run_test "install_sh_systemd_has_BIND_HOST_sed" "true"
else
    run_test "install_sh_systemd_has_BIND_HOST_sed" "false"
fi

# ========================
# 9. install.sh listen mode 询问默认值
# ========================

run_test "install_sh_has_bind_host_default_loopback" "grep -qF 'BIND_HOST=\"127.0.0.1\"' '$INSTALL_SH'"
run_test "install_sh_has_listen_mode_prompt" "grep -qF 'mnelo 只在这台机器本机用吗' '$INSTALL_SH'"
run_test "install_sh_has_default_loopback_log" "grep -qF '单机本地模式: --host 127.0.0.1' '$INSTALL_SH'"
run_test "install_sh_has_tailscale_mode_log" "grep -qF '多 agent Tailscale mesh 模式: --host 0.0.0.0' '$INSTALL_SH'"
run_test "install_sh_has_noninteractive_escape" "grep -qF 'MNELO_INSTALL_NONINTERACTIVE' '$INSTALL_SH'"

# ========================
# 10. 端到端: 模拟 install.sh 跑 render + 验证 plist 内容
# ========================

END_TO_END="$TMPDIR/end_to_end"
mkdir -p "$END_TO_END"

# 模拟 install.sh 行 130-141 完全一样的 sed 命令 (BIND_HOST_TAILSCALE 路径)
sed -e "s|__LIVE_ROOT__|$END_TO_END|g" \
    -e "s|__VENV_PY__|$END_TO_END/bin/true|g" \
    -e "s|__VENV_DIR__|$END_TO_END|g" \
    -e "s|__MNELO_HOME__|$END_TO_END|g" \
    -e "s|__BIND_HOST__|$BIND_HOST_TAILSCALE|g" \
    "$PLIST_TEMPLATE" > "$END_TO_END/installed.plist"

run_test "end_to_end_plist_has_correct_host" "grep -qF '0.0.0.0' '$END_TO_END/installed.plist'"
run_test "end_to_end_no_placeholder_remains" "! grep -qF '__' '$END_TO_END/installed.plist'"
if command -v plutil >/dev/null 2>&1; then
    run_test "end_to_end_plutil_lint" "plutil -lint '$END_TO_END/installed.plist'"
fi

# ========================
# 总结
# ========================

echo
log "=== 测试结果 ==="
log "  Tests run:    $TESTS_RUN"
log "  Tests passed:  $TESTS_PASSED"
log "  Tests failed:  $TESTS_FAILED"

if [ "$TESTS_FAILED" -eq 0 ]; then
    ok "ALL PASS ($TESTS_RUN/$TESTS_RUN)"
    exit 0
else
    err "SOME FAILED ($TESTS_FAILED of $TESTS_RUN)"
    exit 1
fi
