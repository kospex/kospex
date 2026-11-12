"""KOSPEX_DEPS_ENRICH and -enrich/-no-enrich: parse dependencies without lookups.

With enrichment off, `kospex sca` and `krunner osi` parse dependency files and
save the rows, but contact nothing: no deps.dev, no PyPI, no Maven BOM fetch, no
version-list fetch for a range. The rows are saved as `not_checked`, so the web
UI never shows a package nobody looked up as "Up to date".

Precedence: the command-line flag, then the KOSPEX_DEPS_ENRICH environment
variable, then kospex.env, then the default (enrich).
"""
import contextlib
import io
import socket
import subprocess

import pytest
import sqlite_utils
from click.testing import CliRunner

import kospex_schema as KospexSchema
from kospex.habitat_config import HabitatConfig
from kospex_dependencies import KospexDependencies

NOT_CHECKED = "not_checked"


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    home = tmp_path / "kospex_home"
    home.mkdir()
    monkeypatch.setenv("KOSPEX_HOME", str(home))
    monkeypatch.setenv("KOSPEX_DB", str(home / "kospex.db"))
    monkeypatch.delenv("KOSPEX_DEPS_ENRICH", raising=False)
    monkeypatch.delenv("KOSPEX_CONFIG", raising=False)
    HabitatConfig.reset_instance()
    yield home
    HabitatConfig.reset_instance()


@pytest.fixture
def connections(monkeypatch):
    """Every outbound connection attempt, refused and recorded.

    Blocked at the socket, below requests, urllib and the url_cache, so a lookup
    added later by any route is caught without this test knowing about it.
    """
    attempts = []

    def refuse(*args, **kwargs):
        attempts.append(args)
        raise OSError("network disabled in this test")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    return attempts


# --------------------------------------------------------------------------
# The setting


class TestSetting:
    def test_default_is_to_enrich(self):
        assert HabitatConfig.get_instance().deps_enrich is True

    @pytest.mark.parametrize("value", ["false", "False", "0", "no", "off", " false "])
    def test_environment_turns_it_off(self, monkeypatch, value):
        monkeypatch.setenv("KOSPEX_DEPS_ENRICH", value)
        assert HabitatConfig.get_instance().deps_enrich is False

    @pytest.mark.parametrize("value", ["true", "1", "yes", "on"])
    def test_environment_turns_it_on(self, monkeypatch, value):
        monkeypatch.setenv("KOSPEX_DEPS_ENRICH", value)
        assert HabitatConfig.get_instance().deps_enrich is True

    def test_kospex_env_turns_it_off(self, _home):
        (_home / "kospex.env").write_text("KOSPEX_DEPS_ENRICH=false\n")
        assert HabitatConfig.get_instance().deps_enrich is False

    def test_environment_beats_kospex_env(self, _home, monkeypatch):
        (_home / "kospex.env").write_text("KOSPEX_DEPS_ENRICH=false\n")
        monkeypatch.setenv("KOSPEX_DEPS_ENRICH", "true")
        assert HabitatConfig.get_instance().deps_enrich is True

    def test_an_unrecognised_value_is_refused_not_guessed(self, monkeypatch):
        """A typo must not silently pick either answer: one way sends package names out,
        the other quietly drops the advisory data."""
        monkeypatch.setenv("KOSPEX_DEPS_ENRICH", "nope")
        with pytest.raises(ValueError, match="KOSPEX_DEPS_ENRICH"):
            HabitatConfig.get_instance().deps_enrich


class TestDecision:
    def test_no_flag_follows_the_setting(self, monkeypatch):
        monkeypatch.setenv("KOSPEX_DEPS_ENRICH", "false")
        enrich, reason = KospexDependencies.enrich_decision(None)
        assert enrich is False
        assert "KOSPEX_DEPS_ENRICH" in reason

    def test_enrich_flag_overrides_the_setting(self, monkeypatch):
        monkeypatch.setenv("KOSPEX_DEPS_ENRICH", "false")
        assert KospexDependencies.enrich_decision(True)[0] is True

    def test_no_enrich_flag_overrides_the_default(self):
        enrich, reason = KospexDependencies.enrich_decision(False)
        assert enrich is False
        assert "-no-enrich" in reason

    def test_default_enriches(self):
        assert KospexDependencies.enrich_decision(None)[0] is True


class TestInitTemplates:
    def test_kospex_init_template_has_the_setting_commented_out(self):
        import kospex_utils
        lines = kospex_utils.KOSPEX_DEFAULT_ENV.splitlines()
        assert any(l.startswith("#") and "KOSPEX_DEPS_ENRICH=" in l for l in lines)
        assert not any(l.startswith("KOSPEX_DEPS_ENRICH") for l in lines)

    def test_habitat_config_template_has_the_setting_commented_out(self, _home):
        HabitatConfig.get_instance().ensure_directories(create_config=True)
        lines = (_home / "kospex.env").read_text().splitlines()
        assert any(l.startswith("#") and "KOSPEX_DEPS_ENRICH=" in l for l in lines)
        assert not any(l.startswith("KOSPEX_DEPS_ENRICH") for l in lines)


# --------------------------------------------------------------------------
# No lookups, for every parser

FIXTURES = {
    "requirements.txt": "requests==2.31.0\nclick>=8.0\n",
    "pyproject.toml": '[project]\nname="d"\ndependencies=["requests>=2.31.0"]\n',
    "uv.lock": 'version = 1\nrevision = 3\nrequires-python = ">=3.9"\n\n'
               '[[package]]\nname = "app"\nsource = { editable = "." }\n'
               'dependencies = [\n    { name = "requests" },\n]\n\n'
               '[[package]]\nname = "requests"\nversion = "2.32.3"\n'
               'source = { registry = "https://pypi.org/simple" }\n',
    "package.json": '{"name":"d","version":"1.0.0",'
                    '"dependencies":{"express":"^4.18.0","x":"npm:lodash@4.17.21"},'
                    '"devDependencies":{"jest":"~29.0.0"}}',
    "package-lock.json": '{"name":"d","version":"1.0.0","lockfileVersion":3,'
                         '"packages":{"":{"name":"d","dependencies":{"lodash":"^4.17.21"}},'
                         '"node_modules/lodash":{"version":"4.17.21",'
                         '"resolved":"https://registry.npmjs.org/lodash/-/lodash-4.17.21.tgz"}}}',
    "yarn.lock": '# yarn lockfile v1\n\n'
                 'lodash@^4.17.21:\n  version "4.17.21"\n'
                 '  resolved "https://registry.yarnpkg.com/lodash/-/lodash-4.17.21.tgz#679591c5"\n',
    "go.mod": "module x\nrequire (\n  github.com/pkg/errors v0.9.1\n)\n",
    "demo.csproj": '<Project><ItemGroup>'
                   '<PackageReference Include="Serilog" Version="3.1.1" />'
                   '</ItemGroup></Project>',
    # A versionless dependency and an imported BOM: with enrichment on, the BOM
    # is fetched from deps.dev before anything else.
    "pom.xml": '<project><modelVersion>4.0.0</modelVersion>'
               '<groupId>com.example</groupId><artifactId>app</artifactId><version>1.0</version>'
               '<dependencyManagement><dependencies><dependency>'
               '<groupId>org.springframework.boot</groupId>'
               '<artifactId>spring-boot-dependencies</artifactId><version>3.3.0</version>'
               '<type>pom</type><scope>import</scope>'
               '</dependency></dependencies></dependencyManagement>'
               '<dependencies><dependency><groupId>org.slf4j</groupId>'
               '<artifactId>slf4j-api</artifactId></dependency>'
               '<dependency><groupId>junit</groupId><artifactId>junit</artifactId>'
               '<version>[4.12,5.0)</version></dependency></dependencies></project>',
    "libs.versions.toml": '[libraries]\nokio = "com.squareup.okio:okio:3.9.1"\n',
}


def _write(tmp_path, name):
    path = tmp_path / name
    path.write_text(FIXTURES[name])
    return str(path)


def _assess(kd, path, **kw):
    with contextlib.redirect_stdout(io.StringIO()):
        return kd.assess(path, **kw) or []


def _kdeps():
    """Built the way the commands build it, so a lookup takes the real route
    to the network (and the url_cache) rather than failing on a missing query."""
    from kospex_query import KospexQuery
    db = KospexSchema.connect_or_create_kospex_db()
    return KospexDependencies(kospex_db=db, kospex_query=KospexQuery(kospex_db=db))


class TestNoLookups:
    @pytest.mark.parametrize("name", sorted(FIXTURES))
    def test_sca_parses_without_any_connection(self, name, tmp_path, connections):
        records = _assess(_kdeps(), _write(tmp_path, name), enrich=False)

        assert records, f"{name} produced no rows"
        assert connections == []

    @pytest.mark.parametrize("name", sorted(FIXTURES))
    def test_osi_parses_without_any_connection(self, name, tmp_path, connections):
        from krunner import enrich_dependency_records
        kd = _kdeps()
        records = [{**r, "package_type": r.get("package_type")}
                   for r in _assess(kd, _write(tmp_path, name), enrich=False)]

        enrich_dependency_records(records, kd, enrich=False)

        assert connections == []

    def test_the_guard_catches_a_lookup(self, tmp_path, connections):
        """Control: with enrichment on, the same fixture does try to connect,
        so an empty `connections` above means something."""
        _assess(_kdeps(), _write(tmp_path, "requirements.txt"), enrich=True)
        assert connections


class TestUncheckedRows:
    def test_sca_rows_are_marked_not_checked(self, tmp_path, connections):
        records = _assess(_kdeps(), _write(tmp_path, "package.json"), enrich=False)

        for r in records:
            assert r["resolution"] == NOT_CHECKED
            assert r["advisories"] is None
            assert r["versions_behind"] is None
            assert r["resolved_version"] == ""
            assert r.get("last_checked") is None

    def test_sca_rows_keep_what_the_file_says(self, tmp_path, connections):
        """Everything that comes from the file is still recorded."""
        by_name = {r["package_name"]: r for r in _assess(
            _kdeps(), _write(tmp_path, "package.json"), enrich=False)}

        assert by_name["express"]["package_version"] == "^4.18.0"
        assert by_name["express"]["package_use"] == KospexSchema.PACKAGE_USE_DIRECT
        assert by_name["jest"]["package_use"] == KospexSchema.PACKAGE_USE_DEV
        assert by_name["express"]["version_kind"]

    def test_osi_rows_are_marked_not_checked(self):
        from krunner import enrich_dependency_records
        kd = KospexDependencies()

        def no_lookup(*a, **k):
            raise AssertionError("looked up with enrichment off")
        kd.lookup_dependency = no_lookup
        kd.maven_bom_versions = no_lookup

        rows = [{"package_type": "pypi", "package_name": "requests",
                 "package_version": "2.31.0", "requirements_type": "direct"}]
        enrich_dependency_records(rows, kd, enrich=False)

        assert rows[0]["resolution"] == NOT_CHECKED
        assert rows[0]["advisories"] is None
        assert rows[0]["versions_behind"] is None
        assert rows[0]["resolved_version"] == ""
        assert rows[0]["package_use"] == KospexSchema.PACKAGE_USE_DIRECT


# --------------------------------------------------------------------------
# Saving


def _row(**over):
    row = {"_repo_id": "github.com~acme~demo", "hash": "h1", "file_path": "requirements.txt",
           "package_type": "pypi", "package_name": "requests", "package_version": "2.31.0",
           "package_use": "direct"}
    row.update(over)
    return row


LOOKED_UP = {"resolution": "resolved", "advisories": 2, "versions_behind": 5,
             "resolved_version": "2.31.0", "published_at": "2023-05-22",
             "source_repo": "https://github.com/psf/requests", "default": "true"}


def _saved(db):
    return list(db.query(f"SELECT * FROM {KospexSchema.TBL_DEPENDENCY_DATA}"))


class TestSave:
    def test_a_not_checked_row_has_no_last_checked(self):
        db = KospexSchema.connect_or_create_kospex_db()
        KospexDependencies(kospex_db=db).save_dependencies(
            [_row(**KospexDependencies.not_checked_fields())])

        (saved,) = _saved(db)
        assert saved["resolution"] == NOT_CHECKED
        assert saved["last_checked"] is None

    def test_resaving_unchecked_keeps_the_previous_lookup(self):
        """The same file at the same commit has the same key, and the earlier
        lookup still describes it. Parsing with lookups off must not throw that
        away: the row keeps its answer and its old last_checked, so it ages into
        the usual staleness warning instead of becoming "not checked"."""
        db = KospexSchema.connect_or_create_kospex_db()
        kd = KospexDependencies(kospex_db=db)
        kd.save_dependencies([_row(**LOOKED_UP)])
        (before,) = _saved(db)

        kd.save_dependencies([_row(**KospexDependencies.not_checked_fields())])

        (saved,) = _saved(db)
        assert saved["latest"] == 1
        for column in list(LOOKED_UP) + ["last_checked"]:
            assert saved[column] == before[column], column

    def test_a_new_commit_is_not_checked_even_after_an_older_lookup(self):
        """Only the same key keeps its lookup. A new commit may hold different
        content, so its rows are not checked; the old commit's rows keep theirs."""
        db = KospexSchema.connect_or_create_kospex_db()
        kd = KospexDependencies(kospex_db=db)
        kd.save_dependencies([_row(**LOOKED_UP)])

        kd.save_dependencies([_row(hash="h2", **KospexDependencies.not_checked_fields())])

        rows = {r["hash"]: r for r in _saved(db)}
        assert rows["h2"]["latest"] == 1
        assert rows["h2"]["resolution"] == NOT_CHECKED
        assert rows["h2"]["last_checked"] is None
        assert rows["h2"]["advisories"] is None
        assert rows["h1"]["latest"] == 0
        assert rows["h1"]["resolution"] == "resolved"

    def test_a_row_with_no_check_date_is_not_kept_as_a_lookup(self):
        """Rows saved before last_checked existed carry no proof of a lookup.
        Copying them onto a not_checked row would drop the marker and pass old
        counts off as checked."""
        db = KospexSchema.connect_or_create_kospex_db()
        kd = KospexDependencies(kospex_db=db)
        kd.save_dependencies([_row(**LOOKED_UP)])
        db.execute(f"UPDATE {KospexSchema.TBL_DEPENDENCY_DATA} "
                   "SET resolution = NULL, last_checked = NULL")

        kd.save_dependencies([_row(**KospexDependencies.not_checked_fields())])

        (saved,) = _saved(db)
        assert saved["resolution"] == NOT_CHECKED
        assert saved["advisories"] is None

    def test_an_unchecked_row_stays_unchecked(self):
        db = KospexSchema.connect_or_create_kospex_db()
        kd = KospexDependencies(kospex_db=db)
        kd.save_dependencies([_row(**KospexDependencies.not_checked_fields())])
        kd.save_dependencies([_row(**KospexDependencies.not_checked_fields())])

        (saved,) = _saved(db)
        assert saved["resolution"] == NOT_CHECKED
        assert saved["last_checked"] is None

    def test_an_enriched_row_still_gets_last_checked(self):
        db = KospexSchema.connect_or_create_kospex_db()
        KospexDependencies(kospex_db=db).save_dependencies(
            [_row(resolution="resolved", advisories=0, versions_behind=0)])

        (saved,) = _saved(db)
        assert saved["last_checked"]


class TestLookedUpRows:
    """The count krunner osi prints before a run with lookups off."""

    def _query(self, db):
        from kospex_query import KospexQuery
        return KospexQuery(kospex_db=db)

    def test_nothing_looked_up(self):
        db = KospexSchema.connect_or_create_kospex_db()
        KospexDependencies(kospex_db=db).save_dependencies(
            [_row(**KospexDependencies.not_checked_fields())])

        summary = self._query(db).looked_up_dependencies()
        assert summary["rows"] == 0
        assert summary["last_checked"] is None

    def test_counts_current_looked_up_rows(self):
        db = KospexSchema.connect_or_create_kospex_db()
        kd = KospexDependencies(kospex_db=db)
        kd.save_dependencies([_row(**LOOKED_UP)])
        kd.save_dependencies([_row(_repo_id="github.com~acme~other",
                                   **KospexDependencies.not_checked_fields())])

        summary = self._query(db).looked_up_dependencies()
        assert summary["rows"] == 1
        assert summary["last_checked"] == _saved(db)[0]["last_checked"]

    def test_superseded_rows_do_not_count(self):
        db = KospexSchema.connect_or_create_kospex_db()
        kd = KospexDependencies(kospex_db=db)
        kd.save_dependencies([_row(**LOOKED_UP)])
        kd.save_dependencies([_row(hash="h2", **KospexDependencies.not_checked_fields())])

        assert self._query(db).looked_up_dependencies()["rows"] == 0

    def test_scoped_to_a_repo(self):
        db = KospexSchema.connect_or_create_kospex_db()
        kd = KospexDependencies(kospex_db=db)
        kd.save_dependencies([_row(**LOOKED_UP)])

        query = self._query(db)
        assert query.looked_up_dependencies({"repo_id": "github.com~acme~demo"})["rows"] == 1
        assert query.looked_up_dependencies({"repo_id": "github.com~acme~other"})["rows"] == 0


# --------------------------------------------------------------------------
# kweb


class TestWebLabel:
    def test_dependencies_page_says_not_checked_not_up_to_date(self):
        import kweb2
        html = kweb2.templates.get_template("dependencies.html").render({
            "data": [{"package_name": "requests", "package_type": "pypi",
                      "package_version": "2.31.0", "resolution": NOT_CHECKED,
                      "versions_behind": None, "advisories": None}],
            "request": None})

        assert "not checked" in html.lower()
        assert "Up to date" not in html

    def test_status_label(self):
        from kweb2 import UNRESOLVED_STATUS_LABELS
        assert UNRESOLVED_STATUS_LABELS[NOT_CHECKED] == "Not checked"


class TestNoCheckDatePanel:
    """The "No check date recorded" panel on /dependencies/."""

    EXTRACTED = {"github.com~acme~demo": {"examined_at": "2026-11-12T10:00:00",
                                          "files": {"requirements.txt": "extracted"}}}

    def _files(self, rows):
        import kospex_web as KospexWeb
        return KospexWeb.never_checked_files(rows, self.EXTRACTED)

    def test_unchecked_rows_say_lookups_were_off(self):
        (entry,) = self._files([_row(**KospexDependencies.not_checked_fields())])

        assert "lookups off" in entry["reason"]
        assert "did not refresh" not in entry["reason"]

    def test_a_failed_extraction_outranks_lookups_off(self):
        """Rows left from an earlier run with lookups off stay current when the
        file later can't be read. The failure is the reason worth showing."""
        import kospex_web as KospexWeb
        outcomes = {"github.com~acme~demo": {"examined_at": "2026-11-12T10:00:00",
                                             "files": {"requirements.txt": "parse_error"}}}
        (entry,) = KospexWeb.never_checked_files(
            [_row(**KospexDependencies.not_checked_fields())], outcomes)

        assert "could not read" in entry["reason"]

    def test_other_rows_keep_the_extraction_reason(self):
        """A row with no check date that wasn't parsed with lookups off, such as
        one saved before last_checked existed."""
        (entry,) = self._files([_row(resolution="resolved", last_checked=None)])

        assert "did not refresh" in entry["reason"]


# --------------------------------------------------------------------------
# kospex sca


@pytest.fixture
def sca_cli(monkeypatch):
    """kospex_cli with a dependencies object that fails on any lookup."""
    import kospex_cli

    kd = KospexDependencies()
    looked_up = []

    def record(pt, pn, pv):
        looked_up.append(pn)
        return {"package_name": pn, "package_version": pv, "package_type": pt,
                "versions_behind": 0, "advisories": 0, "resolution": "resolved"}
    kd.depsdev_record = record
    kd.get_pypi_source_repo = lambda name: ""
    kd._published_versions = lambda *a: None
    monkeypatch.setattr(kospex_cli.kospex, "dependencies", kd)
    monkeypatch.setattr(kospex_cli, "warn_if_behind", lambda *a, **kw: 0)
    return kospex_cli, looked_up


def _sca(kospex_cli, *args):
    return CliRunner().invoke(kospex_cli.cli, ["sca", *args])


class TestScaCommand:
    def test_no_enrich_lists_packages_without_lookups(self, sca_cli, tmp_path):
        kospex_cli, looked_up = sca_cli
        result = _sca(kospex_cli, "-no-enrich", _write(tmp_path, "requirements.txt"))

        assert result.exit_code == 0, result.output
        assert "requests" in result.stdout
        assert looked_up == []
        assert "-no-enrich" in result.stderr

    def test_the_setting_turns_lookups_off(self, sca_cli, tmp_path, monkeypatch):
        kospex_cli, looked_up = sca_cli
        monkeypatch.setenv("KOSPEX_DEPS_ENRICH", "false")
        result = _sca(kospex_cli, _write(tmp_path, "requirements.txt"))

        assert result.exit_code == 0, result.output
        assert looked_up == []
        assert "KOSPEX_DEPS_ENRICH" in result.stderr
        assert "-enrich" in result.stderr

    def test_enrich_flag_overrides_the_setting(self, sca_cli, tmp_path, monkeypatch):
        kospex_cli, looked_up = sca_cli
        monkeypatch.setenv("KOSPEX_DEPS_ENRICH", "false")
        result = _sca(kospex_cli, "-enrich", _write(tmp_path, "requirements.txt"))

        assert result.exit_code == 0, result.output
        assert "requests" in looked_up

    def test_default_still_looks_up(self, sca_cli, tmp_path):
        kospex_cli, looked_up = sca_cli
        result = _sca(kospex_cli, _write(tmp_path, "requirements.txt"))

        assert result.exit_code == 0, result.output
        assert "requests" in looked_up

    def test_package_lookup_is_refused_when_lookups_are_off(self, sca_cli, monkeypatch):
        kospex_cli, _ = sca_cli
        monkeypatch.setenv("KOSPEX_DEPS_ENRICH", "false")
        result = _sca(kospex_cli, "-package", "npm:left-pad")

        assert result.exit_code != 0
        assert "-enrich" in result.stderr

    def test_a_bad_setting_is_reported(self, sca_cli, tmp_path, monkeypatch):
        kospex_cli, looked_up = sca_cli
        monkeypatch.setenv("KOSPEX_DEPS_ENRICH", "nope")
        result = _sca(kospex_cli, _write(tmp_path, "requirements.txt"))

        assert result.exit_code != 0
        assert "KOSPEX_DEPS_ENRICH" in result.stderr
        assert looked_up == []


# --------------------------------------------------------------------------
# krunner osi

REPO = "github.com~acme~a"


@pytest.fixture
def osi_env(tmp_path, monkeypatch, _home):
    """A file-backed kospex home with one repo carrying one manifest."""
    from kospex.db.migrator import Migrator

    repo_dir = tmp_path / "code" / "a"
    repo_dir.mkdir(parents=True)
    (repo_dir / "requirements.txt").write_text("requests==2.31.0\n")
    monkeypatch.setenv("KOSPEX_CODE", str(tmp_path / "code"))
    HabitatConfig.reset_instance()

    db = KospexSchema.connect_or_create_kospex_db()
    Migrator(db).apply_pending()
    db[KospexSchema.TBL_REPOS].insert(
        {"_repo_id": REPO, "_git_server": "github.com", "_git_owner": "acme",
         "_git_repo": "a", "file_path": str(repo_dir)}, pk="_repo_id")
    db[KospexSchema.TBL_FILE_METADATA].insert(
        {"_repo_id": REPO, "Provider": "requirements.txt", "Filename": "requirements.txt",
         "hash": "h0", "latest": 1, "tech_type": "|dependencies|"},
        pk=["Provider", "hash", "_repo_id"])

    looked_up = []
    monkeypatch.setattr(
        KospexDependencies, "lookup_dependency",
        lambda self, pt, pn, pv, bom_version=None: (
            looked_up.append(pn) or ({"resolution": "resolved", "versions_behind": 0,
                                      "advisories": 0}, pv)))
    monkeypatch.chdir(tmp_path)
    # Wide enough that rich doesn't wrap a log line in the middle of a phrase.
    monkeypatch.setenv("COLUMNS", "400")
    return db, looked_up


def _osi(*args):
    import importlib
    import krunner
    importlib.reload(krunner)
    return CliRunner().invoke(krunner.cli, ["osi", *args])


def _resolutions(db):
    return [r["resolution"] for r in db.query(
        f"SELECT resolution FROM {KospexSchema.TBL_DEPENDENCY_DATA} WHERE latest = 1")]


class TestOsiCommand:
    @pytest.mark.parametrize("scope", [["-all"], ["-next", "5"]])
    def test_no_enrich_saves_unchecked_rows(self, osi_env, scope):
        db, looked_up = osi_env
        result = _osi("-no-enrich", *scope)

        assert result.exit_code == 0, result.output
        assert looked_up == []
        assert _resolutions(db) == [NOT_CHECKED]

    @pytest.mark.parametrize("scope", [["-all"], ["-next", "5"]])
    def test_the_setting_turns_lookups_off(self, osi_env, monkeypatch, scope):
        db, looked_up = osi_env
        monkeypatch.setenv("KOSPEX_DEPS_ENRICH", "false")
        result = _osi(*scope)

        assert result.exit_code == 0, result.output
        assert looked_up == []
        assert "KOSPEX_DEPS_ENRICH" in result.output

    def test_enrich_flag_overrides_the_setting(self, osi_env, monkeypatch):
        db, looked_up = osi_env
        monkeypatch.setenv("KOSPEX_DEPS_ENRICH", "false")
        result = _osi("-enrich", "-all")

        assert result.exit_code == 0, result.output
        assert looked_up == ["requests"]
        assert _resolutions(db) == ["resolved"]

    def test_a_later_enriched_run_replaces_unchecked_rows(self, osi_env):
        db, looked_up = osi_env
        assert _osi("-no-enrich", "-all").exit_code == 0
        assert _osi("-all").exit_code == 0

        assert _resolutions(db) == ["resolved"]

    def test_no_enrich_keeps_and_reports_earlier_lookups(self, osi_env):
        db, looked_up = osi_env
        assert _osi("-all").exit_code == 0

        result = _osi("-no-enrich", "-all")

        assert result.exit_code == 0, result.output
        assert _resolutions(db) == ["resolved"]
        assert "were looked up before" in result.output

    def test_no_enrich_confirms_nothing_was_looked_up(self, osi_env):
        db, looked_up = osi_env
        result = _osi("-no-enrich", "-all")

        assert result.exit_code == 0, result.output
        assert "No current dependency rows" in result.output
        assert "were looked up before" not in result.output

    def test_no_enrich_report_takes_a_scope(self, osi_env):
        db, looked_up = osi_env
        assert _osi("-all").exit_code == 0

        result = _osi("-no-enrich", REPO)

        assert result.exit_code == 0, result.output
        assert "were looked up before" in result.output

    def test_an_enriched_run_says_nothing_about_earlier_lookups(self, osi_env):
        assert _osi("-all").exit_code == 0
        result = _osi("-all")

        assert "looked up before" not in result.output
        assert "No current dependency rows" not in result.output

    @pytest.mark.parametrize("args", [[], ["-next", "5", "-all"]])
    def test_no_report_before_an_argument_error(self, osi_env, args):
        result = _osi("-no-enrich", *args)

        assert result.exit_code != 0
        assert "looked up" not in result.output

    def test_a_failed_report_does_not_stop_the_run(self, osi_env, monkeypatch):
        import sqlite3
        from kospex_query import KospexQuery

        def no_column(self, request_id=None):
            raise sqlite3.OperationalError("no such column: last_checked")
        monkeypatch.setattr(KospexQuery, "looked_up_dependencies", no_column)
        db, looked_up = osi_env

        result = _osi("-no-enrich", "-all")

        assert result.exit_code == 0, result.output
        assert _resolutions(db) == [NOT_CHECKED]
