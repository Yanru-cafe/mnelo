#!/usr/bin/env python3
"""
restore_db.py — mnelo DB 快照恢复 (DESIGN §3.11 + TASKS_BACKUP_RESTORE A2).

选择快照 → 校验 sha256 → PRAGMA integrity_check → 隔离当前 db →
原子替换 live db. dry-run 只跑校验不落盘.

用法:
  python scripts/restore_db.py --list
  python scripts/restore_db.py --latest --dry-run
  python scripts/restore_db.py --from 2026-08-05-140429 --dry-run
  python scripts/restore_db.py --latest              # 实际恢复
  python scripts/restore_db.py --from YYYY-MM-DD-HHMMSS --target /tmp/foo.db
"""
import argparse
import datetime as dt
import gzip
import hashlib
import os
import shutil
import sqlite3
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))
from config import config as _config  # noqa: E402


def _expand(p):
    return Path(os.path.expandvars(os.path.expanduser(str(p))))


def _default_snapshot_dir():
    return _expand(_config.db_path).parent / "snapshots"


def _read_backup_config():
    snap_dir = _expand(
        getattr(_config, "backup_snapshot_dir", None) or _default_snapshot_dir()
    )
    return snap_dir


def _verify_sha256(gz_path: Path) -> tuple[bool, str]:
    """Return (ok, sha256_hex)."""
    # backup_db.py 写的是 <ts>.db.gz.sha256 (gzip + 显式 .sha256 后缀),
    # Path.with_suffix 只换最后一个后缀, 不能用. 显式 append.
    sha_path = gz_path.parent / (gz_path.name + ".sha256")
    if not sha_path.exists():
        return False, "no sha256 sidebar"
    expected = sha_path.read_text().split()[0]
    h = hashlib.sha256()
    with gz_path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    actual = h.hexdigest()
    return actual == expected, actual


def _integrity_check(db_path: Path) -> dict:
    """Run PRAGMA integrity_check + quick_check + foreign_key_check. Returns dict."""
    con = sqlite3.connect(str(db_path))
    try:
        out = {}
        # integrity_check 返 'ok' 列表, length == 1 时正常
        rows = con.execute("PRAGMA integrity_check").fetchall()
        out["integrity_check"] = rows[0][0] if rows else "no-result"
        rows = con.execute("PRAGMA quick_check").fetchall()
        out["quick_check"] = rows[0][0] if rows else "no-result"
        rows = con.execute("PRAGMA foreign_key_check").fetchall()
        # foreign_key_check 异常时返 (table, rowid, parent) tuples; 空 = ok
        out["foreign_key_check"] = "ok" if not rows else f"violations: {len(rows)}"
        # 统计
        stats = {}
        for tbl in ("chunks", "entities", "relations", "audit_log"):
            try:
                n = con.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
                stats[tbl] = n
            except sqlite3.OperationalError:
                stats[tbl] = None
        out["row_counts"] = stats
        return out
    finally:
        con.close()


def _list_snapshots(snapshot_dir: Path) -> list[dict]:
    if not snapshot_dir.exists():
        return []
    out = []
    for gz in sorted(snapshot_dir.glob("*.db.gz"), reverse=True):
        sha_ok, sha = _verify_sha256(gz)
        out.append({
            "name": gz.name,
            "path": str(gz),
            "size_mb": round(gz.stat().st_size / 1024 / 1024, 3),
            "mtime": gz.stat().st_mtime,
            "sha256_ok": sha_ok,
            "sha256": sha,
        })
    return out


def _select_snapshot(snapshot_dir: Path, ts: str | None) -> Path:
    """Resolve --from / --latest to a concrete .db.gz path."""
    if not snapshot_dir.exists():
        raise FileNotFoundError(f"快照目录不存在: {snapshot_dir}")
    if ts is None:
        # latest
        gzs = sorted(snapshot_dir.glob("*.db.gz"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not gzs:
            raise FileNotFoundError(f"无快照: {snapshot_dir}")
        return gzs[0]
    # explicit timestamp
    target = snapshot_dir / f"{ts}.db.gz"
    if not target.exists():
        # 尝试 prefix 匹配
        matches = list(snapshot_dir.glob(f"{ts}*.db.gz"))
        if not matches:
            raise FileNotFoundError(f"无快照匹配 '{ts}': {snapshot_dir}")
        if len(matches) > 1:
            names = ", ".join(m.name for m in matches[:5])
            raise ValueError(f"'{ts}' 匹配多个快照 ({len(matches)}): {names}...")
        return matches[0]


def _atomic_replace(src: Path, target: Path) -> None:
    """Decompress src to <target>.tmp, then mv to target atomically."""
    tmp = target.with_suffix(target.suffix + ".tmp")
    with gzip.open(src, "rb") as f_in, tmp.open("wb") as f_out:
        shutil.copyfileobj(f_in, f_out, length=1 << 20)
    # mv 是 atomic on same filesystem
    os.replace(tmp, target)


def _isolate(target: Path) -> Path:
    """Move current live db → memory.db.corrupt-<date>. Returns the corrupt path."""
    if not target.exists():
        return None
    ts = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    corrupt = target.parent / f"{target.name}.corrupt-{ts}"
    shutil.move(str(target), str(corrupt))
    return corrupt


def restore(
    snapshot_dir: Path,
    ts: str | None,
    target: Path | None = None,
    dry_run: bool = False,
) -> dict:
    """Run one restore. Returns stats dict."""
    target = Path(target) if target else _expand(_config.db_path)
    gz_path = _select_snapshot(snapshot_dir, ts)
    report = {"selected": str(gz_path), "target": str(target), "dry_run": dry_run}

    # 1. sha256
    sha_ok, sha = _verify_sha256(gz_path)
    report["sha256_ok"] = sha_ok
    report["sha256"] = sha
    if not sha_ok:
        report["error"] = "sha256 校验失败"
        return report

    # 2. 解压到 tmp, 跑 integrity_check
    tmp = target.parent / f"{target.name}.{ts}.validate.tmp"
    if tmp.exists():
        tmp.unlink()
    with gzip.open(gz_path, "rb") as f_in, tmp.open("wb") as f_out:
        shutil.copyfileobj(f_in, f_out, length=1 << 20)

    try:
        check = _integrity_check(tmp)
        report["integrity_check"] = check
        if check["integrity_check"] != "ok":
            report["error"] = f"integrity_check failed: {check['integrity_check']}"
            return report
        if check["quick_check"] != "ok":
            report["error"] = f"quick_check failed: {check['quick_check']}"
            return report
        if check["foreign_key_check"] != "ok":
            report["error"] = f"foreign_key_check failed: {check['foreign_key_check']}"
            return report

        if dry_run:
            return report

        # 3. 隔离当前 db
        corrupt = _isolate(target)
        if corrupt:
            report["isolated_to"] = str(corrupt)
        else:
            report["isolated_to"] = None

        # 4. 原子替换 (从 tmp → target)
        try:
            os.replace(tmp, target)
            report["restored"] = str(target)
        except Exception:
            # 失败: 从 corrupt 恢复
            if corrupt:
                shutil.move(str(corrupt), str(target))
            raise

        return report
    finally:
        if tmp.exists():
            tmp.unlink()


def main():
    ap = argparse.ArgumentParser(description="mnelo DB 快照恢复")
    ap.add_argument("--list", action="store_true", help="列出所有快照 + 校验状态")
    ap.add_argument("--from", dest="ts", default=None,
                    help="指定快照 timestamp (YYYY-MM-DD-HHMMSS)")
    ap.add_argument("--latest", action="store_true", help="选最新快照")
    ap.add_argument("--dry-run", action="store_true", help="只校验不恢复")
    ap.add_argument("--snapshot-dir", type=Path, default=None,
                    help="覆盖 config [backup] snapshot_dir")
    ap.add_argument("--target", type=Path, default=None,
                    help="恢复目标路径 (默认 live db 路径)")
    args = ap.parse_args()

    snap_dir = Path(args.snapshot_dir) if args.snapshot_dir else _read_backup_config()

    if args.list:
        snaps = _list_snapshots(snap_dir)
        for s in snaps:
            print(s)
        if not snaps:
            print(f"(no snapshots in {snap_dir})")
        return 0

    # --from / --latest 必须二选一, 默认走 latest
    if args.ts and args.latest:
        print("ERROR: --from 和 --latest 互斥", file=sys.stderr)
        return 2
    ts = args.ts if args.ts else None

    report = restore(snap_dir, ts, target=args.target, dry_run=args.dry_run)
    print(report)
    if report.get("error"):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
