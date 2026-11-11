import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MATERIALIZER = REPO / "dashboard" / "materializer.py"

spec = importlib.util.spec_from_file_location("materializer", MATERIALIZER)
assert spec is not None and spec.loader is not None
materializer = importlib.util.module_from_spec(spec)
sys.modules.setdefault("materializer", materializer)
spec.loader.exec_module(materializer)


def rss_payload(feed_url: str, items: int, title: str = "t") -> dict:
    body = "".join(f"<item><title>{title}{i}</title></item>" for i in range(items))
    return {
        "fetch_ts": "2026-11-11T11:30:41.622248+00:00",
        "payload": {"feed_url": feed_url, "xml": f"<rss><channel>{body}</channel></rss>"},
    }


def write_jsonl(path: Path, lines: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
    return path


class TestItemCounting:
    def test_rss_xml_items_are_counted_from_the_embedded_xml(self):
        payload = rss_payload("https://cointelegraph.com/rss", 25)["payload"]
        assert materializer.item_count(payload) == 25

    def test_generic_list_payloads_are_counted(self):
        payload = {"feed_url": "https://api.example.com/av", "items": [{"id": 1}, {"id": 2}]}
        assert materializer.item_count(payload) == 2

    def test_unrecognized_shape_counts_zero_items_but_is_a_fetch(self):
        payload = {"feed_url": "https://api.example.com/av", "body": "opaque"}
        assert materializer.item_count(payload) == 0


class TestMaterialize:
    def test_rss_archive_day_and_feed_depth_land_in_summary_table(self, tmp_path):
        news = tmp_path / "news"
        write_jsonl(
            news / "rss" / "2026-11-11.jsonl",
            [rss_payload("https://www.coindesk.com/arc/outboundfeeds/rss/", 25),
             rss_payload("https://cointelegraph.com/rss", 31)],
        )
        out = tmp_path / "state" / "news_summary.sqlite"
        stats = materializer.materialize(news, out)
        assert stats["files"] == 1 and stats["lines"] == 2
        con = materializer.connect(out)
        rows = con.execute(
            "SELECT day, source, feed, fetches, items FROM news_summary ORDER BY feed").fetchall()
        con.close()
        assert rows == [
            ("2026-11-11", "rss", "cointelegraph.com", 1, 31),
            ("2026-11-11", "rss", "www.coindesk.com", 1, 25),
        ]

    def test_multiple_files_and_fetches_aggregate_per_day_and_feed(self, tmp_path):
        news = tmp_path / "news"
        write_jsonl(news / "rss" / "2026-11-10.jsonl", [rss_payload("https://example.com/rss", 5)])
        write_jsonl(news / "rss" / "2026-11-11.jsonl",
                    [rss_payload("https://example.com/rss", 7), rss_payload("https://example.com/rss", 4)])
        out = tmp_path / "state" / "news_summary.sqlite"
        materializer.materialize(news, out)
        con = materializer.connect(out)
        rows = con.execute("SELECT day, feed, fetches, items FROM news_summary ORDER BY day").fetchall()
        con.close()
        assert rows == [("2026-11-10", "example.com", 1, 5), ("2026-11-11", "example.com", 2, 11)]

    def test_reruns_are_idempotent_upserts(self, tmp_path):
        news = tmp_path / "news"
        write_jsonl(news / "rss" / "2026-11-11.jsonl", [rss_payload("https://example.com/rss", 5)])
        out = tmp_path / "state" / "news_summary.sqlite"
        materializer.materialize(news, out)
        materializer.materialize(news, out)
        con = materializer.connect(out)
        n = con.execute("SELECT COUNT(*) FROM news_summary").fetchone()[0]
        items = con.execute("SELECT items FROM news_summary").fetchone()[0]
        runs = con.execute("SELECT COUNT(*) FROM materializer_runs").fetchone()[0]
        con.close()
        assert n == 1 and items == 5 and runs == 2

    def test_since_selects_only_newer_files(self, tmp_path):
        news = tmp_path / "news"
        write_jsonl(news / "rss" / "2026-11-01.jsonl", [rss_payload("https://old.example.com/rss", 99)])
        write_jsonl(news / "rss" / "2026-11-11.jsonl", [rss_payload("https://new.example.com/rss", 3)])
        out = tmp_path / "state" / "news_summary.sqlite"
        import datetime as dt

        materializer.materialize(news, out, since=dt.date(2026, 11, 5))
        con = materializer.connect(out)
        feeds = [r[0] for r in con.execute("SELECT feed FROM news_summary").fetchall()]
        con.close()
        assert feeds == ["new.example.com"]

    def test_unrecognized_source_dir_shape_still_records(self, tmp_path):
        news = tmp_path / "news"
        write_jsonl(news / "av" / "2026-11-11.jsonl",
                    [{"fetch_ts": "2026-11-11T12:00:00+00:00", "payload": {"items": [{"a": 1}]}}])
        out = tmp_path / "state" / "news_summary.sqlite"
        materializer.materialize(news, out)
        con = materializer.connect(out)
        rows = con.execute("SELECT day, source, feed, fetches, items FROM news_summary").fetchall()
        con.close()
        assert rows == [("2026-11-11", "av", "unknown", 1, 1)]
