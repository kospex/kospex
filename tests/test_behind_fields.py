"""The upgrade path to the latest release, as `kospex sca` and `krunner osi` show it.

behind_fields() turns releases_between() into flat BEHIND_FIELDS. They reach the
`kospex sca` table (latest / behind), its -out CSV and -json, the `krunner osi`
CSV, and `sca -package TYPE:NAME@VERSION` -- but never dependency_data: they
are not columns until the package facts tables
(changes/202611-package-facts-tables.md).
"""
import csv
import json
from urllib.parse import quote

import pytest

import kospex_schema as KospexSchema
import krunner_utils as KrunnerUtils
from kospex_dependencies import KospexDependencies

BASE = "https://api.deps.dev/v3alpha/systems"

# react's shape after 19.2.0: fixes also went out to the 19.0.x and 19.1.x lines.
REACT_LIKE = [
    ("19.2.1", "2025-12-03T00:00:00Z", True),
    ("19.1.2", "2025-12-03T00:00:00Z", False),
    ("19.0.1", "2025-12-03T00:00:00Z", False),
    ("19.2.0", "2025-10-01T00:00:00Z", False),
    ("19.1.1", "2025-07-28T00:00:00Z", False),
    ("19.1.0", "2025-03-28T00:00:00Z", False),
    ("19.0.0", "2024-12-05T00:00:00Z", False),
    ("19.0.0-rc.1", "2024-11-01T00:00:00Z", False),
    ("18.3.1", "2024-04-26T00:00:00Z", False),
    ("18.3.0", "2024-04-25T00:00:00Z", False),
]


class _Query:
    def __init__(self, responses):
        self.responses = responses

    def url_request_with_status(self, url, **_kwargs):
        return self.responses.get(url, (None, 404))

    def url_request(self, url, **_kwargs):
        return self.url_request_with_status(url)[0]


def _responses(system, name, versions):
    package = f"{BASE}/{system}/packages/{quote(name, safe='')}"
    listing = json.dumps({"versions": [
        {"versionKey": {"version": v}, "publishedAt": d, "isDefault": default}
        for v, d, default in versions]})
    responses = {package: (listing, 200)}
    for v, d, default in versions:
        responses[f"{package}/versions/{v}"] = (json.dumps(
            {"versionKey": {"system": system.upper(), "name": name, "version": v},
             "publishedAt": d, "isDefault": default, "advisoryKeys": []}), 200)
    return responses


def _kd(system="npm", name="pkg", versions=REACT_LIKE, db=None):
    return KospexDependencies(kospex_db=db,
                              kospex_query=_Query(_responses(system, name, versions)))


class TestBehindFields:

    def test_the_upgrade_path_skips_fixes_to_superseded_lines(self):
        fields = _kd().behind_fields("npm", "pkg", "18.3.0")
        assert fields == {
            "latest_version": "19.2.1", "behind_level": "major",
            "behind_major": 1, "behind_minor": 2, "behind_patch": 2, "behind_other": 0,
            "behind_releases": 8, "behind_on_major": 1, "latest_on_major": "18.3.1",
        }

    def test_up_to_date(self):
        fields = _kd().behind_fields("npm", "pkg", "19.2.1")
        assert fields["behind_level"] == "none"
        assert fields["behind_releases"] == 0

    def test_ahead_of_latest_has_no_counts(self):
        fields = _kd().behind_fields("npm", "pkg", "20.0.0")
        assert fields["latest_version"] == "19.2.1"
        assert fields["behind_level"] == "ahead"
        assert fields["behind_major"] is None

    @pytest.mark.parametrize("version", ["19.0.0-rc.1", "", None, "latest"])
    def test_not_measurable_gives_every_key_as_none(self, version):
        fields = _kd().behind_fields("npm", "pkg", version)
        assert fields == dict.fromkeys(KospexDependencies.BEHIND_FIELDS)

    def test_an_oversized_version_in_the_listing_is_skipped(self):
        """One published version with a 5000-digit component must not end the
        lookup -- or the krunner osi batch it runs in (CWE-1284)."""
        huge = "1" * 5000 + ".0.0"
        kd = _kd(versions=REACT_LIKE + [(huge, "2026-01-01T00:00:00Z", False)])
        assert kd.behind_fields("npm", "pkg", "18.3.0")["behind_level"] == "major"
        assert kd.behind_fields("npm", "pkg", huge) == KospexDependencies.empty_behind_fields()

    def test_no_deps_dev_record_gives_every_key_as_none(self):
        kd = KospexDependencies(kospex_query=_Query({}))
        assert kd.behind_fields("npm", "nope", "1.0.0") == KospexDependencies.empty_behind_fields()


class TestDepsdevRecord:

    def test_a_resolved_lookup_carries_the_behind_fields(self):
        record = _kd().depsdev_record("npm", "pkg", "18.3.0")
        assert record["resolution"] == "resolved"
        assert record["behind_level"] == "major"
        assert record["latest_version"] == "19.2.1"
        assert (record["behind_major"], record["behind_minor"], record["behind_patch"]) == (1, 2, 2)
        # versions_behind is unchanged, publish order: 19.2.1, 19.1.2, 19.0.1, 19.2.0,
        # 19.1.1, 19.1.0, 19.0.0 and 18.3.1 are all listed after 18.3.0.
        assert record["versions_behind"] == 8

    def test_the_package_listing_is_fetched_once(self):
        kd = _kd()
        asked = []
        real = kd.kospex_query.url_request_with_status

        def counting(url, **kwargs):
            asked.append(url)
            return real(url, **kwargs)

        kd.kospex_query.url_request_with_status = counting
        kd.depsdev_record("npm", "pkg", "18.3.0")
        assert asked.count(f"{BASE}/npm/packages/pkg") == 1


class TestNotStored:

    def test_save_dependencies_does_not_write_them(self, tmp_path, monkeypatch):
        monkeypatch.setenv("KOSPEX_HOME", str(tmp_path))
        monkeypatch.setenv("KOSPEX_DB", str(tmp_path / "kospex.db"))
        db = KospexSchema.connect_or_create_kospex_db()
        kd = _kd(db=db)
        record, version = kd.lookup_dependency("npm", "pkg", "18.3.0")
        row = {"_repo_id": "github.com~acme~demo", "hash": "h1", "file_path": "package.json",
               "package_type": "npm", "package_version": "18.3.0", "package_use": "direct",
               **record, "resolved_version": version}
        assert row["behind_level"] == "major"

        # Raises "has no column named latest_version" if they reach the upsert.
        assert kd.save_dependencies([row]) == 1
        (saved,) = db.query(f"SELECT * FROM {KospexSchema.TBL_DEPENDENCY_DATA}")
        assert not set(KospexDependencies.BEHIND_FIELDS) & set(saved)
        assert saved["versions_behind"] == 8


class TestScaTable:

    def test_behind_label(self):
        label = KospexDependencies.behind_label
        assert label({"behind_level": "major", "behind_major": 3, "behind_minor": 8,
                      "behind_patch": 4}) == "major (3/8/4)"
        assert label({"behind_level": "patch", "behind_major": 0, "behind_minor": 0,
                      "behind_patch": 2}) == "patch (0/0/2)"
        assert label({"behind_level": "none"}) == "none"
        assert label({"behind_level": "ahead"}) == "ahead"
        assert label({}) is None

    def test_json_and_csv_rows_carry_latest_and_behind(self):
        kd = _kd()
        record, _ = kd.lookup_dependency("npm", "pkg", "18.3.0")
        (row,) = kd.table_rows([record])
        assert row["latest_version"] == "19.2.1"
        assert row["behind"] == "major (1/2/2)"
        assert "behind_major" not in row

    def test_escape_sequences_do_not_reach_the_terminal(self):
        """CWE-150: rich passes ESC through even with markup off, and these
        values come from deps.dev and the manifest."""
        import io

        from rich.console import Console

        buf = io.StringIO()
        console = Console(file=buf, force_terminal=True, width=300)
        KospexDependencies().print_table_rows(
            [{"package_name": "x", "latest_version": "1.0.0\x1b]0;title\x07",
              "source_repo": "https://example.com/r\x1b[2J"}], console=console)
        out = buf.getvalue()
        assert "\x1b]0;title" not in out
        assert "\x1b[2J" not in out
        assert "1.0.0" in out

    def test_the_table_shows_them(self):
        from rich.console import Console

        kd = _kd()
        record, _ = kd.lookup_dependency("npm", "pkg", "18.3.0")
        console = Console(record=True, width=250)
        kd.print_table_rows([record], console=console)
        text = console.export_text()
        assert "behind (M/m/p)" in text
        assert "major (1/2/2)" in text


class TestKrunnerCsv:

    def _rows(self):
        def row(name, version):
            return {"package_type": "npm", "package_name": name, "package_version": version,
                    "requirements_type": "direct"}
        # A resolved package, then one deps.dev has never heard of.
        return [row("pkg", "18.3.0"), row("nope", "1.0.0")]

    def test_every_row_has_the_columns_and_the_csv_writes(self, tmp_path):
        from krunner import enrich_dependency_records

        rows = enrich_dependency_records(self._rows(), _kd())
        for field in KospexDependencies.BEHIND_FIELDS:
            assert field in rows[0] and field in rows[1]
        assert rows[0]["behind_level"] == "major"
        assert rows[1]["behind_level"] is None

        path = tmp_path / "osi.csv"
        KrunnerUtils.write_dict_to_csv(str(path), rows)
        with open(path, newline="") as fh:
            reader = csv.DictReader(fh)
            written = list(reader)
        # Last, so a reader indexing the existing columns is unaffected.
        assert reader.fieldnames[-len(KospexDependencies.BEHIND_FIELDS):] == list(
            KospexDependencies.BEHIND_FIELDS)
        assert written[0]["latest_version"] == "19.2.1"
        assert written[0]["behind_patch"] == "2"

    def test_without_lookups_the_columns_are_still_there(self):
        from krunner import enrich_dependency_records

        rows = enrich_dependency_records(self._rows(), _kd(), enrich=False)
        for row in rows:
            assert {f: row[f] for f in KospexDependencies.BEHIND_FIELDS} == \
                KospexDependencies.empty_behind_fields()


class TestScaPackage:

    def test_upgrade_path_lines(self):
        from kospex_cli import _upgrade_path_lines

        fields = _kd().behind_fields("npm", "pkg", "18.3.0")
        lines = _upgrade_path_lines("18.3.0", fields)
        assert lines == [
            "Upgrade path to 19.2.1: 1 major, 2 minor and 2 patch releases.",
            "Without a major upgrade: 18.3.0 → 18.3.1, a patch upgrade "
            "(1 newer 18.x release).",
        ]

    def test_without_a_major_names_the_size_of_the_jump(self):
        from kospex_cli import _upgrade_path_lines

        # 18.x has a newer minor and a patch on it: the jump is a minor.
        versions = REACT_LIKE + [("18.2.0", "2022-06-14T00:00:00Z", False)]
        fields = _kd(versions=versions).behind_fields("npm", "pkg", "18.2.0")
        assert _upgrade_path_lines("18.2.0", fields, "npm")[1] == (
            "Without a major upgrade: 18.2.0 → 18.3.1, a minor upgrade "
            "(2 newer 18.x releases).")

    def test_latest_on_the_same_major_gives_only_the_path(self):
        """No "Without a major upgrade" line: it would repeat the path."""
        from kospex_cli import _upgrade_path_lines

        fields = _kd().behind_fields("npm", "pkg", "19.0.0")
        assert fields["behind_level"] == "minor"
        assert _upgrade_path_lines("19.0.0", fields, "npm") == [
            "Upgrade path to 19.2.1: 2 minor and 1 patch releases."]

    def test_newest_on_its_major(self):
        from kospex_cli import _upgrade_path_lines

        fields = _kd().behind_fields("npm", "pkg", "18.3.1")
        assert _upgrade_path_lines("18.3.1", fields)[1] == (
            "18.3.1 is the latest 18.x; any upgrade from here is a major one.")

    @pytest.mark.parametrize("version", ["19.2.1", "20.0.0", "19.0.0-rc.1"])
    def test_no_lines_without_a_gap(self, version):
        from kospex_cli import _upgrade_path_lines

        assert _upgrade_path_lines(version, _kd().behind_fields("npm", "pkg", version)) == []
