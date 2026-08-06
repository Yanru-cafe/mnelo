# L2 autonomous maintenance layer

An **opt-in** layer that keeps long-running memory healthy without any
LLM in the loop. State persists in the `meta` and `audit_log` tables; the
same code paths run from the CLI, the MCP server, or a cron job.

## The 4 passes

| Pass | What it does | Side effect |
|---|---|---|
| `hygiene` | `importance` decay toward floor (0.1) + per-`memory_type` TTL (ephemeral 7d / fact 365d / preference 180d / episode+decision 730d / procedure permanent) | soft, non-destructive by default |
| `promote` | high-frequency `fact`s graduate to structured `canonical_fact` entities | additive (new entities only) |
| `decay` | inline inside `hygiene` — stale low-importance items lose weight, never get hard-deleted | metadata-only |
| `audit_log_gc` | prune `audit_log` entries older than 1 year (TASKS §3 L2 hygiene GC) | metadata-only |

## Opt-in: it ships off, you flip one SQL row

```sql
-- enabled by default? NO. Ship-default off (DESIGN §5.7):
UPDATE meta SET value='1' WHERE key='l2.enabled';
-- optional: turn off the dry-run safety net
UPDATE meta SET value='0' WHERE key='l2.dry_run';

-- inspect state:
SELECT key, value FROM meta WHERE key LIKE 'l2.%';
```

Or via the `memory_maintenance` MCP tool — defaults to dry-run
regardless of the meta setting, so a cron-triggered sweep is safe by
construction.

## Safety

- **`dry_run=true` is the default**. `hygiene` and `promote` produce
  proposals without touching chunks; only `purges` (TTL deletion) need
  an explicit `confirm_destructive=true` flag.
- **Per-proposal transactions** — a bad proposal never poisons the
  batch.
- **Every action lands in `audit_log`** with `before/after` JSON +
  `revert_sql`. Roll anything back via `memory_audit_undo`.
- **Re-entrant guard** — `meta.l2.running=1` blocks a second sweep
  while one is in flight.

## Measured in production

A live instance (8/6) reports ~47 k `audit_log` rows accumulated across
multiple L2 sweeps. One configured run had `last_run_hygiene` 1h before
snapshot and `audit_log_total=47408`. On the same instance, `hygiene`
after decay left `decay_floor_chunks=2259` (kept at importance 0.1) and
`purge_backlog=2170` (proposals waiting for a destructive run).

Use `scripts/health_check.py` (or the `/health` endpoint) to read the
latest `last_run_*` timestamps. `memory_stats` exposes the same
counters.
