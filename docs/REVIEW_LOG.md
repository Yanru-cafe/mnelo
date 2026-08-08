# mnelo 代码审查日志

本文件由定时审查任务自动追加。每次审查新推送提交后，在文末追加一个章节。

---

## 2026-08-06 12:43 审查 de5aa965..245ea541

- 范围: de5aa965..245ea541（共 7 个提交）
- 提交:
  - 48a7487 feat(schema): M1 task/loop 状态机 — task_states + state_transitions + seed 默认转移矩阵
  - 9c9125c feat(task-states): M2 Step 1 — transition() CAS + 4 测试 (M1 7→10 测试)
  - 47f73c8 test(task-states): M2 Step 2 — 并发 CAS 2 测试 (12/12 pass)
  - 23afebc feat(task-states): M2 Step 3 — list_tasks() + replay_task() + 4 测试 (16/16 pass)
  - 81d5838 feat(task-states): M2 Step 4 — loop_tick() 4 verdict + 6 测试 (22/22 pass)
  - 4242c92 feat(task-states): M2 Step 5 — task_create + loop_create + 终端簿记 5 测试 (27/27 pass)
  - 245ea54 docs: scrub session-level '实战' filler in M1/M2 ship files (27/27 pass 0 regression)
- 结论: 发现 4 个问题（1 中 / 2 低 / 1 极低）
- 发现:
  - **[中] 秒级时间戳可产生零长状态窗，asof 回放丢失中间状态**
    - 位置: task_states.py:60（`_default_now` 用 `timespec="seconds"`）配合 `transition()` 步骤 3（同一 `ts` 既关旧窗又开新窗）
    - 问题: 时间粒度仅到秒，同秒内两次转移会让中间窗 `valid_from == valid_until`
    - 失败场景: 并发 agent 或快速连续两次转移（如 A 在 10:05 open→in_progress，B 在同秒 in_progress→waiting），产生 `open[10:00,10:05) → in_progress[10:05,10:05) → waiting[10:05,None)`。`replay_task(asof='10:05')` 只见 waiting，in_progress 中间态在回放中完全不可见。已用脚本复现。这是 asof 跨时间回放这一核心设计卖点的数据缺口。建议: 时间戳提到毫秒级，或 CAS 时强制 strictly-increasing。
  - **[低] task_create 防双 spawn 是 check-then-write，无 CAS，并发下可双开同一 loop**
    - 位置: task_states.py:593（检查 `cfg["active_task_id"]`）与 task_states.py:649（`UPDATE entities` 写 active_task_id）两步非原子
    - 问题: 检查与写入之间无锁/CAS，与同批 `transition()` 的 CAS 语义不一致（边界 §8 明示"防双 spawn"）
    - 失败场景: 两个并发 task_create 对同一 loop 都读到 `active_task_id=None`，都通过 LoopHasActiveTaskError 检查并各自 INSERT task + 写 loop，后写覆盖前写，第一个 task 成为孤儿（loop 不再指向它）。M5 单线程调用当前无碍，但并发正确性是 M2 明示目标。
  - **[低] `import re` 位于模块 docstring 之前，`task_states.__doc__` 失效**
    - 位置: task_states.py:1
    - 问题: 模块 docstring 必须是文件第一个语句；`import re` 抢先使 `"""..."""` 变为未绑定的字符串表达式
    - 失败场景: 实测 `task_states.__doc__ is None`，任何依赖 `__doc__` 的自省（如 `help()`、CLI 文档）失效；同时违反 flake8 E402（模块级 import 不在顶部）。
  - **[极低] `List` 未导入（局部注解，当前无运行时错误）**
    - 位置: task_states.py:383 与 :471（`params: List[Any]`）
    - 问题: typing 仅导入 `Any, Dict, Optional`，无 `List`
    - 失败场景: 因是局部变量注解（PEP 526 不求值），27 个测试全过不报错；但静态检查 F821 报 undefined name，一旦 `List[...]` 挪到函数签名或返回注解即 NameError。建议补 `from typing import List`。
- 测试: 6 个受影响测试文件全部通过，`27 passed`（test_task_loop_m1_schema / test_task_states_transition / test_task_states_concurrent / test_task_states_list_replay / test_task_states_loop_tick / test_task_states_create），0 regression。
- 补充（同日 12:50 并行审查实例去重后新增 3 条，合计发现 7 条）:
  - **[中] 中文名 task/loop 的 id slug 全部退化**
    - 位置: task_states.py:607-611（task_create）、task_states.py:703（loop_create）
    - 问题: `re.sub(r"[^a-z0-9-]", "-", name.lower())[:30].strip("-")` 把纯中文名整段替换为连字符，strip 后为空 → slug 回落 "task"/"loop"。mnelo 主语言是中文，故中文命名实体全部命中此退化路径。
    - 失败场景: 已实测 `'采购耗材'` 与 `'下单发货'` 均得 `task:20260806-task`，靠碰撞循环补 -1/-2；ID 不含语义、随创建顺序漂移，破坏 DESIGN §2.1 `task:YYYYMMDD-<slug>` 约定与 asof 可回放的可读性。
  - **[低] "并发 CAS"测试实为顺序模拟，真竞态未覆盖**
    - 位置: tests/test_task_states_concurrent.py:13-16（docstring 自认"没有真并发"）
    - 问题: 两个测试仅在单线程按顺序执行语句求证 CAS 语义；transition() 真并发下"关旧窗+开新窗"竞态与 `ux_task_current_state` 唯一索引兜底路径未被测试。上文[中]零长状态窗（同秒双转移）正源于该路径漏测。
  - **[低] transition() 关旧窗+开新窗在 autocommit 连接上非原子**
    - 位置: task_states.py:174-185
    - 问题: CAS 的 UPDATE（关旧窗）与 INSERT（开新窗）是两条语句；若调用方未包事务，UPDATE 立即提交，INSERT 失败（唯一索引/证据 FK 冲突）时旧窗已关 → task 变"无活动窗"，后续 transfer 报 TaskNotFoundError。当前调用方（测试/未来 M3 API）需自行保证事务包裹。

## 2026-08-06 13:21 审查 e98de450..131aae4
- 范围: e98de450..131aae4（共 3 个提交）
- 提交:
  - `afa3f779` fix(task-states): review-pass 6 项整改 (RF1-RF7) — 41/41 测试 pass
  - `6714fd74` docs: scrub 2 '实战' filler in test_review_fixes.py (RF5 docstring)
  - `131aae45` feat(mcp): M3 Step 8-11 — task_transition/list/replay + loop_create/tick (50/50 pass)
- 结论: 发现 4 个问题（1 高 / 1 中 / 2 低）。测试声明 "41/41" 与 "50/50" 在本机不成立——`test_review_fixes.py` 有 2 个用例因硬编码 macOS 路径 FAIL（见[中] #2）。
- 发现:
  - **[高] MCP 层 task/loop 工具无事务回滚，RF3 失败路径泄漏孤儿行**
    - 位置: mcp_server.py:432-433（`_handle_task_simple`：`func(...)` 后直接 `commit()`，异常无 `rollback`）
    - 问题: `task_create`（task_states.py:693-717）在 INSERT entity + INSERT 状态窗之后才做 RF3 原子 UPDATE；当 UPDATE 命中 0 行（并发双 spawn 竞态，正是 RF3 要防的场景）或中途任何 SQL 异常时 raise，此前两行 INSERT 留在 sqlite3 默认隐式事务里未提交。`_call_tool`（mcp_server.py:650-663）的 `except Exception` 只返回错误 JSON，不 rollback。下一次任意成功的 tool call 的 `commit()` 会把孤儿 task entity + open 状态窗一并落库。
    - 失败场景: 两个连接并发对同一 loop 调 `memory_task_create`（RF3 竞态窗口：pre-check SELECT 后、原子 UPDATE 前另一连接写入 `active_task_id`），后到者报 `LoopHasActiveTaskError` 错误，但其 task 实体 + 状态窗仍被下一次调用提交 → 记忆库出现"失败调用"产生的幻影 task，`memory_task_list` 可见、永远没有关联 loop。已用最小 sqlite 复现（UPDATE 0 行 → raise → 无 rollback → 下次 commit 孤儿行落库）。建议: `_handle_task_simple`/`_call_tool` 用 `with mem._conn:` 包裹（异常自动回滚），或 except 分支显式 `mem._conn.rollback()`。transition() 关旧窗后 INSERT 失败的同类路径同样受益。
  - **[中] test_review_fixes.py 硬编码已删除的 `/Users/apple/.hermes/...` 绝对路径，2 个用例本机 FAIL**
    - 位置: tests/test_review_fixes.py:237（test_rf5）与 :266（test_rf7）
    - 问题: `concurrent_test = Path("/Users/apple/.hermes/memory/tests/test_task_states_concurrent.py")` 与 `Path("/Users/apple/.hermes/memory/task_states.py").read_text()` 引用的是项目历史中已"彻底移除"的旧记忆目录（CLAUDE.md 明示本机无 Hermes 残留）。仓库内真实文件在 `tests/test_task_states_concurrent.py` 与 `task_states.py`（repo-relative）。
    - 失败场景: 本机 `pytest tests/test_review_fixes.py -q` → `test_rf5_concurrent_test_present_with_caveat`（断言路径存在，AssertionError）与 `test_rf7_list_typing_imported`（FileNotFoundError）FAIL，8 过 2 败。提交信息声称的 "41/41" / "50/50 pass" 在本机不成立；测试不可移植、非自包含。建议: 改用 `_REPO / "task_states.py"` 与 `_REPO / "tests" / "test_task_states_concurrent.py"`。
  - **[低] `_slugify` docstring 示例为编造，与实现矛盾；RF2 测试名/注释仍称"拼音"**
    - 位置: task_states.py:85-88（docstring 示例）、tests/test_review_fixes.py:145-148（test_rf2 名称与注释）
    - 问题: docstring 声称 "更新 task 列表" → "e7b2f910"（hash 路径），但实现走 ASCII 路径返回 `"task"`（含英文字母即命中分支 1）；所列 md5 示例值全部与真实 `hashlib.md5(name.encode()).hexdigest()[:8]` 不符（如 "采购耗材" 实际 `a49a962a`，非 `3c8e2f1a`）。同时 test_rf2 名字/注释仍写"拼音 fallback / cai-gou-hao-cai"，而实现注释明确"弃 pinyin"改用 md5。
    - 失败场景: 维护者据 docstring 推断中文名一律走 hash（含中英混合名），实际 "更新 task 列表" 得 `task:20260806-task` 且与纯 "task" 名同日起碰撞；docstring 与代码不符，误导后续改动。测试虽通过（仅断言前缀与长度），但命名/注释传递了错误的实现心智模型。
  - **[低] RF1 只修了默认时间戳；`now` override 秒级精度仍可制造零长状态窗**
    - 位置: task_states.py:69-72（`_default_now` 毫秒级）与 :221（`transition` 的 `ts = now or _default_now()`）
    - 问题: 默认路径已提毫秒级，但 MCP 工具 `memory_task_transition`/`memory_task_create` 仍公开暴露 `now` 覆盖参数且不校验精度。同一 task 连续两次 transition 传入相同秒级 `now`（如均 `"2026-08-06T10:00"`），仍产生 `valid_from == valid_until` 的零长窗——正是 RF1 与上轮[中]发现要消除的场景。
    - 失败场景: 调用方带 `now` 秒级值快速两跳（如 A open→in_progress、B in_progress→waiting 同 now），`replay_task(asof='10:00')` 只见 open/waiting，in_progress 中间态在 asof 回放中不可见，核心设计卖点受损。建议: 工具层校验/拒绝秒级 `now`，或 transition CAS 时强制严格递增。
- 测试: 本机运行 9 个受影响测试文件 → `45 passed, 2 failed`（fail 全部来自上述[中] #2 的两个路径用例）；`test_review_fixes.py` 8 过 2 败，其余 8 个 task/loop 测试文件 37 个用例全过，0 regression。

## 2026-08-06 13:22 审查 131aae4..21ecf61（推送期间新到提交，并入本轮）
- 范围: 131aae4..21ecf61（共 1 个提交）
- 提交:
  - `21ecf61` feat(mcp): M3 Step 12 — memory_loop_update + memory_loop_list (62/62 pass)
- 结论: 发现 3 个问题（全部低严重度）。Step 12 测试在本机 12 个用例全过。
- 发现:
  - **[低] `loop_update` 绕过 `loop_create` 的 interval_hours>0 校验，可写坏 tick 判定**
    - 位置: task_states.py:838-840（`loop_update` 直接 `cfg["interval_hours"] = interval_hours`，无校验）；对照 task_states.py:755-758（`loop_create` 校验 `interval_hours <= 0` 拒绝）
    - 问题: `memory_loop_update` 的 `interval_hours` 参数不受任何范围约束；`loop_create` 明确拒绝 <=0，更新路径却可绕过。
    - 失败场景: 对已建 loop 调 `memory_loop_update(interval_hours=0)` → `loop_tick` 里 `elapsed_hours < interval_hours`（0）恒为 False → verdict 恒 "due"（每次 tick 都判定到期）；设负数则恒 "not_due"（永不到期）。loop 轮转语义被静默破坏，且与 create 路径行为不一致。建议: `loop_update` 内补 `if interval_hours is not None and interval_hours <= 0: raise`。
  - **[低] `loop_update` / `list_loops` 不过滤 `valid_until IS NULL`，可操作已软删 loop**
    - 位置: task_states.py:821（loop_update 查 loop）与 :909（list_loops 拉 entities）
    - 问题: 其余所有 task/loop 访问器（transition:196、loop_tick:323、task_create:615、loop_create 均带 `AND valid_until IS NULL`）都排除软删实体；`loop_update`/`list_loops` 是唯二不带该过滤的新函数，可读取/改写已软删的 loop 行。
    - 失败场景: 一个 loop 被软删（`valid_until` 已设）后，`memory_loop_list` 仍列出它、`memory_loop_update` 仍能改它的 properties 并新写状态窗，恢复逻辑/清理逻辑会被幽灵 loop 干扰。建议: 两个查询补 `AND valid_until IS NULL`。
  - **[低] `loop_update` enabled 切换是 check-then-write（非 CAS），并发下可关窗后 INSERT 撞唯一索引；叠加[高]#1 的无回滚，部分写入会落地**
    - 位置: task_states.py:855-866（SELECT 当前窗 → UPDATE 关旧 → INSERT 新窗，两步非原子）
    - 问题: 与上轮 RF3 修复的 task_create 竞态同型——先读后写。并发两个 `loop_update(enabled=...)` 对同一 loop：双方都读到同一活动窗并各自 UPDATE 关掉，再各自 INSERT 新活动窗，第二个 INSERT 撞 `ux_task_current_state` 部分唯一索引（schema.sql:176）抛 IntegrityError。
    - 失败场景: 该 IntegrityError 在 `_handle_task_simple`/`_call_tool` 不 rollback（[高]#1 根因）→ 关旧窗的 UPDATE 留在隐式事务，下一次成功调用 commit 后 loop 变"无活动窗"（既非 running 也非 dormant），`list_loops`/`transition` 对它的判定失真。建议: enabled 切换也改 CAS 单语句（或 `INSERT ... WHERE NOT EXISTS` + 唯一索引兜底），并随 [高]#1 一起补事务回滚。
- 测试: 本机 `pytest tests/test_task_states_loop_update_list.py tests/test_mcp_loop_update_list.py -q` → `12 passed`；与 131aae4 之前的 task/loop 套件无 regression（前述 45 过 2 败不变）。

## 2026-08-06 13:35 审查 303128b..f0d3878
- 范围: 303128b..f0d3878（共 1 个提交）
- 提交:
  - `f0d3878` feat(cli): M3 Step 13 — task_manager.py CLI 5 子命令 + 8 测试 (70/70 pass)
- 结论: 发现 5 个问题（1 高 / 1 中 / 2 低 / 1 信息）。CLI 主体（scripts/task_manager.py）与 task_states.py 既有函数签名逐一比对全部匹配，正确性无虞；但新增测试文件 `test_task_manager_cli.py` 在本机 8/8 FAIL，提交声称的 "70/70 pass" 不可复现。
- 发现:
  - **[高] 新增 CLI 测试文件整包硬编码作者 macOS 路径，本机 8/8 FAIL（重复上轮已标记反模式）**
    - 位置: tests/test_task_manager_cli.py:24（`_PY = "/Users/apple/hermes-agent/venv/bin/python3"`）与 :66-69（`_setup()` 直连 `/Users/apple/.hermes/memory/memory.db`）
    - 问题: `_PY` 指向作者 mac 专用解释器路径，本机（Linux）不存在；`_setup()` 的 DB 路径正是 CLAUDE.md 明示本机已"彻底移除"的旧 Hermes 目录。两条硬编码任一即可让全部 8 个用例失败——这是上轮在 test_review_fixes.py 已标记并建议修复的同类问题（repo 相对路径）在本轮新建文件中复发。
    - 失败场景: 本机 `pytest tests/test_task_manager_cli.py -q` → **8 failed**，全部 `sqlite3.OperationalError: unable to open database file`（_setup 在 connect 即抛）；即使绕过 _setup，subprocess 也会因解释器不存在抛 FileNotFoundError。新 CLI 的唯一测试覆盖在非作者机器上完全不可运行，"70/70 pass" 只对作者成立。建议: `_PY` 改为 `sys.executable`（继承当前 pytest 解释器），`_setup()` 的 DB 路径改从 `config.resolve_db_path()` 或 `_REPO` 相对解析。
  - **[中] 测试无 DB 隔离：CLI 直写 live 记忆库，清理却指向另一路径 → 跑一次套件污染真实记忆库**
    - 位置: tests/test_task_manager_cli.py:66-76（`_setup()` 清理硬编码旧路径）+ 该文件 subprocess 启动的 CLI（`Memory()` → `config.resolve_db_path()`）
    - 问题: CLI 本身无 `--db` 参数指向临时库，subprocess 写的库与 `_setup()` 清理的库不是同一个（本机 live 为 `/root/work/mnelo-data/memory.db`）。即使在本轮 [高]#1 修复后（_setup 改对路径），测试也会在**真实的记忆库**上建 `task:20260806-cli-*` / `loop:cli-*` 行；mnelo 是记忆库，测试垃圾直接进 production 数据。
    - 失败场景: 任一台机器上运行该测试套件 → live DB 出现测试 task/loop 实体与状态窗；若 cleanup 路径与 resolve_db_path 不一致（如作者日后改 MNELO_MEMORY_DIR），垃圾行永不清理，`list tasks`/`memory_task_list` 把测试数据当真实记忆返回。建议: `_setup()` 建临时 DB 并通过 env 传给 subprocess（`MNELO_MEMORY_DIR=<tmp>`）隔离，测试结束清理。
  - **[低] CLI falsy 默认值掩蔽合法 0 值**
    - 位置: scripts/task_manager.py:60（`priority=args.priority or 3`）、:77（`interval_hours=args.interval_hours or 24`）、:102/:113（`limit=args.limit or 50`）
    - 问题: `or` 把假值当未传。task_states 明确支持 priority 0-5（task_create docstring），`--priority 0` 被静默改为 3；`--limit 0` 被当 50。
    - 失败场景: 用户 `task_manager.py create task --name x --priority 0` 期望最低优先级，落库为 priority=3，与既有 open 任务同权重；低优先级调度语义被 CLI 悄悄吞掉。建议: 用 `if args.X is not None` 区分未传与 0。
  - **[低] main() 不捕获 task_states 异常，错误裸 traceback**
    - 位置: scripts/task_manager.py:183-190（`main()` 的 `args.func(args, mem)` 无 try/except）
    - 问题: `transition`/`task_create`/`loop_tick` 会抛 `TaskLoopError`/`TaskNotFoundError`/`LoopNotFoundError`（非法 transition、未知 id、`--interval-hours` 传负等），CLI 直接打印完整 Python traceback。与 docstring 宣称的"Claude Code friendly / Bash 驱动"定位不符。
    - 失败场景: `task_manager.py move task:不存在 --to done` → 3 屏 traceback 而非一行中文错误；Claude agent 解析 stderr 提取失败原因变难。数据安全不受影响（sqlite 隐式事务在 close 时回滚），纯 UX。建议: main() 捕获异常打印 `{code}: {message}` 并以非零码退出。
  - **[信息] `_commit_or_print` 死代码**
    - 位置: scripts/task_manager.py:29-34
    - 问题: 定义了但从未调用；docstring 声称"事务包裹 task_states 调用"，函数体却是 `pass`，且实际提交逻辑散布在各 cmd_* 里。具误导性，易让后续维护者以为有统一事务入口。
    - 建议: 删除该函数，或在 cmd_* 内真正复用统一 commit/rollback 助手。
- 测试: 本机 `pytest tests/test_task_manager_cli.py -q` → **8 failed**（全部 `OperationalError`，硬编码路径，见[高]#1）；`pytest tests/test_task_loop_m1_schema.py tests/test_task_states_list_replay.py -q` → **10 passed**（本轮对既有 fixture 的清理补充无 regression）。

## 2026-08-06 13:55 审查 1e720eba..dea3978
- 范围: 1e720eba..dea3978（共 1 个提交）
- 提交:
  - `dea3978` fix(task-states+review): RF8-RF14 整改 — 77/77 测试 pass
- 结论: 通过（发现 3 个问题：1 中 / 2 低，无高）。RF8 事务回滚、RF11 秒级零长窗推进、RF12 interval_hours 校验、RF13 软删过滤、RF14 enabled CAS 全部落地且实现正确；7 个新测试 + RF9 硬编码路径可移植化均通过。遗留：上轮 [高]（test_task_manager_cli.py 硬编码 macOS 路径）本轮 diff 未触碰，仍在。
- 发现:
  - **[中] RF8 数据完整性修复的 MCP 接线零测试覆盖**
    - 位置: mcp_server.py:476-485 + tests/test_review_fixes.py:283-337
    - 问题: 本提交涉及数据完整性的核心修复（失败路径 `mem._conn.rollback()` 防孤儿行）落在 `_handle_task_simple`，但新增 `test_rf8_rollback_on_task_create_double_spawn` 只直接调用 `task_states.task_create` 并**手动** `m._conn.rollback()` 模拟 MCP 行为，从未真正走 `_call_tool('memory_task_*')` → `_handle_task_simple` 触发异常路径。mcp_server.py 那 30 行改动（commit 移入 try、except 兜底、错误 JSON 返回）零测试接触。
    - 失败场景: 若未来有人从 `_handle_task_simple` 的 except 分支移除 rollback（或把 commit 挪出 try），test_rf8 依旧通过——它测的是 task_states 层语义而非被改的接线；孤儿行保证悄然失效且 CI 不报警。建议补一个走真实 MCP 分发、触发 task_states 抛错、断言 DB 无孤儿行的测试。
  - **[低] RF8 错误契约与其余工具不一致：task 工具直通完整 str(e) + 丢失全 traceback**
    - 位置: mcp_server.py:479-485
    - 问题: except 分支把 `str(e)` 原样塞进返回 JSON；同文件 `_call_tool` 外层对其他工具刻意只返异常类型名、不带原始消息（防泄露内部路径/SQL 细节）。task 工具成为唯一把完整底层异常消息直通 MCP client 的入口。另失败只记 `logger.warning`（无 traceback），改动前该路径异常冒泡到外层 `logger.exception`（全 traceback），排障信息降级。
    - 失败场景: 真并发下 RF14 输家触发的裸 `UNIQUE constraint failed: task_states.task_id` 现直通 client；运营排查 task 工具失败只剩一行 warning，无堆栈定位不到 task_states 内部哪一步抛错。建议: 领域错误（TaskLoopError 等）保留 message，底层 sqlite/IntegrityError 只返类型名，并改用 logger.exception。
  - **[低] RF11 只对「已关闭窗」递增，非单调 now 仍可写负长窗**
    - 位置: task_states.py:228-240
    - 问题: 递增保护查询 `valid_until IS NOT NULL`（已关窗）的 max 并推进 ts，但不校验当前活动窗的 valid_from。若调用方传的 `now` 早于当前活动窗 valid_from（乱序回放 / 重试消息携带旧时间戳），关旧窗的 valid_until 会小于该窗 valid_from → 负长窗（valid_from > valid_until），asof 回放出现时间空洞。
    - 失败场景: task_create 建初始窗 valid_from="2026-08-06T11:00:00" 后，transition(now="2026-08-06T10:00:05")（回拨）→ RF11 查到无已关窗（cur_vu=None）不推进 → 初始窗 valid_until="10:00:05" < valid_from="11:00:00"。零长窗修了，负长窗仍可能。正常 MCP 调用不传 now 不触发；建议递增比较参考 `max(cur_vu, current_valid_from)`。
- 测试: 本机 `.venv/bin/python -m pytest tests/test_review_fixes.py -q` → **17 passed**；12 个 task/loop 相关文件（task_states_*/task_loop_m1_schema/mcp_task*/asof_replay）→ **72 passed, 0 failed**。全量套件中 test_backup_restore.py / test_task_manager_cli.py 在本机触发 zvec "Illegal instruction"（无 AVX2，CLAUDE.md 已知约束）崩溃、CLI 另有硬编码 `/Users/apple` 路径 8/8 失败——均属既有环境/遗留问题，与本提交 diff 无关（本提交仅触碰 mcp_server.py / task_states.py / test_review_fixes.py，全绿）。

---

## 2026-08-06 15:16 审查 ef36def7..b6e35c6

- 范围: ef36def721beb8cc6309952d6acfa07e8cf17329..b6e35c62edbffe4607ec919079b3b316158b7c88（共 1 个提交）
- 提交:
  - b6e35c6 fix(review): RF15-RF17 整改 — 真 MCP 接线测试 + 错误契约统一 + 乱序 now 修因 (89/89 pass)
- 结论: 发现 1 个问题（中）。生产代码（task_states.transition 递增逻辑、mcp_server 错误契约/RF8 回滚）经独立实测正确；问题集中在新增测试自身的可移植性。

- 发现:
  - **[中] tests/test_review_rf15_rf16.py — 两个子进程测试在干净 checkout 上必失败，并污染仓库**
    - 位置: tests/test_review_rf15_rf16.py `_subprocess_mcp_call` 的 `setup_src`（约 line 104-117）
    - 问题: `_setup` 硬编码 `sqlite3.connect('<repo>/memory.db')`（`mcp_server_path.parent / 'memory.db'` = `/root/work/mnelo/memory.db`）。该文件不被跟踪（`.gitignore` 忽略 `*.db`），干净 checkout 上不存在 → `sqlite3.connect` 新建一个空库 → 紧随其后的 `DELETE FROM task_states` 抛 `sqlite3.OperationalError: no such table: task_states` → 子进程 rc≠0 → `_subprocess_mcp_call` 抛 `AssertionError` → `test_rf15_double_spawn_rollback_no_orphan` 与 `test_rf16_task_loop_error_preserves_message_and_code` 两个测试必失败。同时在 repo 根留下一个 0 字节 `memory.db`（被 .gitignore 隐形，属工作树污染）。
    - 附带问题: 子进程 env 只传 PATH/HOME/MNELO_MEMORY_SEARCH_BACKEND，不含 `MNELO_MEMORY_DIR`，故子进程内 `Memory()` 解析到 `~/.hermes/memory/memory.db`——而项目 8/6 已明确移除 `~/.hermes`。即清理 DELETE 打的 DB（repo 根 memory.db）与测试实际写入的 DB（~/.hermes）不是同一个，跨次运行的清理无效；残留仅被"task id 带时间戳唯一"掩盖。提交信息称 89/89 pass，推断是作者本机残留了带 schema 的 repo 根 memory.db 才通过。
    - 失败场景: 干净 checkout 上 `pytest tests/test_review_rf15_rf16.py` → `AssertionError: subprocess failed: rc=1, stderr=sqlite3.OperationalError: no such table: task_states`（本机已实测复现；且实测后 repo 根出现 0 字节 memory.db）。
    - 建议: 清理连接与 `Memory()` 用同一 DB 路径（统一走 `config.resolve_db_path()`），并在子进程 env 显式设 `MNELO_MEMORY_DIR` 指向临时目录；或在执行 DELETE 前先建表/保证 DB 存在。

- 测试:
  - 本机全量 pytest 套件被既有环境问题阻断：conftest session 级 autouse fixture `_clean_test_data_session` 在 `usearch/index.py load` 处 `Fatal Python error: Aborted`（与 mnelo 数据目录索引文件状态有关）。本提交未触碰 conftest.py / memory.py / search_index.py，属既有环境问题，非本提交引入。
  - 因此改用独立 harness 直接实测本提交改动的核心逻辑（对 live 库副本，绕过 search index）：
    - `task_states.transition` 新递增逻辑（RF17/RF11）：实测 4 场景——乱序 now 回拨（初始窗 11:00:00 + now=10:00:05）、同秒连跳、正常未来 now、同秒链式 5 次转移 → 全部无零长/负长窗，新窗 valid_from 严格 > 旧窗 valid_until（通过）。
    - `mcp_server._handle_task_simple` 错误契约（RF16）+ RF8 回滚：实测 TaskNotFoundError → 返回保 message/code/field/type/tool；底层错（TypeError）→ 只返类型名不泄内部信息；loop 双 spawn → 无孤儿 entity/窗口行（通过）。
    - `tests/test_review_rf15_rf16.py` 静态测试 `test_rf15_real_mcp_wiring_in_source`：当前源码满足其全部断言（通过）；两个子进程测试在干净状态实测失败（见发现 1）。

---

## 2026-08-06 15:20 审查 b6e35c6..401cf10

- 范围: b6e35c6..401cf10476d37f135df4f67d9c901181e80efbfb（共 1 个提交）
- 提交:
  - 401cf10 feat(step14): F6 真并发测试 — threading + subprocess 隔离验证 (94/94 pass)
- 结论: 发现 3 个问题（中 / 低）。本提交仅动测试（新增 tests/test_step14_concurrency.py + 2 个既有 fixture 清理），无生产代码改动，无数据完整性风险。核心问题与上一轮 RF15/RF16 同类：子进程测试依赖本机残留文件，干净 checkout 必失败。

- 发现:
  - **[中] tests/test_step14_concurrency.py — `_setup()` 在干净 checkout 上必失败，并污染仓库**
    - 位置: tests/test_step14_concurrency.py:53-73（`_setup` 连 `_REPO / "memory.db"`）
    - 问题: 与 tests/test_review_rf15_rf16.py 完全同根因——清理连接硬编码 repo 根 `memory.db`（不被跟踪，干净 checkout 不存在）。`sqlite3.connect` 新建空库 → `DELETE FROM task_states` 抛 `sqlite3.OperationalError: no such table: task_states` → `_setup()` 抛错 → 5 个 F6 测试全部 setup 即失败（本机已实测复现）。同时在 repo 根留下 0 字节 `memory.db`（被 .gitignore 隐形）。
    - 附带: 子进程 env 不含 `MNELO_MEMORY_DIR`，`Memory()` 解析到 `~/.hermes/memory/memory.db`（项目 8/6 已明确移除 ~/.hermes，会被重建）——清理 DELETE 打的 DB 与子进程实际写入的 DB 不是同一个，跨次清理无效。提交信息称 94/94 pass，推断依赖作者本机残留的带 schema 的 repo 根 memory.db。
    - 失败场景: 干净 checkout 上 `pytest tests/test_step14_concurrency.py` → `OperationalError: no such table: task_states`（setup 阶段），且 repo 根出现 0 字节 memory.db。
  - **[中] test_f6_3 断言过弱，无法验证其声称的并发序列化契约**
    - 位置: tests/test_step14_concurrency.py:279-289
    - 问题: docstring（line 223-228）明确期望 "1 success + 3 errors (StateTransitionError)"，但测试体只 `assert len(oks) >= 1`，从不校验 errors 的数量或类型。若并发保护失效、4 线程全部成功（in_progress→in_progress 若按注释所说视为合法幂等），`len(oks)==4 >= 1`，测试仍通过 → 该测试无法检测它声称要覆盖的"同 task 并发 transition 应序列化拒收"缺陷。
    - 失败场景: 人为让并发 UPDATE 全部成功（CAS 失效）→ 断言仍通过，覆盖形同虚设。
  - **[低] 子进程 snippet 硬编码 macOS 遗留路径 `/Users/apple/.hermes/memory`**
    - 位置: tests/test_step14_concurrency.py:86,103,159,179,234,251,303,325,373,390
    - 问题: 所有子进程代码 `sys.path.insert(0, '/Users/apple/.hermes/memory')`，本机（Linux /root）该目录不存在，insert 是 no-op；imports 实际靠 `python -c` 的 cwd（`cwd=_REPO`）解析才能工作。属可移植性残留，若某环境 python 不从 cwd 解析则 import 失败；且与项目 8/6 "彻底移除 .hermes" 的清理方向相悖。

- 测试:
  - 本机全量 pytest 仍被既有 conftest session fixture（usearch load abort）阻断，与上轮相同。
  - 实测本提交的测试自身在干净状态的行为:
    - `_setup()` 清理逻辑在无 `/root/work/mnelo/memory.db` 时抛 `OperationalError: no such table: task_states` 并新建空 memory.db（见发现 1，实测复现）。
    - 静态核对 test_f6_3 断言仅 `len(oks) >= 1`，docstring 所述 "1+3" 契约未落为断言（见发现 2）。
  - 本提交改动的生产代码（无）与上轮已验证的 transition/CAS/错误契约不受影响；F6 依赖的底层（RF3 CAS 双 spawn、RF14 enabled CAS、RF17 乱序 now）在上轮独立 harness 实测通过。

## 2026-08-06 15:41 审查 6b0a5ab..def4136
- 范围: 6b0a5ab..def4136（共 1 个提交）
- 提交:
  - def4136 feat(m5.1): cron/timer loop tick wrapper + launchd plist (6/6 pass)
- 结论: 发现 4 个问题（1 高 / 1 中 / 2 低中），1 处风格问题
- 发现:
  - **[高] scripts/mnelo_loop_tick_cron.py:161 — cron 传 UTC-aware `now`，与存储的 naive 时间戳相减必抛 TypeError，稳态下永远检不出 due loop**
    - 位置: scripts/mnelo_loop_tick_cron.py:161（`now_ts = datetime.now(timezone.utc).isoformat(timespec="milliseconds")`），交互于 task_states.py:418-426（loop_tick step5）
    - 问题: `last_cycle_done_at` 由 `task_states.transition()` 以 naive local 写入（`_default_now()` = `datetime.now().isoformat(...)`，task_states.py:72，无 tz）。cron 传入 `+00:00` 的 aware now。loop_tick 里 `now_dt - last_dt` 在 Python 3.10 抛 `TypeError: can't subtract offset-naive and offset-aware datetimes`（已实测复现），被 `except (ValueError, TypeError)` 包装成 `LoopNotFoundError` → cron 每 loop 落入 `error_loops`。
    - 失败场景: 某 loop 完成第一个 cycle（last_cycle_done_at 被 transition 写入）后，之后每次 tick 都报 `last_cycle_done_at 解析失败`，verdict 永远不是 `due`/`not_due`，只能走 `--dry-run` 首次扫描或手工清空才恢复——本提交要实现的「cron 驱动到期检测」在稳态下完全失效。首次运行（last_cycle_done_at=None）不受影响，掩盖了问题。
    - 建议: cron 侧与 `_default_now()` 对齐（naive local，或统一改 aware 并同时改 transition 写入端）。
  - **[中] scripts/launchd/ai.mnelo.loop_tick.plist:14-25 — EnvironmentVariables 缺 `MNELO_MEMORY_DIR`，macOS 部署会打开错误/遗留库或直接崩溃**
    - 位置: scripts/launchd/ai.mnelo.loop_tick.plist:14-25（仅设 MNELO_HOME / PYTHONPATH / MNELO_MEMORY_SEARCH_BACKEND）
    - 问题: launchd 不读 shell profile，`config.py:47` 解析 `MNELO_MEMORY_DIR` 缺失时回落 `~/.hermes/memory`（项目 8/6 已移除的旧路径）。两情形: ① 旧路径无库 → `Memory()` 建空库，`_migrate_schema` 对不存在表 ALTER → `sqlite3.OperationalError: no such table: entities`（已实测复现），cron 每 30 分钟崩溃退出 1，且把已删除的 ~/.hermes 重建出来；② 旧库残留 → 对错误 DB 写 audit_log/digest，实际 loop 数据在 MNELO_MEMORY_DIR 里，全部漏检。install.sh Linux 分支（install.sh:266）正确设了 `MNELO_MEMORY_DIR=$LIVE_ROOT`，两分支不对称。
    - 失败场景: macOS 安装后 `launchctl` 每 30 分钟跑 `mnelo_loop_tick_cron.py` → 日志见 `no such table: entities`（或对错误库空转），loop 到期永远不被发现。
    - 建议: plist 增加 `<key>MNELO_MEMORY_DIR</key><string>__LIVE_ROOT__</string>`。
  - **[低中] tests/test_m5_1_cron_tick.py:44-53,88-94,64/112/131/193 — 测试库路径不一致，干净 checkout 上必失败，仅作者本机假绿**
    - 位置: `_run`/`_create_loop` 子进程 env（:44-53, :88-94）不设 `MNELO_MEMORY_DIR`，子进程 `Memory()` 落到 `~/.hermes/memory/memory.db`；而 `_setup`/断言直接 `sqlite3.connect(_REPO / "memory.db")`（:64,112,131,193）。
    - 问题: 与上轮在 test_step14_concurrency.py / test_review_rf15_rf16.py 记录的同一反模式在新测试文件复现。干净机器上两个库不同：loop 写入 ~/.hermes（空库时 `loop_create` 前 `Memory()` init 即因缺表崩溃），断言读 repo 根库为 0 行 → 测试失败；即便两库都存在，跨库写入/读取也清不干净。只有作者本机两条路径恰好指向同一带 schema 的 DB 才 6/6 通过。此外 :156/:215 硬编码 digest 日期 `2026-08-06.json`，而脚本用 UTC 当天日期写文件——任何晚于 2026-08-06 的运行该断言必失败，测试是日期敏感的。
    - 失败场景: 干净 checkout 上 `pytest tests/test_m5_1_cron_tick.py` → `_create_loop` 子进程 init 崩（no such table）或断言读到 0 行；8/7 起即使环境就绪，digest 断言也失败。
    - 建议: 子进程 env 传 `MNELO_MEMORY_DIR` 指向测试用临时库，断言改读同一路径，digest 断言用脚本输出的实际路径（或相对 mtime 找最新文件）。
  - **[低] scripts/mnelo_loop_tick_cron.py:22 — docstring 声称的隔离保证与实现不符**
    - 位置: 文件头 docstring（:21-27）「subprocess mcp_memory…走 MCP 不直接调 task_states.* 防 SQL 写入冲突」
    - 问题: 实现是 `_mcp_call` 同进程直接 `import memory/task_states` 直连 SQLite，从不 spawn MCP（:86-109 注释也自认）。「多 MCP instance 并行隔离」承诺未兑现，且每次 `_mcp_call` 新建 `Memory()`（触发 embedder 模型加载 + _migrate_schema + 索引构建），每 loop 一次连接，30 分钟 cron 每轮重复开销；并发 tick 时靠的是 SQLite WAL/busy_timeout 而非文档所述的隔离。属误导性文档 + 轻微效率问题。
    - 失败场景: 维护者据 docstring 认为有跨进程隔离而放松并发约束，或误以为写 MCP 调用可并行。
  - **[风格] scripts/install.sh:242-277 — 新块 12 空格缩进与仓库 4 空格风格不一致**
    - 位置: scripts/install.sh:242（M5.1 块起始 `if [ "$(uname -s)"...`）
    - 问题: 该块位于顶层（前一个备份 `fi` 之后），shell 忽略缩进、语义正确，但与仓库既有缩进风格不统一，易让后续编辑误以为嵌在某函数/条件内。
- 测试:
  - 本机无法运行 `pytest tests/test_m5_1_cron_tick.py tests/test_task_states_loop_tick.py`：既有 conftest session fixture 在 `Memory()` 初始化时触发 native 扩展堆损坏（`corrupted size vs. prev_size`，无 AVX2 环境，plist 注释亦承认「测试环境 SIGSEGV 兜底」），属既有环境限制、非本提交引入，与上轮相同。
  - 已用独立最小片段实测关键契约: ① naive − aware 相减抛 `TypeError`（Finding 1 成立）; ② 空库 `Memory()` init 抛 `OperationalError: no such table: entities`（Finding 2 情形①成立）; ③ `list_loops` 返回 `{loops,count,truncated}` 且 loop 含 loop_id/name/trigger/interval_hours、`loop_tick` 返回 verdict/active_task_id/last_cycle_done_at、`audit_log` 列与 UNIQUE(run_id,pass_name,action_type,ref_id,status) 均与新脚本假设一致（run_id 每次带 uuid 唯一，无约束冲突）。
  - 提交声称 6/6 pass 无法在本机复现；鉴于 Finding 3，推断通过依赖作者本机 ~/.hermes 与 repo 根 memory.db 路径重合。

## 2026-08-06 15:58 审查 cd0254d..8b3e483
- 范围: cd0254d..8b3e483（共 1 个提交）
- 提交:
  - 8b3e483 fix(review): M17+M18+M19 review-pass 整改 (100/100 pass)
- 结论: 发现 2 个问题（1 高 / 1 中）
- 发现:
  - **[高] tests/test_step14_concurrency.py:34-61 — `_run_in_subprocess` 子进程 env 剥离 `MNELO_MEMORY_DIR`，DB 解析回落已移除的 `~/.hermes/memory` → 5 个测试全部 `unable to open database file`，M17/M19「干净 checkout / Linux / CI 可跑」的承诺未兑现**
    - 位置: `_run_in_subprocess` 的 `env = {"PATH":..., "HOME":..., "MNELO_MEMORY_SEARCH_BACKEND":...}`（:47-51）。传 `env=` 给 `subprocess.run` 时子进程环境被整体替换，不继承父进程 `MNELO_MEMORY_DIR`/`MNELO_MEMORY_DB_PATH`。子进程内 `import memory` → `memory.DB_PATH = config.resolve_db_path()`（config.py:51-62）在无 env 时回落到 `DEFAULT_LIVE_ROOT / "memory.db"` = `~/.hermes/memory/memory.db`（config.py:47 默认值）。
    - 问题: 本机 `~/.hermes` 已被移除（CLAUDE.md 记录），SQLite 不会自动创建父目录 → 实测每个子进程 `sqlite3.connect('/root/.hermes/memory/memory.db')` 抛 `OperationalError: unable to open database file`。直接驱动 5 个测试函数（绕过崩坏的 session fixture）：**5/5 FAIL**，全部 `AssertionError: subprocess failed: rc=1`。提交声称「M17+M19 跨 4 子进程测试可移植性增强」「干净 checkout / Linux / CI 都 OK」，本机（正是该场景）完全不可运行。作者机通过，仅因 `~/.hermes/memory` 恰好存在且指向同一 DB。
    - 失败场景: 任何 `MNELO_MEMORY_DIR` 指向非 `~/.hermes/memory` 且旧目录不存在的环境（本机、干净 CI），`pytest tests/test_step14_concurrency.py` 5/5 崩溃。
    - 建议: 继承 `os.environ` 后仅覆盖 PATH 等键，或显式把 `MNELO_MEMORY_DIR`/`MNELO_MEMORY_DB_PATH` 透传进子进程 env；最稳妥是让调用方把解析好的 `db_path` 直接注入 snippet。
  - **[中] tests/test_step14_concurrency.py:92-134 — `_setup()` 清理的 DB 与子进程实际使用的 DB 不一致（与上轮 def4136 Finding 3 同根因再犯）**
    - 位置: `_setup()` 用父进程 env 的 `config.resolve_db_path()`（:106），解析到 `/root/work/mnelo-data/memory.db`；而子进程（Finding 1 的 env 剥离）落到 `~/.hermes/memory/memory.db`。M17 docstring 声称「跟子进程 memory.Memory() 走同一路径解析」，不成立。
    - 问题: 即使 `~/.hermes/memory` 存在、测试能跑，清理也只删 `/root/work/mnelo-data/memory.db` 的 step14 行，子进程库里的 step14 残留（task_states/entities）跨测试、跨运行累积。F6.1 期望「1 success + 3 error」，若子进程库遗留未清理的同名 loop 活跃窗，可能全部失败或断言失真；F6.3 同理。修复只解决了「干净 checkout 上建 0 字节库」这个子问题，主路径（清同一库）没对齐。另注意 `_setup()` 现在会向 /root/work/mnelo-data/memory.db（生产 live 库）执行 DELETE——两库不同时，等于在生产库上反复删 step14 前缀行。
    - 失败场景: 连续两次运行测试（子进程库遗留上一轮 step14-loop 活跃 task），F6.1/F6.3 断言不稳；或生产库被误清理。
    - 建议: 与 Finding 1 一并修复——统一 DB 路径来源（子进程 env 透传 + `_setup` 同源解析），或全部用独立临时库 + `MNELO_MEMORY_DB_PATH` 指向它。
  - 说明（非发现）: F6.3 强断言本身经核对成立——`transition()` 允许图 + RF11 严格递增下，4 并发要么 1 OK+3 ERR、要么 4 OK，终态均为 `in_progress`；`len(oks)+len(errs)==4` 能抓住子进程崩溃/线程悬挂，是对旧 `len(oks)>=1` 的真实加强。`_SafeFormatDict`/format_map 改造经逐一核对所有 snippet，无裸 `{}` 会触发 `ValueError`；`config.resolve_db_path()` 返回 `Path`，`_setup` 的 `.exists()/.unlink()` 类型安全。
- 测试:
  - 全量 pytest 仍被既有 conftest session fixture（`_clean_test_data_session` → `Memory()` → usearch index `load` 原生 abort：`free(): corrupted unsorted chunks`）阻断，与上轮同环境问题（usearch 库/索引格式不匹配），非本提交引入——本提交仅改测试文件。
  - 已绕过该 fixture 直接驱动被改的 5 个测试函数：**5/5 FAIL**，全部因子进程 `OperationalError: unable to open database file`（Finding 1 实测复现）。
  - 已静态核对 `transition()`/`loop_tick` 语义与 F6.3 新断言匹配（见说明）。提交声称 100/100 pass 在本机不可复现；推断作者机通过依赖 `~/.hermes/memory` 与 `MNELO_MEMORY_DIR` 恰好同指。

## 2026-08-06 16:35 审查 131afca..0d0fd1b
- 范围: 131afca..0d0fd1b（共 1 个提交）
- 提交:
  - 0d0fd1b fix(review): M20-M26 review-pass 整改 (104/104 pass)
- 结论: 通过（发现 1 个低严重度问题）
- 发现:
  - **[低] scripts/mnelo_loop_tick_cron.py:32 — M20 整改后 `timezone` 已无使用处，import 残留未清**
    - 位置: `from datetime import datetime, timezone`（:32）。本提交把 `_log`/`now_ts`/`today` 共 3 处 `datetime.now(timezone.utc)` 全部改为 `datetime.now()`（:44/:165/:213），`timezone` 从此零引用。
    - 问题: 纯 lint 级清理遗漏（`F401` unused import），无功能影响、无失败场景。属「宁缺毋滥」边缘项，一并记录供下次顺手清理。
    - 建议: 改为 `from datetime import datetime`。
- 核实（非发现）:
  - **M20 修复正确性（实测）**: naive/aware 减法实测——旧版 `now=aware` 与 naive `last_cycle_done_at` 相减抛 `TypeError`，被 `loop_tick` 的 `except (ValueError, TypeError)` 捕获转 `LoopNotFoundError` → cron 记入 `error_loops`，due loop 永远检不出（与提交描述一致）；新版 `now=naive` 正常算 `elapsed_hours`。`now_ts` 用 `datetime.now().isoformat(timespec="milliseconds")` 与 `task_states._default_now()` 同格式（naive local ms）。**前向兼容**: live DB 6 个 loop 均无 `last_cycle_done_at`，旧 cron 从未实际 tick 过，无 aware 时间戳存量需迁移。
  - **M21 修复成立**: `install.sh` 确实对 `ai.mnelo.loop_tick.plist` 做 `__LIVE_ROOT__`/`__VENV_PY__`/`__MNELO_HOME__` sed 替换（~:246），`MNELO_MEMORY_DIR=__LIVE_ROOT__` 部署时会解析为真实路径，不再回落 `~/.hermes/memory`。
  - **M25/M26 同源一致性**: `_run_in_subprocess` env 显式注入 `MNELO_MEMORY_DIR=str(_REPO)`（子进程环境被整体替换，无 `MNELO_MEMORY_DB_PATH` 泄漏），`_setup()` 直连 `_REPO/"memory.db"`——`config.resolve_db_path()` 优先 `MNELO_MEMORY_DB_PATH`、次 `MNELO_MEMORY_DIR`，两者解析到同一路径。上一轮「高」发现（子进程回落 `~/.hermes`）确认被修复。
  - **M22 `_latest_digest_path`**: 读取目录与 cron 默认 `--output-dir`（`Path.home()/".hermes/cron/output/loop_tick"`）一致；按 mtime 取最新，消除跨日硬编码失效。
  - **新测试契约**: `test_m5_1_naive_now_avoids_subtract_error` 的 `loop_create` 调用省略 `enabled`，默认 `True`（task_states.py:773），契约成立；内嵌子进程 `env=os.environ.copy()` 注入 `MNELO_MEMORY_DIR`，cron 输出 `verdicts:` 行恒打印，断言可判定。
- 测试:
  - 全量 pytest 仍被既有 conftest session fixture（`_clean_test_data_session` → `memory.Memory()` → usearch index `load` 原生 abort：`Aborted`/`free(): corrupted unsorted chunks`）阻断。已确认该崩溃在 base（131afca）与 new（0d0fd1b）上、以及全新 DB/索引下均复现——既有环境问题（usearch native 在本 Linux 机不稳定，对应提交描述「测试环境 SIGSEGV 兜底」），**非本提交引入**。本提交未触 conftest / search_index / memory。
  - 已绕过 conftest 做静态契约验证（等价 M20/M21/M22 三条静态断言，全部通过）：cron/两测试文件 `ast.parse` 语法编译通过；cron 源码无 `timezone.utc`、含 naive `datetime.now().isoformat`；plist 含 `MNELO_MEMORY_DIR`+`__LIVE_ROOT__`；测试 env 含 `MNELO_MEMORY_DIR`+`_latest_digest_path`。
  - 提交声称 104/104 pass 在本机不可直接复现（usearch native 崩溃阻断任何构造 `memory.Memory()` 的路径），但全部改动点经静态核实与 M20 行为实测与提交描述一致，未发现高/中危问题。

## 2026-08-06 17:10 审查 2e0d5fb..dfa8cba
- 范围: 2e0d5fb..dfa8cba（共 3 个提交）
- 提交:
  - d1b5a81 feat(m5.2): L2 stuck_task Proposal + apply/list helpers (10/10 pass)
  - 81b49c4 fix(lint): M27 review-pass cleanup — 删 timezone import 残留
  - dfa8cba feat(m5.3): D11 TTL 豁免 — task/loop 不被 L2 decay 自动删 (10/10 pass)
- 结论: 发现 2 个中等问题 + 1 个低危
- 发现（按严重度）:

  **[中] forget_task / forget_loop 未实现「已软删即抛错」契约（task_states.py:1466 / :1559）**
  - 问题: 两函数 docstring 声明 "TaskLoopError: ... 已经是 valid_until IS NOT NULL"（task_states.py:1454），
    但代码只检查 `row` 是否存在，未检查 `row[0]`（valid_until 列）。`# row[0] is valid_until column`
    注释表明本应检查却漏掉。
  - 失败场景: 对同一 task/loop 连续调用两次 `forget_task`。第二次不抛错、静默返回
    `rows_invalidated=0`，并再写一条 `forced_forget` audit_log 行，其 `before_json.status_before`
    仍伪造为 "active"——审计痕迹失真（本应只有一条删除记录）。已用实际调用复现。
  - 建议: 补 `if row[0] is not None: raise TaskLoopError(...已软删, code="TaskAlreadyForgotten")`。

  **[中] propose_stale_tasks 去重不会对「已 apply 但仍 stale」的 task 再次提议（task_states.py:1207-1212）**
  - 问题: 去重查询按 `pass_name='stuck_task' AND status='proposed' AND ref_id=?` 判断"已有 pending Proposal"。
    而 audit_log 是 append-only，`apply_stale_proposal` 只新增 `stale_resolved/applied` 行、**不翻转原 Proposal 行**
    status，原行永远保持 `status='proposed'`。因此"pending"检查实际等价于"曾被提议过"。
  - 失败场景: 某 stale task 被提议 → agent 评估后 apply（如 applied_action='ignored_will_revisit'），
    task 仍停留在原 stale 状态 → 之后每轮 `propose_stale_tasks` 都命中旧的 `status='proposed'` 行而跳过，
    该 task 永不再被上浮，stuck_task 检测对这类 task 静默失效。已用实际调用复现
    （apply 后二次 propose：proposed=0、skipped_existing>0）。
  - 建议: 去重改为排除已有 `action_type='stale_resolved' AND status='applied'` 的 ref_id，
    或 apply 时把原 Proposal 行 status 置为 'applied'，让"pending"语义名实相符。

  **[低] memory.forget() D11 拦截代码与 docstring 不符、含死代码（memory.py:685-700）**
  - 问题: docstring 声称"显式 confirm_forget=True 才接受"，但方法签名没有 `confirm_forget` 参数，
    实际是 task/loop 一律抛 ValueError；`confirm_forget = False  # 占位` 是无效死代码。
  - 影响: 功能上安全（已核实无既有调用方传 task/loop kind；D11 就是禁止 L2 自动删 task/loop），
    但误导后续维护者以为存在 confirm 放行路径。
  - 建议: 删除占位死代码，docstring 改为"一律拦截，显式删除走 task_states.forget_task / forget_loop"。

- 测试:
  - 环境: 全量 pytest 被既有 conftest session fixture（usearch index `load` 原生 abort）与
    zvec 在无 AVX2 CPU 上的 SIGILL 阻断。本轮找到可复现路径：
    `scripts/init_db.py` 初始化全新 memory.db + 设 `MNELO_MEMORY_SEARCH_BACKEND=usearch`（避开 zvec SIGILL），
    conftest session 清理不再 load 既有索引。
  - 新增测试: test_m5_2_stale_proposal.py + test_m5_3_d11_forget.py —— **20/20 通过**。
  - 关联回归: test_task_states_{transition,create,concurrent,loop_tick}.py + m5_2/m5_3 —— **37/37 通过**。
  - 未跑: test_m5_1_cron_tick.py 需仓库目录预置 memory.db（工作树缺失，环境问题，与本次改动无关；
    本次对 cron 脚本仅删 timezone import，已静态确认无残留引用）。
  - 本次两个发现均为测试未覆盖的边界（double-forget、apply 后再提议），已实测复现。

## 2026-08-06 17:30 审查 aaca03a..6bbbcd0
- 范围: aaca03a..6bbbcd0（共 1 个提交）
- 提交:
  - 6bbbcd0 feat(m5.4) + fix(m5.2 + m5.4): e2e 演练 + 2 个 bug 修复 (3/3 pass)
- 结论: 发现 2 个问题（均在新测试文件；task_states.py 两个修复经核实正确）
- 审查维度小结:
  - task_states.py list_tasks asof 修复（L472）: 此前 asof 分支两个 `?` 占位只绑一个参数，
    任何 asof 查询都会抛 "Incorrect number of bindings"。补 `params.append(asof)` 正确。已直接实测。
  - task_states.py list_stale_proposals（L1376-1394）: status='proposed' 分支用
    action_type='stale_review' + `ref_id NOT IN (SELECT ... action_type='stale_resolved' AND status='applied')`
    排除已闭环 Proposal —— 与 propose_stale_tasks（stale_review/proposed）和 apply_stale_proposal
    （stale_resolved/applied）写入模式逐字段核对一致；else 分支保持原行为。逻辑正确。
- 发现: 按严重度列出
  - [中] tests/test_m5_4_e2e_purchase.py `_setup()`（约 L38-46）清理前缀与实际生成的 id 不匹配，且不删
    chunk，运行后残留脏数据，导致任何后续 pytest 会话的 conftest session fixture 崩溃。
    - 具体: task_create 生成 id 为 `task:YYYYMMDD-<slug>`（实测 `task:20260806-e2e-restock-1`），
      `_setup()` 只删 `LIKE 'task:e2e%'` 匹配不到；`_remember_chunk` 插入的 `chunk:e2e-*` 从不删除。
      一次运行实测残留: 4 chunks + 3 entities + 7 task_states + 3 audit_log。
    - 失败场景: 运行本测试文件后，下一个 pytest 会话的 autouse `_clean_test_data_session`
      （先 `DELETE FROM chunks`、从不清理 task_states/audit_log）在
      `DELETE FROM chunks WHERE source LIKE '%test%'` 处抛
      `sqlite3.IntegrityError: FOREIGN KEY constraint failed`
      （task_states.evidence_chunk_id → chunks(id)，schema.sql:172）。已实测复现。
      若在默认 DB_PATH（live 记忆库）运行，还会把幽灵 open 任务/测试 chunk 写进生产记忆库。
    - 建议: `_setup()` 改用带前导通配的前缀（如 `task:%e2e-`）并补
      `DELETE FROM chunks WHERE id LIKE 'chunk:e2e-%'`；e2e 测试避免对 live 库跑。
  - [低] tests/test_m5_4_e2e_purchase.py:31 硬编码 `REPO = Path("/Users/apple/.hermes/memory")` —
    该路径在本机已被移除、不存在；测试能过仅因 conftest 把 repo root 注入 sys.path。裸跑/复制到
    无 conftest 环境会 import 失败。建议改为 `Path(__file__).resolve().parent.parent`。
- 测试:
  - 环境: 全量 pytest 被既有 conftest session fixture（usearch index load 原生 abort）阻断；本轮用
    `scripts/init_db.py` 初始化全新临时 memory.db + `MNELO_MEMORY_SEARCH_BACKEND=usearch` 隔离运行。
  - 新增: test_m5_4_e2e_purchase.py —— **3/3 通过**。
  - 回归（各在独立新库上）: test_m5_2_stale_proposal **10/10**、test_task_states_list_replay **4/4**、
    test_task_states_create **5/5**、test_task_states_transition **4/4**、test_m5_3_d11_forget **10/10**。
  - list_tasks(asof) 修复直接验证: asof 单查 / asof+state / 默认路径均正常（修复前会抛绑定数错误）。
  - 未跑: test_asof_replay.py 在 setUpClass 的 memory.remember → usearch index.add 处原生段错误
    （本机既有环境问题；本提交未触及 search_index / memory.remember 路径，与此改动无关）。

## 2026-08-06 17:35 审查 6bbbcd0..3d3bbb2
- 范围: 6bbbcd0..3d3bbb2（共 1 个提交）
- 提交:
  - 3d3bbb2 fix(review): M28 review-pass 整改 (27/27 pass)
- 结论: 通过（无新增问题）—— 本提交修复了上轮审查的两个发现，并有对应测试覆盖
- 审查维度小结:
  - propose_stale_tasks 去重修复（task_states.py L1208-1226）: 旧逻辑只查 status='proposed'，
    audit_log append-only 使原 Proposal 行永远 'proposed'，apply 后第二次 propose 永远被跳过
    （stuck_task 检测静默失效）。新逻辑用 `NOT EXISTS(stale_resolved AND status='applied')`
    判定「未 resolved 才跳过」，与 list_stale_proposals / apply_stale_proposal 的 resolved
    判定一致；apply 后 task 仍 stale 会正确再提议。参数绑定 `(r[0], r[0])` 正确。
  - forget_task（L1505-1510）/ forget_loop（L1603-1609）重复 forget 拦截: 新增
    valid_until 已非 NULL 即抛错；底层 UPDATE 均带 `AND valid_until IS NULL`（CAS 安全），
    重复调用不会二次写坏数据，只是提前给出明确错误。正确。
  - memory.forget 死代码清理（memory.py L680-697）: 移除 `confirm_forget = False` 占位
    （上轮审查发现），task/loop 一律抛 ValueError 行为不变，docstring 对齐。
    全库无 confirm_forget 残留引用。
- 发现: 无
- 测试（各在独立新库上）:
  - 新增 test_m28_review_fixes.py —— **4/4 通过**（double-forget / apply 后再提议 / 死代码静态校验）。
  - test_m5_2_stale_proposal.py（修改后）—— **10/10 通过**。
  - test_m5_3_d11_forget.py —— **10/10 通过**。
  - test_m5_4_e2e_purchase.py（memory.forget 变更后回归）—— **3/3 通过**。
  - 环境: 同上轮（init_db.py 全新临时库 + usearch 隔离运行；全量套件仍被既有
    conftest usearch index load 原生 abort 阻断）。
## 2026-08-06 17:51 审查 d0fe4ab..41a468e
- 范围: d0fe4ab..41a468e（共 1 个提交）
- 提交:
  - 41a468e fix(review): M29 review-pass 整改 (30/30 pass)
- 结论: 通过（修复核实正确）—— 上轮两个发现（_setup 清理前缀不匹配 + 硬编码路径）均正确修复并有测试覆盖；残留收敛、不再触发下个会话 conftest 崩溃
- 审查维度小结:
  - _setup()（tests/test_m5_4_e2e_purchase.py L32-55）: 前缀改为 `task:%e2e-%`/`loop:%e2e-%`（兼容 `task:YYYYMMDD-<slug>` 日期前缀，实测 `task:20260806-e2e-restock-1`），补删 chunks（`chunk:e2e-%`/source 含 e2e-）、relations、audit_log（after_json）。修复前残留 4 chunks+3 entities+7 task_states+3 audit_log → 下个会话 conftest 在 `DELETE FROM chunks` 处 FK constraint failed；修复后实测残留收敛为 0 chunks + 1 ghost open task + 2 audit_log，不再崩溃。删除顺序（audit_log→task_states→chunks→relations→entities）正确：task_states.task_id/evidence_chunk_id 的 FK 目标先于被删源，relations 无 FK。
  - REPO 硬编码修复: test_m5_4 L23 改 `Path(__file__).resolve().parent.parent`。
  - 新测试 test_m29_review_fixes.py: 3 测覆盖前缀匹配、chunks 清理、无硬编码路径。task_create id 格式断言（`^task:\d{8}-e2e-`）与 _slugify/task_id 生成逻辑（L588-615）逐字段核对一致。
- 发现: 按严重度列出
  - [低] tests/test_m5_4_e2e_purchase.py:47 relations 清理只覆盖 `task:%e2e-%`，与 task_states/entities 清理同时覆盖的 `loop:%e2e-%` 不一致。
    - 具体: `DELETE FROM relations WHERE source_id LIKE 'task:%e2e-%' OR target_id LIKE 'task:%e2e-%'`，缺 loop 前缀分支。
    - 失败场景: 若未来 e2e 测试创建指向 `loop:e2e-*` 的关系行，删除 loop 实体后这些 relations 行残留为孤儿（无 FK 约束不崩溃，但污染图谱）。当前 m5.4 全链路不建 relations（实测残留 relations=0），不触发。
  - [低] tests/test_m5_4_e2e_purchase.py:209 test_e2e_proposal_then_apply_resolves 结束无 teardown 清理。
    - 具体: 该测试是文件最后一个，跑完不再有下一个 `_setup()` 兜底；整文件运行后残留幽灵 open 任务 `task:20260729-e2e-stale`（task_states 当前窗 state=open、valid_until=NULL）+ 2 条 audit_log（stale_review proposed / stale_resolved applied）。
    - 失败场景: 残留无 chunks，不再触发下个会话 conftest FK 崩溃；但该幽灵 open 任务对 propose_stale_tasks/digest 可见，且若 e2e 误对 live 库运行会污染生产记忆库。建议测试结束后（或 tearDown）也调用 _setup()。
- 测试:
  - 环境: 全量 pytest 仍被既有 conftest session fixture（usearch index load 原生 abort）阻断——本机既有问题，parent commit 上同样复现，与本次提交无关。沿用上轮工作区方案: `scripts/init_db.py` 全新临时库 + `MNELO_MEMORY_DIR`/`MNELO_MEMORY_SEARCH_BACKEND=usearch` 隔离运行。
  - test_m29_review_fixes.py —— **3/3 通过**。
  - test_m5_4_e2e_purchase.py —— **3/3 通过**。
  - 修复验证（关键场景）: 跑完上述 6 测后，在同一库上开启新 pytest 会话（触发 autouse `_clean_test_data_session`）跑 test_m5_2_stale_proposal.py —— **10/10 通过，不再崩溃**（修复前该场景正是 FK constraint failed 崩溃点）；第二遍重跑 test_m5_4 + test_m29 —— **6/6 通过**。

## 2026-08-06 18:16 审查 f95dae2..1a38589
- 范围: f95dae2..1a38589（共 2 个提交）
- 提交:
  - 747b26e feat(m30): 3 多角度优化 — race/validation/digest contract (34/34 pass)
  - 1a38589 fix(review): M31 review-pass 整改 — 2 低危修复 (17/17 pass)
- 结论: 发现 4 个问题（1 中 + 3 低）。M30 三个优化点（BEGIN IMMEDIATE 事务化、threshold 校验、digest 截断）功能正确、均有测试且本文件单独通过；但**新增 test_m30_optimization.py 的 `_setup` 清理前缀不匹配，每次运行泄漏 2 条 ghost open task 进共享库，会破坏既有 test_m4_digest_block4.py**（与上轮 M29/M31 修的 e2e 泄漏同类，建议本轮一并修复）。
- 发现: 按严重度列出
  - [中] tests/test_m30_optimization.py:30-31 `_setup()` 清理前缀与生成 id 不匹配 → 每轮泄漏，且破坏 test_m4_digest_block4.py。
    - 具体: 清理用 `task_id LIKE 'task:m30-%'` / `id LIKE 'task:m30-%'`；但 `_create_stale_task` 走 `task_create(now=10 天前)`，id 生成式为 `task:YYYYMMDD-<slug>`（task_states.py:102 `f"task:{date_part}-{slug}"`），实际 id 如 `task:20260727-m30-race-dup`——日期前缀夹在 `task:` 与 `m30` 之间，`task:m30-%` 永不命中。audit_log 清理（L32 用 `task:%m30-%`）命中，故只漏 task_states + entities 两表。
    - 失败场景: 单会话内 m30→m4 顺序运行（pytest 默认收集序），泄漏的 ghost open task 使 test_m4_digest_block4 的 `counts.active_tasks == 2` 断言失败——**实测: test_m4_digest_block4 单独在全新库 6/6 通过；m30→m4 同会话 3/6 失败**。反复运行同 id 撞名累加 `-2/-3` 变体；若对 live 库运行则污染生产 task_states/entities。修复: task_states/entities 清理改 `task:%m30-%`（对齐 audit_log 模式）。
  - [低] task_states.py:1134-1148 M30.2 校验插入到 docstring 之前 → `propose_stale_tasks.__doc__` 为 None。
    - 具体: `if not isinstance(stale_days_threshold, int)...` 抛错块位于函数体首句，其后的 `"""..."""` 不再是函数 docstring，降级为死字符串字面量。实测 `propose_stale_tasks.__doc__ is None` → True。
    - 失败场景: 破坏 help()/内省/任何基于 `__doc__` 的工具与文档生成；新 contributor 读不到参数说明。修复: 把 docstring 移到校验语句之前（docstring 必须是函数体第一条语句）。
  - [低] task_states.py:1483 render_digest_block4 截断用 `text_lines[-1].endswith("...")` 反推，可被名字以 "..." 结尾的 task 欺骗。
    - 具体: active task 循环截断后 append `"..."` 作哨兵，dormant loop 段用 `if not text_lines[-1].endswith("...")` 判断是否已截断。若最后一个 active task 的渲染行恰以 "..." 结尾（如任务名 `pending...`），即便 block4 远未到 2000 字符也会误判已截断 → dormant loop 块被静默丢弃。
    - 失败场景: digest 缺失 dormant loop 信息，agent 上下文看不到未闭环 loop；属信息丢失而非数据损坏。修复: 在截断处显式置 bool（如 `truncated = True`），不要从末行内容反推。
  - [低] tests/test_m30_optimization.py M30.1 标题称"并发安全"，但测试实为同连接顺序双 apply。
    - 具体: 两次 `apply_stale_proposal` 在同一连接上串行执行，只验证幂等（第二次抛 ProposalAlreadyResolved），未创建真实并发（双连接/双线程）。
    - 失败场景: BEGIN IMMEDIATE 的序列化行为与并发第二个写者拿锁时 `database is locked` 的上抛路径完全未被覆盖，race fix 的核心主张无测试背书。建议加双线程（或双连接）并发 apply 用例，断言恰好一行 stale_resolved。
- 测试:
  - 环境: 全量 pytest 仍被既有 conftest session fixture（usearch index load 原生 abort）阻断——本机既有问题，parent commit 上同样复现，与本次提交无关。沿用上轮工作区方案: `scripts/init_db.py` 全新临时库 + `MNELO_MEMORY_DIR`/`MNELO_MEMORY_SEARCH_BACKEND=usearch` 隔离运行。
  - test_m30_optimization.py + test_m5_4_e2e_purchase.py —— **7/7 通过**。
  - test_m28_review_fixes.py + test_m5_2_stale_proposal.py —— **13/13 通过**。
  - 归因验证（跨文件泄漏）: test_m4_digest_block4.py 单独在全新库 —— **6/6 通过**；m30→m4 同会话 —— m30 5/5 + m4 3/6（泄漏致 count 断言失败）；m28→m4 同会话 —— 同为 3/6 失败（m28 泄漏为既有问题，不在本次范围，但证明 test_m4 的 count 断言对跨文件残留普遍脆弱）。

## 2026-08-06 19:15 审查 7a62ec0..f2bf621
- 范围: 7a62ec0..f2bf621（共 3 个提交）
- 提交:
  - b655a7d fix(review): M32 review-pass 整改 — 1 中 + 3 低 (56/56 pass)
  - af56cd5 feat(m33): 多角度持续优化 — forget race + reason validation (42/42 pass)
  - f2bf621 feat(m34): list_stale_proposals status + limit 白名单校验 (68/68 pass)
- 结论: 通过（发现 2 个低危）。M32 整改正确（fixture 前缀日期通配符、docstring 归位、digest 显式 truncated flag、M32.4 双连接真并发测试）；M33 BEGIN IMMEDIATE 原子化 + reason 强校验、M34 status/limit 白名单实现干净。跨文件泄漏场景实测修复。
- 发现: 按严重度列出
  - [低] task_states.py:1569/1690 — forget_task/forget_loop 的 reason 非 str 类型抛未处理 AttributeError。
    - 具体: `reason_clean = reason.strip() if reason else ""`；truthy 非 str（如 int 12345）→ `.strip()` AttributeError，未收敛到 TaskLoopError。M34 给 limit 加了 isinstance 守卫，M33 reason 校验未加，两批「API 边界」风格不一致。
    - 失败场景: 调用方误传非字符串 reason → 崩 AttributeError（无 code 字段）而非可预期的 TaskLoopError（ReasonRequiredError / ReasonTooShortError）。
  - [低] task_states.py:1533 — render_digest_block4 dormant loop 段仍用旧长度公式。
    - 具体: dormant loop 段 `sum(len(s) + 1 for s in text_lines) > 2000-3` 沿用旧公式；task 段（1514）已换 `sum(len(s))+n-1` + 显式 truncated flag，且追加的 "..." 不参与长度核算。与 M32.3 声明的精确长度计算修复不一致。
    - 失败场景: 极端长 dormant loop name 时 digest 尾段贴 2000 边界、截断判定与 task 段不一致。
- 测试:
  - 环境: 全量 pytest 仍被既有 conftest session fixture（usearch index load 原生 abort）阻断——本机既有问题，parent commit 上同样复现，与本次提交无关。沿用隔离方案: scripts/init_db.py 全新临时库 + MNELO_MEMORY_DIR/MNELO_MEMORY_SEARCH_BACKEND=usearch 运行。
  - test_m34 + test_m33 + test_m30（含 M32.4 双连接并发 apply）—— 17/17 通过
  - test_m30 → test_m4 同会话（M32.1 跨文件泄漏验证，修复前 3/6 失败）—— 11/11 通过
  - test_m5_3_d11_forget（reason 长度回归）—— 10/10 通过
  - docstring 核实: propose_stale_tasks / apply_stale_proposal __doc__ 均非空
  - reason 边界实测: int → AttributeError（即发现 1）；纯空白 → ReasonRequiredError 正常

## 2026-08-06 21:02 审查 a8e7dca..cd0cd05
- 范围: a8e7dca..cd0cd05（共 1 个提交）
- 提交:
  - cd0cd05 fix(review): M35 review-pass 整改 — 2 低危 API 边界 (75/75 pass)
- 结论: 通过（修复核实正确）。M35.1 reason isinstance(str) 守卫生效——int/None/list 均抛 InvalidReasonTypeError（TaskLoopError），不再 AttributeError，与 M34 limit 守卫风格一致；M35.2 dormant loop 段 digest 改新公式（sum(len(s))+n-1）+ 60-char name 截断 + 显式 truncated flag，与 task 段对称。7 个新测试覆盖类型边界与 ≤2000 截断断言。
- 发现: 无新增发现。
- 测试:
  - 环境: 隔离临时库（scripts/init_db.py + MNELO_MEMORY_DIR + MNELO_MEMORY_SEARCH_BACKEND=usearch）。
  - test_m35(7)+test_m34(5)+test_m33(7)+test_m30(5) —— 24/24 通过
  - 跨文件回归 test_m35+test_m4+test_m5_3 —— 23/23 通过
  - 边界实测: reason=12345/None/['x']/42 → 全部 InvalidReasonTypeError

## 2026-08-06 22:05 审查 3977ff2..0886ac9
- 范围: 3977ff2..0886ac9（共 2 个提交）
- 提交:
  - 7dbdf74 feat(m36): transition() API 边界守卫 — reason/task_id/to_state/evidence (85/85 pass)
  - 0886ac9 docs(guide): 写死 usearch f16 精度约定 + 启动崩溃索引损坏排查（纯文档，3 文件 39 行）
- 结论: 通过（2 低危，无需整改级发现）。M36 把 transition() reason 从「仅 force=True 必填」改为「always 必填 + isinstance(str) 守卫」，与 docstring「reason: required」契约对齐，与 M33/M35 forget 路径同语义；task_id/to_state/evidence_chunk_id 非 str 早抛 TaskLoopError InvalidInputError（带 field/code），不再依赖 SQLite 类型转换静默失败。
- 发现:
  - [低] M36 测试隔离: test_m36_transition_guards.py 直接连 memory.DB_PATH（config 解析），未用既有 init_db.py 临时库模式。_setup() 仅删 m36 前缀行、_create_task 会真建 task——若 MNELO_MEMORY_DIR 指向 live 会轻度污染（m36 前缀隔离，风险低）。建议后续按既有隔离模式加固。
  - [低] transition() docstring "force: bypass allowed graph (要求 reason)" 措辞可误读为「仅 force 时需 reason」；主契约行 "reason: required" 正确。建议补一句：force 只绕转移图，reason 恒必填。
- 测试:
  - 环境: 隔离临时库（scripts/init_db.py + MNELO_MEMORY_DIR + MNELO_MEMORY_SEARCH_BACKEND=usearch）。
  - test_m36(10) —— 10/10 通过
  - 回归: test_review_fixes 18/18 + test_task_states_loop_tick 6/6 通过
  - test_asof_replay 在本机 usearch index.add 段错误——既有环境问题（REVIEW_LOG 已记录），非本提交引入
  - 调用方核实: MCP memory_task_transition 工具契约明示 reason 必填；CLI task_manager.py 用 `reason or "(no reason)"` 兜底；test_review_fixes 全部 transition 调用带非空 reason——M36 always-required 行为变更无运行时破坏
  - docs 0886ac9: 内容与实测一致（rebuild_index.py --fresh 重建 17 chunk 恢复启动崩溃；f32 Index load f16 文件 2/2 崩已复现），zh/en/RUNBOOK 三处同步

## 2026-08-06 21:34 审查 eed483f..（M36 2 低危整改）
- 范围: eed483f 之后工作区整改 2 低危（未先提交，随本 commit 一并 push）
- 整改:
  - [低 2] task_states.py transition() docstring: "force: bypass allowed graph (要求 reason)" → "force: bypass allowed graph; reason 仍必填 (D8 纠正门需审计痕迹)" + reason 行补 "恒必填, 不依赖 force"。消除「仅 force 时需 reason」歧义，与 M36 行为一致。
  - [低 1] tests/test_m36_transition_guards.py: 加模块级 `_assert_isolated_db()` fail-fast 守卫——解析出的 DB 若含真实 chunk（source 非 test/audit）则 SystemExit，拒绝对 live 记忆库写入；DB 不存在也给可操作提示。既有隔离模式不变（mktemp + init_db.py + MNELO_MEMORY_DIR）。
- 验证:
  - 隔离临时库 test_m36 → 10/10 通过（守卫生效放行）
  - 守卫对 live 库（30 真实 chunk）→ 正确拒绝运行
  - 守卫对不存在 DB → 清晰 SystemExit 提示
- 结论: 2 低危整改完成，行为无回归（docstring 为纯注释；守卫只在含真实数据时阻断）。

## 2026-08-06 21:44 审查 4bf3e2b..9e32ae2
- 范围: 4bf3e2b..9e32ae2（共 1 个提交）
- 提交:
  - 9e32ae2 fix(ops): health_check.py MCP 探测兼容 PATH 缺 /usr/sbin (5.2h uptime)
- 结论: 通过（1 极低危，理论性，非整改级）。双探测设计正确：lsof 三路径（lsof//usr/sbin/lsof//usr/bin/lsof）→ ps 兜底找 mcp_server.py 进程 → lsof `-a -p` 二次验证该 pid 持有端口；etime 解析失败不再掩盖 alive（parse_etime 自带 try/except 永不抛，返回 None，正常路径即得 (True,pid,None)）。
- 发现:
  - [极低·理论] check_mcp_alive() 的 `except Exception: return (True, int(pid_str), None)` 重复解析 pid_str——若 lsof 输出非数字（理论场景；`lsof -t` 仅输出数字 pid，实测不可触发），int() 在 except 内再抛会传播打崩整个 health_check，而非如实返回 DOWN。建议复用 try 内已解析的 `pid` 变量，纯防御性加固。
- 验证:
  - 实测 check_mcp_alive() = (True, 1197572, 831) ✓（本机 lsof 在 /usr/bin/lsof，三路径覆盖）
  - 场景模拟: ① 裸 lsof 缺失（沙箱 PATH）→ 显式路径回退 → 仍 alive ✓（修复目的达成）② lsof 完全缺失 → (False, None, None) ③ 正常 → alive
  - parse_etime 边界: ''/garbage → None（永不抛）；'5-03:14:22'→443662；'45:23'→2723
  - 新 health_check 全流程: ✅ MCP server alive PID 1197572, uptime 0.2h
- 备注: lsof 完全缺失时仍报 DOWN 属已知边界（ps 兜底也需 lsof 验证端口；根因是 PATH 剥离非 lsof 缺失，超出本提交范围）。后续可用 ss/netstat 做最终兜底增强，非缺陷。

## 2026-08-06 21:51 审查 57f7886..c75d3cb
- 范围: 57f7886..c75d3cb（共 1 个提交）
- 提交:
  - c75d3cb fix(ops): M37 defensive pid parse — 复用 try 内已解析 pid
- 结论: 通过（M37 极低危整改核实正确）。pid_str 只解析一次到 pid；int() 失败（ValueError/TypeError）→ 返回 (False, None, None) 与行为语义一致（lsof 给无效输入 → 真 DOWN），不再 except 内二次 int() 打崩整个 health_check。
- 发现: 无新增。
- 验证:
  - 正常路径: check_mcp_alive() = (True, 1197572, 1122) ✓
  - 模拟 lsof 输出非数字 pid_str → (False, None, None)，不崩 ✓（此前版本会崩溃）

## 2026-08-06 22:00 f16 契约运行时断言（并行会话审计报告评估）

- 背景: 并行审查会话报告 live usearch.index "1.2K / loaded size 0 (空)"，建议加运行时断言 + 重建索引。
- 评估结论: **报告前提在本机不成立**。实测 live 索引 = 20172 字节 / 17 向量 / dtype=F16 / ndim=512（health_check 交叉确认 vectors 17）；报告修复命令带 `launchctl stop ai.hermes.gateway`（macOS），非本机场景；其 "magic=0100000000040000" 与实际文件头 "11000000 00040000" 不符。**无需重建。**
- 采纳的加固: search_index.py:417-427 加运行时断言——磁盘索引 load 后 `dtype != ScalarKind.F16` 立即抛 RuntimeError（提示 `rebuild_index.py --backend usearch --fresh`）。实测依据: f32 文件 load 进 f16 Index 后 `.dtype` 静默变 `ScalarKind.F32`，后续 add 写 f16 → 混合精度文件 → 原生堆损坏（`free(): corrupted unsorted chunks`，即本机 21:13 崩溃根因一类）。断言把静默损坏变成启动期快速失败，落实 f16 永久契约。
- 测试: tests/test_search_index.py 新增 `TestUsearchF16Assertion`（2 用例，用空索引文件构造，避开本机 usearch add 偶发 SIGSEGV）— f16 加载通过 / f32 触发 RuntimeError。
- 验证: test_search_index.py 13/13 pass（隔离临时库 + init_db.py）。
- 备注: MCP server（PID 1197572）仍在跑旧代码，断言在下次自然重启生效；live 索引本就 f16，重启亦安全。本轮改动不触发自动审查（审查 cron 23713671 已暂停）。

## 2026-08-06 22:30 文档修复: 安装部署说明显式化 MNELO_MEMORY_SEARCH_BACKEND

- 背景: 用户要求确保 `MNELO_MEMORY_SEARCH_BACKEND` 在安装部署说明里"正确、清晰"。
- 触发事件: 验证时序状态机测试时三文件全崩 `dumped core`。根因链: conftest `Memory()` → `build_search_index('auto')` → `zvec_available()` 主进程 `import zvec` → 本机 (Ivy Bridge 无 AVX2) SIGILL。本机 venv **残留 zvec 0.6.0**，pytest 临时库 (`mktemp -d`) **无 config.toml** → fallback `auto` → 崩。live 库有 config.toml `backend='usearch'` 故 server 不崩。带 `MNELO_MEMORY_SEARCH_BACKEND=usearch` 后 21/21 pass，非代码回归。
- **新发现 (代码级隐患, 待评估)**: `search_index.py` 顶部 docstring (8/6) 明确警告"zvec 可用性检测必须在子进程进行, 不可在 mnelo 进程内 try-import (会把 mnelo 一起带崩)", 但 `zvec_available()` (search_index.py:585-601) 实际是**主进程 import** (注释: macOS 26 launchd fork 子进程跑 zvec native mmap 必现 BlockingIOError)。结果是: 装了 zvec 但无 AVX2 的机器上 auto 链 import 即 SIGILL, **与 docstring 契约矛盾**, 且 config.toml.example 的 "auto: 用不了 zvec 用 usearch" 描述对"残留 zvec 的无 AVX2 机"不成立。修复方向候选: zvec_available 先检测 CPU AVX2 (无则直接 False 不 import), 或恢复子进程检测+launchd fork 保护。**未改代码** — 文档层已显式化规避。
- 文档改动 (3 文件, 纯文档):
  - `README.zh.md` / `README.md`: backend 选择章节修正"自动降级/CPU 不重要"误导 → 明确 auto 主进程 import zvec 的 SIGILL 陷阱; 加 env 优先级 (env > config.toml > auto)、持久化推荐 config.toml + env 双设、合法值。
  - `README.zh.md` / `README.md` agent 安装步骤第 3 步: 无 AVX2 机器显式 `backend='usearch'` 警告。
  - `docs/RUNBOOK.md` 新增 §2.2: 无 AVX2 机器必须显式 usearch, 含根因、config.toml+env 双设示例、为什么双设。

## 2026-08-06 22:40 修复 zvec_available 主进程 import SIGILL 隐患（承接 22:30 记录）

- 根因: `zvec_available()` 主进程 `try-import zvec`，但 zvec 0.6 在无 AVX2 CPU 上 import 是进程级 SIGILL (非 Python 异常)，try/except 拦不住 → auto 链把整个 mnelo 带崩。与 search_index.py 顶部 docstring"必须子进程检测"契约矛盾。
- 修复: 主进程 import 前先 `_cpu_has_avx2()` 探测 CPU (Linux /proc/cpuinfo flags / macOS sysctl machdep.cpu.leaf7_features)。**明确无 AVX2 → 直接返回 False 绝不 import**，auto 链安全回落 usearch；平台探测不了 (None) 才回退 try-import。保留主进程路径 (macOS launchd fork 子进程 BlockingIOError 约束)。
- 测试: `TestZvecAvx2Gate` 4 用例 — CPU 探测返回 bool/None；无 AVX2 不 import 返回 False (此测试本机无 fake 注入，若走了 import 直接 SIGILL 崩测试进程即防回归)；AVX2 路径 import fake zvec；探测不了回退 import。
- 附带修复: `TestZvecBackendWithFake.setUp` 原 `zvec_available = lambda: True` 永久改写模块属性不恢复，污染后续测试类 (TestZvecAvx2Gate 的 mock 因此失效) → 改用 `mock.patch.object` + tearDown 恢复。
- 验证: test_search_index.py 17/17 pass；真实场景 (无 config.toml 临时库 + auto 链，此前 dumped core) → `zvec_available()=False`、zvec 未 import、auto 实际走 usearch/f16，不再崩；M38 + usearch 回归 16/16 pass。

## 2026-08-06 22:50 完善 RUNBOOK.md Linux 服务管理

- 背景: OPERATIONS.md VPS 章节一直引用 "RUNBOOK.md 'Linux deployment' note"，但 RUNBOOK 只有 macOS launchd (§5.1)，Linux 部分是空白。用户要求完善。
- 改动:
  - RUNBOOK.md §5.2 新增 **systemd 守护 (Linux 推荐)**: 完整 `mnelo-mcp.service` unit (Restart=always 应对 usearch 无 AVX2 偶发原生崩溃、`--host 127.0.0.1` 安全绑定、`MNELO_MEMORY_SEARCH_BACKEND=usearch` 显式环境变量呼应 §2.2 SIGILL 坑、User 最小权限); systemctl enable/status/journalctl 命令; 系统级 vs 用户级 unit 取舍。
  - RUNBOOK.md §5.2 备用: `setsid nohup` 后台方式 (本机实测部署方式, 无开机自启/自拉起, 需 cron @reboot 或 supervisor)。
  - RUNBOOK.md §9.1 重启: 补 Linux systemctl restart + nohup 重启命令。
  - RUNBOOK.md 标题/§1 系统要求: 改为 "macOS launchd / Linux systemd"。
  - OPERATIONS.md VPS 部署步骤 3: 引用更新为 "systemd unit 模板见 RUNBOOK §5.2"。
- 原 §5.2 测试 SSE 顺延为 §5.3 (无外部交叉引用破坏, 已 grep 验证)。

## 2026-08-06 23:15 完善 mnelo Linux 服务管理 — systemd 支持（模板 + install.sh 集成）

- 背景: 22:50 只写了 RUNBOOK §5.2 文档（手动抄 unit）。用户要求"完善 Linux 服务管理, 比如 systemd 支持" → 从文档升级为**代码级支持**。
- 改动:
  - 新增 `scripts/systemd/mnelo-mcp.service` **unit 模板**（对齐 launchd plist 的 sed 占位符约定: `__LIVE_ROOT__`/`__VENV_PY__`/`__USER_LINE__`/`__WANTED_BY__`）。含 `Restart=always`（应对无 AVX2 usearch 偶发原生崩溃）、`--host 127.0.0.1` 安全绑定、`MNELO_MEMORY_SEARCH_BACKEND=usearch` 显式环境变量（呼应 §2.2 SIGILL 坑）、`MNELO_MEMORY_SERVER_PORT=8086` env（config chain: env > toml > default）、LimitNOFILE/NPROC 资源限制（对齐 launchd plist）、日志写 `<LIVE_ROOT>/logs/`（与 backup/loop_tick cron 同目录）。
  - `scripts/install.sh` 第 10 步 Linux 分支从"跳过 launchd"升级为真 systemd 安装: **root → 系统级** `/etc/systemd/system/mnelo-mcp.service`（`WantedBy=multi-user.target` + `User=<当前用户>`）; **非 root → 用户级** `~/.config/systemd/user/mnelo-mcp.service`（`WantedBy=default.target`, user unit 不允许 `User=` 故注释）+ `loginctl enable-linger`（失败 warn, 提示 root 手动）。用户级自动 export `XDG_RUNTIME_DIR`（SSH 非登录 shell 连不上 user manager 的坑）。幂等: enable 失败不 abort（`|| warn` 提示手动命令）。
  - install.sh 头部注释第 6 步: macOS plist / Linux systemd 双路径说明。
  - `docs/RUNBOOK.md` §5.2 改为引用随包模板 + 自动安装说明; 手动模板里 `StandardOutput` 修正为实际路径（`/path/to/mnelo-data/logs/`, 不留 `__LIVE_ROOT__` 占位符）。
  - `docs/OPERATIONS.md` VPS 步骤 3: 删除过时的 "mnelo doesn't ship a unit file" → 改为 install.sh 自带模板说明。
- 验证: `bash -n` 通过; 模板两种 scope 渲染（sed）后 `systemd-analyze verify` **0 unit 错误**（仅假路径可执行性警告 + 系统无关 unit 警告）; 无占位符残留。

## 2026-08-08 08:20 访问方式切换: SSE → streamable-http (MCP 2025-03-26)

- 背景: 并行会话 a3bf1df 加了 streamable-http transport + dual。用户要求"不用 sse, 改用新方式访问 mnelo"。
- 改动:
  - `api/mnelo_client.py`: 新增 streamable-http transport (默认)。`DEFAULT_MCP_URL=http://127.0.0.1:8086/mcp`; `MneloClient(url=..., transport=...)` — URL 含 /sse 自动回落 sse (后兼容); `sse_url` 改 property (旧代码读写属性仍有效, 测试 _make_real_client 兼容); `_ensure_mcp` 按 transport 返回 sse_client / streamable_http_client; `_run_session` 抽取 (两 transport 共享 session+解析逻辑)。新 SDK API `streamable_http_client(url, http_client=...)` 用配置好 Bearer+timeout 的 httpx.AsyncClient 传入 (旧名 streamablehttp_client 已弃用)。
  - `scripts/session_start_digest.py`: 默认连 /mcp; env 覆盖 MNELO_MEMORY_URL > MNELO_MEMORY_SSE_URL (旧)。
  - `scripts/mn.py` / `mcp_server.py` docstring: transport 示例更新。
  - `scripts/systemd/mnelo-mcp.service` + `scripts/launchd/ai.mnelo.mcp.plist` + `scripts/install.sh`: --transport sse → streamable-http。
  - `docs/RUNBOOK.md` / `docs/AGENTS.md` / 根 `RUNBOOK.md`: §5.3 测试改为 /mcp 401 probe + /health; client YAML transport: streamable-http url: /mcp; §9.1 重启命令同步。
- 验证:
  - scratch server (8091, streamable-http) 起新 client 全通过 (stats/recall/digest)。
  - 正式 server 重启: `--transport streamable-http --host 127.0.0.1 --port 8086`, PID 1291126。全链路: mn.py digest/recall + session_start_digest + health_check 全绿。
  - SSE 后兼容: scratch SSE server 同 DB, 同一 recall 分数逐位一致 (证明结果与 transport 无关)。
  - 测试: test_s6_digest_resilience 8 passed; test_memory 35 passed + 1 deselected。
  - 已知失败 (预存在, 与 transport 无关): `TestRecallScoreFieldAlias::test_recall_score_in_realistic_range` — live DB 被并行会话 covgap 测试 chunk 污染, 查询 '翁氏 D∩W 共振' top-10 尾部 4 hit 单路 rank≥7 → rrf ≤ 1/67=0.0149 < 0.015 阈值。两 transport 分数逐位相同佐证非 transport 问题。

## 2026-08-08 08:31 清理: 数据敏感测试改数据无关 + 删根目录重复文件

- 背景: 用户要求"1 清理, 2 也清理"。(1) 上轮 08:20 记录的已知失败 `test_recall_score_in_realistic_range`;(2) 并行会话留下的根目录重复文件。
- 清理 1 — live DB 测试 chunk 污染 + 数据敏感测试:
  - live 库清掉 12 条测试污染: 10 个测试 chunk（`update:updated` 'test update' ×3、`update:test_update` ×1、`update:fix` asof TOKEN_B ×4、`main_block_demo_*` ×2）+ 2 个 demo 实体（`main_block_demo_stock/person_1785995322`），全部走 `forget()` 软删（向量移除 + 关系级联 + 30 天 purge 队列）。active chunk 39→22, 残留 0 测试污染; 22 个 active chunk 全为真实记忆, 无真实 chunk 误删。
  - 测试修复: 原断言绝对区间 `0.015 < rrf_score < 0.07` 依赖 live 库对查询有足够相关内容。查询 '翁氏 D∩W 共振' 无真实命中 → 纯单路向量邻居 rank≥7 即掉下任何固定下限（1/67=0.0149）。改为**数据无关不变量**: `0 < rrf_score < 1`（RRF=Σ1/(K+rank), K=60, 若干项到不了 1）+ hits 按 rrf_score 降序 + alias 一致性。live 库 / 隔离临时库均成立。验证: TestRecallScoreFieldAlias 3 passed; test_memory 28 passed。
- 清理 2 — 根目录重复文件删除:
  - `git rm` 4 个根目录重复文件: `RUNBOOK.md`（478 行, 重构前陈旧副本, docs/RUNBOOK.md 599 行为权威）、`test_auth.py`、`test_config_validation_coverage.py`（与 tests/ 逐字节相同）、`test_coverage_gaps.py`（859 行旧版, tests/ 860 行为新版且对当前 validate_id 语义正确）。无 pyproject testpaths → 根目录 `pytest` 会重复收集这些测试（旧版还会挂）。
  - 保留版验证: tests/test_auth.py 11 passed + 1 skipped; tests/test_config_validation_coverage.py 30 passed; tests/test_memory.py 28 passed; tests/test_coverage_gaps.py 74 passed（此前）。
- 环境注: 全量 pytest 仍被既有 usearch index load 原生 abort 阻断（REVIEW_LOG 多轮记录的**本机既有问题**, parent commit 同复现, 与本次无关）; 逐文件跑 + 隔离临时库可验证。
