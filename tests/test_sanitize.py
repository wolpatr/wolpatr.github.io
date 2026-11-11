import textwrap
from pathlib import Path

import pytest

from web_portfolio.sanitize import Finding, sanitize_paths

REPO = Path(__file__).resolve().parents[1]


def sanitize_text(text: str, tmp_path: Path) -> list[Finding]:
    target = tmp_path / "sample.md"
    target.write_text(textwrap.dedent(text), encoding="utf-8")
    return sanitize_paths([target])


class TestCleanContent:
    def test_clean_documents_yield_no_findings(self, tmp_path):
        target = tmp_path / "clean.md"
        target.write_text(
            "# Projects\n\nTwo dry-run arms live. 15 fold trials recorded. Gate week 1.\n",
            encoding="utf-8",
        )
        assert sanitize_paths([target]) == []


class TestForbiddenPatterns:
    @pytest.mark.parametrize(
        "text",
        [
            'api_key: "AAAA-FAKE-KEY-0000"\n',
            "password = 'hunter2hunter2'\n",
            "auth_token = aaaaaaaa.bbbbbbbb.cccccc\n",
            'secret: "topsecretvalue"\n',
        ],
    )
    def test_assignments_to_secret_names_are_refused(self, tmp_path, text):
        findings = sanitize_text(text, tmp_path)
        assert findings, "secret-shaped assignment not caught"
        assert all("credential" in f.pattern for f in findings)

    def test_bearer_prefixed_value_refused(self, tmp_path):
        findings = sanitize_text("Authorization: Bearer qwertyuiop1234567890\n", tmp_path)
        assert len(findings) == 1

    def test_hex_blob_refused(self, tmp_path):
        blob = "a1" * 32
        findings = sanitize_text(f"key: {blob}\n", tmp_path)
        assert len(findings) == 1

    def test_openai_style_key_refused(self, tmp_path):
        findings = sanitize_text("connect with sk-abcdefghij1234567890abcd\n", tmp_path)
        assert len(findings) == 1

    def test_github_token_refused(self, tmp_path):
        findings = sanitize_text("token: ghp_abcdefghijklmnopqrstuvwx\n", tmp_path)
        assert len(findings) == 1

    def test_windows_user_path_refused(self, tmp_path):
        findings = sanitize_text("data at C:\\Users\\patri\\Documents\\Projects\\data_agent\n", tmp_path)
        assert len(findings) == 1

    def test_unix_home_path_refused(self, tmp_path):
        findings = sanitize_text("logs in /home/patri/tmp\n", tmp_path)
        assert len(findings) == 1

    def test_env_file_reference_refused(self, tmp_path):
        findings = sanitize_text("set the password in the `.env` file\n", tmp_path)
        assert len(findings) == 1

    def test_private_config_marker_refused(self, tmp_path):
        findings = sanitize_text("values live in config-private.json\n", tmp_path)
        assert len(findings) == 1

    def test_trade_store_names_refused(self, tmp_path):
        findings = sanitize_text("we read tradesv3.dryrun.sqlite directly\n", tmp_path)
        assert len(findings) == 1


class TestFindingsAreActionableButSafe:
    def test_finding_points_at_file_and_line(self, tmp_path):
        findings = sanitize_text("# Title\n\nintro line\n\npassword: oops12345\n", tmp_path)
        assert findings[0].path.name == "sample.md"
        assert findings[0].line == 5

    def test_finding_does_not_echo_the_secret_value(self, tmp_path):
        findings = sanitize_text('api_key: "SUPER-SECRET-VALUE-123"\n', tmp_path)
        assert "SUPER-SECRET-VALUE-123" not in findings[0].snippet
        assert "credential" in findings[0].pattern


class TestScope:
    def test_directories_are_scanned_recursively(self, tmp_path):
        (tmp_path / "content").mkdir()
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "page.md").write_text("password: leaky-thing\n", encoding="utf-8")
        (tmp_path / "content" / "quiet.yaml").write_text("title: ok\n", encoding="utf-8")
        findings = sanitize_paths([tmp_path / "content", tmp_path / "docs"])
        assert len(findings) == 1
        assert findings[0].path == tmp_path / "docs" / "page.md"


class TestContract:
    def test_real_repo_content_and_docs_are_clean(self):
        findings = sanitize_paths([REPO / "content", REPO / "docs"])
        assert findings == [], "forbidden patterns in tracked content: " + "; ".join(
            f"{f.path}@{f.line} {f.pattern}" for f in findings
        )
