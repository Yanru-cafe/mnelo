"""
test_forget_undo_e2e.py — [8/10 主人验证报告 fix] 端到端验 forget_junk + audit_undo.

[8/10 review B8 followup] 主人实测: 原 INSERT OR IGNORE revert_sql 路径
soft-delete 后 undo 静默失效 (原行 valid_until 已非空, INSERT OR IGNORE 撞
PK 跳过 → 0 恢复). 修后用 UPDATE 风格 (valid_until = NULL WHERE id = ? AND
valid_until = ts).

[8/10 主人验证报告 fix v2] ROOT / src_db / forget_junk 脚本路径用 __file__
相对路径, 不写死 /Users/apple/hermes/memory. Linux/CI collection 之前
报 FileNotFoundError. ROOT = __file__.parent.parent (repo 父目录),
src_db = ROOT/memory.db, fje = ROOT/scripts/forget_junk_entities.py.

本测试隔离 DB 跑:
  1. 准备 1 个 active entity (anno: 前缀) + 1 个 relation 引用它
  2. forget_junk.forget_one (软删 + 写 audit_log + 排队 purged_queue)
  3. memory.audit_undo(audit_id) (走 executescript revert_sql)
  4. 验证 entity valid_until = NULL (恢复)
  5. 验证 relation valid_until = NULL (cascade 恢复)
  6. 验证 revert_sql 是 UPDATE 风格 (不是 INSERT OR IGNORE)
"""
import sys
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path

# [8/10 主人验证报告 fix v2] __file__ 相对路径, 跨平台
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 测试用隔离 DB
tmpdir = tempfile.mkdtemp(prefix='forget_undo_test_')
test_db = Path(tmpdir) / 'memory.db'
src_db = ROOT / 'memory.db'

if not src_db.exists():
    raise SystemExit(f'[setup] 源 DB 不存在: {src_db} — 测试需 live 库 schema 模板')

# 复制 source schema (sqlite 文件 cp)
shutil.copy(src_db, test_db)
print(f'[setup] 隔离 DB: {test_db}')

# 改 env 让 memory.py 用测试 DB
os.environ['MNELO_MEMORY_DIR'] = tmpdir
os.environ['MNELO_MEMORY_DB_PATH'] = str(test_db)

# 重新 import memory (拿新 DB_PATH)
import config
config.config.db_path = test_db  # 同步更新 singleton

import memory as memmod
memmod.DB_PATH = test_db

m = memmod.Memory(db_path=test_db)
print(f'[setup] memory.Memory instance db_path: {m.db_path}')

# 加载 forget_junk 脚本
import importlib.util as _ilu
fje_spec = _ilu.spec_from_file_location('forget_junk_entities', str(ROOT / 'scripts' / 'forget_junk_entities.py'))
fje = _ilu.module_from_spec(fje_spec)
fje_spec.loader.exec_module(fje)

# === 测试 1: forget_one + audit_undo (UPDATE 风格 revert_sql) ===

# 1. 准备 1 个 active entity (anno: 前缀)
test_eid = 'anno:test_junk_undo_e2e'
m._conn.execute(
    "INSERT INTO entities (id, kind, name, properties_json, source, importance, valid_from, created_at) "
    "VALUES (?, 'concept', 'test_junk', '{}', 'manual', 0.3, ?, ?)",
    (test_eid, '2026-08-10T00:00:00', '2026-08-10T00:00:00'),
)
m._conn.execute(
    "INSERT INTO entities (id, kind, name, properties_json, source, importance, valid_from, created_at) "
    "VALUES ('master_test_undo', 'person', 'test master', '{}', 'manual', 0.5, ?, ?)",
    ('2026-08-10T00:00:00', '2026-08-10T00:00:00'),
)
m._conn.execute(
    "INSERT INTO relations (source_id, target_id, relation, weight, valid_from, created_at) "
    "VALUES ('master_test_undo', ?, 'mentions', 1.0, ?, ?)",
    (test_eid, '2026-08-10T00:00:00', '2026-08-10T00:00:00'),
)
m._conn.commit()
print(f'[1] 准备 entity {test_eid} + 1 个 relation 引用')

# 2. forget_one (走 forget_junk, 写 audit_log + purged_queue)
updated, edges = fje.forget_one(m._conn, test_eid, reason='test_undo_e2e')
print(f'[2] forget_one: updated={updated}, edges={edges}')
assert updated == 1, 'forget_one 应 1 entity'
assert edges == 1, 'forget_one 应 cascade 1 relation'

# 验证 entity + relation 软删
after_valid = m._conn.execute(
    "SELECT valid_until FROM entities WHERE id = ?", (test_eid,)
).fetchone()[0]
rel_valid = m._conn.execute(
    "SELECT valid_until FROM relations WHERE source_id='master_test_undo' AND target_id=?",
    (test_eid,),
).fetchone()[0]
print(f'[2] after forget: entity valid_until={after_valid!r}, rel valid_until={rel_valid!r}')
assert after_valid is not None, 'forget 后 entity valid_until 应非空'
assert rel_valid is not None, 'forget 后 relation valid_until 应非空'

# 找 audit_log id
audit_id = m._conn.execute(
    "SELECT id FROM audit_log WHERE ref_id = ? AND status = 'applied'",
    (test_eid,),
).fetchone()[0]
print(f'[2] audit_log id = {audit_id}')

# 验证 revert_sql 是 UPDATE 风格
revert_sql = m._conn.execute(
    "SELECT revert_sql FROM audit_log WHERE id = ?", (audit_id,)
).fetchone()[0]
print(f'[2] revert_sql: {revert_sql}')
assert 'UPDATE entities SET valid_until = NULL' in revert_sql, \
    'revert_sql 必须是 UPDATE 风格 (不是 INSERT OR IGNORE)'
assert 'INSERT OR IGNORE' not in revert_sql, '绝不能 INSERT OR IGNORE'
assert 'UPDATE relations SET valid_until = NULL' in revert_sql, '必须 cascade relation'

# 3. audit_undo (走 memory.audit_undo)
undo_result = m.audit_undo(audit_id)
print(f'[3] undo result: {undo_result}')
assert undo_result['status'] == 'reverted', 'audit_undo 应返 reverted'

# 4. 验证 entity 恢复 (valid_until = NULL)
final_valid = m._conn.execute(
    "SELECT valid_until FROM entities WHERE id = ?", (test_eid,)
).fetchone()[0]
print(f'[4] after undo: entity valid_until = {final_valid!r} (None = 恢复)')
assert final_valid is None, f'undo 后 entity 有效 (valid_until=NULL), got {final_valid!r}'

# 5. 验证 relation 恢复 (cascade)
final_rel_valid = m._conn.execute(
    "SELECT valid_until FROM relations WHERE source_id='master_test_undo' AND target_id=?",
    (test_eid,),
).fetchone()[0]
print(f'[5] after undo: rel valid_until = {final_rel_valid!r} (None = 恢复)')
assert final_rel_valid is None, f'undo 后 relation 有效 (valid_until=NULL), got {final_rel_valid!r}'

# 6. 验证 audit_log 多了 1 条 'reverted' 记录
reverted_count = m._conn.execute(
    "SELECT COUNT(*) FROM audit_log WHERE ref_id = ? AND status = 'reverted'",
    (test_eid,),
).fetchone()[0]
print(f'[6] audit_log reverted records: {reverted_count}')
assert reverted_count == 1, 'audit_undo 应写 1 条 reverted 记录'

m.close()
print()
print('✅ ALL ASSERTIONS PASS')
print(f'   - forget_one 软删 + 写 audit_log (before_json + revert_sql) + purged_queue')
print(f'   - revert_sql 是 UPDATE 风格 (UPDATE entities/relations SET valid_until = NULL WHERE ... AND valid_until = ts)')
print(f'   - audit_undo 走 executescript 恢复 entity + 会 cascade 恢复 relation')
print(f'   - audit_log 写 1 条 reverted 记录')
print(f'   - 0 INSERT OR IGNORE 残留')
print(f'   - 路径用 __file__ 相对路径, 跨平台')