import argparse
import json
import sys
from pathlib import Path

from web_portfolio.build import build as run_build
from web_portfolio.build import run_mkdocs, sanitize_gate, scrape_or_reuse
from web_portfolio.progress import load_now_entries, load_progress
from web_portfolio.render import write_generated_docs
from web_portfolio.sanitize import format_findings, sanitize_paths


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def cmd_build(args) -> int:
    result = run_build(_repo_root(), do_scrape=not args.no_scrape, do_mkdocs=not args.no_mkdocs)
    for path in result.generated:
        print(f"[web build] generated {path}")
    print(f"[web build] scraped: {result.scraped}, site: {result.site_built}")
    return 0


def cmd_scrape(args) -> int:
    doc, scraped = scrape_or_reuse(_repo_root())
    print(f"[web scrape] wrote content/visuals.json (fresh: {scraped}, generated_utc: {doc.get('generated_utc')})")
    return 0


def cmd_lint(args) -> int:
    root = _repo_root()
    findings = sanitize_paths([root / "content", root / "docs"])
    if findings:
        print(format_findings(findings), file=sys.stderr)
        print(f"[web lint] {len(findings)} refusal(s)", file=sys.stderr)
        return 1
    print("[web lint] clean")
    return 0


def cmd_serve(args) -> int:
    root = _repo_root()
    progress = load_progress(root / "content" / "progress.yaml")
    now_entries = load_now_entries(root / "docs" / "now.md")
    visuals = json.loads((root / "content" / "visuals.json").read_text(encoding="utf-8"))
    write_generated_docs(root, progress, visuals, now_entries)
    sanitize_gate(root)
    proc = run_mkdocs(root, serve=True, port=args.port)
    return proc.returncode if proc is not None else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="web", description="Portfolio site build tooling (uv run web ...)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_build = sub.add_parser("build", help="scrape → generate docs → sanitize → mkdocs build")
    p_build.add_argument("--no-scrape", action="store_true", help="reuse content/visuals.json as-is")
    p_build.add_argument("--no-mkdocs", action="store_true", help="generate docs only, skip mkdocs")
    p_build.set_defaults(func=cmd_build)

    p_scrape = sub.add_parser("scrape", help="refresh content/visuals.json from the public GitHub API")
    p_scrape.set_defaults(func=cmd_scrape)

    p_lint = sub.add_parser("lint", help="sanitizer contract gate over content/ + docs/")
    p_lint.set_defaults(func=cmd_lint)

    p_serve = sub.add_parser("serve", help="generate docs locally, then mkdocs serve on 127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8000)
    p_serve.set_defaults(func=cmd_serve)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except Exception as exc:  # surfaced, not swallowed
        print(f"[web {args.command}] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
