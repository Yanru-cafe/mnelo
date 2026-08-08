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
