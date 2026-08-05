"""session_start_digest.py 钩子脚本容错测试 (TASKS_L2_SESSION_STATE S3/S6).

覆盖容错保证:
- mnelo MCP 未跑 → 静默 (stdout 空 + exit 0, 不刷错误日志)
- digest 关闭 → 静默
- 运行中 → stdout 含 [mnelo-digest] 标记 (需 server 在跑, 标记为可选)

用 subprocess 跑脚本 (真实部署形态), 隔离环境。
"""
import os
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "session_start_digest.py"


def _run_script(env_extra=None):
    env = os.environ.copy()
    env.pop("MNEOLO_AUTH_TOKEN", None)  # 不依赖外部 token
    if env_extra:
        env.update(env_extra)
    r = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True, text=True, timeout=20, env=env,
    )
    return r


class TestSessionStartHookSilent(unittest.TestCase):
    def test_01_mcp_down_is_silent(self):
        """mnelo MCP 未跑 → stdout 空 + exit 0 + 无错误日志 (容错核心)."""
        r = _run_script()
        self.assertEqual(r.returncode, 0, f"exit 应 0, got {r.returncode}")
        self.assertEqual(r.stdout.strip(), "", f"stdout 应空, got: {r.stdout!r}")
        # stderr 不应有 mnelo_client ERROR (容错路径要真静默)
        self.assertNotIn("ERROR", r.stderr, f"stderr 不应有错误日志: {r.stderr!r}")

    def test_02_digest_disabled_is_silent(self):
        """MNELO_MEMORY_DIGEST_ENABLED=false → 静默 (即使 server 在跑)."""
        r = _run_script({"MNELO_MEMORY_DIGEST_ENABLED": "false"})
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), "")


if __name__ == "__main__":
    unittest.main()
