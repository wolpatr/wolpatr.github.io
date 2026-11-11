"""Build orchestration (spec §6): validate content → scrape (with stale-tolerant
fallback) → render generated docs → sanitizer gate → mkdocs build to site/.
The sanitizer runs after generation so nothing unscanned reaches the site.
"""

from __future__ import annotations

import dataclasses
import json
import shutil
import subprocess
import sys
from pathlib import Path

from web_portfolio.progress import load_now_entries, load_progress
from web_portfolio.render import write_generated_docs
from web_portfolio.sanitize import format_findings, sanitize_paths
from web_portfolio.scrape import (
    VISUALS_SCHEMA,
    GitHubClient,
    RateLimited,
    ScrapeError,
    build_visuals,
)


class SanitizeError(RuntimeError):
    """The sanitizer gate refused the build (forbidden patterns found)."""


@dataclasses.dataclass
class BuildResult:
    scraped: bool
    generated: list[str]
    site_built: bool


def _load_visuals(path: Path) -> dict:
    doc = json.loads(path.read_text(encoding="utf-8"))
    if doc.get("schema") != VISUALS_SCHEMA:
        raise ValueError(f"visuals.json: unexpected schema {doc.get('schema')!r} (expected {VISUALS_SCHEMA!r})")
    return doc


def scrape_or_reuse(root: Path) -> tuple[dict, bool]:
    """Scrape GitHub into visuals.json; on any transport/rate failure, reuse the
    previous committed visuals.json (which is the deploy's own safety net)."""
    content_dir = root / "content"
    progress = load_progress(content_dir / "progress.yaml")
    previous_path = content_dir / "visuals.json"
    previous: dict | None = None
    if previous_path.exists():
        previous = _load_visuals(previous_path)
    try:
        client = GitHubClient()
        names = [p.repo_name for p in progress.projects] + [t.repo_name for t in progress.tail]
        doc = build_visuals(client, progress.owner, names, previous)
        (content_dir / "visuals.json").write_text(
            json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        return doc, True
    except (RateLimited, ScrapeError, OSError, ValueError) as exc:
        if previous is None:
            raise
        print(f"[web build] scrape unavailable ({exc}) — reusing previous visuals.json", file=sys.stderr)
        return previous, False


def sanitize_gate(root: Path) -> None:
    findings = sanitize_paths([root / "content", root / "docs"])
    if findings:
        raise SanitizeError("sanitizer refused the build:\n" + format_findings(findings))


def run_mkdocs(root: Path, serve: bool = False, port: int | None = None) -> subprocess.CompletedProcess | None:
    exe_suffix = ".exe" if sys.platform.startswith("win") else ""
    mkdocs_exe = shutil.which("mkdocs") or str(Path(sys.executable).parent / f"mkdocs{exe_suffix}")
    cmd = [mkdocs_exe, "serve" if serve else "build"]
    if serve and port:
        cmd += ["--dev-addr", f"127.0.0.1:{port}"]
    return subprocess.run(cmd, cwd=root, check=False)


def build(root: Path, *, do_scrape: bool = True, do_mkdocs: bool = True) -> BuildResult:
    root = Path(root)
    progress = load_progress(root / "content" / "progress.yaml")
    now_entries = load_now_entries(root / "docs" / "now.md")

    if do_scrape:
        visuals, scraped = scrape_or_reuse(root)
    else:
        visuals, scraped = _load_visuals(root / "content" / "visuals.json"), False

    generated = write_generated_docs(root, progress, visuals, now_entries)
    sanitize_gate(root)
    site_built = False
    if do_mkdocs:
        result = run_mkdocs(root)
        if result is not None and result.returncode != 0:
            raise RuntimeError(f"mkdocs build failed with exit code {result.returncode}")
        site_built = True
    return BuildResult(scraped=scraped, generated=generated, site_built=site_built)
