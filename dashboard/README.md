# Local evidence dashboard — Grafana OSS (read-only)

Localhost, authenticated, read-only view over the data_agent evidence stores
(spec §5). Data lives in the sibling private-ish working copy; this container
only reads it. Nothing here is exposed beyond `127.0.0.1:3000`.

## Setup (one-time)

```bash
cd dashboard
Set-Content -Path .env -Value "GF_SECURITY_ADMIN_PASSWORD=change-me"   # PowerShell; .env is gitignored
docker compose up -d
```

Then open http://127.0.0.1:3000 and log in as `admin` with that password.

## Panels

| Store (source of truth) | Mount | Datasource | Panels |
|---|---|---|---|
| `docs/experiments/ledger.sqlite` (trials) | `../data_agent/...:/data/experiments:ro` | `evidence-ledger` | folds & mean test_sharpe per day, trials detail table |
| `bot/tradesv3.dryrun.sqlite` (v0.1 arm) | `../data_agent/bot:/data/bot:ro` | `arm-v01` | closed trades, win rate, weeks live, equity |
| `bot/tradesv3.dryrun.v02.sqlite` (v0.2 arm) | same | `arm-v02` | open trades, equity |
| news JSONL archive | materialized | `news-summary` | items per day, feeds detail |

The news archive is JSONL, which Grafana cannot read directly; the materializer
flattens it into `state/news_summary.sqlite` (also mounted read-only):

```bash
uv run dashboard/materializer.py                    # src defaults to ../data_agent/bot/user_data/news
uv run dashboard/materializer.py --since 2026-11-01 # re-run on demand, idempotent upserts
```

Notes:
- Datasources are provisioned with `editable: false`; dashboards with
  `allowUiUpdates: false`. Treat the JSON in `dashboards/` and
  `provisioning/` as the code it is. The SQLite plugin opens paths
  `query_only(1)` by default; sqlite-vec layers are intentionally out of reach.
- Everything binds to `127.0.0.1` only; `GF_USERS_ALLOW_SIGN_UP` is off.
- To re-apply provisioning after editing the JSON: `docker compose restart grafana`.
