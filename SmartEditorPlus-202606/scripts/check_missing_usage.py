#!/usr/bin/env python3

import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


try:
    from smarteditorplus.utils.storage import (
        AZURE_STORAGE_ACTIVITY_LOG_CONTAINER_NAME,
        get_blob_service_client,
    )
except Exception as e:  # pragma: no cover
    raise SystemExit(
        "Failed to import storage helpers. Run from repo root and ensure dependencies are installed. "
        f"(error={e})"
    )


@dataclass(frozen=True)
class BlobMeta:
    blob_name: str
    container: str
    category: str | None
    day: date | None

    @property
    def filename(self) -> str:
        return self.blob_name.rsplit("/", 1)[-1]

    @property
    def storage_path(self) -> str:
        return f"{self.container}/{self.blob_name}"


def _parse_yyyy_mm_dd_arg(s: str) -> date:
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"Invalid date '{s}'. Expected format: YYYY-MM-DD (e.g. 2026-01-06)."
        )


def _date_range_inclusive(start: date, end: date) -> Iterable[date]:
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def _normalize_prefix(prefix: str) -> str:
    prefix = (prefix or "").lstrip("/")
    if prefix and not prefix.endswith("/"):
        prefix += "/"
    return prefix


def _extract_meta_from_path(blob_name: str, prefix: str) -> BlobMeta:
    """Best-effort parse for activity logs.

    Expected layout:
      {prefix}{category}/{YYYY}/{MM}/{DD}/.../{id}.json

    In this repo, prefix is usually "activity-logs/" and the default container is from
    AZURE_STORAGE_ACTIVITY_LOG_CONTAINER_NAME.
    """
    # Example: chat/2025/11/26/10/xxxx.json
    rel = blob_name
    if prefix and rel.startswith(prefix):
        rel = rel[len(prefix) :]

    parts = [p for p in rel.split("/") if p]

    category: str | None = None
    day: date | None = None

    if len(parts) >= 4:
        category = parts[0]
        yyyy, mm, dd = parts[1], parts[2], parts[3]
        if len(yyyy) == 4 and len(mm) == 2 and len(dd) == 2:
            try:
                day = datetime.strptime(f"{yyyy}-{mm}-{dd}", "%Y-%m-%d").date()
            except ValueError:
                day = None

    # container is injected later
    return BlobMeta(blob_name=blob_name, container="", category=category, day=day)


def _iter_dict_like(obj: Any) -> Iterable[dict[str, Any]]:
    """Yield dicts found in obj (dict / list nesting)."""
    queue: list[Any] = [obj]
    visited = 0
    max_nodes = 5000

    while queue and visited < max_nodes:
        cur = queue.pop(0)
        visited += 1

        if isinstance(cur, dict):
            yield cur
            for v in cur.values():
                if isinstance(v, (dict, list)):
                    queue.append(v)
        elif isinstance(cur, list):
            for v in cur:
                if isinstance(v, (dict, list)):
                    queue.append(v)


def _has_usage(payload: Any, missing_if_zero: bool) -> bool:
    """Return True if payload contains usable token usage."""
    for d in _iter_dict_like(payload):
        if "usage" not in d:
            continue
        usage = d.get("usage")
        if not isinstance(usage, dict) or not usage:
            continue

        if not missing_if_zero:
            return True

        # Treat as present only if some count is > 0
        for k in ("prompt_tokens", "completion_tokens", "total_tokens", "cached_tokens", "input_tokens", "output_tokens"):
            v = usage.get(k)
            try:
                if v is not None and int(v) > 0:
                    return True
            except Exception:
                continue

        # If usage exists but all expected fields are absent, still treat as present.
        return True

    return False


def _download_and_check_missing(container_client, container: str, blob_name: str, prefix: str, missing_if_zero: bool) -> tuple[BlobMeta, bool, str | None]:
    """Returns (meta, is_missing, error_message)."""
    meta0 = _extract_meta_from_path(blob_name, prefix)
    meta = BlobMeta(blob_name=meta0.blob_name, container=container, category=meta0.category, day=meta0.day)

    try:
        blob_client = container_client.get_blob_client(blob_name)
        raw = blob_client.download_blob().readall()
        text = raw.decode("utf-8", errors="replace").strip()

        if not text:
            return meta, True, None

        payload = json.loads(text)
        has_usage = _has_usage(payload, missing_if_zero=missing_if_zero)
        return meta, (not has_usage), None

    except Exception as e:
        # 解析不能は「欠落」と扱うと運用上見つけやすいので、欠落扱い。
        return meta, True, f"{type(e).__name__}: {e}"


def _group_path(container: str, prefix: str, meta: BlobMeta, group_by: str) -> str:
    base = f"{container}/{prefix.rstrip('/')}" if prefix else container

    if group_by == "category":
        # 要件の「コンテナ/Blobパスごと」に合わせ、カテゴリ配下の日付フォルダ単位で集計する
        # 例: tempstorage/activity-logs/chat/2025/12/25
        category = meta.category or "unknown"
        if not meta.day:
            return f"{base}/{category}/unknown-date"
        return f"{base}/{category}/{meta.day.strftime('%Y/%m/%d')}"

    if group_by == "date":
        if not meta.day:
            return f"{base}/unknown-date"
        return f"{base}/{meta.day.strftime('%Y/%m/%d')}"

    # blob-path: category + YYYY/MM/DD (day-level)
    if not meta.day:
        d = "unknown-date"
    else:
        d = meta.day.strftime("%Y/%m/%d")

    category = meta.category or "unknown"
    return f"{base}/{category}/{d}"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Check activity logs in Azure Blob Storage and report logs with missing usage.",
    )
    p.add_argument("--start-date", required=True, type=_parse_yyyy_mm_dd_arg, help="YYYY-MM-DD")
    p.add_argument("--end-date", required=True, type=_parse_yyyy_mm_dd_arg, help="YYYY-MM-DD")

    p.add_argument(
        "--container",
        default=AZURE_STORAGE_ACTIVITY_LOG_CONTAINER_NAME,
        help="Azure Blob container name (default from AZURE_STORAGE_ACTIVITY_LOG_CONTAINER_NAME).",
    )
    p.add_argument(
        "--prefix",
        default="",
        help="Blob prefix to scan (default: empty; logs are stored as {category}/YYYY/MM/DD/...).",
    )

    p.add_argument(
        "--group-by",
        choices=["category", "date", "blob-path"],
        required=True,
        help="Grouping dimension.",
    )
    p.add_argument(
        "--sort-groups-by",
        choices=["ratio", "total", "missing"],
        required=True,
        help="Sort key for group_summary.",
    )
    p.add_argument("--sort-desc", action="store_true", help="Sort descending")

    p.add_argument("--max-workers", type=int, default=10)
    p.add_argument("--missing-if-zero", action="store_true", help="Treat usage=0 as missing")
    p.add_argument("--limit-groups", type=int, default=0, help="0 = no limit")
    p.add_argument("--limit-missing-files", type=int, default=0, help="0 = no limit")

    p.add_argument(
        "--output",
        default="",
        help="Write JSON output to this file path (UTF-8). If omitted, prints to stdout.",
    )

    return p.parse_args()


def main() -> int:
    args = parse_args()

    start: date = args.start_date
    end: date = args.end_date
    if end < start:
        raise SystemExit("end-date must be >= start-date")

    prefix = _normalize_prefix(args.prefix)
    client = get_blob_service_client()
    if not client:
        raise SystemExit(
            "Azure Blob client is not available. Set AZURE_STORAGE_CONNECTION_STRING and retry."
        )

    container = str(args.container)
    container_client = client.get_container_client(container)

    # List candidate blobs
    # Optimization: precompute target date strings and filter by blob name.
    target_date_paths = {d.strftime("%Y/%m/%d") for d in _date_range_inclusive(start, end)}

    blobs = container_client.list_blobs(name_starts_with=prefix)

    candidates: list[str] = []
    for b in blobs:
        name = getattr(b, "name", "")
        if not name:
            continue
        # Only JSON-ish logs; skip folder placeholders
        if name.endswith("/"):
            continue
        if not name.lower().endswith(".json"):
            continue
        if any(dp in name for dp in target_date_paths):
            candidates.append(name)

    group_stats: dict[str, dict[str, float]] = {}
    missing_files: list[dict[str, Any]] = []

    total_logs = 0
    missing_logs = 0

    errors: list[dict[str, Any]] = []

    with ThreadPoolExecutor(max_workers=max(1, int(args.max_workers))) as ex:
        futures = [
            ex.submit(
                _download_and_check_missing,
                container_client,
                container,
                blob_name,
                prefix,
                args.missing_if_zero,
            )
            for blob_name in candidates
        ]

        for fut in as_completed(futures):
            meta, is_missing, err = fut.result()
            total_logs += 1

            gpath = _group_path(container, prefix, meta, args.group_by)
            gs = group_stats.setdefault(
                gpath,
                {"total_logs": 0, "missing_logs": 0},
            )
            gs["total_logs"] += 1

            if is_missing:
                missing_logs += 1
                gs["missing_logs"] += 1

                if args.limit_missing_files <= 0 or len(missing_files) < args.limit_missing_files:
                    missing_files.append(
                        {
                            "filename": meta.filename,
                            "blob_name": meta.blob_name,
                            "storage_path": meta.storage_path,
                            "container": meta.container,
                            "category": meta.category,
                            "date": meta.day.isoformat() if meta.day else None,
                            "group_path": gpath,
                        }
                    )

            if err:
                errors.append({"blob_name": meta.blob_name, "error": err})

    group_summary: list[dict[str, Any]] = []
    for path, s in group_stats.items():
        t = int(s["total_logs"])
        m = int(s["missing_logs"])
        ratio = (m / t) if t else 0.0
        group_summary.append(
            {
                "path": path,
                "total_logs": t,
                "missing_logs": m,
                "missing_ratio": round(ratio, 6),
            }
        )

    def _sort_key(x: dict[str, Any]):
        if args.sort_groups_by == "ratio":
            return x["missing_ratio"]
        if args.sort_groups_by == "missing":
            return x["missing_logs"]
        return x["total_logs"]

    group_summary.sort(key=_sort_key, reverse=bool(args.sort_desc))
    if args.limit_groups and args.limit_groups > 0:
        group_summary = group_summary[: int(args.limit_groups)]

    out = {
        "params": {
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "container": container,
            "prefix": prefix,
            "group_by": args.group_by,
            "sort_groups_by": args.sort_groups_by,
            "sort_desc": bool(args.sort_desc),
            "missing_if_zero": bool(args.missing_if_zero),
        },
        "overall": {
            "total_logs": total_logs,
            "missing_logs": missing_logs,
            "missing_ratio": round((missing_logs / total_logs) if total_logs else 0.0, 6),
        },
        "group_summary": group_summary,
        "missing_files": missing_files,
    }

    if errors:
        out["errors"] = errors

    output_json = json.dumps(out, ensure_ascii=False, indent=2)
    if args.output:
        out_path = Path(str(args.output)).expanduser()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(output_json + "\n", encoding="utf-8")
    else:
        print(output_json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
