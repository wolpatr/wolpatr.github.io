import pytest

from web_portfolio.progress import (
    ProgressError,
    load_progress,
    parse_now_entries,
    parse_progress_text,
)

VALID = """
site:
  title: "Portfolio"
  tagline: "Building in public"
  contact: "Placeholder contact — not set yet."
arms:
  live_count: 2
  statement: "Two dry-run arms live."
evidence:
  trials_recorded: 15
  gate_week: 1
projects:
  - slug: data-agent
    repo: wolpatr/data_agent
    status: active
    percent: 65
    description: "Trading research agent: experiments, gates, dry-run arms."
    since: "2026-01-10"
  - slug: lovebird
    repo: wolpatr/lovebird
    status: active
    percent: 40
    description: "ESP32-S3 tamagotchi pets."
tail:
  - repo: wolpatr/minirocket
    note: "Fork"
  - repo: wolpatr/empty-starter
    note: "Placeholder, effectively empty"
"""


def parse(text: str):
    return parse_progress_text(text)


class TestValidProgress:
    def test_valid_document_round_trips(self):
        p = parse(VALID)
        assert p.site.title == "Portfolio"
        assert p.arms.live_count == 2
        assert p.evidence.trials_recorded == 15
        assert [proj.slug for proj in p.projects] == ["data-agent", "lovebird"]
        assert p.projects[0].repo == "wolpatr/data_agent"
        assert p.projects[0].status == "active"
        assert p.projects[0].percent == 65
        # since parses to a real date, not a string
        assert p.projects[0].since.isoformat() == "2026-01-10"
        assert len(p.tail) == 2
        assert p.tail[0].note == "Fork"


class TestStrictness:
    def test_unknown_top_level_key_refused(self):
        with pytest.raises(ProgressError) as err:
            parse(VALID + "\nanalytics: true\n")
        assert "analytics" in str(err.value)

    def test_unknown_project_key_refused(self):
        text = VALID.replace("description: \"Trading research agent: experiments, gates, dry-run arms.\"\n", 
                             "description: \"Trading research agent: experiments, gates, dry-run arms.\"\n    private_notes: secret\n")
        with pytest.raises(ProgressError) as err:
            parse(text)
        assert "private_notes" in str(err.value)

    def test_tail_entries_take_no_progress_storytelling(self):
        text = VALID.replace("    note: \"Fork\"", "    note: \"Fork\"\n    percent: 20")
        with pytest.raises(ProgressError) as err:
            parse(text)
        assert "percent" in str(err.value)


class TestPercentBounds:
    @pytest.mark.parametrize(
        "bad_yaml",
        ["101", "-5", '"65"', "65.5", "true"],
        ids=["too-high", "negative", "string", "float", "bool"],
    )
    def test_percent_must_be_int_within_0_100(self, bad_yaml):
        text = VALID.replace("percent: 65", f"percent: {bad_yaml}")
        with pytest.raises(ProgressError) as err:
            parse(text)
        assert "percent" in str(err.value).lower()


class TestSlugs:
    def test_slug_must_be_kebab_case(self):
        text = VALID.replace("slug: data-agent", "slug: Data Agent")
        with pytest.raises(ProgressError) as err:
            parse(text)
        assert "slug" in str(err.value).lower()

    def test_slugs_must_be_unique(self):
        text = VALID.replace("slug: lovebird", "slug: data-agent")
        with pytest.raises(ProgressError) as err:
            parse(text)
        assert "duplicate" in str(err.value).lower()


class TestDeclaredValues:
    def test_status_must_be_declared_kind(self):
        text = VALID.replace("status: active\n    percent: 65", "status: cancelled\n    percent: 65")
        with pytest.raises(ProgressError) as err:
            parse(text)
        assert "status" in str(err.value).lower()

    def test_shipped_resting_is_a_status(self):
        text = VALID.replace("status: active\n    percent: 65", "status: shipped-resting\n    percent: 65")
        p = parse(text)
        assert p.projects[0].status == "shipped-resting"

    def test_repo_must_be_owner_and_name(self):
        text = VALID.replace("repo: wolpatr/data_agent", "repo: data_agent")
        with pytest.raises(ProgressError) as err:
            parse(text)
        assert "repo" in str(err.value).lower()

    def test_since_must_be_a_date(self):
        text = VALID.replace("since: \"2026-01-10\"", "since: \"last January\"")
        with pytest.raises(ProgressError) as err:
            parse(text)
        assert "since" in str(err.value).lower()


class TestLoadFromFile:
    def test_load_progress_reads_yaml_from_disk(self, tmp_path):
        path = tmp_path / "progress.yaml"
        path.write_text(VALID, encoding="utf-8")
        p = load_progress(path)
        assert p.arms.live_count == 2

    def test_missing_file_is_a_progress_error(self, tmp_path):
        with pytest.raises(ProgressError):
            load_progress(tmp_path / "nope.yaml")


VALID_NOW = """
## 2026-11-11

Two dry-run arms went live yesterday on paper trading. The RSS news
adapter shipped into the comparison arm.

Paragraph two of the same entry.

## 2026-10-28

Started the portfolio build.
"""


class TestNowEntries:
    def test_entries_listed_latest_first(self):
        entries = parse_now_entries(VALID_NOW)
        assert [e.date.isoformat() for e in entries] == ["2026-11-11", "2026-10-28"]

    def test_first_paragraph_is_the_hero_summary(self):
        entries = parse_now_entries(VALID_NOW)
        assert entries[0].paragraphs[0].startswith("Two dry-run arms went live")

    def test_non_dated_heading_refused(self):
        with pytest.raises(ProgressError):
            parse_now_entries("## Latest update\n\nText\n")

    def test_h1_title_refused_the_file_starts_with_dated_entries(self):
        with pytest.raises(ProgressError):
            parse_now_entries("# Now\n\n## 2026-11-11\n\nBody\n")

    def test_order_must_be_latest_first(self):
        asc = "## 2026-10-28\n\nA\n\n## 2026-11-11\n\nB\n"
        with pytest.raises(ProgressError):
            parse_now_entries(asc)
