"""Sanitized-data contract (spec §4): a build-time lint over content/ + docs/
refusing secrets, key-shaped values, and private-path/store markers — failing
the build rather than leaking. Matched values are never echoed into output.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Iterable
from pathlib import Path

DEFAULT_SCAN_SUFFIXES = {".md", ".yaml", ".yml", ".json", ".js", ".css", ".html", ".txt", ".toml"}


@dataclasses.dataclass(frozen=True)
class Finding:
    path: Path
    line: int
    pattern: str
    snippet: str


@dataclasses.dataclass(frozen=True)
class ForbiddenPattern:
    name: str
    regex: re.Pattern[str]

    def find_on(self, line: str) -> re.Match[str] | None:
        return self.regex.search(line)


FORBIDDEN_PATTERNS: tuple[ForbiddenPattern, ...] = (
    ForbiddenPattern(
        "credential-assignment",
        re.compile(
            r"(?i)\b("
            r"api[_-]?key|apikey|access[_-]?token|auth[_-]?token|"
            r"secret|password|passwd|credential|api[_-]?token"
            r")\s*[:=]\s*['\"$]?[^\s'\"$]{4,}"
        ),
    ),
    ForbiddenPattern("bearer-token", re.compile(r"(?i)bearer\s+[A-Za-z0-9\-._~+/]{8,}")),
    ForbiddenPattern("hex-secret-blob", re.compile(r"\b[a-fA-F0-9]{64}\b")),
    ForbiddenPattern("openai-style-key", re.compile(r"\bsk-[A-Za-z0-9]{16,}\b")),
    ForbiddenPattern("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ForbiddenPattern("windows-private-path", re.compile(r"(?i)\b[a-z]:\\+users\\+[a-z0-9._-]+")),
    ForbiddenPattern("unix-private-path", re.compile(r"(?i)(?:^|(?<=[\s\"'(>:]))/home/[a-z0-9._-]+")),
    ForbiddenPattern("env-file-reference", re.compile(r"(?i)^\s*(?:[^#\n]*?)\S*\.env\b")),
    ForbiddenPattern("private-config-marker", re.compile(r"(?i)config[_-]?private|private[_-]?config")),
    ForbiddenPattern("trade-store-name", re.compile(r"(?i)tradesv3|dryrun\.sqlite")),
)


def _scan_file(path: Path) -> list[Finding]:
    findings: list[Finding] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return findings
    for index, line in enumerate(text.splitlines(), start=1):
        for pattern in FORBIDDEN_PATTERNS:
            match = pattern.find_on(line)
            if match is not None:
                prefix = line[max(0, match.start() - 40):match.start()]
                suffix = line[match.end():match.end() + 20]
                snippet = f"{prefix}«redacted»{suffix}".strip()
                findings.append(
                    Finding(
                        path=path,
                        line=index,
                        pattern=pattern.name,
                        snippet=snippet if snippet else "«redacted»",
                    )
                )
    return findings


def sanitize_paths(paths: Iterable[Path]) -> list[Finding]:
    """Scan each path (file or directory, recursive) for forbidden patterns."""
    findings: list[Finding] = []
    for root in paths:
        root = Path(root)
        if root.is_dir():
            candidates = sorted(
                child for child in root.rglob("*")
                if child.is_file() and child.suffix.lower() in DEFAULT_SCAN_SUFFIXES
            )
        elif root.is_file():
            if root.suffix.lower() not in DEFAULT_SCAN_SUFFIXES:
                candidates = []
            else:
                candidates = [root]
        else:
            candidates = []
        for candidate in candidates:
            findings.extend(_scan_file(candidate))
    return findings


def format_findings(findings: list[Finding]) -> str:
    return "\n".join(f"{finding.path}:{finding.line}: {finding.pattern} — {finding.snippet}" for finding in findings)
