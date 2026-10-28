"""`kospex sca` output, -scope, -json, -out and the -save guard rails (#237, #151).

sca is an analyst's look at a dependency file. By default it prints the file,
one tab-separated line per package and a rich table, filtered by -scope
(default `direct`). -json puts only the table rows on stdout. -save is opt-in
and refused unless the repository and its HEAD are synced and the worktree is
clean, and it writes through save_dependencies() -- the krunner osi writer --
so a package removed from the manifest is demoted rather than left current.
"""
import csv
import datetime
import json
import subprocess

import pytest
from click.testing import CliRunner

import kospex_schema as KospexSchema
import kospex_utils as KospexUtils
from kospex_dependencies import KospexDependencies

PACKAGE_JSON = (
    '{"name":"d","version":"1.0.0",'
    '"dependencies":{"express":"4.18.2"},'
    '"devDependencies":{"jest":"29.0.0","eslint":"8.0.0"},'
    '"peerDependencies":{"react":"18.2.0"}}'
)
REPO_URL = "https://github.com/acme/demo.git"
REPO_ID = "github.com~acme~demo"


def _stub_depsdev(kd):
    kd.depsdev_record = lambda pt, pn, pv: {
        "package_name": pn, "package_version": pv, "package_type": pt,
        "versions_behind": 3, "advisories": 1, "days_ago": 42,
        "published_at": "2026-01-01T00:00:00Z", "resolution": "resolved",
        "source_repo": f"https://github.com/acme/{pn}", "default": False,
        "authors": 7,
    }
    kd.get_pypi_source_repo = lambda name: ""
    return kd


def _fresh_db(tmp_path, monkeypatch):
    from kospex.habitat_config import HabitatConfig

    home = tmp_path / "kospex_home"
    home.mkdir()
    monkeypatch.setenv("KOSPEX_HOME", str(home))
    monkeypatch.setenv("KOSPEX_DB", str(home / "kospex.db"))
    HabitatConfig.reset_instance()
    return KospexSchema.connect_or_create_kospex_db()


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args],
                          check=True, capture_output=True, text=True).stdout.strip()


def _git_repo(tmp_path, content=PACKAGE_JSON):
    repo = tmp_path / "demo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "dev@example.com")
    _git(repo, "config", "user.name", "Dev")
    _git(repo, "remote", "add", "origin", REPO_URL)
    (repo / "package.json").write_text(content)
    _git(repo, "add", "package.json")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


def _sync(db, repo):
    """Record the repo and its HEAD the way a sync would, minimally."""
    db.table(KospexSchema.TBL_REPOS).insert({"_repo_id": REPO_ID})
    db.table(KospexSchema.TBL_COMMITS).insert(
        {"_repo_id": REPO_ID, "hash": _git(repo, "rev-parse", "HEAD")})


@pytest.fixture
def cli_deps(monkeypatch):
    """kospex_cli with its dependencies object replaced by a stubbed one."""
    import kospex_cli

    kd = _stub_depsdev(KospexDependencies())
    monkeypatch.setattr(kospex_cli.kospex, "dependencies", kd)
    return kospex_cli, kd


def _invoke(kospex_cli, *args):
    return CliRunner().invoke(kospex_cli.cli, ["sca", *args])


class TestDaysAgo:
    def test_depsdev_record_sets_whole_days_since_release(self):
        kd = KospexDependencies()
        published = (datetime.datetime.now(datetime.timezone.utc)
                     - datetime.timedelta(days=10, hours=5))
        kd.deps_dev_status = lambda *a: (
            {"publishedAt": published.strftime("%Y-%m-%dT%H:%M:%SZ")}, 200)
        kd.get_versions_behind = lambda *a: {"versions_behind": 0}

        details = kd.depsdev_record("pypi", "requests", "2.25.0")

        assert details["days_ago"] == 10
        assert isinstance(details["days_ago"], int)

    def test_unresolved_has_no_days_ago(self):
        details = KospexDependencies().depsdev_record("pypi", "pyyaml", "")
        assert "days_ago" not in details

    def test_assess_keeps_the_integer_from_depsdev_record(self, tmp_path):
        """assess() used to overwrite it with a two-decimal float."""
        path = tmp_path / "package.json"
        path.write_text(PACKAGE_JSON)

        records = _stub_depsdev(KospexDependencies()).assess(str(path), quiet=True)

        assert {r["days_ago"] for r in records} == {42}

    def test_a_range_is_looked_up_at_its_floor(self, tmp_path):
        """days_ago describes the floor: the version the lookup resolved to."""
        kd = _stub_depsdev(KospexDependencies())
        looked_up = []
        stub = kd.depsdev_record
        kd.depsdev_record = lambda pt, pn, pv: looked_up.append(pv) or stub(pt, pn, pv)
        path = tmp_path / "requirements.txt"
        path.write_text("requests>=2.25.0\n")

        kd.assess(str(path), quiet=True)

        assert looked_up == ["2.25.0"]


class TestColumns:
    def test_no_authors_or_malware_column(self):
        fields = KospexDependencies().get_table_field_names()
        assert "authors" not in fields
        assert "malware" not in fields

    def test_table_rows_are_exactly_the_table_columns(self):
        kd = KospexDependencies()
        rows = kd.table_rows([{"package_name": "x", "authors": 3, "package_use": "direct",
                               "advisories": "", "versions_behind": 0}])
        assert list(rows[0]) == kd.get_table_field_names()
        assert rows[0]["published_at"] is None
        assert rows[0]["advisories"] is None        # the template's "" seed
        assert rows[0]["versions_behind"] == 0      # a real zero survives

    def test_missing_values_render_as_dash(self):
        assert KospexDependencies.summary_line(
            {"package_name": "pyyaml", "package_version": "", "published_at": None}
        ) == "pyyaml, -"

    def test_summary_line_is_name_and_version_only(self):
        assert KospexDependencies.summary_line(
            {"package_name": "click", "package_version": "<9,>=8.4.1",
             "published_at": "2026-05-22T04:08:35Z"}
        ) == "click, <9,>=8.4.1"

    def test_extras_brackets_survive_rich_rendering(self):
        from rich.console import Console

        console = Console(record=True, width=200)
        KospexDependencies().print_table_rows(
            [{"package_name": "mkdocstrings[python]"}], console=console)

        assert "mkdocstrings[python]" in console.export_text()


class TestScope:
    def test_parse(self):
        parse = KospexDependencies.parse_scope
        assert parse("direct") == {"direct"}
        assert parse(" dev , Transitive ") == {"dev", "transitive"}
        assert parse("all") == set(KospexDependencies.scope_names())
        assert "unknown" in parse("all")

    @pytest.mark.parametrize("bad", ["", "build", "direct,nope"])
    def test_parse_rejects_and_names_valid_values(self, bad):
        with pytest.raises(ValueError, match="direct"):
            KospexDependencies.parse_scope(bad)

    def test_every_package_use_value_is_selectable(self):
        assert KospexSchema.PACKAGE_USE_VALUES <= set(KospexDependencies.scope_names())

    def test_null_package_use_is_the_unknown_scope(self):
        records = [{"package_use": None}, {"package_use": "direct"}]
        assert KospexDependencies.filter_by_scope(records, {"unknown"}) == [records[0]]

    def test_footer_offers_only_scopes_present(self):
        records = [{"package_use": u} for u in ("direct", "dev", "dev", "peer")]

        footer = KospexDependencies.scope_footer(records, {"direct"})

        assert footer == (
            "sca run with scope [direct]: 1 of 4 packages shown. "
            "Other scopes in this file: dev (2), peer (1). "
            "Use -scope dev,peer or -scope all."
        )

    def test_footer_when_everything_is_shown(self):
        records = [{"package_use": "direct"}]
        assert KospexDependencies.scope_footer(records, {"direct"}) == (
            "sca run with scope [direct]: 1 of 1 packages shown.")


class TestCli:
    def test_default_output(self, cli_deps, tmp_path):
        kospex_cli, _ = cli_deps
        path = tmp_path / "package.json"
        path.write_text(PACKAGE_JSON)

        result = _invoke(kospex_cli, str(path))

        assert result.exit_code == 0, result.output
        lines = result.output.splitlines()
        assert lines[0] == str(path)
        assert lines[1] == "express, 4.18.2"
        assert "jest" not in result.output          # dev is not the default scope
        assert "{" not in result.output             # no dict dump
        assert "Other scopes in this file: dev (2), peer (1)" in result.output
        assert lines[-1] == ("Dependency data not saved to db, use the -save option "
                             "to store these results")

    def test_scope_selects_rows(self, cli_deps, tmp_path):
        kospex_cli, _ = cli_deps
        path = tmp_path / "package.json"
        path.write_text(PACKAGE_JSON)

        result = _invoke(kospex_cli, "-scope", "dev", "-json", str(path))

        assert sorted(r["package_name"] for r in json.loads(result.stdout)) == [
            "eslint", "jest"]

    def test_invalid_scope_exits_1(self, cli_deps, tmp_path):
        kospex_cli, _ = cli_deps
        path = tmp_path / "package.json"
        path.write_text(PACKAGE_JSON)

        assert _invoke(kospex_cli, "-scope", "build", str(path)).exit_code == 1

    def test_json_stdout_is_only_json(self, cli_deps, tmp_path):
        kospex_cli, kd = cli_deps
        path = tmp_path / "package.json"
        path.write_text(PACKAGE_JSON)

        result = _invoke(kospex_cli, "-json", "-malware", str(path))

        assert result.exit_code == 0, result.output
        rows = json.loads(result.stdout)
        assert [r["package_name"] for r in rows] == ["express"]
        assert list(rows[0]) == kd.get_table_field_names()
        assert "deprecated" in result.stderr
        assert "sca run with scope [direct]" in result.stderr
        assert "Dependency data not saved to db" in result.stderr
        assert "not saved" not in result.stdout

    def test_out_writes_csv_of_the_table(self, cli_deps, tmp_path):
        kospex_cli, kd = cli_deps
        path = tmp_path / "package.json"
        path.write_text(PACKAGE_JSON)
        out = tmp_path / "sca.csv"

        result = _invoke(kospex_cli, "-out", str(out), str(path))

        assert result.exit_code == 0, result.output
        with open(out, newline="") as fh:
            reader = csv.DictReader(fh)
            assert reader.fieldnames == kd.get_table_field_names()
            assert [r["package_name"] for r in reader] == ["express"]

    def test_save_is_off_by_default(self, cli_deps, tmp_path, monkeypatch):
        kospex_cli, kd = cli_deps
        repo = _git_repo(tmp_path)
        saved = []
        monkeypatch.setattr(kd, "save_dependencies", lambda *a, **k: saved.append(a))

        result = _invoke(kospex_cli, str(repo / "package.json"))

        assert result.exit_code == 0, result.output
        assert saved == []

    def test_refused_save_never_calls_deps_dev(self, cli_deps, tmp_path, monkeypatch):
        kospex_cli, kd = cli_deps
        path = tmp_path / "package.json"
        path.write_text(PACKAGE_JSON)
        monkeypatch.setattr(kd, "assess", lambda *a, **k: pytest.fail("assess ran"))

        result = _invoke(kospex_cli, "-save", str(path))

        assert result.exit_code == 1
        assert "not inside a git repository" in result.stderr


class TestSavePreconditions:
    def test_clean_synced_repo_passes(self, tmp_path, monkeypatch):
        db = _fresh_db(tmp_path, monkeypatch)
        repo = _git_repo(tmp_path)
        _sync(db, repo)

        assert KospexDependencies(kospex_db=db).save_preconditions(
            str(repo / "package.json")) == []

    def test_repo_not_synced(self, tmp_path, monkeypatch):
        db = _fresh_db(tmp_path, monkeypatch)
        repo = _git_repo(tmp_path)

        problems = KospexDependencies(kospex_db=db).save_preconditions(
            str(repo / "package.json"))

        assert len(problems) == 1
        assert f"repository {REPO_ID} is not in the kospex DB" in problems[0]

    def test_head_not_synced(self, tmp_path, monkeypatch):
        db = _fresh_db(tmp_path, monkeypatch)
        repo = _git_repo(tmp_path)
        _sync(db, repo)
        (repo / "README").write_text("x")
        _git(repo, "add", "README")
        _git(repo, "commit", "-q", "-m", "second")

        problems = KospexDependencies(kospex_db=db).save_preconditions(
            str(repo / "package.json"))

        assert len(problems) == 1
        assert problems[0].startswith("HEAD ")

    def test_modified_tracked_file_anywhere(self, tmp_path, monkeypatch):
        db = _fresh_db(tmp_path, monkeypatch)
        repo = _git_repo(tmp_path)
        _sync(db, repo)
        (repo / "package.json").write_text(PACKAGE_JSON.replace("4.18.2", "4.19.0"))

        problems = KospexDependencies(kospex_db=db).save_preconditions(
            str(repo / "package.json"))

        assert problems == ["the worktree has modified or staged files (commit or stash them)"]

    def test_untracked_target_file(self, tmp_path, monkeypatch):
        db = _fresh_db(tmp_path, monkeypatch)
        repo = _git_repo(tmp_path)
        _sync(db, repo)
        sub = repo / "web"
        sub.mkdir()
        (sub / "package.json").write_text(PACKAGE_JSON)

        problems = KospexDependencies(kospex_db=db).save_preconditions(
            str(sub / "package.json"))

        assert problems == ["web/package.json is not tracked by git (commit it first)"]

    def test_unrelated_untracked_files_do_not_count(self, tmp_path, monkeypatch):
        db = _fresh_db(tmp_path, monkeypatch)
        repo = _git_repo(tmp_path)
        _sync(db, repo)
        (repo / "notes.txt").write_text("scratch")

        assert KospexDependencies(kospex_db=db).save_preconditions(
            str(repo / "package.json")) == []


class TestSaveWritesLikeKrunner:
    def test_removed_package_is_demoted_and_source_stamped(self, tmp_path, monkeypatch):
        """#151: the direct upsert never demoted, so a removed package stayed current."""
        db = _fresh_db(tmp_path, monkeypatch)
        repo = _git_repo(tmp_path)
        kd = _stub_depsdev(KospexDependencies(kospex_db=db))
        kd.assess(str(repo / "package.json"), save=True, source="kospex sca", quiet=True)

        (repo / "package.json").write_text(PACKAGE_JSON.replace(
            '"jest":"29.0.0",', ""))
        _git(repo, "commit", "-q", "-am", "drop jest")
        kd.assess(str(repo / "package.json"), save=True, source="kospex sca", quiet=True)

        current = {r[0] for r in db.execute(
            "SELECT package_name FROM dependency_data WHERE latest = 1").fetchall()}
        sources = {r[0] for r in db.execute(
            "SELECT DISTINCT source FROM dependency_data").fetchall()}
        assert current == {"express", "eslint", "react"}
        assert sources == {"kospex sca"}


class TestFindGitBase:
    def test_a_git_file_marks_a_repo_root(self, tmp_path):
        """Linked worktrees and submodules have a `.git` FILE, not a directory."""
        outer = tmp_path / "outer"
        inner = outer / "inner"
        inner.mkdir(parents=True)
        (outer / ".git").mkdir()
        (inner / ".git").write_text("gitdir: /elsewhere\n")
        (inner / "package.json").write_text("{}")

        assert KospexUtils.find_git_base(str(inner / "package.json")) == str(inner)
