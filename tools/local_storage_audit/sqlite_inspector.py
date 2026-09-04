from __future__ import annotations

import sqlite3
from pathlib import Path
from urllib.parse import quote

from .discovery import classify_scope
from .models import (
    ColumnInfo,
    DatabaseInfo,
    ForeignKeyInfo,
    IndexInfo,
    SchemaObject,
    TableInfo,
)


SQLITE_HEADER = b"SQLite format 3\x00"


def _quote_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _readonly_uri(path: Path) -> str:
    # URI mode=ro prevents accidental creation and write access.
    normalized = path.resolve().as_posix()
    return f"file:{quote(normalized, safe='/:')}?mode=ro"


def _read_header(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return handle.read(len(SQLITE_HEADER)) == SQLITE_HEADER
    except OSError:
        return False


def inspect_sqlite_database(
    path: Path,
    root: Path,
    *,
    include_row_counts: bool = True,
) -> DatabaseInfo:
    relative_path = str(path.relative_to(root)) if path.is_relative_to(root) else str(path)
    result = DatabaseInfo(
        path=str(path.resolve()),
        relative_path=relative_path.replace("\\", "/"),
        scope=classify_scope(path, root),
        size_bytes=path.stat().st_size if path.exists() else 0,
        sqlite_header=_read_header(path),
        readable=False,
    )

    if not result.sqlite_header:
        result.error = "File extension suggests SQLite, but the SQLite header was not detected."
        return result

    try:
        conn = sqlite3.connect(_readonly_uri(path), uri=True)
    except sqlite3.Error as exc:
        result.error = f"Unable to open database read-only: {exc}"
        return result

    try:
        conn.execute("PRAGMA query_only = ON")
        result.user_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        result.schema_version = int(conn.execute("PRAGMA schema_version").fetchone()[0])

        schema_rows = conn.execute(
            """
            SELECT type, name, tbl_name, sql
            FROM sqlite_master
            WHERE name NOT LIKE 'sqlite_%'
            ORDER BY type, name
            """
        ).fetchall()

        for object_type, name, table_name, sql in schema_rows:
            if object_type == "table":
                result.tables.append(
                    _inspect_table(
                        conn,
                        name,
                        sql,
                        include_row_counts=include_row_counts,
                    )
                )
            elif object_type == "view":
                result.views.append(
                    SchemaObject(
                        type="view",
                        name=name,
                        table_name=table_name,
                        sql=sql,
                    )
                )
            elif object_type == "trigger":
                result.triggers.append(
                    SchemaObject(
                        type="trigger",
                        name=name,
                        table_name=table_name,
                        sql=sql,
                    )
                )

        result.readable = True
        return result
    except sqlite3.Error as exc:
        result.error = f"SQLite inspection failed: {exc}"
        return result
    finally:
        conn.close()


def _inspect_table(
    conn: sqlite3.Connection,
    table_name: str,
    create_sql: str | None,
    *,
    include_row_counts: bool,
) -> TableInfo:
    quoted = _quote_identifier(table_name)
    table = TableInfo(name=table_name, sql=create_sql)

    for row in conn.execute(f"PRAGMA table_info({quoted})").fetchall():
        # cid, name, type, notnull, dflt_value, pk
        table.columns.append(
            ColumnInfo(
                name=row[1],
                declared_type=row[2] or None,
                not_null=bool(row[3]),
                default=None if row[4] is None else str(row[4]),
                primary_key_position=int(row[5]),
            )
        )

    for row in conn.execute(f"PRAGMA foreign_key_list({quoted})").fetchall():
        table.foreign_keys.append(
            ForeignKeyInfo(
                id=int(row[0]),
                seq=int(row[1]),
                target_table=row[2],
                from_column=row[3],
                to_column=row[4],
                on_update=row[5],
                on_delete=row[6],
            )
        )

    for row in conn.execute(f"PRAGMA index_list({quoted})").fetchall():
        # seq, name, unique, origin, partial
        index_name = row[1]
        index = IndexInfo(
            name=index_name,
            unique=bool(row[2]),
            origin=row[3] if len(row) > 3 else None,
            partial=bool(row[4]) if len(row) > 4 else False,
        )
        index_quoted = _quote_identifier(index_name)
        for info_row in conn.execute(f"PRAGMA index_info({index_quoted})").fetchall():
            if info_row[2] is not None:
                index.columns.append(info_row[2])
        table.indexes.append(index)

    if include_row_counts:
        try:
            table.row_count = int(
                conn.execute(f"SELECT COUNT(*) FROM {quoted}").fetchone()[0]
            )
        except sqlite3.Error as exc:
            table.row_count_error = str(exc)

    return table
