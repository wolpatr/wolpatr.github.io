"""Content model: progress.yaml (THE hand-maintained content file) + now.md parsing.

Seam notes (spec §7.1): strict keys, kebab-case unique slugs, percent bounds 0-100.
Everything public-facing is declared here or scraped; nothing is pulled from private stores.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import itertools
import re
from pathlib import Path
from typing import Any

import yaml

STATUSES: dict[str, str] = {
    "active": "Active",
    "shipped": "Shipped",
    "resting": "Resting",
    "shipped-resting": "Shipped · Resting",
}
_PERCENT_MIN, _PERCENT_MAX = 0, 100
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_REPO_RE = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")
_NOW_HEADING_RE = re.compile(r"^##\s+(\d{4}-\d{2}-\d{2})\s*$")


class ProgressError(ValueError):
    """Refusal of a content file that violates the declared content model."""


@dataclasses.dataclass(frozen=True)
class Site:
    title: str
    tagline: str
    contact: str


@dataclasses.dataclass(frozen=True)
class ArmsTile:
    live_count: int
    statement: str


@dataclasses.dataclass(frozen=True)
class Evidence:
    trials_recorded: int
    gate_week: int


@dataclasses.dataclass(frozen=True)
class Project:
    slug: str
    repo: str
    status: str
    percent: int
    description: str
    since: dt.date | None

    @property
    def owner(self) -> str:
        return self.repo.split("/", 1)[0]

    @property
    def repo_name(self) -> str:
        return self.repo.split("/", 1)[1]

    @property
    def status_label(self) -> str:
        return STATUSES[self.status]

    @property
    def repo_url(self) -> str:
        return f"https://github.com/{self.repo}"


@dataclasses.dataclass(frozen=True)
class TailEntry:
    repo: str
    note: str

    @property
    def repo_name(self) -> str:
        return self.repo.split("/", 1)[1]

    @property
    def repo_url(self) -> str:
        return f"https://github.com/{self.repo}"


@dataclasses.dataclass(frozen=True)
class Progress:
    site: Site
    arms: ArmsTile
    evidence: Evidence
    projects: list[Project]
    tail: list[TailEntry]

    @property
    def owner(self) -> str:
        """The portfolio's GitHub owner: all declared repos must live under it."""
        owners = {entry.repo.split("/", 1)[0] for entry in [*self.projects, *self.tail]}
        if len(owners) != 1:
            state = ", ".join(sorted(owners))
            raise ProgressError(f"progress.yaml: all repos must belong to one owner (saw {state})")
        return owners.pop()


def _fail(origin: str, problems: list[str]) -> None:
    if problems:
        raise ProgressError(f"{origin}: invalid content model — {' / '.join(problems)}")


def _strict_mapping(origin: str, value: Any, where: str, allowed: set[str], required: set[str] | None = None) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProgressError(f"{origin}: invalid content model — {where} must be a mapping")
    unknown = sorted(set(value) - allowed)
    missing = sorted((required if required is not None else allowed) - set(value))
    problems: list[str] = []
    if unknown:
        problems.append(f"{where}: unknown key(s) {', '.join(unknown)} (allowed: {', '.join(sorted(allowed))})")
    if missing:
        problems.append(f"{where}: missing key(s) {', '.join(missing)}")
    _fail(origin, problems)
    return value


def _require_str(origin: str, value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(origin, [f"{where} must be a non-empty string"])
    return value


def _require_int(origin: str, value: Any, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(origin, [f"{where} must be an integer (got {type(value).__name__}: {value!r})"])
    return value


def _require_percent(origin: str, value: Any, where: str) -> int:
    percent = _require_int(origin, value, where)
    if not _PERCENT_MIN <= percent <= _PERCENT_MAX:
        _fail(origin, [f"{where} must be within {_PERCENT_MIN}-{_PERCENT_MAX} (got {percent})"])
    return percent


def _require_status(origin: str, value: Any, where: str) -> str:
    status = _require_str(origin, value, where)
    if status not in STATUSES:
        _fail(origin, [f"{where} must be one of {', '.join(STATUSES)} (got {status!r})"])
    return status


def _require_repo(origin: str, value: Any, where: str) -> str:
    repo = _require_str(origin, value, where)
    if not _REPO_RE.match(repo):
        _fail(origin, [f"{where} must look like owner/name (got {repo!r})"])
    return repo


def _parse_project(origin: str, raw: Any) -> Project:
    if not isinstance(raw, dict):
        raise ProgressError(f"{origin}: invalid content model — each project must be a mapping")
    strict = _strict_mapping(
        origin, raw, f"project '{raw.get('slug', raw.get('repo', '?'))}'",
        {"slug", "repo", "status", "percent", "description", "since"},
        required={"slug", "repo", "status", "percent", "description"},
    )
    slug = _require_str(origin, strict["slug"], "project slug")
    if not _SLUG_RE.match(slug):
        _fail(origin, [f"project slug {slug!r} must be kebab-case (lowercase letters, digits, dashes)"])
    since: dt.date | None = None
    if strict.get("since") is not None:
        since_raw = _require_str(origin, strict["since"], f"project {slug!r} 'since'")
        try:
            since = dt.date.fromisoformat(since_raw)
        except ValueError:
            _fail(origin, [f"project {slug!r} 'since' must be an ISO date YYYY-MM-DD (got {since_raw!r})"])
    return Project(
        slug=slug,
        repo=_require_repo(origin, strict["repo"], f"project {slug!r} 'repo'"),
        status=_require_status(origin, strict["status"], f"project {slug!r} 'status'"),
        percent=_require_percent(origin, strict["percent"], f"project {slug!r} 'percent'"),
        description=_require_str(origin, strict["description"], f"project {slug!r} 'description'"),
        since=since,
    )


def _parse_tail(origin: str, raws: Any) -> list[TailEntry]:
    if not isinstance(raws, list):
        return []
    tail: list[TailEntry] = []
    for raw in raws:
        if not isinstance(raw, dict):
            raise ProgressError(f"{origin}: invalid content model — each tail entry must be a mapping")
        strict = _strict_mapping(
            origin, raw, f"tail entry '{raw.get('repo', '?')}'",
            {"repo", "note"},
        )
        tail.append(
            TailEntry(
                repo=_require_repo(origin, strict["repo"], "tail entry 'repo'"),
                note=_require_str(origin, strict["note"], "tail entry 'note'"),
            )
        )
    return tail


def parse_progress_text(text: str, origin: str = "progress.yaml") -> Progress:
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ProgressError(f"{origin}: invalid YAML — {exc}") from exc
    if not isinstance(doc, dict):
        raise ProgressError(f"{origin}: top level must be a mapping")
    top = _strict_mapping(origin, doc, "top level", {"site", "arms", "evidence", "projects", "tail"})

    site_raw = _strict_mapping(origin, top["site"], "site", {"title", "tagline", "contact"})
    site = Site(
        _require_str(origin, site_raw["title"], "site.title"),
        _require_str(origin, site_raw["tagline"], "site.tagline"),
        _require_str(origin, site_raw["contact"], "site.contact"),
    )

    arms_raw = _strict_mapping(origin, top["arms"], "arms", {"live_count", "statement"})
    arms = ArmsTile(
        _require_int(origin, arms_raw["live_count"], "arms.live_count"),
        _require_str(origin, arms_raw["statement"], "arms.statement"),
    )

    evidence_raw = _strict_mapping(origin, top["evidence"], "evidence", {"trials_recorded", "gate_week"})
    evidence = Evidence(
        _require_int(origin, evidence_raw["trials_recorded"], "evidence.trials_recorded"),
        _require_int(origin, evidence_raw["gate_week"], "evidence.gate_week"),
    )

    if not isinstance(top["projects"], list) or not top["projects"]:
        _fail(origin, ["projects must be a non-empty list"])
    projects: list[Project] = []
    slugs: list[str] = []
    for raw in top["projects"]:
        project = _parse_project(origin, raw)
        slugs.append(project.slug)
        projects.append(project)
    duplicates = sorted({slug for slug in slugs if slugs.count(slug) > 1})
    _fail(origin, [f"projects: duplicate slug(s) {', '.join(duplicates)}"] if duplicates else [])

    tail = _parse_tail(origin, top.get("tail", []))
    return Progress(site=site, arms=arms, evidence=evidence, projects=projects, tail=tail)


def load_progress(path: Path) -> Progress:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ProgressError(f"{path}: cannot read content file — {exc}") from exc
    return parse_progress_text(text, str(path))


@dataclasses.dataclass(frozen=True)
class NowEntry:
    date: dt.date
    paragraphs: list[str]

    @property
    def summary(self) -> str:
        collapsed = " ".join(self.paragraphs[0].split())
        if len(collapsed) <= 220:
            return collapsed
        return collapsed[:217].rstrip() + "…"


def parse_now_entries(text: str) -> list[NowEntry]:
    """Parse the hand-written now.md: '## YYYY-MM-DD' headings, latest first."""
    if text.startswith("## ") and " " in text.splitlines()[0]:
        heading_date_part = text.splitlines()[0][3:].strip()
        if not _NOW_HEADING_RE.match(text.splitlines()[0]):
            raise ProgressError(
                f"now.md: headings must be plain dates '## YYYY-MM-DD' (got {heading_date_part!r})"
            )
    entries: list[NowEntry] = []
    current_date: dt.date | None = None
    buffer: list[str] = []

    def flush() -> None:
        if current_date is None:
            return
        body = [p.strip() for p in "\n".join(buffer).split("\n\n") if p.strip()]
        if not body:
            raise ProgressError(f"now.md: entry {current_date.isoformat()} has no body text")
        entries.append(NowEntry(date=current_date, paragraphs=body))

    for line in text.splitlines():
        if line.startswith("## "):
            heading = _NOW_HEADING_RE.match(line)
            if heading is None:
                raise ProgressError(
                    f"now.md: headings must be plain dates '## YYYY-MM-DD' (got {line.removeprefix('## ').strip()!r})"
                )
            flush()
            buffer = []
            current_date = dt.date.fromisoformat(heading.group(1))
        elif line.lstrip().startswith("#") and line.strip():
            raise ProgressError(
                f"now.md: headings must be plain dates '## YYYY-MM-DD' (got {line.strip()!r})"
            )
        elif current_date is not None:
            buffer.append(line)
        elif line.strip():
            raise ProgressError(f"now.md: stray content before the first dated entry: {line.strip()!r}")
    flush()
    if not entries:
        raise ProgressError("now.md: no dated '## YYYY-MM-DD' entries found")
    for previous, today in itertools.pairwise(entries):
        if today.date >= previous.date:
            raise ProgressError(
                f"now.md: entries must be listed latest first "
                f"({today.date.isoformat()} follows {previous.date.isoformat()})"
            )
    return entries


def load_now_entries(path: Path) -> list[NowEntry]:
    try:
        return parse_now_entries(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ProgressError(f"{path}: cannot read now.md — {exc}") from exc
