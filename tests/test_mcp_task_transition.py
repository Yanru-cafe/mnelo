"""[8/6 M3 Step 8] Integration test for memory_task_transition MCP tool."""
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

import importlib.util as _ilu

def _load(name: str):
    spec = _ilu.spec_from_file_location(name, _REPO / f"{name}.py")
    mod = _ilu.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_load("config")
_load("embedder")
_load("search_index")
_load("validation")
_load("auth")
mcp = _load("mcp_server")


def _setup():
    """Clean fixtures using a fresh Memory instance (don't disturb mcp singleton)."""
    from memory import Memory
    mem = Memory()
    mem._conn.execute("PRAGMA foreign_keys = OFF")
    try:
        mem._conn.execute(
            "DELETE FROM task_states WHERE task_id LIKE 'task:tlm8-%' "
            "OR task_id LIKE 'task:20260806-t8-%'"
        )
        mem._conn.execute(
            "DELETE FROM entities WHERE id LIKE 'task:tlm8-%' "
            "OR id LIKE 'task:20260806-t8-%'"
        )
    finally:
        mem._conn.execute("PRAGMA foreign_keys = ON")
    mem._conn.commit()
    mem.close()


def test_tool_schema_listed():
    """memory_task_transition 出现在 TOOLS schema."""
    tool_names = [t["name"] for t in mcp.TOOLS]
    assert "memory_task_transition" in tool_names
    schema = next(t for t in mcp.TOOLS if t["name"] == "memory_task_transition")
    assert sorted(schema["inputSchema"]["required"]) == ["reason", "task_id", "to_state"]


def test_call_tool_task_transition_normal():
    """走 MCP: task_create → task_transition, 验证 CAS 关旧+开新."""
    _setup()
    create_r = mcp._call_tool("memory_task_create", {
        "name": "t8-normal",
        "now": "2026-08-06T10:00",
    })
    task_id = json.loads(create_r)["task_id"]

    r = mcp._call_tool("memory_task_transition", {
        "task_id": task_id,
        "to_state": "in_progress",
        "reason": "agent A: handoff",
        "now": "2026-08-06T10:05",
    })
    data = json.loads(r)
    assert data["from_state"] == "open"
    assert data["to_state"] == "in_progress"
    assert data["task_id"] == task_id
    assert isinstance(data["window_id"], int)


def test_call_tool_task_transition_invalid_rejected():
    """open → waiting (非 allowed graph) 报 error."""
    _setup()
    create_r = mcp._call_tool("memory_task_create", {
        "name": "t8-invalid",
        "now": "2026-08-06T10:00",
    })
    task_id = json.loads(create_r)["task_id"]

    r = mcp._call_tool("memory_task_transition", {
        "task_id": task_id,
        "to_state": "waiting",
        "reason": "should fail",
        "now": "2026-08-06T10:05",
    })
    data = json.loads(r)
    # 走 mcp_server 错误路径: ValidationError 之类
    assert "error" in data or data.get("type") == "error", f"unexpected {data}"
