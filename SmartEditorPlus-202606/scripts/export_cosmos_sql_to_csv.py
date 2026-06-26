#!/usr/bin/env python3
"""Cosmos DB: SQL実行結果を全件CSVにエクスポートするユーティリティ。

使い方例:
    # 1) スクリプト内の DEFAULT_SQL を使う (SQL引数なし)
    python scripts/export_cosmos_sql_to_csv.py \
        --database ChatHistoryDB --container Sessions \
        -o out.csv

    # 2) 実行時にSQLを上書き
    python scripts/export_cosmos_sql_to_csv.py \
        --database ChatHistoryDB --container Sessions \
        --sql "SELECT * FROM c WHERE c.userId = 'xxx'" \
        -o out.csv

接続情報は引数優先。未指定の場合は環境変数を参照します:
  COSMOS_ENDPOINT, COSMOS_KEY, COSMOS_DATABASE_NAME, COSMOS_CONTAINER_NAME

注意:
- クロスパーティションクエリを有効化しています。
- ネストした値(dict/list)はJSON文字列としてCSVに書き出します。
- 大量件数でもメモリに載せないため、内部的にNDJSON一時ファイルを経由します。
"""

from __future__ import annotations

import argparse
import base64
import csv
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from dotenv import load_dotenv

try:
    from azure.cosmos import CosmosClient
except Exception as e:  # pragma: no cover
    raise SystemExit(
        "azure-cosmos が import できません。`pip install -r requirements.txt` を実行してください。\n"
        f"詳細: {e}"
    )


# スクリプト内で既定のSQLを定義できます。
# 例: 特定ユーザーのみ、期間指定などにカスタマイズしてください。
DEFAULT_SQL = "SELECT DISTINCT LEFT(c.timestamp, 10) AS DateLabel, c.userName FROM c WHERE IS_DEFINED(c.timestamp)"


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Cosmos DBに接続してSQLを実行し、全件をCSVへ出力します。",
    )

    parser.add_argument("--endpoint", default=os.environ.get("COSMOS_ENDPOINT"))
    parser.add_argument("--key", default=os.environ.get("COSMOS_KEY"))
    parser.add_argument(
        "--database",
        default=os.environ.get("COSMOS_DATABASE_NAME"),
        help="Cosmos DB database id (未指定時はCOSMOS_DATABASE_NAME)",
    )
    parser.add_argument(
        "--container",
        default=os.environ.get("COSMOS_CONTAINER_NAME"),
        help="Cosmos DB container id (未指定時はCOSMOS_CONTAINER_NAME)",
    )

    sql_group = parser.add_mutually_exclusive_group(required=False)
    sql_group.add_argument(
        "--sql",
        "-q",
        help="実行するCosmos SQL。未指定の場合はスクリプト内の DEFAULT_SQL を使用",
    )
    sql_group.add_argument(
        "--sql-file",
        help="SQLを読み込むファイルパス。未指定の場合はスクリプト内の DEFAULT_SQL を使用",
    )

    parser.add_argument(
        "--output",
        "-o",
        help="出力CSVパス。未指定の場合は ./cosmos_export_YYYYmmdd_HHMMSS.csv",
    )
    parser.add_argument(
        "--encoding",
        default="utf-8-sig",
        help="CSVのエンコーディング (既定: utf-8-sig / Excel互換)",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=1000,
        help="Cosmos query_items の max_item_count (既定: 1000)",
    )
    parser.add_argument(
        "--max-items",
        type=int,
        default=0,
        help="デバッグ用: 最大取得件数。0は無制限 (既定: 0)",
    )
    parser.add_argument(
        "--ndjson",
        help="中間NDJSONの出力パス。未指定の場合は一時ファイルを使用",
    )

    return parser.parse_args(argv)


def _read_sql(args: argparse.Namespace) -> str:
    if args.sql:
        return args.sql.strip()
    if args.sql_file:
        sql_path = Path(args.sql_file)
        return sql_path.read_text(encoding="utf-8").strip()
    return DEFAULT_SQL.strip()


def _validate_required(name: str, value: Optional[str]) -> str:
    if not value:
        raise SystemExit(f"必須パラメータが未設定です: {name}")
    return value


def _safe_cell(value: Any) -> str:
    """CSVセルとして安全に文字列化する。"""
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    if isinstance(value, (bytes, bytearray)):
        return base64.b64encode(bytes(value)).decode("ascii")
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _iter_query_items(
    *,
    endpoint: str,
    key: str,
    database_name: str,
    container_name: str,
    sql: str,
    page_size: int,
) -> Iterable[Dict[str, Any]]:
    # 既存コードと同様の呼び出し方に合わせる
    client = CosmosClient(endpoint, key)
    database = client.get_database_client(database_name)
    container = database.get_container_client(container_name)

    iterator = container.query_items(
        query=sql,
        enable_cross_partition_query=True,
        max_item_count=page_size,
    )
    for item in iterator:
        if isinstance(item, dict):
            yield item
        else:
            yield {"value": item}


def _choose_columns(all_keys: Set[str]) -> List[str]:
    keys = set(all_keys)
    ordered: List[str] = []
    for preferred in ["id", "userId", "task_id", "project_id", "created_at", "timestamp", "_ts"]:
        if preferred in keys:
            ordered.append(preferred)
            keys.remove(preferred)
    ordered.extend(sorted(keys))
    return ordered


def export_to_csv(
    *,
    endpoint: str,
    key: str,
    database_name: str,
    container_name: str,
    sql: str,
    output_csv: Path,
    encoding: str,
    page_size: int,
    max_items: int,
    ndjson_path: Optional[Path],
) -> Tuple[int, Path]:
    all_keys: Set[str] = set()
    count = 0

    created_temp_ndjson = False

    if ndjson_path is None:
        tmp = tempfile.NamedTemporaryFile(prefix="cosmos_export_", suffix=".ndjson", delete=False)
        ndjson_path = Path(tmp.name)
        tmp.close()
        created_temp_ndjson = True

    try:
        with ndjson_path.open("w", encoding="utf-8") as f:
            for item in _iter_query_items(
                endpoint=endpoint,
                key=key,
                database_name=database_name,
                container_name=container_name,
                sql=sql,
                page_size=page_size,
            ):
                all_keys.update(item.keys())
                f.write(json.dumps(item, ensure_ascii=False))
                f.write("\n")

                count += 1
                if max_items and count >= max_items:
                    break

        columns = _choose_columns(all_keys)
        output_csv.parent.mkdir(parents=True, exist_ok=True)

        with output_csv.open("w", encoding=encoding, newline="") as out_f:
            writer = csv.DictWriter(out_f, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()

            with ndjson_path.open("r", encoding="utf-8") as in_f:
                for line in in_f:
                    if not line.strip():
                        continue
                    obj = json.loads(line)
                    row = {k: _safe_cell(obj.get(k)) for k in columns}
                    writer.writerow(row)

        return count, output_csv

    finally:
        if created_temp_ndjson:
            try:
                ndjson_path.unlink(missing_ok=True)
            except Exception:
                pass


def main(argv: Optional[List[str]] = None) -> int:
    load_dotenv()
    args = _parse_args(argv)

    endpoint = _validate_required("--endpoint / COSMOS_ENDPOINT", args.endpoint)
    key = _validate_required("--key / COSMOS_KEY", args.key)
    database_name = _validate_required("--database / COSMOS_DATABASE_NAME", args.database)
    container_name = _validate_required("--container / COSMOS_CONTAINER_NAME", args.container)

    sql = _read_sql(args)
    if not sql:
        raise SystemExit("SQLが空です")

    if args.output:
        output_csv = Path(args.output)
    else:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_csv = Path(f"cosmos_export_{ts}.csv")

    ndjson_path = Path(args.ndjson) if args.ndjson else None

    rows, out_path = export_to_csv(
        endpoint=endpoint,
        key=key,
        database_name=database_name,
        container_name=container_name,
        sql=sql,
        output_csv=output_csv,
        encoding=args.encoding,
        page_size=args.page_size,
        max_items=args.max_items,
        ndjson_path=ndjson_path,
    )

    print(f"Exported {rows} rows -> {out_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
