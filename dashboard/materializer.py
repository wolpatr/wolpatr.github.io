#!/usr/bin/env python3
"""Materializer (spec §5.3, TDD seam 4): flatten the news JSONL archive — which
Grafana cannot read directly — into a small summary SQLite that ordinary SQL
dashboards can query.

Input layout (produced by the data_agent news module, read-only):
    <news_root>/<source>/<YYYY-MM-DD>.jsonl
      one line per fetch: {"fetch_ts": "...", "payload": {...}}
    - RSS shape:   payload.feed_url + payload.xml (a full RSS document string)
    - list shapes: payload.items / payload.articles / payload.entries / payload.data
    - anything else: counted as a fetch with 0 items

Output (default: dashboard/state/news_summary.sqlite, written locally):
    news_summary(day, source, feed, fetches, items)
    materializer_runs(run_utc, files, lines, sources)

Run:
    uv run dashboard/materializer.py [--src ...] [--out ...] [--since YYYY-MM-DD]

Stdlib only, so it runs with plain python too. The archive is opened read-only;
the output DB is separate from all evidence stores.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sqlite3
import urllib.parse
from pathlib import Path

ITEM_TAGS_RE = re.compile(r"<(?:item|entry)\b", re.IGNORECASE)
FILE_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
LIST_KEYS = ("items", "articles", "entries", "data", "news")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS news_summary (
    day     TEXT NOT NULL,
    source  TEXT NOT NULL,
    feed    TEXT NOT NULL,
    fetches INTEGER NOT NULL,
    items   INTEGER NOT NULL,
    PRIMARY KEY (day, source, feed)
);
CREATE TABLE IF NOT EXISTS materializer_runs (
    run_utc TEXT NOT NULL,
    files   INTEGER NOT NULL,
    lines   INTEGER NOT NULL,
    sources INTEGER NOT NULL
);
"""

AGGREGATE_SQL = "INSERT INTO news_summary (day, source, feed, fetches, items) VALUES (?, ?, ?, ?, ?)" \
    " ON CONFLICT(day, source, feed)" \
    " DO UPDATE SET fetches = excluded.fetches, items = excluded.items"


def connect(out_db: Path) -> sqlite3.Connection:
    """Read-accessor for consumers (tests, scripts): one known way to open the summary DB."""
    return sqlite3.connect(out_db)


def item_count(payload: object) -> int:
    """Count items inside one fetch payload, by source shape."""
    if not isinstance(payload, dict):
        return 0
    xml = payload.get("xml")
    if isinstance(xml, str) and xml.strip():
        return len(ITEM_TAGS_RE.findall(xml))
    for key in LIST_KEYS:
        value = payload.get(key)
        if isinstance(value, list):
            return len(value)
    return 0


def _feed_of(payload: dict) -> str:
    feed_url = payload.get("feed_url")
    if isinstance(feed_url, str) and feed_url.strip():
        host = urllib.parse.urlparse(feed_url).netloc.strip()
        if host:
            return host
    for key in ("feed", "source", "provider"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "unknown"


def _archive_files(news_root: Path, since: dt.date | None) -> list[tuple[Path, dt.date | None]]:
    files: list[tuple[Path, dt.date | None]] = []
    for source_dir in sorted(p for p in news_root.iterdir() if p.is_dir()) if news_root.is_dir() else []:
        for path in sorted(source_dir.glob("*.jsonl")):
            stamp: dt.date | None = None
            match = FILE_DATE_RE.search(path.stem)
            if match:
                try:
                    stamp = dt.date.fromisoformat(match.group(1))
                except ValueError:
                    stamp = None
            if since is not None and stamp is not None and stamp < since:
                continue
            files.append((path, stamp))
    return files


def materialize(news_root: Path, out_db: Path, since: dt.date | None = None) -> dict:
    """Flatten the archive into out_db. Idempotent: re-runs upsert the same rows."""
    aggregates: dict[tuple[str, str, str], list[int]] = {}
    files_scanned = 0
    lines_scanned = 0
    sources: set[str] = set()

    for path, file_day in _archive_files(news_root, since):
        files_scanned += 1
        source = path.parent.name or "unknown"
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped:
                    continue
                lines_scanned += 1
                try:
                    record = json.loads(stripped)
                except json.JSONDecodeError:
                    record = {}
                payload = record.get("payload") if isinstance(record, dict) else None
                if not isinstance(payload, dict):
                    payload = {}
                fetch_ts = record.get("fetch_ts") if isinstance(record, dict) else None
                day = file_day
                if day is None and isinstance(fetch_ts, str) and fetch_ts[:10]:
                    try:
                        day = dt.date.fromisoformat(fetch_ts[:10])
                    except ValueError:
                        day = None
                if day is None:
                    day = dt.date.today()
                key = (day.isoformat(), source, _feed_of(payload))
                bucket = aggregates.setdefault(key, [0, 0])
                bucket[0] += 1
                bucket[1] += item_count(payload)
                sources.add(source)

    out_db.parent.mkdir(parents=True, exist_ok=True)
    con = connect(out_db)
    try:
        con.executescript(_SCHEMA)
        for (day, source, feed), (fetches, items) in sorted(aggregates.items()):
            con.execute(AGGREGATE_SQL, (day, source, feed, fetches, items))
        con.execute(
            "INSERT INTO materializer_runs (run_utc, files, lines, sources) VALUES (?, ?, ?, ?)",
            (dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
             files_scanned, lines_scanned, len(sources)),
        )
        con.commit()
    finally:
        con.close()
    return {
        "files": files_scanned,
        "lines": lines_scanned,
        "sources": len(sources),
        "rows": len(aggregates),
        "out_db": str(out_db),
    }


def main(argv: list[str] | None = None) -> int:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--src", type=Path, default=(repo_root / ".." / "data_agent" / "bot" / "user_data" / "news").resolve(),
                        help="news archive root (default: ../data_agent/bot/user_data/news)")
    parser.add_argument("--out", type=Path, default=repo_root / "dashboard" / "state" / "news_summary.sqlite",
                        help="output summary sqlite (default: dashboard/state/news_summary.sqlite)")
    parser.add_argument("--since", type=str, default=None, help="only process files dated on/after YYYY-MM-DD")
    args = parser.parse_args(argv)
    since = dt.date.fromisoformat(args.since) if args.since else None
    stats = materialize(args.src, args.out, since=since)
    print(json.dumps(stats, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
