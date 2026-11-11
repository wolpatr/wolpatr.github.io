"""Seam 2 (spec §7.2): the GitHub scrape → content/visuals.json.

The scrape is unauthenticated public-API only (no credentials on this path),
rate-limit aware, and resilient: a failed or rate-limited call falls back to
the previous visuals.json entry marked stale, instead of failing the deploy.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from typing import Protocol

VISUALS_SCHEMA = "visuals.v1"
DEFAULT_WINDOW_WEEKS = 12
_BUDGET_HEADROOM = 5


class RateLimited(RuntimeError):
    """The unauthenticated rate budget is exhausted."""


class ScrapeError(RuntimeError):
    """A declared repo vanished at the source — the build must fail loudly."""


class VisualsClient(Protocol):
    def repo(self, owner: str, name: str) -> dict: ...
    def languages(self, owner: str, name: str) -> dict[str, int]: ...
    def commit_activity(self, owner: str, name: str) -> list[dict] | None: ...  # None = stats warming up
    def user(self, owner: str) -> dict: ...


def _now_utc() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")


def _repo_entry(client: VisualsClient, owner: str, name: str, weeks: int) -> dict:
    payload = client.repo(owner, name)
    languages = client.languages(owner, name)
    activity_raw = client.commit_activity(owner, name)
    window = (activity_raw or [])[-weeks:]
    return {
        "pushed_at": payload.get("pushed_at"),
        "created_at": payload.get("created_at"),
        "language": payload.get("language"),
        "platform_languages": {str(k): int(v) for k, v in (languages or {}).items()},
        "open_issues": int(payload.get("open_issues_count") or 0),
        "stargazers": int(payload.get("stargazers_count") or 0),
        "commit_activity": [{"week": w.get("week"), "total": int(w.get("total") or 0)} for w in window],
        "commits_4w": (
            sum(int(w.get("total") or 0) for w in window[-4:]) if window else None
        ),
        "stale": False,
    }


def build_visuals(
    client: VisualsClient,
    owner: str,
    repo_names: Sequence[str],
    previous: Mapping[str, object] | None,
    weeks: int = DEFAULT_WINDOW_WEEKS,
    budget_headroom: int = _BUDGET_HEADROOM,
) -> dict:
    """Scrape `owner`'s declared repos into the visuals.json document.

    `previous` is the last known-good visuals.json (may be None). Any repo that
    cannot be scraped falls back to its previous entry (marked stale); a
    declared repo with no previous entry fails the scrape loudly.
    """
    if hasattr(client, "remaining_budget"):
        remaining = client.remaining_budget()
        if remaining is not None and remaining < len(repo_names) * 3 + 1 + budget_headroom:
            raise RateLimited(
                f"unauthenticated rate budget too low ({remaining} left) — skipping scrape"
            )

    previous_repos = dict((previous or {}).get("repos") or {})
    previous_user = dict((previous or {}).get("user") or {})
    repos: dict[str, dict] = {}

    def stale_fallback(full: str) -> dict | None:
        """Previous entry for `full`, marked stale; None if we have none."""
        if full in previous_repos:
            return {**previous_repos[full], "stale": True}
        return None

    rate_limited = False

    for name in repo_names:
        full = f"{owner}/{name}"
        fallback = stale_fallback(full)
        if rate_limited:
            if fallback is not None:
                repos[full] = fallback
                continue
            raise RateLimited(f"rate budget exhausted and no previous data for {full}")
        try:
            repos[full] = _repo_entry(client, owner, name, weeks)
        except RateLimited:
            rate_limited = True
            if fallback is not None:
                repos[full] = fallback
                continue
            raise
        except (LookupError, OSError, ValueError) as exc:
            if fallback is not None:
                repos[full] = fallback
                continue
            raise ScrapeError(f"declared repo {full} could not be scraped (and no previous data exists): {exc}") from exc

    user_block: dict = {}
    if not rate_limited:
        try:
            user_payload = client.user(owner)
            user_block = {key: user_payload.get(key) for key in ("login", "public_repos", "html_url")}
            user_block["stale"] = False
        except (RateLimited, LookupError, OSError, ValueError):
            pass
    if not user_block or user_block.get("stale"):
        if previous_user:
            user_block = {**previous_user, "stale": True}
        else:
            user_block = {"login": owner, "stale": True}

    return {
        "schema": VISUALS_SCHEMA,
        "generated_utc": _now_utc(),
        "source": "github-api-v3-unauthenticated",
        "user": user_block,
        "repos": {full: repos[full] for full in sorted(repos)},
    }


class GitHubClient:
    """Real client: unauthenticated GitHub REST v3 via urllib."""

    def __init__(self, api_base: str = "https://api.github.com", timeout: float = 20.0,
                 user_agent: str = "wolpatr-portfolio-build", stats_backoff_s: float = 0.0) -> None:
        self._api_base = api_base.rstrip("/")
        self._timeout = timeout
        self._headers = {"Accept": "application/vnd.github+json", "User-Agent": user_agent}
        self._stats_backoff_s = stats_backoff_s
        self.remaining: int | None = None

    def remaining_budget(self) -> int | None:
        return self.remaining

    def _get_json(self, path: str) -> dict | list | None:
        request = urllib.request.Request(f"{self._api_base}{path}", headers=self._headers)
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                self.remaining = int(response.headers.get("X-RateLimit-Remaining", self.remaining or 0))
                if response.status in (202, 204):
                    # Statistics endpoints answer 202 (computing) or 204 (no commits)
                    # with an empty body — both mean "no data yet".
                    return None
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 403:
                remaining = exc.headers.get("X-RateLimit-Remaining")
                if remaining == "0":
                    self.remaining = 0
                    raise RateLimited("GitHub API rate budget exhausted (403)") from exc
            if exc.code == 404:
                raise LookupError(f"not found: {path}") from exc
            raise OSError(f"GitHub API error {exc.code} for {path}") from exc
        except urllib.error.URLError as exc:
            raise OSError(f"cannot reach GitHub API: {exc}") from exc
        return payload

    def repo(self, owner: str, name: str) -> dict:
        return self._get_json(f"/repos/{owner}/{name}")

    def languages(self, owner: str, name: str) -> dict[str, int]:
        return self._get_json(f"/repos/{owner}/{name}/languages") or {}

    def commit_activity(self, owner: str, name: str) -> list[dict] | None:
        payload = self._get_json(f"/repos/{owner}/{name}/stats/commit_activity")
        if payload is None or isinstance(payload, list):
            return payload
        if self._stats_backoff_s > 0:
            time.sleep(self._stats_backoff_s)
        retry = self._get_json(f"/repos/{owner}/{name}/stats/commit_activity")
        if retry is not None and not isinstance(retry, list):
            return None
        return retry

    def user(self, owner: str) -> dict:
        return self._get_json(f"/users/{owner}")
