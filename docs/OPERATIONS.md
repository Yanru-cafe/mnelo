# Operations — backup / restore, launchd, repo ↔ live sync

## Backup / restore

Quick reference (full design rationale: see git history `docs/TASKS_BACKUP_RESTORE.md` removed in 8/8 cleanup):

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

## VPS deployment (cheap US VPS — ~$10/year tier)

mnelo is **designed to fit the cheapest US VPS tier** (the ~$10/year
bracket — Racknerd / BandwagonHost / Hostinger KVM1 / etc.). The
usearch f16 vector backend (2 bytes/dim) keeps both RAM and disk
footprint low enough that even a 1 GB VPS with 25 GB disk is plenty.

### Why usearch f16 unlocks cheap VPS

| | zvec (INT8) | usearch (f16) |
|---|---|---|
| Vector file size | 1 byte/dim (INT8) | 2 bytes/dim (f16) |
| Native FTS | ✅ | ❌ (use SQLite FTS5 separately) |
| AVX2 required | ✅ | ❌ |
| Works on KVM1 (cheapest tier) | sometimes (AVX2 flag-dependent) | ✅ always |
| Recall quality at 512d | INT8 ≈ −1.5% vs f32 | f16 ≈ −0.4% vs f32 |

For a $10/year KVM1 (1 vCPU / 1 GB RAM / 25 GB SSD / no AVX2 guarantee):
usearch f16 is the right pick. zvec may fail to `import` (SIGILL on
non-AVX2 CPUs); the `auto` chain falls back to usearch automatically.

### Hardware requirements (per vector count)

| Vectors | RAM (f16) | Disk (f16) | CPU | VPS tier |
|---|---|---|---|---|
| 1k | 50 MB | 5 MB | 1 vCPU | KVM1 (1 GB) ✅ |
| 5k | 110 MB | 25 MB | 1 vCPU | KVM1 (1 GB) ✅ |
| 15k | 200 MB | 75 MB | 1 vCPU | KVM2 (2 GB) ✅ |
| 50k | 600 MB | 250 MB | 2 vCPU | KVM4 (4 GB) ✅ |
| 100k+ | 1.2 GB | 500 MB | 2-4 vCPU | dedicated tier |

Plus ~200 MB constant for the embedder (bge-small-zh) — so budget
**1 GB total RAM minimum**. The cheapest KVM1 tier is fine for ~5k
vectors, comfortably covers most personal-agent memory sizes.

### Recommended $10/year VPS providers

- **Racknerd** — 1 GB / 1 vCPU / 25 GB SSD, ~$10/year, multi-DC (US + EU)
- **BandwagonHost** — 1 GB / 1 vCPU / 20 GB SSD, ~$10/year, Los Angeles
- **Hostinger KVM1** — 1 GB / 1 vCPU / 20 GB SSD, ~$10/year, US/EU/Asia
- **GreenCloud** — 1 GB / 1 vCPU / 25 GB SSD, ~$12/year, US/EU/Asia
- **CloudCone** — 1 GB / 1 vCPU / 30 GB SSD, ~$12/year, US

All ship Ubuntu 22.04 / Debian 12 by default — mnelo's `scripts/install.sh`
runs unchanged. (Avoid OpenVZ / LXC; mnelo needs proper KVM/VM.)

### Setup on VPS

```bash
ssh root@your-vps
# Point your domain or just an A record at the VPS IP, or skip TLS:
# - Self-host without TLS: bind 127.0.0.1 + SSH-tunnel from agent side.
# - Expose with TLS: use caddy / nginx + Let's Encrypt (free).

# Install mnelo as usual
git clone https://github.com/chinesewebman/mnelo.git /opt/mnelo
cd /opt/mnelo
bash scripts/install.sh
# Choose:
#   1) memory_dir = /var/lib/mnelo (or /home/mnelo)
#   2) backend = 'usearch' (don't rely on auto chain — most KVM1 lack AVX2)
#   3) Linux service: install.sh ships scripts/systemd/mnelo-mcp.service —
#      run install as root to get the system-level unit (WantedBy=multi-user.target)
#      + `systemctl enable --now mnelo-mcp`; container / no-systemd hosts use
#      the setsid nohup fallback in RUNBOOK.md §5.2
#   4) backup schedule = local snapshot dir on same VPS (option 1)

# Verify
python3 scripts/health_check.py
curl -sS http://127.0.0.1:8086/health | jq
```

### Security posture on a public VPS

- **Bind to 127.0.0.1 by default** — only SSH into the VPS to reach it.
  The agent (running locally or on another host) connects via
  `ssh -L 8086:127.0.0.1:8086 your-vps`.
- For remote access without SSH tunnel: put mnelo behind TLS
  (caddy / nginx), and **enable Bearer auth** (`MNELO_MEMORY_SERVER_TOKEN`).
  The `scripts/install.sh` step "auth token" prompts to set this.
- mnelo is single-user. Don't expose the MCP port to the LAN/internet
  without TLS + Bearer.

### Why this matters for VPS-as-relay agents

The pattern is: **local Claude Code / Hermes** connects over the
internet to **a cheap VPS running mnelo**, so the agent's memory
persists across machines (laptop at home, workstation at office, etc).
The $10/year VPS replaces what Mem0 / Zep charge ~$20-100/month for.
For agents that already need a VPS as a relay / tunnel endpoint
anyway, mnelo slots in as a free extension.

## Known limitations

| Limit | Workaround |
|---|---|
| Single-user (no multi-tenant) | Don't expose port 8086 to LAN |
| **PII advisory only** — mnelo doesn't auto-redact or refuse; callers decide what to store | Stance per Content-neutral design; advisory hits are logged to `audit_log (pass_name='pii_audit')`; `/health` exposes `pii_warnings_last_24h` + recommended `memory_audit_list` review |
| bge-small-zh is CN-tuned | Swap to `bge-small-en-v1.5` for EN-heavy workloads |
| L2 maintenance layer is **opt-in** (default `l2.enabled=0`) | Ship-default off per DESIGN §5.7; flip one `UPDATE meta SET value='1' WHERE key='l2.enabled'` to enable |

See [RUNBOOK.md](RUNBOOK.md) for full operational guidance — install,
launchd, client connection, recovery.
