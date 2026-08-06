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
