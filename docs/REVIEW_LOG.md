# mnelo 代码审查日志

本文件由定时审查任务自动追加。每次审查新推送提交后，在文末追加一个章节。

---

## 2026-08-09 07:50 审查 c0e23ee..e28bf1e

- 范围: c0e23ee..e28bf1e（22 个提交，40 文件，+2959 行）
- 方法: 4 个并行子代理（核心逻辑 / 脚本 / 文档 / 测试质量）+ 对 high/medium 发现逐条实测复核
- 结论: 发现 15 个问题（1 高 / 7 中 / 7 低）。新增测试总体真实覆盖、无假绿；核心机制无回归
- 发现:

  - **[高] namespace guard 与历史裸 id stock 实体不一致（live DB 迁移风险）**
    - 位置: memory.py:210 `_enforce_entity_namespace_guard`（c8abae2 引入）规则 4: 无 `:` id 必须配 `_NAMELESS_KINDS`（person/provider/event/task/setup/system/host/position_snapshot/concept/canonical_fact），**stock 不在其中**
    - 问题: live DB 有 10 个真实 stock 实体（sh600028/sh600036/sh600089/sh600519/sh601318/sh601398/sh601988/sz000333/sz000858/sz002594）用**裸 id**（08-06 13:48:09 一次性批量导入，带 ticker/sector 结构化字段，无 chunk 关联）
    - 失败场景: 隔离 DB 实测 `remember(entities=[{id:'sh600089',kind:'stock'}])` → `ValidationError: non-namespaced id 'sh600089' requires kind in [...]`。任何未来对裸 id stock 实体的 upsert/更新、或把 live 数据导出重导，全部被拒
    - 缓解: 该 10 个实体是静态导入数据（无活跃周期写路径，recall 走 chunks 不受影响）；新写入正确格式 `stock:` 前缀在白名单放行
    - 建议: 单次迁移 UPDATE 这 10 个实体 id 加 `stock:` 前缀，或显式标注接受。另注: c8abae2 自认的"guard 抛错时 chunk INSERT 留孤儿"gap 已被后续 fix（预校验移到 INSERT 之前，memory.py remember step 0.5）修复，实测被拒场景 0 残留 chunk

  - **[中] mnelo_remote_client.py:194 forget() 100% broken**
    - 位置: scripts/mnelo_remote_client.py:194 `forget()` 发 `{"id": chunk_id}`；mcp_server.py:169 memory_forget schema required 是 `target_id`
    - 失败场景: 每次 `forget()` 被 MCP 校验拒（missing required `target_id`），跨 VPS 记忆删除功能不可用
    - 建议: 改发 `{"target_id": chunk_id}`

  - **[中] HermesMneloClient self-blind source filter**
    - 位置: scripts/mnelo_remote_client.py:322-337
    - 问题: remember 强制前缀 `hermes-gw/<orig>`，但 recall 默认精确 filter `source="hermes-gw"` → 自己写的内容通过默认 recall 找不回
    - 建议: recall 改用前缀匹配，或 remember 不追加子前缀

  - **[中] l2_destructive_sop.sh 三处问题**
    - 位置: scripts/l2_destructive_sop.sh
    - 问题 1: dry-run 固定 `passes=["hygiene"]`，destructive 用 `passes=['$PASSES']` → dry-run 不能反映 destructive 实际行为
    - 问题 2: 第 34 行硬编码 macOS `/Users/apple/hermes-agent/venv/bin/python3`
    - 问题 3: 第 141 行 shell 变量拼接存在 command injection surface
    - 建议: dry-run 与 destructive 共用同一 pass 集；python 路径走环境变量/`PYTHON`；参数走数组而非字符串拼接

  - **[中] config.py:364 `[task] stale_days_threshold` 裸 int() 无防护**
    - 位置: config.py:364（4b09bfb 提 config 时未加 try/except）
    - 失败场景: config.toml 该值配错（非数字）→ 模块级 `from config import config` 抛 ValueError → validation.py import 崩溃，整个 server 起不来
    - 建议: 包 try/except，非法值回落默认

  - **[中] 0.0.0.0/:: bind 无 ipfilter（纵深防御回归）**
    - 位置: mcp_server.py:1094 `_validate_loopback_host`（3e538de 放行任意 host bind）
    - 问题: 网络层只有 Bearer token 单点认证，无 IP 白名单。Tailscale 多 agent 场景可接受，但作为 listen mode 公开时暴露面扩大
    - 建议: 可配置 ipfilter（默认保留 loopback），或文档明示依赖 token 为唯一防线

  - **[中] scripts/cleanup_demo_entities_2026_08_09.py 审计语义不完整**
    - 位置: scripts/cleanup_demo_entities_2026_08_09.py
    - 问题: 声称 "audit undo + 30-day purge recovery"，但 audit 只写 plain files，不写 audit_log 表、不排队 purged_queue → undo/purge 机制无法兑现
    - 另: `datetime('now','localtime')` 空格分隔时间戳破坏 asof 回放字典序比较
    - 建议: 走标准 memory.audit 路径 + 时间戳用 ISO 无空格格式

  - **[中] scripts/forget_junk_entities.py:79-87 audit_log INSERT 缺 revert_sql**
    - 位置: scripts/forget_junk_entities.py:79-87
    - 失败场景: memory_audit_undo 读 revert_sql 为空 → ValueError
    - 建议: 补 revert_sql（完整 entity 行 JSON）

  - **[低] tests/test_a5_a6_a7_usearch_integration.py:26 硬编码 macOS python 路径**
    - 位置: tests/test_a5_a6_a7_usearch_integration.py:27,117（`/Users/apple/hermes-agent/venv/bin/python3`）
    - 失败场景: Linux 上 a6/a7 4 个测试 FileNotFoundError（本次实测确认）
    - 建议: 用 `sys.executable`

  - **[低] docs/SCHEMA.md 过时引用**
    - :565 引用不存在的 `scripts/import_legacy.py`；:149/:154 仍写 sqlite-vss `vss0(...)`（实际 schema 是 vec0）
    - 建议: 按实际 schema 修订

  - **[低] README dead links + config.toml.example 未同步**
    - README.md:89 / README.zh.md:83 指向已删除的 REVIEW_LOG.md（本次重建后恢复）
    - config.toml.example 缺 [rate_limit]/[validation]/[task]/[client] sections（4b09bfb/42d7954 新增配置未同步）

  - **[低] tests/test_m36_transition_guards.py:40 `_assert_isolated_db` 阻止全量 pytest**
    - 拒绝在有 chunks 的 DB 上跑，而 init_db seed 1 chunk → 冲突；全量跑被 SystemExit 拦
    - 建议: 让 guard 接受 seed 数据或临时换隔离 DB

  - **[中·测试] tests/test_mcp_task_transition.py 污染 live 库 + 不回查副作用**
    - `_setup()` 与 `mcp._call_tool` 默认连 `/root/work/mnelo-data/memory.db`（非 tmp_path）→ 留 `task:20260806-t8-invalid` 实体 + 状态窗残留；CI 无 live 库直接挂
    - 且只断言 transition 返回的 JSON dict，从不回查 task_states 表验证 CAS 副作用（旧窗 valid_until 关、新窗开）——关旧开新真实语义没被测到
    - 建议: 改 tmp_path 隔离 DB；断言后回查 task_states 行

  - **[中·测试] tests/test_e1_classify_normalize.py 只测 classify 骨架**
    - `classify_memory_type()` 仅 hasattr 断言；episode 复合规则 / procedure strict regex / decision>episode 优先级 / markdown 引用排除 / "我"主语防误伤等核心分支零行为测试
    - 建议: 补分类决策的行为断言

  - **[中·测试] install.sh 测试第 8 节不验证真实替换**
    - 只 grep sed 块含 `__BIND_HOST__` 字面量，不执行 install.sh 对比产物；变异破坏替换后仍绿
    - 建议: 执行真实 sed 渲染产物对比

  - **[低·测试] 弱断言集**
    - test_install_sh 的 plist_host_is_loopback 裸 grep 恒通过；test_remember_rollback 的 test_failed_remember_does_not_leak_audit 非判别性（变异后仍绿）；namespace guard allowed 系列只"不抛=pass"不验证写入行存在

- 测试: 新增 80 个测试通过 + install.sh 33/33 + 状态机 24 + 索引/PII/config 81。a6/a7 4 个失败为硬编码 macOS 路径（环境）。numpy SIGILL（Ivy Bridge 无 AVX2）导致 ~10 个历史测试 core dump——环境限制，与本次 diff 无关，需 `MNELO_MEMORY_SEARCH_BACKEND=usearch`

---

## 2026-08-09 09:50 修复验证 d9c92c8..f34830a

- 范围: d9c92c8..f34830a（7 个 review-fix 提交，对应 B1/B2/B5/B6/B7/B8）
- 方法: 隔离 DB 端到端实测（`.backup` 快照 → `--yes` 真跑 → 一致性/guard/undo 复核）+ 逐文件 diff 审读
- 结论: **6/7 修复生效，2 处残留未修完整**
- 验证通过:
  - **B1 namespace 迁移** ✅ scripts/migrate_stock_namespace_2026_08_09.py：dry-run 默认、事务原子、audit_log+revert_sql 留痕。隔离实测 10 实体 → `stock:` 前缀、400 relations 同步 0 残留、guard 放行新格式仍拒裸 id。FK 关闭无约束问题。注意默认 `--db` 指向已废弃 `~/.hermes/memory/memory.db`，本机须显式传 `--db /root/work/mnelo-data/memory.db`
  - **B2 forget()** ✅ 改发 `{"target_id": chunk_id}` 对齐 schema。但 3557e91 只改 2 文件，commit 声称的 mock 测试未落地 → 无测试覆盖（低）
  - **B5 config stale_days** ✅ try/except + `>=1` 校验 + 回落 7
  - **B5 l2_sop 部分** ✅ VENV_PY env 化 + passes 走 env 解析（去 `'$PASSES'` 注入面）+ dry-run/destructive 共用 PASSES。但见下方残留
  - **B6 0.0.0.0 bind** ✅ stderr warn + ipfilter_cidrs 建议，warn-only 不破坏兼容
  - **B7 cleanup_demo** ✅ 改走 Memory.forget 接口（自动 audit_log + purged_queue），ISO 8601 时间戳，无 macOS 路径残留
- 残留（验证发现，未修）:
  - **[中] l2_destructive_sop.sh 仍硬编码 `MNELO_HOME=/Users/apple/.hermes`**（第 84 行 dry-run + 139 行 destructive）。VENV_PY/passes 修了，这条漏了。run_hygiene.py:23 用 MNELO_HOME 路由 DB/config → 本机（live=/root/work/mnelo-data，`~/.hermes` 已移除）跑该脚本连错/找不到 DB。建议改 `${MNELO_MEMORY_DIR:-...}` 或移除该行让 wrapper 走标准 config
  - **[中] B8 forget_junk revert_sql 用 `INSERT OR IGNORE`，undo 静默失效**。实测：forget_one 软删后原行仍在（valid_until 非空），revert_sql 的 `INSERT OR IGNORE INTO entities` 撞主键被静默跳过 → `audit_undo` 后实体未恢复（COUNT=0）。`memory.audit_undo` 直接 executescript 不前置清理。从"ValueError 崩溃"改善为"静默不生效"，修复不完整。正确应为 UPDATE 风格（还原 valid_until=NULL + 恢复字段）。相关测试（test_digest.py:157）只覆盖 UPDATE 风格 revert_sql，INSERT 风格路径无测试钉住
  - **[低] config.toml.example 仍未同步 [client]/[task] sections**（3557e91 新增 DEFAULT_TAILSCALE_HOST 配置项）


---

## 2026-08-09 10:30 第二轮修复验证 e0ac9c1

- 范围: 1475c7b..e0ac9c1（9 个提交，针对 review B2/B9/B10/B11/B12/B13/B14 + 上一轮 2 处残留）
- 方法: 隔离 DB 端到端实测（undo 恢复 / guard 行为 / 5 测试文件针对性跑）+ diff 审读
- 结论: **6 项修复生效，3 个修复引入新 bug**
- 生效:
  - **l2_sop MNELO_HOME env 化** ✅ e17b0e4: `${MNELO_HOME:-$HOME/.hermes}`（不再硬编码 macOS）
  - **forget_junk revert_sql UPDATE 风格** ✅ fbc10a0: 隔离实测 forget_one 软删 entity+relation → audit_undo 全恢复（0→1, 0→1）。`ts` 复合条件精确还原本次软删，`_json.dumps` 转义防注入
  - **a6/a7 macOS 路径 → sys.executable** ✅ 2647937（3/4 过，a7 另有脚本健壮性问题见下）
  - **SCHEMA.md 修正** ✅ cd832f4（sqlite-vss → vec0、import_legacy.py 标注）
  - **config.toml.example 同步** ✅ 058c36e（[client]/[task]/[rate_limit]/[validation] sections）
  - **classify 行为断言** ✅ 37465f8（test_e1_classify_normalize 全过）
- 引入的新 bug（修复自身缺陷）:
  - **[中] 8e6c498 m36 guard `n_total==0 → 拒` 反伤隔离空库**: 修复假设"init_db seed 1 个 manual chunk"，但 `scripts/init_db.py`（112 行）只建表+验证，**不 seed 任何 chunk**。隔离 `init_db` 后 chunks=0 → 新 guard 拒 `DB 0 chunk` → 按文档 `MNELO_MEMORY_DIR=$(mktemp -d) && init_db && pytest` 流程 test_m36 直接 SystemExit，与修复意图（放行 init_db 种子库）相反。旧逻辑（拒绝含真实 chunk 的库）反而放行隔离空库
  - **[中] e0ac9c1 新测试 test_forget_junk_undo_e2e.py 三处 macOS 硬编码**: ROOT=`/Users/apple/.hermes/memory`、src_db 路径、脚本 import 路径。Linux/CI collection 直接 FileNotFoundError，本机无法跑。逻辑本身正确（端到端验 undo），应改 repo 相对路径
  - **[中] 7be2f00 mcp_task_transition 回查断言查错表**: `_count_state_transitions_for_task` 用 `SELECT ... FROM state_transitions WHERE task_id=?`，但 state_transitions 是**转移规则表**（列 id/scope/from_state/to_state，无 task_id），task 状态窗在 **task_states** 表（有 task_id）。2 个测试 `OperationalError: no such column: task_id`。应改查 task_states
  - **[低] a7 repair 对 tmp 小库（0 向量）抛 RuntimeError**: 路径修复后暴露 `[usearch] 自动重建 0/1 向量` RuntimeError。脚本健壮性问题，非回归（原失败原因是路径）


---

## 2026-08-09 10:55 第三轮修复验证 2635ca0

- 范围: 5c8a9f5..2635ca0（3 个提交，针对上轮 3 个新 bug）
- 结论: **1 项完整生效，2 项修复不完整**
- 生效:
  - **aab4dbe m36 guard** ✅ 改为「验证必要表存在（task_states/entities/chunks）+ n_total>50/n_live>5 阈值拒 live」，`n_total==0` 放行。隔离 init_db 空库不再误杀（m36 全过）
- 不完整:
  - **[中] 0748cc5 e2e 测试 schema 模板来源错误**: macOS 硬编码消除了（`ROOT=__file__.parent.parent`），但 `src_db = ROOT/memory.db` 依赖 repo 根存在完整 schema 的 memory.db——本机该文件是 4096 字节空残留（gitignore，无 schema 表），复制后 `no such table: entities` 仍失败。模板应从 `config.resolve_db_path()` 拿（如 mcp 测试 `_isolated_db`），而非猜 repo 根文件。另: 该测试模块级 `os.environ['MNELO_MEMORY_DIR']=tmpdir` 污染后续收集的文件（m36 读到 e2e 的 tmpdir → 报缺 schema）
  - **[中] 2635ca0 mcp 回查仍失败（got 0）**: 表名修对了（OperationalError 消失），但 `_isolated_db` 创建的回查 `mem` 用 `tmp_path/memory.db`，而 `mcp._call_tool` 走 `_get_mem()` 单例连 **config/env 库**——task_create/transition 实际写 env 库，回查查 tmp_path 空库 → 断言 0 行。7be2f00 的「tmp_path 隔离」未真正实现: mcp 单例未切到 tmp_path，测试仍写 config 库（若 config 是 live 则污染 live）。修复: `_isolated_db` 内 `mcp._mem_instance = mem`（monkeypatch 单例），或统一 env 后重载
- 附注: 本机 repo 根 `/root/work/mnelo/memory.db` 是 4096 空残留文件（被 .gitignore），非代码


---

## 2026-08-09 11:00 修复两处测试代码（授权直接 push）

- 范围: 本地 2 个测试文件（针对 2635ca0 批 2 处不完整 + 1 处新发现的断言逻辑错）
- 改动:
  - **test_mcp_task_transition.py**: `_isolated_db` 加 `mcp._mem_instance = mem` —— mcp._call_tool 单例切到隔离库, task_create/transition/回查同库, 隔离真正生效; 两个测试 finally 恢复 `mcp._mem_instance = None` 防污染后续. 另修正 invalid 用例断言: 非法 transition 后 task_states 应为 **1** 行（task_create 写入的初始 open 窗保留, 无副作用）, 原期望 0 是逻辑错误（7be2f00 引入, 被表名 OperationalError 掩盖）
  - **test_forget_junk_undo_e2e.py**: `src_db` 改从 `config.resolve_db_path()` 拿 schema 模板（原 `ROOT/memory.db` 本机是 4096 空残留, 复制后 `no such table`）; 整个脚本包 `try/finally` 恢复模块级修改的 `MNELO_MEMORY_*` env（防污染后续收集的 test_m36）
- 验证: 3 文件 13 passed（e2e + mcp_transition + m36 互不干扰）; 全受影响组 36 passed / 1 failed
- 遗留（非本次范围）: **a7 repair 自动重建维度不匹配** —— `test_a7_repair_actually_removes_orphan_when_not_dry_run` 用 dim=4 构造临时索引, repair 脚本触发自动重建时用 512 维 embedder → `The number of vector dimensions doesn't match`. 脚本健壮性问题（非回归, 原失败原因先后是 macOS 路径 / 0 向量 RuntimeError）


---

## 2026-08-09 11:15 修复 a7 repair 维度不匹配（授权直接 push）

- 范围: `scripts/repair_index.py` 单文件（针对上轮遗留: a7 repair 自动重建维度不匹配）
- 根因（多层）:
  1. `repair()` 硬编码 `build_search_index(backend, db_path, dim=512)`
  2. 磁盘索引维度 ≠ 512 时, `UsearchIndex.__init__` 预检失败 → `_auto_rebuild` 全量重建 —— 违背 repair 语义（只该清孤儿, 不该顺带重建）; 重建把孤儿顺带丢掉, repair 报 `deleted: 0` 误导
  3. 重建 embedder 输出 512 维 → 写进低维索引 → `The number of vector dimensions doesn't match!`; a7 测试末尾 `UsearchIndex(db, dim=4)` reload 再触发重建 → RuntimeError
- 修复:
  - `_resolve_backend()`: 镜像 `_pick_backend` 优先级解析最终后端（auto: zvec > usearch）
  - `_probe_usearch_dim()`: 从 usearch 文件头 `Index.metadata().dimensions` 探测磁盘索引真实维度; 读不到/损坏 → 回落 512（此时 `_auto_rebuild` 正常兜底）
  - `repair()`: usearch 探真实维度, zvec 保持 512（zvec 集合 schema 固定, 且无维度校验重建）
- 验证:
  - `test_a5_a6_a7_usearch_integration.py` 5 passed（a7 真删孤儿场景通过）
  - repair 输出 `{'kept': 1, 'deleted': 1}` —— 孤儿被 repair 真删（此前重建路径报 deleted:0）; reload dim=4 索引 keys=1 ✓
  - 受影响组 8 passed（a5/a6/a7 + mcp_task_transition + forget_junk_undo_e2e）
  - 基线对比: 对 repair_index.py 改动 stash 前后, test_digest/test_drift_fix_round15/test_backup_restore/test_benchmark_round15/test_edge_cases/test_asof_replay 失败/段错误逐文件一致 —— **零回归**（均为预存问题: usearch f16 SIGSEGV、namespace guard 拒裸 stock id 等, 见 07:50 审查章）
- Risk 复盘: 探测读损坏索引头 → 回落 512 → 预检失败 → `_auto_rebuild` 兜底（与修复前行为一致）; auto 后端残留旧 usearch.index 时用 `_resolve_backend` 门控, 只有解析为 usearch 才探测, 不污染 zvec 维度
