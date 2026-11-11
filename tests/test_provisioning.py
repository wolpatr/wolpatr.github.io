"""Seam 5 (spec §7.5): Grafana provisioning integrity — datasource uid wiring,
folder/dashboard sanity, and SQL that actually parses against mirrors of the
real store schemas (ledger trials, freqtrade trades/wallet_history, news_summary).
"""

import json
import re
import sqlite3
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
DASHBOARD_DIR = REPO / "dashboard"
SQLITE_TYPE = "frser-sqlite-datasource"


@pytest.fixture(scope="module")
def datasources() -> dict[str, dict]:
    path = DASHBOARD_DIR / "provisioning" / "datasources" / "datasources.yml"
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert doc["apiVersion"] == 1
    entries = {}
    for ds in doc["datasources"]:
        assert ds["uid"] not in entries, f"duplicate datasource uid {ds['uid']}"
        entries[ds["uid"]] = ds
    return entries


@pytest.fixture(scope="module")
def dashboards() -> list[dict]:
    jsons = sorted((DASHBOARD_DIR / "dashboards").glob("*.json"))
    assert jsons
    parsed = []
    uids = []
    for path in jsons:
        doc = json.loads(path.read_text(encoding="utf-8"))
        parsed.append(doc)
        uids.append(doc["uid"])
    assert len(uids) == len(set(uids)), "dashboard uids must be unique"
    return parsed


# ---------------------------------------------------------------- datasources


class TestDatasources:
    def test_each_datasource_is_the_sqlite_plugin_with_readonly_paths(self, datasources):
        for uid, ds in datasources.items():
            assert ds["type"] == SQLITE_TYPE, uid
            path = (ds.get("jsonData") or {}).get("path", "")
            assert path.startswith("/data/"), f"{uid} must point into a mounted /data path (got {path})"
            assert ds.get("editable") is False
            assert ds.get("access") == "proxy"

    def test_the_four_content_items_have_datasources(self, datasources):
        assert set(datasources) == {"evidence-ledger", "arm-v01", "arm-v02", "news-summary"}


class TestDashboardProvider:
    def test_provider_uses_polling_safe_interval_for_bind_mounts(self):
        doc = yaml.safe_load(
            (DASHBOARD_DIR / "provisioning" / "dashboards" / "dashboards.yml").read_text(encoding="utf-8")
        )
        assert doc["apiVersion"] == 1
        for provider in doc["providers"]:
            assert provider["options"]["path"] == "/var/lib/grafana/dashboards"
            assert provider["updateIntervalSeconds"] > 10

    def test_provisioned_dashboards_are_immutable_in_ui(self):
        doc = yaml.safe_load(
            (DASHBOARD_DIR / "provisioning" / "dashboards" / "dashboards.yml").read_text(encoding="utf-8")
        )
        for provider in doc["providers"]:
            assert provider["allowUiUpdates"] is False


# ---------------------------------------------------------------- dashboards


def iter_panel_targets(panel: dict):
    for target in panel.get("targets") or []:
        yield panel, target


def all_targets(dashboard: dict):
    for panel in dashboard.get("panels") or []:
        yield from iter_panel_targets(panel)


class TestDashboardSanity:
    def test_each_dashboard_has_core_fields_and_no_overlapping_panel_ids(self, dashboards):
        for doc in dashboards:
            assert doc.get("title"), "dashboard title required"
            ids = [p["id"] for p in doc["panels"]]
            assert len(ids) == len(set(ids)), "panel ids must be unique"
            for panel in doc["panels"]:
                assert panel.get("gridPos"), f"panel {panel.get('title')} missing gridPos"

    def test_every_query_panel_has_a_wired_datasource(self, dashboards, datasources):
        for doc in dashboards:
            for panel in doc["panels"]:
                kind = panel.get("type", "")
                if kind in ("text",):
                    continue
                panel_ds = (panel.get("datasource") or {}).get("uid")
                for _, target in iter_panel_targets(panel):
                    ds_uid = target_ds_uid(panel, target)
                    if panel_ds is None and ds_uid is None:
                        continue
                    assert ds_uid in datasources or panel_ds in datasources, (
                        f"panel {panel.get('title')} target unwired: {ds_uid or panel_ds}"
                    )


def target_ds_uid(panel: dict, target: dict) -> str | None:
    ds = target.get("datasource") or panel.get("datasource")
    if isinstance(ds, dict):
        return ds.get("uid")
    if isinstance(ds, str):  # legacy string-form datasource ref
        return ds
    return None


# ---------------------------------------------------------------- SQL


TRIALS_SCHEMA = """
CREATE TABLE trials (
    trial_id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL, created_utc TEXT NOT NULL,
    git_sha TEXT, yaml_hash TEXT, merged_config_hash TEXT, freqtrade_run_id TEXT,
    runner_hash TEXT, runner_schema_version TEXT, window_start TEXT, window_end TEXT,
    mode TEXT NOT NULL, model_id TEXT, test_sharpe REAL, metrics_json TEXT
);
"""

TRADES_SCHEMA = """
CREATE TABLE trades (
    id INTEGER PRIMARY KEY, pair TEXT, is_open INTEGER, open_rate REAL, close_rate REAL,
    open_date TEXT, close_date TEXT, close_profit REAL, close_profit_abs REAL,
    exit_reason TEXT, strategy TEXT, enter_tag TEXT, timeframe TEXT
);
"""

WALLET_SCHEMA = """
CREATE TABLE wallet_history (
    id INTEGER PRIMARY KEY, timestamp TEXT, currency TEXT, rate REAL, quote_currency TEXT,
    balance REAL, total_quote REAL, total_position_value REAL, collateral REAL, leverage REAL
);
"""

NEWS_SCHEMA = """
CREATE TABLE news_summary (
    day TEXT NOT NULL, source TEXT NOT NULL, feed TEXT NOT NULL,
    fetches INTEGER NOT NULL, items INTEGER NOT NULL, PRIMARY KEY (day, source, feed)
);
"""


@pytest.fixture(scope="module")
def probe_db() -> sqlite3.Connection:
    con = sqlite3.connect(":memory:")
    con.execute(TRIALS_SCHEMA)
    con.execute(TRADES_SCHEMA)
    con.execute(WALLET_SCHEMA)
    con.execute(NEWS_SCHEMA)
    con.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, trade_id INTEGER, ft_order_side TEXT)")
    return con


class TestSqlIntegrity:
    def test_every_target_query_parses_against_real_store_shapes(self, dashboards, probe_db):
        seen = 0
        for doc in dashboards:
            for panel, target in all_targets(doc):
                sql = target.get("queryText")
                if not sql:
                    continue
                seen += 1
                assert target.get("rawQuery") is True
                assert target.get("queryType") in ("table", "time series")
                try:
                    probe_db.execute("EXPLAIN QUERY PLAN " + sql)
                except sqlite3.Error as exc:
                    pytest.fail(f"unparsable SQL in panel {panel['title']!r}: {exc} — {sql}")
        assert seen >= 8, "provisioned panels must carry real SQL"

    def test_time_series_queries_expose_a_time_first_column(self, dashboards):
        for doc in dashboards:
            for panel, target in all_targets(doc):
                if panel.get("type") == "timeseries":
                    sql = target.get("queryText", "").lower()
                    assert re.search(r"as\s+[\"']?time[\"']?\s*[,)]", sql), (
                        f"timeseries panel {panel['title']!r} needs a time column alias"
                    )


# ---------------------------------------------------------------- compose


class TestCompose:
    @pytest.fixture(scope="class")
    @classmethod
    def compose(cls):
        return yaml.safe_load((DASHBOARD_DIR / "docker-compose.yml").read_text(encoding="utf-8"))

    def test_pinned_grafana_with_preinstalled_sqlite_plugin(self, compose):
        service = compose["services"]["grafana"]
        assert service["image"] == "grafana/grafana-oss:13.3.1"
        preinstall = service["environment"]["GF_PLUGINS_PREINSTALL"]
        assert "frser-sqlite-datasource@4.0.6" in preinstall
        assert service["restart"] == "unless-stopped"

    def test_admin_password_required_from_gitignored_env(self, compose):
        env = compose["services"]["grafana"]["environment"]
        assert env["GF_SECURITY_ADMIN_PASSWORD"].startswith("${GF_SECURITY_ADMIN_PASSWORD:?")

    def test_localhost_only_publish_and_readonly_store_mounts(self, compose):
        service = compose["services"]["grafana"]
        assert "127.0.0.1:3000:3000" in service["ports"]
        volume_list = [item.split(":")[0] for item in service["volumes"]]
        assert "./provisioning:/etc/grafana/provisioning:ro" in service["volumes"]
        assert "./dashboards:/var/lib/grafana/dashboards:ro" in service["volumes"]
        assert "../data_agent/docs/experiments:/data/experiments:ro" in service["volumes"]
        assert "../data_agent/bot:/data/bot:ro" in service["volumes"]
        assert volume_list.count("../data_agent/bot") == 1

    def test_every_provisioned_sqlite_path_exists_in_the_mount_plan(self, datasources, compose):
        mounts = compose["services"]["grafana"]["volumes"]
        for uid, ds in datasources.items():
            container_path = ds["jsonData"]["path"]
            assert container_path.endswith(".sqlite")
            # the DB's directory must be one of the mounted, read-only host dirs
            parent = "/".join(container_path.strip("/").split("/")[:2])  # data/<dir>
            assert any(f"/{parent}" in m for m in mounts), f"{uid}: {container_path} has no matching mount"
