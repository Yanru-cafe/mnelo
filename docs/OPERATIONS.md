# Operations — backup / restore, launchd, repo ↔ live sync

## Backup / restore

See [TASKS_BACKUP_RESTORE.md](TASKS_BACKUP_RESTORE.md) for the full
design rationale. Quick reference:

```bash
# Manual backup (writes to config [backup] snapshot_dir + sha256 sidebar)
python scripts/backup_db.py
python scripts/backup_db.py --dry-run   # preview only

# List snapshots + verify sha256
python scripts/restore_db.py --list

# Verify a snapshot (dry-run, never touches live)
python scripts/restore_db.py --latest --dry-run

# Actual restore (isolates current db → memory.db.corrupt-<date>, atomic replace)
python scripts/restore_db.py --from 2026-08-05-030000
# or: python scripts/restore_db.py --latest
```

**⚠️ PRIVACY**: if the user picks GitHub auto-push (option 3), the
destination repo MUST be private. mnelo.db contains personal memory,
decisions, preferences, entity relationships — PII-level. Pushing to a
public repo = data leak. Verify before enabling `ai.mnelo.backup.plist`
with a GitHub-backed `snapshot_dir`.

**Schedule**: `ai.mnelo.backup.plist` runs `wed+sun 03:00` via launchd.
Install step 12 prompts the user where to store backups (1: local, 2:
NAS, 3: GitHub repo, 4: custom) and retention count (default 30 ≈ 4
weeks).

**Recovery drill (run monthly)**: `scripts/restore_db.py --latest
--dry-run` confirms the most recent snapshot is healthy. If this fails,
the snapshot is corrupt — DESIGN §3.11.2 says fall back to the previous
one. If all snapshots fail, the backup chain is untrustworthy;
investigate `logs/mnelo.backup.error.log` and re-test by manually
running `backup_db.py`.

## launchd plist

Install: `bash scripts/install.sh` registers the MCP plist
(`~/Library/LaunchAgents/ai.mnelo.mcp.plist`).

```bash
# start
launchctl load ~/Library/LaunchAgents/ai.mnelo.mcp.plist

# stop
launchctl unload ~/Library/LaunchAgents/ai.mnelo.mcp.plist

# restart (after code update via repo↔live sync)
launchctl kickstart -k gui/$(id -u)/ai.mnelo.mcp

# check status
launchctl list | grep mnelo

# view error log
tail -50 /tmp/mnelo-mcp.err
```

## Repo ↔ live sync (post-commit hook)

mnelo has two copies of every `.py` / `.sql` file: the repo (whatever
directory you cloned it into) and the live server dir (set via
`MNELO_MEMORY_DIR`; defaults to `~/mnelo-data`). The repo ships a
**post-commit hook** that syncs edited files to live, backs up the old
version, and runs `health_check.py` after:

```bash
cd <your-clone-dir> && git config core.hooksPath .githooks
```

Skips `memory.db` / `config.toml` / `*.md` / `tests/` (by design).
Restart the MCP server after sync:
`launchctl kickstart -k gui/$(id -u)/ai.mnelo.mcp`.

## Known limitations

| Limit | Workaround |
|---|---|
| Single-user (no multi-tenant) | Don't expose port 8086 to LAN |
| **PII advisory only** — mnelo doesn't auto-redact or refuse; callers decide what to store | Stance per Content-neutral design; advisory hits are logged to `audit_log (pass_name='pii_audit')`; `/health` exposes `pii_warnings_last_24h` + recommended `memory_audit_list` review |
| bge-small-zh is CN-tuned | Swap to `bge-small-en-v1.5` for EN-heavy workloads |
| L2 maintenance layer is **opt-in** (default `l2.enabled=0`) | Ship-default off per DESIGN §5.7; flip one `UPDATE meta SET value='1' WHERE key='l2.enabled'` to enable |

See [RUNBOOK.md](RUNBOOK.md) for full operational guidance — install,
launchd, client connection, recovery.
