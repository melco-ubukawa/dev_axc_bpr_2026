#!/usr/bin/env python3

import argparse
import csv
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
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
class ExtractedRow:
    timestamp: str
    username: str


def _normalize_prefix(prefix: str) -> str:
    prefix = (prefix or "").lstrip("/")
    if prefix and not prefix.endswith("/"):
        prefix += "/"
    return prefix


def _parse_datetime_or_date(s: str, *, is_end: bool) -> datetime:
    """Parse user input as either YYYY-MM-DD or ISO datetime.

    - If date-only, returns start-of-day (is_end=False) or end-of-day (is_end=True) in UTC.
    - If timezone is missing, assumes UTC.
    """
    raw = (s or "").strip()
    if not raw:
        raise argparse.ArgumentTypeError("Empty datetime")

    # Date-only (YYYYMMDD)
    if len(raw) == 8 and raw.isdigit():
        try:
            d = datetime.strptime(raw, "%Y%m%d").date()
        except ValueError:
            raise argparse.ArgumentTypeError(
                f"Invalid date '{s}'. Expected YYYYMMDD (e.g. 20260126)."
            )
        if is_end:
            dt = datetime.combine(d, time(23, 59, 59, 999999))
        else:
            dt = datetime.combine(d, time(0, 0, 0))
        return dt.replace(tzinfo=timezone.utc)

    # Date-only
    if len(raw) == 10 and raw[4] == "-" and raw[7] == "-":
        try:
            d = datetime.strptime(raw, "%Y-%m-%d").date()
        except ValueError:
            raise argparse.ArgumentTypeError(
                f"Invalid date '{s}'. Expected YYYY-MM-DD (e.g. 2026-01-26)."
            )
        if is_end:
            dt = datetime.combine(d, time(23, 59, 59, 999999))
        else:
            dt = datetime.combine(d, time(0, 0, 0))
        return dt.replace(tzinfo=timezone.utc)

    # ISO datetime (allow trailing Z)
    try:
        iso = raw.replace("Z", "+00:00")
        dt = datetime.fromisoformat(iso)
    except Exception:
        raise argparse.ArgumentTypeError(
            f"Invalid datetime '{s}'. Expected ISO format (e.g. 2026-01-26T12:34:56+00:00) or YYYY-MM-DD."
        )

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt.astimezone(timezone.utc)


def _iter_dict_like(obj: Any) -> Iterable[dict[str, Any]]:
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


def _extract_username(payload: Any) -> str | None:
    """Best-effort username extraction from known log shapes."""
    if isinstance(payload, dict):
        uname = payload.get("username") or payload.get("userName")
        if uname is None:
            uname = payload.get("userEmail") or payload.get("userId")
        if isinstance(uname, str) and uname:
            return uname

    for d in _iter_dict_like(payload):
        v = d.get("username")
        if isinstance(v, str) and v:
            return v

        v2 = d.get("userName")
        if isinstance(v2, str) and v2:
            return v2

        v3 = d.get("userEmail")
        if isinstance(v3, str) and v3:
            return v3

        v4 = d.get("userId")
        if isinstance(v4, str) and v4:
            return v4

    return None


def _extract_day_hint_from_blob_name(blob_name: str, prefix: str) -> date | None:
    """Extract YYYY/MM/DD from blob path if it matches expected activity-logs layout."""
    rel = blob_name
    if prefix and rel.startswith(prefix):
        rel = rel[len(prefix) :]

    parts = [p for p in rel.split("/") if p]
    # Expected: {category}/{YYYY}/{MM}/{DD}/{HH}/...json
    if len(parts) < 4:
        return None

    yyyy, mm, dd = parts[1], parts[2], parts[3]
    if len(yyyy) == 4 and len(mm) == 2 and len(dd) == 2:
        try:
            return datetime.strptime(f"{yyyy}-{mm}-{dd}", "%Y-%m-%d").date()
        except ValueError:
            return None
    return None


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Export activity log last_modified(timestamp) and username from Azure Blob Storage to CSV. "
            "Scans activity logs under the activity-log container (AZURE_STORAGE_ACTIVITY_LOG_CONTAINER_NAME) by default."
        )
    )

    p.add_argument(
        "--start",
        required=True,
        type=lambda s: _parse_datetime_or_date(s, is_end=False),
        help="Start datetime (ISO) or date (YYYY-MM-DD). Date means start-of-day UTC.",
    )
    p.add_argument(
        "--end",
        required=True,
        type=lambda s: _parse_datetime_or_date(s, is_end=True),
        help="End datetime (ISO) or date (YYYY-MM-DD). Date means end-of-day UTC.",
    )

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
        "--output",
        default="activity_logs_export.csv",
        help="Output CSV path (default: activity_logs_export.csv).",
    )

    p.add_argument("--max-workers", type=int, default=10)
    p.add_argument(
        "--encoding",
        default="utf-8-sig",
        help="CSV encoding (default: utf-8-sig for Excel).",
    )

    p.add_argument(
        "--limit",
        type=int,
        default=0,
        help="0 = no limit. For debugging, stop after exporting N rows.",
    )

    return p.parse_args()


def _download_and_extract(container_client, blob_name: str, last_modified_utc: datetime) -> tuple[ExtractedRow | None, str | None]:
    try:
        blob_client = container_client.get_blob_client(blob_name)
        raw = blob_client.download_blob().readall()
        text = raw.decode("utf-8", errors="replace").strip()
        if not text:
            return None, None

        payload = json.loads(text)
        uname = _extract_username(payload)
        if not uname:
            return None, None

        ts = last_modified_utc.astimezone(timezone.utc).isoformat()
        return ExtractedRow(timestamp=ts, username=uname), None

    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def main() -> int:
    args = parse_args()

    start_dt: datetime = args.start
    end_dt: datetime = args.end

    if end_dt < start_dt:
        raise SystemExit("--end must be >= --start")

    prefix = _normalize_prefix(args.prefix)

    client = get_blob_service_client()
    if not client:
        raise SystemExit(
            "Azure Blob client is not available. Set AZURE_STORAGE_CONNECTION_STRING and retry."
        )

    container = str(args.container)
    container_client = client.get_container_client(container)

    blobs = container_client.list_blobs(name_starts_with=prefix)

    target_blobs: list[tuple[str, datetime]] = []
    for b in blobs:
        name = getattr(b, "name", None) or ""
        if not name:
            continue
        if not name.lower().endswith(".json"):
            continue
        lm = getattr(b, "last_modified", None)
        if not isinstance(lm, datetime):
            # Fallback: fetch properties (slower, but avoids silently missing data).
            try:
                lm = container_client.get_blob_client(name).get_blob_properties().last_modified
            except Exception:
                continue
        if lm.tzinfo is None:
            lm = lm.replace(tzinfo=timezone.utc)
        lm_utc = lm.astimezone(timezone.utc)
        # Filter by Blob last_modified (timestamp source)
        if not (start_dt <= lm_utc <= end_dt):
            continue
        target_blobs.append((name, lm_utc))

    # Download + parse in parallel
    rows: list[ExtractedRow] = []
    errors = 0

    with ThreadPoolExecutor(max_workers=max(1, int(args.max_workers))) as executor:
        futures = [
            executor.submit(_download_and_extract, container_client, blob_name, last_modified_utc)
            for (blob_name, last_modified_utc) in target_blobs
        ]

        for f in as_completed(futures):
            row, err = f.result()
            if err:
                errors += 1
                continue
            if not row:
                continue

            rows.append(row)
            if args.limit and len(rows) >= int(args.limit):
                break

    # Sort for readability
    rows.sort(key=lambda r: r.timestamp)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with out_path.open("w", encoding=str(args.encoding), newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "username"])
        for r in rows:
            writer.writerow([r.timestamp, r.username])

    print(
        f"Exported {len(rows)} rows to {out_path} (container={container}, prefix={prefix}, "
        f"period={start_dt.isoformat()}..{end_dt.isoformat()}, scanned_blobs={len(target_blobs)}, errors={errors})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
