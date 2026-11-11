"""Render glue (not a TDD seam, spec §7): generated docs from the declared
content (progress.yaml), the scraped signals (visuals.json) and the hand-written
now.md. Everything HTML-side is escaped; visuals are inline SVG + CSS bars with
no client-side JavaScript.
"""

from __future__ import annotations

import html
from collections.abc import Mapping

from web_portfolio.progress import NowEntry, Progress, Project

_SPARK_WIDTH, _SPARK_HEIGHT = 140, 32


def esc(text: object) -> str:
    return html.escape(str(text if text is not None else ""), quote=False)


def svg_sparkline(week_entries: Mapping[str, object] | list | None) -> str:
    weeks = list(week_entries or [])
    totals = [int(w.get("total") or 0) for w in weeks if isinstance(w, Mapping)] or [0]
    count = len(totals)
    step = _SPARK_WIDTH / max(count - 1, 1)
    peak = max(totals) or 1
    points: list[str] = []
    for index, total in enumerate(totals):
        x = round(index * step, 1)
        y = round(_SPARK_HEIGHT - 3 - (total / peak) * (_SPARK_HEIGHT - 6), 1)
        points.append(f"{x},{y}")
    last_x, last_y = points[-1].split(",")
    return (
        f'<svg class="pf-spark" viewBox="0 0 {_SPARK_WIDTH} {_SPARK_HEIGHT}" '
        f'preserveAspectRatio="none" role="img" aria-label="Commits per week, recent weeks">'
        f'<polyline points="{" ".join(points)}" fill="none" stroke="currentColor" stroke-width="1.5" '
        f'stroke-linejoin="round" stroke-linecap="round"/>'
        f'<circle cx="{last_x}" cy="{last_y}" r="2" fill="currentColor" transform="translate(-2,-2)"'
        f"/></svg>"
    )


def language_bars(platform_languages: Mapping[str, int] | None) -> str:
    languages = dict(platform_languages or {})
    if not languages:
        return "<p class='pf-muted'>No language data yet.</p>\n"
    total = sum(languages.values()) or 1
    ordered = sorted(languages.items(), key=lambda item: (-item[1], item[0]))
    top = ordered[:4]
    other_bytes = sum(bytes_count for _, bytes_count in ordered[4:])
    rows: list[str] = []
    for name, byte_count in top:
        percent = round(100 * byte_count / total, 1)
        kilobytes = max(1, round(byte_count / 1000))
        rows.append(
            f'<div class="pf-lang"><span class="pf-lang-name">{esc(name)}</span>'
            f'<span class="pf-lang-track"><span class="pf-lang-fill" style="width: {percent}%"></span></span>'
            f'<span class="pf-lang-note">{percent:.1f}% · {kilobytes}k</span></div>'
        )
    if other_bytes:
        remainder = round(100 * other_bytes / total, 1)
        rows.append(
            f'<div class="pf-lang"><span class="pf-lang-name">other</span>'
            f'<span class="pf-lang-track"></span><span class="pf-lang-note">{remainder:.1f}%</span></div>'
        )
    return '<div class="pf-langs">' + "".join(rows) + "</div>\n"


def chip(project: Project) -> str:
    return f'<span class="pf-chip pf-chip-{esc(project.status)}">{esc(project.status_label)}</span>'


def percent_bar(project: Project) -> str:
    return (
        f'<div class="pf-bar" role="progressbar" aria-label="Declared progress" '
        f'aria-valuemin="0" aria-valuemax="100" aria-valuenow="{project.percent}">'
        f'<div class="pf-bar-fill" style="width: {project.percent}%;"></div></div>\n'
        f'<p class="pf-bar-caption"><strong>{project.percent}%</strong> declared progress, hand-maintained</p>\n'
    )


def _repo_entry(visuals: Mapping, project: Project) -> dict:
    entry = (visuals.get("repos") or {}).get(project.repo) or {}
    return entry if isinstance(entry, Mapping) else {}


def project_card(project: Project, visuals: Mapping) -> str:
    entry = _repo_entry(visuals, project)
    pushed = entry.get("pushed_at")
    pushed_text = esc(str(pushed)[:10]) if pushed else "—"
    commits = entry.get("commits_4w")
    commits_text = "no data yet" if commits is None else f"{commits} commits (4w)"
    return (
        f'<div class="pf-card">'
        f'<h3><a href="projects/{project.slug}/">{esc(project.repo_name)}</a></h3>'
        f"{chip(project)}"
        f"<p>{esc(project.description)}</p>"
        f'<div class="pf-card-meta">pushed {pushed_text} · {commits_text}</div>'
        f'</div>\n'
    )


def _milestone_text(entry: NowEntry) -> str:
    paragraph = entry.paragraphs[0]
    lines = [line.strip().removeprefix("- ").strip() for line in paragraph.splitlines() if line.strip()]
    if paragraph.lstrip().startswith("-"):
        return " · ".join(lines)
    collapsed = " ".join(paragraph.split())
    return " ".join(collapsed.split(". ")[:2]).rstrip(".") + "."


def render_index(progress: Progress, visuals: Mapping, now_entries: list[NowEntry]) -> str:
    lines: list[str] = [
        f"# {esc(progress.site.title)}",
        "",
        f"*{esc(progress.site.tagline)}*",
        "",
        "## Now",
        "",
        esc(now_entries[0].summary),
        "",
        "[Read the full Now log →](now.md)",
        "",
        "## Live evidence",
        "",
        '<div class="pf-tiles">'
        + f'<div class="pf-tile"><div class="pf-tile-value">{progress.arms.live_count}</div>'
        + '<div class="pf-tile-label">dry-run arms live</div></div>'
        + f'<div class="pf-tile"><div class="pf-tile-value">{progress.evidence.trials_recorded}</div>'
        + '<div class="pf-tile-label">trials recorded</div></div>'
        + f'<div class="pf-tile"><div class="pf-tile-value">{progress.evidence.gate_week}</div>'
        + '<div class="pf-tile-label">gate week (v0.1)</div></div>'
        + f'<div class="pf-tile"><div class="pf-tile-value">{esc(visuals.get("user", {}).get("public_repos", ""))}</div>'
        + '<div class="pf-tile-label">public repos</div></div>'
        + "</div>",
        "",
        f"*{esc(progress.arms.statement)}*",
        "",
        "## Projects",
        "",
        '<div class="pf-cards">',
        *[project_card(p, visuals) for p in progress.projects],
        "</div>",
        "",
        "## The honest tail",
        "",
        '<ul class="pf-tail">'
        + "".join(
            f'<li><a href="https://github.com/{esc(t.repo)}">{esc(t.repo_name)}</a> · {esc(t.note)}</li>'
            for t in progress.tail
        )
        + "</ul>",
        "",
        "## Milestones",
        "",
        *[f"- {entry.date.isoformat()}: {esc(_milestone_text(entry))}" for entry in now_entries],
        "",
        "---",
        "",
        esc(progress.site.contact),
        "",
    ]
    return "\n".join(lines)


def render_project_page(progress: Progress, project: Project, visuals: Mapping) -> str:
    entry = _repo_entry(visuals, project)
    pushed = entry.get("pushed_at")
    created = entry.get("created_at")
    commits = entry.get("commits_4w")
    stale = bool(entry.get("stale"))
    issues = entry.get("open_issues")
    stars = entry.get("stargazers")
    lines: list[str] = [
        f"# {esc(project.repo_name)}",
        "",
        f"{chip(project)} · repo since {esc(str(created)[:10]) if created else '—'}",
        "",
        percent_bar(project),
        "",
        esc(project.description),
        "",
        "## Proof signals (scraped at build time)",
        "",
        '<div class="pf-proof">'
        + svg_sparkline(entry.get("commit_activity"))
        + f'<p class="pf-proof-caption">{esc(project.repo_name)}: commits per week (recent weeks'
        + (", data possibly stale" if stale else "")
        + ")</p>"
        + "<ul class=\"pf-proof-list\">"
        + (
            f"<li>Last push: <strong>{esc(str(pushed)[:10])}</strong></li>"
            f"<li>Commits in the last four weeks: <strong>{'no data yet' if commits is None else commits}</strong></li>"
            f"<li>Open issues: <strong>{'—' if issues is None else issues}</strong></li>"
            f"<li>Stars: <strong>{'—' if stars is None else stars}</strong></li>"
        )
        + "</ul>"
        + "</div>",
        "",
        "## Language mix",
        "",
        language_bars(entry.get("platform_languages")),
        "",
        "---",
        "",
        f"Source: [{esc(project.repo)}]({esc(project.repo_url)}) on GitHub · signal data scraped unauthenticated at build time.",
        "",
    ]
    return "\n".join(lines)


def write_generated_docs(root, progress: Progress, visuals: Mapping, now_entries: list[NowEntry]) -> list[str]:
    """Write index.md + docs/projects/<slug>.md. Returns written relative paths."""
    import pathlib

    docs_dir = pathlib.Path(root) / "docs"
    projects_dir = docs_dir / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    index = docs_dir / "index.md"
    index.write_text(render_index(progress, visuals, now_entries), encoding="utf-8")
    written.append(str(index.relative_to(root)))
    for project in progress.projects:
        page = projects_dir / f"{project.slug}.md"
        page.write_text(render_project_page(progress, project, visuals), encoding="utf-8")
        written.append(str(page.relative_to(root)))
    return written
