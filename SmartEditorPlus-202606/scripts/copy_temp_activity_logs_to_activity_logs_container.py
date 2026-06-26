#!/usr/bin/env python3

import argparse
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from azure.core.exceptions import ResourceNotFoundError, HttpResponseError
from azure.storage.blob import BlobSasPermissions, generate_blob_sas


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


try:
    from smarteditorplus.utils.storage import (
        AZURE_STORAGE_ACTIVITY_LOG_CONTAINER_NAME,
        AZURE_STORAGE_CONTAINER_NAME,
        get_blob_service_client,
    )
except Exception as e:  # pragma: no cover
    raise SystemExit(
        "Failed to import storage helpers. Run from repo root and ensure dependencies are installed. "
        f"(error={e})"
    )


@dataclass(frozen=True)
class CopyStats:
    scanned: int
    skipped_existing: int
    copied_started: int
    copied_completed: int
    failed: int


def _normalize_prefix(prefix: str) -> str:
    prefix = (prefix or "").lstrip("/")
    if prefix and not prefix.endswith("/"):
        prefix += "/"
    return prefix


def _ensure_container_exists(client, container_name: str) -> None:
    if not container_name:
        return
    container_client = client.get_container_client(container_name)
    try:
        if not container_client.exists():
            container_client.create_container()
    except HttpResponseError:
        # 権限が無い/既に存在など、環境によって揺れるので best-effort。
        pass


def _get_account_key_or_die(client) -> str:
    cred = getattr(client, "credential", None)
    account_key = getattr(cred, "account_key", None)
    if not account_key:
        raise SystemExit(
            "Account key not available. Ensure AZURE_STORAGE_CONNECTION_STRING is set and contains AccountKey."
        )
    return str(account_key)


def _build_source_sas_url(*, account_name: str, account_key: str, container: str, blob: str, expiry_minutes: int) -> str:
    sas = generate_blob_sas(
        account_name=account_name,
        container_name=container,
        blob_name=blob,
        account_key=account_key,
        permission=BlobSasPermissions(read=True),
        expiry=datetime.now(timezone.utc) + timedelta(minutes=expiry_minutes),
    )
    return f"https://{account_name}.blob.core.windows.net/{container}/{blob}?{sas}"


def _dest_blob_name(source_blob_name: str, *, source_prefix: str, strip_prefix: bool) -> str:
    if not strip_prefix:
        return source_blob_name

    p = _normalize_prefix(source_prefix)
    if p and source_blob_name.startswith(p):
        return source_blob_name[len(p) :]
    return source_blob_name


def _blob_exists(blob_client) -> bool:
    try:
        blob_client.get_blob_properties()
        return True
    except ResourceNotFoundError:
        return False


def _wait_for_copy(dest_blob_client, *, max_wait_seconds: int, poll_interval_seconds: float) -> bool:
    deadline = time.time() + max_wait_seconds
    while True:
        props = dest_blob_client.get_blob_properties()
        copy = getattr(props, "copy", None)
        status = getattr(copy, "status", None)
        if status in ("success", "aborted", "failed"):
            return status == "success"

        if time.time() >= deadline:
            return False

        time.sleep(poll_interval_seconds)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Copy blobs from {tempstorage}/activity-logs/* to {activity-logs} container. "
            "If destination already exists, skip."
        )
    )

    p.add_argument(
        "--source-container",
        default=AZURE_STORAGE_CONTAINER_NAME,
        help=f"Source container (default: {AZURE_STORAGE_CONTAINER_NAME})",
    )
    p.add_argument(
        "--source-prefix",
        default="activity-logs/",
        help="Prefix under source container to copy (default: activity-logs/)",
    )
    p.add_argument(
        "--dest-container",
        default=AZURE_STORAGE_ACTIVITY_LOG_CONTAINER_NAME,
        help=f"Destination container (default: {AZURE_STORAGE_ACTIVITY_LOG_CONTAINER_NAME})",
    )

    p.add_argument(
        "--keep-prefix",
        action="store_true",
        help="Keep source prefix in destination blob name (default: strip prefix).",
    )

    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Only print what would be copied; do not copy.",
    )
    p.add_argument(
        "--max-items",
        type=int,
        default=0,
        help="0 = no limit. For debugging, stop after scanning N blobs.",
    )

    p.add_argument(
        "--sas-expiry-minutes",
        type=int,
        default=120,
        help="SAS expiry minutes for source read (default: 120).",
    )

    p.add_argument(
        "--wait",
        action="store_true",
        help="Wait for each server-side copy to finish (default: false).",
    )
    p.add_argument(
        "--max-wait-seconds",
        type=int,
        default=1800,
        help="Max wait per blob when --wait is set (default: 1800).",
    )
    p.add_argument(
        "--poll-interval-seconds",
        type=float,
        default=0.5,
        help="Polling interval for copy status when --wait is set (default: 0.5).",
    )

    return p.parse_args()


def main() -> int:
    args = parse_args()

    client = get_blob_service_client()
    if not client:
        raise SystemExit("Azure Blob Service Client could not be initialized. Set AZURE_STORAGE_CONNECTION_STRING.")

    source_container = args.source_container
    dest_container = args.dest_container
    source_prefix = _normalize_prefix(args.source_prefix)
    strip_prefix = not args.keep_prefix

    _ensure_container_exists(client, source_container)
    _ensure_container_exists(client, dest_container)

    account_name = client.account_name
    account_key = _get_account_key_or_die(client)

    src_container_client = client.get_container_client(source_container)

    scanned = 0
    skipped_existing = 0
    copied_started = 0
    copied_completed = 0
    failed = 0

    try:
        blobs_iter = src_container_client.list_blobs(name_starts_with=source_prefix or None)
        for blob in blobs_iter:
            scanned += 1
            src_blob_name = blob.name
            dst_blob_name = _dest_blob_name(src_blob_name, source_prefix=source_prefix, strip_prefix=strip_prefix)

            dst_blob_client = client.get_blob_client(container=dest_container, blob=dst_blob_name)
            if _blob_exists(dst_blob_client):
                skipped_existing += 1
                if scanned % 200 == 0:
                    print(f"scanned={scanned} skipped={skipped_existing} copied={copied_started} failed={failed}")
                if args.max_items and scanned >= args.max_items:
                    break
                continue

            if args.dry_run:
                print(f"COPY {source_container}/{src_blob_name} -> {dest_container}/{dst_blob_name}")
                copied_started += 1
                if args.max_items and scanned >= args.max_items:
                    break
                continue

            try:
                source_url = _build_source_sas_url(
                    account_name=account_name,
                    account_key=account_key,
                    container=source_container,
                    blob=src_blob_name,
                    expiry_minutes=args.sas_expiry_minutes,
                )

                dst_blob_client.start_copy_from_url(source_url)
                copied_started += 1

                if args.wait:
                    ok = _wait_for_copy(
                        dst_blob_client,
                        max_wait_seconds=args.max_wait_seconds,
                        poll_interval_seconds=args.poll_interval_seconds,
                    )
                    if ok:
                        copied_completed += 1
                    else:
                        failed += 1
                        print(f"COPY TIMEOUT/FAIL {dest_container}/{dst_blob_name}")

            except Exception as e:
                failed += 1
                print(f"COPY FAILED {source_container}/{src_blob_name} -> {dest_container}/{dst_blob_name}: {type(e).__name__}: {e}")

            if scanned % 200 == 0:
                print(f"scanned={scanned} skipped={skipped_existing} copied={copied_started} failed={failed}")

            if args.max_items and scanned >= args.max_items:
                break

    except Exception as e:
        print(f"ERROR: {type(e).__name__}: {e}")
        return 2

    stats = CopyStats(
        scanned=scanned,
        skipped_existing=skipped_existing,
        copied_started=copied_started,
        copied_completed=copied_completed,
        failed=failed,
    )

    print(
        "DONE "
        f"scanned={stats.scanned} skipped_existing={stats.skipped_existing} "
        f"copied_started={stats.copied_started} copied_completed={stats.copied_completed} failed={stats.failed}"
    )

    return 0 if stats.failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
