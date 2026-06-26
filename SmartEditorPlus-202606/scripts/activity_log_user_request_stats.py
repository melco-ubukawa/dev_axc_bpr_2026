#!/usr/bin/env python3

# 使い方例:
#   基本（1日）:
#     AZURE_STORAGE_CONNECTION_STRING='...' \
#       python3 scripts/activity_log_user_request_stats.py --date 2026-02-03
#
#   期間指定:
#     AZURE_STORAGE_CONNECTION_STRING='...' \
#       python3 scripts/activity_log_user_request_stats.py --start 2026-02-01 --end 2026-02-03
#
# 補足:
# - コンテナ名は AZURE_STORAGE_ACTIVITY_LOG_CONTAINER_NAME（未設定なら activity-logs）
# - 1 JSON blob = 1 リクエストとしてカウント（時刻基準は Blob last_modified のUTC）

import argparse
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
    from azure.storage.blob import BlobServiceClient
except Exception as e:  # pragma: no cover
    raise SystemExit(
        "azure-storage-blob is required. Install dependencies (see requirements.txt). "
        f"(error={e})"
    )

try:
    import config as _config
except Exception:  # pragma: no cover
    _config = None


AZURE_STORAGE_ACTIVITY_LOG_CONTAINER_NAME = (
    getattr(_config, "AZURE_STORAGE_ACTIVITY_LOG_CONTAINER_NAME", None)
    if _config is not None
    else None
) or os.environ.get("AZURE_STORAGE_ACTIVITY_LOG_CONTAINER_NAME", "activity-logs")


def get_blob_service_client() -> BlobServiceClient:
    conn = (
        getattr(_config, "AZURE_STORAGE_CONNECTION_STRING", None)
        if _config is not None
        else None
    ) or os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
    if not conn:
        raise SystemExit(
            "AZURE_STORAGE_CONNECTION_STRING is not set. "
            "Set it in your environment and retry."
        )
    return BlobServiceClient.from_connection_string(conn)


@dataclass(frozen=True)
class TargetBlob:
    blob_name: str
    last_modified_utc: datetime
    day_utc: date


def _normalize_prefix(prefix: str) -> str:
    prefix = (prefix or "").lstrip("/")
    if prefix and not prefix.endswith("/"):
        prefix += "/"
    return prefix


def _parse_datetime_or_date(s: str, *, is_end: bool) -> datetime:
    """Parse user input as either YYYY-MM-DD / YYYYMMDD or ISO datetime.

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
        dt = datetime.combine(d, time(23, 59, 59, 999999) if is_end else time(0, 0, 0))
        return dt.replace(tzinfo=timezone.utc)

    # Date-only (YYYY-MM-DD)
    if len(raw) == 10 and raw[4] == "-" and raw[7] == "-":
        try:
            d = datetime.strptime(raw, "%Y-%m-%d").date()
        except ValueError:
            raise argparse.ArgumentTypeError(
                f"Invalid date '{s}'. Expected YYYY-MM-DD (e.g. 2026-01-26)."
            )
        dt = datetime.combine(d, time(23, 59, 59, 999999) if is_end else time(0, 0, 0))
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


def _date_range_inclusive(start: date, end: date) -> list[date]:
    if end < start:
        return []
    days: list[date] = []
    cur = start
    while cur <= end:
        days.append(cur)
        cur += timedelta(days=1)
    return days


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


def _download_and_extract_username(container_client, blob_name: str) -> tuple[str | None, str | None]:
    try:
        blob_client = container_client.get_blob_client(blob_name)
        raw = blob_client.download_blob().readall()
        text = raw.decode("utf-8", errors="replace").strip()
        if not text:
            return None, None

        payload = json.loads(text)
        return _extract_username(payload), None

    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Aggregate unique users and request counts from activity logs stored in Azure Blob Storage. "
            "Each JSON blob is treated as one request. The time source is blob last_modified (UTC)."
        )
    )

    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument(
        "--date",
        help="Target date (YYYY-MM-DD or YYYYMMDD). Equivalent to --start start-of-day UTC and --end end-of-day UTC.",
    )
    g.add_argument(
        "--start",
        type=lambda s: _parse_datetime_or_date(s, is_end=False),
        help="Start datetime (ISO) or date (YYYY-MM-DD). Date means start-of-day UTC.",
    )

    p.add_argument(
        "--end",
        type=lambda s: _parse_datetime_or_date(s, is_end=True),
        default=None,
        help="End datetime (ISO) or date (YYYY-MM-DD). Required when using --start.",
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

    p.add_argument("--max-workers", type=int, default=10)
    p.add_argument(
        "--limit",
        type=int,
        default=0,
        help="0 = no limit. For debugging, stop after processing N blobs.",
    )
    p.add_argument(
        "--show-daily",
        action="store_true",
        help="Print per-day breakdown (UTC).",
    )
    p.add_argument(
        "--output-json",
        default="",
        help="If set, write JSON result to this path (UTF-8).",
    )

    args = p.parse_args()

    if args.date:
        start_dt = _parse_datetime_or_date(args.date, is_end=False)
        end_dt = _parse_datetime_or_date(args.date, is_end=True)
        args.start = start_dt
        args.end = end_dt
    else:
        if args.start is None or args.end is None:
            raise SystemExit("When using --start, you must also specify --end")

    if args.end < args.start:
        raise SystemExit("--end must be >= --start")

    return args


def main() -> int:
    args = parse_args()

    start_dt: datetime = args.start
    end_dt: datetime = args.end

    start_day = start_dt.date()
    end_day = end_dt.date()
    days = _date_range_inclusive(start_day, end_day)
    if not days:
        raise SystemExit("Invalid date range")

    prefix = _normalize_prefix(args.prefix)

    client = get_blob_service_client()
    if not client:
        raise SystemExit(
            "Azure Blob client is not available. Set AZURE_STORAGE_CONNECTION_STRING and retry."
        )

    container = str(args.container)
    container_client = client.get_container_client(container)

    # 1) Collect target blobs with cheap filters.
    targets: list[TargetBlob] = []

    blobs = container_client.list_blobs(name_starts_with=prefix)

    for b in blobs:
        name = getattr(b, "name", None) or ""
        if not name or not name.lower().endswith(".json"):
            continue

        day_hint = _extract_day_hint_from_blob_name(name, prefix)
        if day_hint and (day_hint < start_day or day_hint > end_day):
            continue

        lm = getattr(b, "last_modified", None)
        if not isinstance(lm, datetime):
            try:
                lm = container_client.get_blob_client(name).get_blob_properties().last_modified
            except Exception:
                continue

        if lm.tzinfo is None:
            lm = lm.replace(tzinfo=timezone.utc)
        lm_utc = lm.astimezone(timezone.utc)

        if not (start_dt <= lm_utc <= end_dt):
            continue

        day_utc = day_hint or lm_utc.date()
        targets.append(TargetBlob(blob_name=name, last_modified_utc=lm_utc, day_utc=day_utc))

        if args.limit and len(targets) >= int(args.limit):
            break

    # 2) Count requests per day (blobs = requests)
    requests_by_day: dict[date, int] = {d: 0 for d in days}
    for t in targets:
        if t.day_utc not in requests_by_day:
            # In case blob name/day_hint is outside (but last_modified is in-range), bucket by last_modified day.
            if start_day <= t.day_utc <= end_day:
                requests_by_day[t.day_utc] = 0
        requests_by_day[t.day_utc] = requests_by_day.get(t.day_utc, 0) + 1

    # 3) Download JSON and extract username in parallel.
    users_by_day: dict[date, set[str]] = {d: set() for d in days}
    users_period: set[str] = set()
    errors = 0

    with ThreadPoolExecutor(max_workers=max(1, int(args.max_workers))) as executor:
        futures = {
            executor.submit(_download_and_extract_username, container_client, t.blob_name): t
            for t in targets
        }

        for f in as_completed(futures):
            t = futures[f]
            uname, err = f.result()
            if err:
                errors += 1
                continue
            if not uname:
                continue

            users_period.add(uname)
            if t.day_utc not in users_by_day:
                users_by_day[t.day_utc] = set()
            users_by_day[t.day_utc].add(uname)

    daily_unique_counts = [len(users_by_day.get(d, set())) for d in days]
    daily_request_counts = [int(requests_by_day.get(d, 0)) for d in days]

    total_unique_period = len(users_period)
    avg_unique_per_day = sum(daily_unique_counts) / len(days)
    total_requests = sum(daily_request_counts)
    avg_requests_per_day = total_requests / len(days)

    summary = {
        "period": {
            "start_utc": start_dt.isoformat(),
            "end_utc": end_dt.isoformat(),
            "days": len(days),
        },
        "unique_users": {
            "total": total_unique_period,
            "avg_per_day": avg_unique_per_day,
        },
        "requests": {
            "total": total_requests,
            "avg_per_day": avg_requests_per_day,
        },
        "scanned": {
            "target_blobs": len(targets),
            "errors": errors,
            "container": container,
            "prefix": prefix,
        },
        "daily": [
            {
                "date": d.isoformat(),
                "requests": int(requests_by_day.get(d, 0)),
                "unique_users": len(users_by_day.get(d, set())),
            }
            for d in days
        ],
    }

    # Human-friendly output
    print(f"Period (UTC): {start_dt.isoformat()} .. {end_dt.isoformat()}  (days={len(days)})")
    print(
        "Unique users: "
        f"avg/day={avg_unique_per_day:.2f}, total(period)={total_unique_period}"
    )
    print(f"Requests:     avg/day={avg_requests_per_day:.2f}, total(period)={total_requests}")
    print(
        f"Scanned: target_blobs={len(targets)}, parse_errors={errors}, container={container}, prefix={prefix or '(empty)'}"
    )

    if args.show_daily:
        print("\nDaily breakdown (UTC):")
        print("date,requests,unique_users")
        for d in days:
            print(
                f"{d.isoformat()},{int(requests_by_day.get(d, 0))},{len(users_by_day.get(d, set()))}"
            )

    if args.output_json:
        out_path = Path(str(args.output_json))
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
