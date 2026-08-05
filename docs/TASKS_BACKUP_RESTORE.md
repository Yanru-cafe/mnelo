# TASKS_BACKUP_RESTORE — mnelo 数据库备份/恢复

**版本**：v0.1 · 2026-08-05
**设计依据**：DESIGN §3.8 (快照) + §3.11 (恢复流程) + 8/5 主人新需求
**TASKS 范围**：scripts/backup_db.py + scripts/restore_db.py + install.sh 询问 + launchd 调度 + README 给 agent 段

---

## 0. 背景与现状盘点 (8/5)

| 组件 | 状态 |
|---|---|
| DESIGN §3.8 快照定义 | ✅ 已定义（`.backup` API + `.db.gz` + rsync NAS） |
| DESIGN §3.11 恢复流程 | ✅ 已定义（6 步 + 完整性校验 + 坏快照降级链） |
| RUNBOOK 命令片段 | ✅ RUNBOOK.md 有 `sqlite3 .backup` 例子 |
| `scripts/backup_db.py` | ❌ 不存在 |
| `scripts/restore_db.py` | ❌ 不存在 |
| `~/.hermes/scripts/dr-backup-memory.sh` | ❌ **dr-backup.sh 引用但不存在** |
| post-commit 自动触发 | ❌ 仅在 DESIGN §3.8 提到，脚本侧未实现 |
| 安装时询问配置 | ❌ install.sh 完全没碰 |
| README 给 agent 段 | ❌ 没提备份/恢复 |

**8/5 主人新需求**：
1. 安装时询问 **备份位置**（可选项 + 用户指定）
2. 备份 **频率**（每周全备 + 周内差异备，看实现可行性）
3. **数据恢复方式教给 agent**
4. README "给 agent 那一节" 补说明

---

## 1. 备份策略决策

### 1.1 差异备可行性分析

SQLite 原生**不支持** 单文件差异备份。WAL 模式下面是 "cp memory.db + cp memory.db-wal" 但：
- WAL 一直在被 MCP 写入，差时间戳没意义
- 恢复时要把 WAL replay，依赖 WAL 完整性
- 实战中 WAL 损坏率高（电掉、重启有概率）

**结论**：差异备复杂度高且易出错，**方案 C (周中+周日 全备) 落地**。

### 1.2 频率方案

| 方案 | 频率 | 体积/月 | 灾难恢复能力 |
|---|---|---|---|
| A. 每日全备 | 30 份/月 | ~300 MB | 保留 30 天，1 天 RPO |
| **B. 周三+周日 全备**（推荐） | 8 份/月 | ~80 MB | 保留 30 天 = 4 周 = 7 天 RPO |
| C. 仅周日 | 4 份/月 | ~40 MB | 保留 30 天 = 4 周 = 7 天 RPO（最差情况） |

**拍板**：方案 B（每周两次全备，周中 + 周日 各一次）。
- 体积：单份 gzip ≈ 5-10 MB × 8 份/月 = 40-80 MB/月
- 调度：周三 03:00 + 周日 03:00（与主人 MEM maintenance 错开）
- 保留：30 份（≈ 4 周历史）

### 1.3 备份位置选项

| 选项 | 路径 | 同步机制 | 适用 |
|---|---|---|---|
| 1. 本地默认 | `~/.hermes/memory/snapshots/` | 仅本地 | 默认 |
| 2. macbot-memory | `~/macbot-memory/work/mnelo-snapshots/` | 自动 rsync → NAS via dr-backup.sh | 推荐（已有基建） |
| 3. GitHub repo (via dr-backup.sh) | `~/macbot-memory/work/mnelo-snapshots/` | 自动 rsync → git add → push → NAS | 已有 macbot-memory 仓库 + dr-backup 部署的用户 |
| 4. 自定义 | 用户输入 | 看用户 | 高级用户 |

**拍板**：4 选项 + 自定义。默认推荐选项 3（owner 已有 dr-backup 基建, 零额外实施成本）。

### 1.3.1 隐私强制约束 (8/5 主人拍板)

**mnelo.db 是 PII 级别**：含个人记忆、决策、偏好、实体关系、持仓、财务事实。**不能 push 到 public GitHub repo**。

**必须**：
- 选 GitHub 周期全备 (选项 3) 前，**先验证 dr-backup.sh 目标 `macbot-memory` 是 private**（`gh repo view chinesewebman/macbot-memory --json visibility` 应为 PRIVATE）
- install.sh 选项 3 后**警告行强制提醒**（"⚠️ 仓库必须是 PRIVATE"）
- README 给 agent 段**开头强制 PRIVACY 警告**
- agent 帮用户配 dr-backup.sh 时，**第一步**验证目标仓库 visibility；public → 拒绝继续，提示先 `gh repo edit --visibility private`（个人 repo）或迁移 → private fork（组织 repo）

**安装时检查**（install.sh 步骤 12 选项 3）：
```bash
if ! gh repo view chinesewebman/macbot-memory --json visibility 2>/dev/null | grep -q PRIVATE; then
    warn "无法确认目标仓库是 private. 主人请手动验证后再启选 3."
fi
```

**注释给 agent**：install.sh 步骤 12 选项 3 的 echo 输出行尾带 ⚠️ 警告。agent 读 README 时第一步看 PRIVACY 段。

---

## 2. 任务分解

### A1. `scripts/backup_db.py` — 备份脚本

**输入**：config `[backup]` 段（snapshot_dir / schedule / retention）
**流程**：
1. 读 config → 缺省 fallback（snapshot_dir = `~/.hermes/memory/snapshots/`）
2. `snapshots/YYYY-MM-DD-HHMMSS.db` 输出
3. SQLite `connection.backup(target)` API（不是 `cp` —— DESIGN §3.8 警告）
4. gzip → `.db.gz`
5. 写 `YYYY-MM-DD-HHMMSS.db.gz.sha256`（SHA256 哈希）
6. retention: 按 mtime 删旧（保留 N 天按 N 份）
7. 报告：`{"path": ..., "size_mb": ..., "duration_sec": ..., "kept": N, "pruned": M}`

**CLI**：
```bash
python scripts/backup_db.py           # 默认从 config
python scripts/backup_db.py --force   # 忽略"今已有"去重 (默认一日内不重复)
python scripts/backup_db.py --dry-run # 只统计不写
```

**幂等性**：同一秒 timestamp 重复写入 → 覆盖（用 `replace_all` 行为）。

### A2. `scripts/restore_db.py` — 恢复脚本

**输入**：
- `--from YYYY-MM-DD-HHMMSS` 选快照
- `--latest` 选最新
- `--list` 列出所有快照 + 哈希 + 大小
- `--dry-run` 只跑校验
- `--target PATH` 恢复目标路径（默认 live db 路径）

**流程**：
1. 选快照（`--list` 列；`--from`/`--latest` 选）
2. 校验 gzip sha256
3. `PRAGMA integrity_check` + `quick_check` + `foreign_key_check`
4. 失败 → 报 fail + 提示降级链（DESIGN §3.11.2）
5. 隔离当前 db → `memory.db.corrupt-<date>`
6. 解压到 `<target>.tmp` → `mv` 原子替换
7. 报告：恢复的快照 + 校验结果

**CLI**：
```bash
python scripts/restore_db.py --list
python scripts/restore_db.py --latest --dry-run            # 验证最新
python scripts/restore_db.py --from 2026-08-05-030000      # 实际恢复
```

### A3. `scripts/launchd/ai.mnelo.backup.plist` — 调度

**StartCalendarInterval**：
- 周三 03:00 (`Weekday = 3`)
- 周日 03:00 (`Weekday = 0`)

**ProgramArguments**：
```xml
<array>
    <string>__VENV_PY__</string>
    <string>__LIVE_ROOT__/scripts/backup_db.py</string>
</array>
```

**条件**：
- `RunAtLoad = false`（不立刻跑，让 launchd 触发）
- `StandardOutPath / StandardErrorPath` 写到 logs

### A4. `scripts/install.sh` 询问

**位置**：health_check 之后（步骤 11 之后），新加步骤 12。

**询问脚本**：
```bash
read -p "Enable scheduled backups? [Y/n] " enable_backup
case "$enable_backup" in
    [Nn]*) ok "跳过备份配置" ;;
    *)
        echo "Snapshot location:"
        echo "  1) ~/.hermes/memory/snapshots/  (local only)"
        echo "  2) ~/macbot-memory/work/mnelo-snapshots/  (auto-rsync to NAS via dr-backup.sh)"
        echo "  3) Custom path"
        read -p "Choice [1/2/3]: " loc_choice
        ... # 写 config.toml [backup] section
        # 装 plist
        ;;
esac
```

写 `config.toml`:
```toml
[backup]
enabled = true
snapshot_dir = "~/.hermes/memory/snapshots"
schedule = "wed+sun"  # wed+sun / daily / weekly
retention = 30
```

非交互模式（CI / 自动化）：env var `MNELO_MEMORY_BACKUP_SKIP_PROMPT=1` 跳过询问（且 stdin 必须是 tty 才询问，否则自动跳过）。

直接走配置无需询问时：用 `MNELO_MEMORY_BACKUP_SNAPSHOT_DIR=...` + `MNELO_MEMORY_BACKUP_RETENTION=N` + `MNELO_MEMORY_BACKUP_ENABLED=1`（[8/5 修订] config.py 新增读取, backup_db.py 通过 config.backup_snapshot_dir 读到。 旧文档 `MNELO_MEMORY_BACKUP_ENABLED/DIR` 拼写不一致已废弃。）

### A5. README "给 agent" 段补 (DESIGN §3.8 + §3.11 实现指向)

**位置**：README.md / README.zh.md §"🤖 For AI agents (adopt mnelo as your memory)" 后补 step 5。

**内容**：
- 备份：跑一次手动备份 + schedule 自动
- 恢复：list / latest / from
- 健康度：定期 dry-run 演练（DESIGN §3.11.3 演练即测试）
- 跨机迁移：备份 → 拷到新机 → 装 → 恢复

### A6. 测试

**`tests/test_backup_restore.py`** (~150 行):
- `test_backup_creates_snapshot` — 跑 backup → snapshot 存在 + gzip magic + sha256 写
- `test_backup_dry_run_creates_nothing` — dry-run 不写
- `test_backup_retention_prunes_old` — 造 5 份 + retention=3 → 剩 3
- `test_restore_list_shows_snapshots` — list 返回所有快照
- `test_restore_dry_run_validates_only` — 不动 live db
- `test_restore_atomic_replace` — 实际恢复 → live db 内容等效
- `test_corrupt_snapshot_fails_safely` — 损坏 sha256 → fail 提示降级
- `test_missing_snapshot_errors_cleanly` — 错的 timestamp → 告诉你没这快照

---

## 3. 实施顺序

1. **A1 + A2 脚本 + A6 测试**（先核心）
2. **A4 install.sh 询问**（脚本能跑后集成）
3. **A3 plist 调度**
4. **A5 README 段**
5. **派 review + ship**

---

## 4. 决策表

| 决策 | 拍板 | 依据 |
|---|---|---|
| 差异备 vs 全备 | 全备（每周两次） | 差异备复杂度高；RPO 7 天可接受 |
| 频率 | 周三 + 周日 03:00 | 错开 rg.maintenance；owner 习惯 |
| 保留 | 30 份 (~4 周) | 1 个月回滚 + 不能引入额外 cron |
| 备份位置 | 询问 3 选项 + 自定义 | owner 8/5 需求 |
| 默认位置 | 选项 1（local ~/.hermes/memory/snapshots） | 零依赖；user 后可切换 |
| 哈希 | SHA256 | DESIGN §3.11.1 双层校验 |
| 恢复原子性 | `mv tmpfile target` | 防写一半 |
| 调度 | launchd plist | 已有 ai.mnelo.mcp 基建 |
| 询问 | install.sh 步骤 12 | 流程不破 |
| env 跳过询问 | `MNELO_MEMORY_BACKUP_*` | CI / 自动化 |

---

## 5. 不在本 TASKS 范围

- **远端 S3 备份**（DESIGN §3.8 提了 rsync NAS，没说 S3）— 留给未来
- **dr-backup.sh 集成**（dr-backup-memory.sh）— 主人已有 `~/.hermes/scripts/dr-backup.sh`；本 TASKS 不动它
- **跨实例 lock**（多 MCP server 备份协调）— 单写者模型，无 lock 需求
- **加密备份**（主人 secrets 备份另用 sensitive-secrets）
- **自动恢复 drill**（DESIGN §3.11.3 提"每月演练"）— 留给 agent 主动跑
