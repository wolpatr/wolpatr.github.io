import json

import pytest

from web_portfolio.scrape import RateLimited, build_visuals


def repo_payload(push, created, language, issues, stars, description=""):
    return {
        "name": "fixture",
        "pushed_at": push,
        "created_at": created,
        "language": language,
        "open_issues_count": issues,
        "stargazers_count": stars,
        "description": description,
    }


def activity(week_totals):
    """Shape as recorded from repos/<o>/<r>/stats/commit_activity."""
    return [{"days": [0, 0, 0, 0, 0, 0, 0], "total": t, "week": 1697616000 + 604800 * i} for i, t in enumerate(week_totals)]


class FakeClient:
    def __init__(self, repos=None, languages=None, stats=None, users=None, remaining=60, raise_on=None):
        self._repos = repos or {}
        self._languages = languages or {}
        self._stats = stats or {}
        self._users = users or {"login": "wolpatr", "public_repos": 10, "html_url": "https://github.com/wolpatr",
                                "url": "https://api.github.com/users/wolpatr", "followers_url": "x"}
        self.remaining = remaining
        self.raise_on = raise_on  # callable(kind, owner, name) -> Exception or None
        self.calls: list[tuple[str, str, str]] = []

    def _guard(self, kind, owner, name):
        self.calls.append((kind, owner, name))
        if self.raise_on is not None:
            exc = self.raise_on(kind, owner, name)
            if exc is not None:
                raise exc

    def repo(self, owner, name):
        self._guard("repo", owner, name)
        return self._repos[f"{owner}/{name}"]

    def languages(self, owner, name):
        self._guard("languages", owner, name)
        return self._languages.get(f"{owner}/{name}", {})

    def commit_activity(self, owner, name):
        self._guard("commit_activity", owner, name)
        return self._stats.get(f"{owner}/{name}")

    def user(self, owner):
        self.calls.append(("user", owner, ""))
        return dict(self._users)


class TestFreshScrape:
    def test_scrape_merges_repo_signals_into_visuals_json(self):
        client = FakeClient(
            repos={"wolpatr/data_agent": repo_payload("2026-11-10T18:22:44Z", "2026-06-01T09:00:00Z", "Python", 0, 0)},
            languages={"wolpatr/data_agent": {"Python": 84500, "Jupyter Notebook": 15200}},
            stats={"wolpatr/data_agent": activity([1, 2, 0, 4, 5, 0, 0, 3])},
        )
        visuals = build_visuals(client, "wolpatr", ["data_agent"], previous=None, weeks=6)
        entry = visuals["repos"]["wolpatr/data_agent"]
        assert entry["pushed_at"] == "2026-11-10T18:22:44Z"
        assert entry["language"] == "Python"
        assert [w["total"] for w in entry["commit_activity"]] == [0, 4, 5, 0, 0, 3]  # most recent six weeks
        assert entry["commits_4w"] == 5 + 0 + 0 + 3  # last four weeks of the stored window
        assert entry["stale"] is False
        assert visuals["user"]["public_repos"] == 10
        assert visuals["schema"] == "visuals.v1"

    def test_commit_activity_warmup_202_leaves_empty_window(self):
        client = FakeClient(
            repos={"wolpatr/data_agent": repo_payload("2026-11-10T18:22:44Z", "2026-06-01T09:00:00Z", "Python", 0, 0)},
            stats={"wolpatr/data_agent": None},
        )
        visuals = build_visuals(client, "wolpatr", ["data_agent"], previous=None)
        entry = visuals["repos"]["wolpatr/data_agent"]
        assert entry["commit_activity"] == []
        assert entry["commits_4w"] is None

    def test_makes_one_repo_call_plus_languages_plus_stats_per_repo(self):
        client = FakeClient(
            repos={"wolpatr/data_agent": repo_payload("2026-11-10", "2026-06-01", "Python", 0, 0)},
            languages={"wolpatr/data_agent": {"Python": 100}},
            stats={"wolpatr/data_agent": activity([1, 1])},
        )
        build_visuals(client, "wolpatr", ["data_agent"], previous=None)
        kinds = sorted(kind for kind, _, _ in client.calls)
        assert kinds == ["commit_activity", "languages", "repo", "user"]


class TestRateLimitAwareness:
    def test_rate_limited_call_falls_back_to_previous_and_marks_stale(self):
        client = FakeClient(
            repos={"wolpatr/first": repo_payload("2026-11-10", "2026-01-01", "Python", 1, 2)},
            languages={"wolpatr/first": {"Python": 10}},
            stats={"wolpatr/first": activity([7])},
            raise_on=lambda kind, owner, name: RateLimited("60 requests per hour exhausted") if "second" in name else None,
        )
        previous = {
            "schema": "visuals.v1",
            "generated_utc": "2026-11-10T04:00:00+00:00",
            "user": {"login": "wolpatr", "public_repos": 10, "stale": True},
            "repos": {"wolpatr/second": {"pushed_at": "2026-11-01", "language": "Python", "stale": True,
                                          "commit_activity": [], "commits_4w": 9}},
        }
        visuals = build_visuals(client, "wolpatr", ["first", "second"], previous=json.loads(json.dumps(previous)))
        assert visuals["repos"]["wolpatr/first"]["stale"] is False
        assert visuals["repos"]["wolpatr/second"]["commits_4w"] == 9
        assert visuals["repos"]["wolpatr/second"]["stale"] is True
        assert visuals["user"]["stale"] is True

    def test_unknown_repo_without_previous_fails_loudly(self):
        client = FakeClient()
        with pytest.raises(Exception):
            build_visuals(client, "wolpatr", ["ghost-repo"], previous=None)

    def test_declared_repo_missing_at_the_source_fails_loudly(self):
        client = FakeClient(
            repos={},
            raise_on=lambda kind, owner, name: LookupError(f"404 {owner}/{name}"),
        )
        with pytest.raises(Exception):
            build_visuals(client, "wolpatr", ["data_agent"], previous=None)


class TestStableShape:
    def test_user_block_is_trimmed_to_presentation_fields(self):
        client = FakeClient(
            repos={"wolpatr/data_agent": repo_payload("2026-11-10", "2026-06-01", "Python", 0, 0)},
            languages={"wolpatr/data_agent": {"Python": 10}},
            stats={"wolpatr/data_agent": activity([1])},
        )
        visuals = build_visuals(client, "wolpatr", ["data_agent"], previous=None)
        assert visuals["user"] == {
            "login": "wolpatr",
            "public_repos": 10,
            "html_url": "https://github.com/wolpatr",
            "stale": False,
        }

    def test_output_is_json_serializable_and_sorted(self):
        client = FakeClient(
            repos={
                "wolpatr/zzz": repo_payload("2026-11-01", "2026-01-01", "Python", 0, 0),
                "wolpatr/aaa": repo_payload("2026-11-02", "2026-01-02", "Rich", 0, 0),
            },
            languages={"wolpatr/zzz": {"Python": 1}, "wolpatr/aaa": {"Rich": 2}},
            stats={"wolpatr/zzz": activity([1]), "wolpatr/aaa": activity([2])},
        )
        visuals = build_visuals(client, "wolpatr", ["zzz", "aaa"], previous=None)
        encoded = json.dumps(visuals)
        assert list(json.loads(encoded)["repos"].keys()) == ["wolpatr/aaa", "wolpatr/zzz"]
