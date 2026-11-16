"""KOSPEX_DEPS_PRIVATE and GOPRIVATE: package names that are never looked up.

An organisation's own packages are named after it -- a Maven groupId under its
reverse domain, a Go module under its Git host -- and those names mean nothing
to a public index. KOSPEX_DEPS_PRIVATE lists them as `ecosystem:prefix`
entries; GOPRIVATE, Go's own setting, is honoured too. A matching name is
never sent anywhere, whatever -enrich or KOSPEX_DEPS_ENRICH say, and its rows
are saved as `private_skipped`.
"""
import contextlib
import io

import pytest
import requests
from click.testing import CliRunner

import kospex_schema as KospexSchema
from kospex.habitat_config import HabitatConfig
from kospex.private_packages import (
    PrivatePackages, goprivate_match, parse_private_prefixes,
)
from kospex_dependencies import KospexDependencies

PRIVATE = "private_skipped"
NOT_CHECKED = "not_checked"


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    home = tmp_path / "kospex_home"
    home.mkdir()
    monkeypatch.setenv("KOSPEX_HOME", str(home))
    monkeypatch.setenv("KOSPEX_DB", str(home / "kospex.db"))
    for name in ("KOSPEX_DEPS_ENRICH", "KOSPEX_DEPS_PRIVATE", "GOPRIVATE", "KOSPEX_CONFIG"):
        monkeypatch.delenv(name, raising=False)
    HabitatConfig.reset_instance()
    yield home
    HabitatConfig.reset_instance()


@pytest.fixture
def sent(monkeypatch):
    """Every URL requested over HTTP, answered 404 without a connection.

    Patched at requests' Session, below requests.get and the url_cache, so a
    lookup added later by any route is caught without this test knowing it."""
    urls = []

    def record(self, method, url, *args, **kwargs):
        urls.append(url)
        response = requests.Response()
        response.status_code = 404
        response._content = b""
        response.url = url
        return response

    monkeypatch.setattr(requests.sessions.Session, "request", record)
    return urls


# --------------------------------------------------------------------------
# The setting


class TestParsing:
    def test_entries(self):
        assert parse_private_prefixes("maven:au.com.acme, go:github.com/acme") == (
            ("maven", "au.com.acme"), ("go", "github.com/acme"))

    @pytest.mark.parametrize("raw", ["", "  ", None, ",", " , "])
    def test_empty_means_none(self, raw):
        assert parse_private_prefixes(raw) == ()

    def test_a_trailing_comma_is_ignored(self):
        assert parse_private_prefixes("maven:au.com.acme,") == (("maven", "au.com.acme"),)

    def test_gradle_is_maven(self):
        assert parse_private_prefixes("gradle:au.com.acme") == (("maven", "au.com.acme"),)

    def test_ecosystem_case_is_ignored(self):
        assert parse_private_prefixes("Maven:au.com.acme") == (("maven", "au.com.acme"),)

    def test_split_on_the_first_colon(self):
        assert parse_private_prefixes("maven:au.com.acme:platform-bom") == (
            ("maven", "au.com.acme:platform-bom"),)

    @pytest.mark.parametrize("raw", ["au.com.acme", "maven:", "maven: ", ":au.com.acme"])
    def test_a_malformed_entry_is_refused(self, raw):
        with pytest.raises(ValueError, match="KOSPEX_DEPS_PRIVATE"):
            parse_private_prefixes(raw)

    @pytest.mark.parametrize("ecosystem", ["npm", "pypi", "nuget", "cargo"])
    def test_an_unsupported_ecosystem_is_refused_not_ignored(self, ecosystem):
        with pytest.raises(ValueError, match="not supported"):
            parse_private_prefixes(f"{ecosystem}:acme")

    @pytest.mark.parametrize("raw", ["go:github.com/acme/*", "go:*.acme.internal",
                                     "maven:au.com.acme.*", "go:github.com/acme/li?",
                                     "go:github.com/[ab]cme"])
    def test_a_glob_is_refused_not_taken_literally(self, raw):
        """A literal `*` would never match, so the setting would look set and
        protect nothing. Globs belong in GOPRIVATE."""
        with pytest.raises(ValueError, match="GOPRIVATE"):
            parse_private_prefixes(raw)

    def test_the_error_names_the_entry(self):
        with pytest.raises(ValueError, match="npm:@acme"):
            parse_private_prefixes("maven:au.com.acme,npm:@acme")


class TestSetting:
    def test_default_is_none(self):
        assert not HabitatConfig.get_instance().deps_private

    def test_environment(self, monkeypatch):
        monkeypatch.setenv("KOSPEX_DEPS_PRIVATE", "maven:au.com.acme")
        assert HabitatConfig.get_instance().deps_private.prefixes == (("maven", "au.com.acme"),)

    def test_kospex_env(self, _home):
        (_home / "kospex.env").write_text("KOSPEX_DEPS_PRIVATE=go:github.com/acme\n")
        assert HabitatConfig.get_instance().deps_private.prefixes == (("go", "github.com/acme"),)

    def test_environment_beats_kospex_env(self, _home, monkeypatch):
        (_home / "kospex.env").write_text("KOSPEX_DEPS_PRIVATE=go:github.com/acme\n")
        monkeypatch.setenv("KOSPEX_DEPS_PRIVATE", "maven:au.com.acme")
        assert HabitatConfig.get_instance().deps_private.prefixes == (("maven", "au.com.acme"),)

    def test_goprivate_from_the_environment(self, monkeypatch):
        monkeypatch.setenv("GOPRIVATE", "*.acme.internal")
        assert HabitatConfig.get_instance().deps_private.goprivate == ("*.acme.internal",)

    def test_a_bad_value_is_refused(self, monkeypatch):
        monkeypatch.setenv("KOSPEX_DEPS_PRIVATE", "npm:@acme")
        with pytest.raises(ValueError):
            HabitatConfig.get_instance().deps_private

    def test_kospex_init_template_has_the_setting_commented_out(self):
        import kospex_utils
        lines = kospex_utils.KOSPEX_DEFAULT_ENV.splitlines()
        assert any(l.startswith("#") and "KOSPEX_DEPS_PRIVATE=" in l for l in lines)
        assert not any(l.startswith("KOSPEX_DEPS_PRIVATE") for l in lines)

    def test_habitat_config_template_has_the_setting_commented_out(self, _home):
        HabitatConfig.get_instance().ensure_directories(create_config=True)
        lines = (_home / "kospex.env").read_text().splitlines()
        assert any(l.startswith("#") and "KOSPEX_DEPS_PRIVATE=" in l for l in lines)
        assert not any(l.startswith("KOSPEX_DEPS_PRIVATE") for l in lines)


# --------------------------------------------------------------------------
# Matching


def _private(spec, goprivate=""):
    return PrivatePackages(parse_private_prefixes(spec),
                           tuple(p for p in goprivate.split(",") if p))


class TestMavenMatching:
    @pytest.mark.parametrize("name", [
        "au.com.acme:core",
        "au.com.acme.platform:auth",
        "AU.COM.ACME:Core",
        "au.com.acme.conventions:au.com.acme.conventions.gradle.plugin",
    ])
    def test_matches(self, name):
        assert _private("maven:au.com.acme").match("maven", name)

    @pytest.mark.parametrize("name", [
        "au.com.acmeinsurance:x",
        "au.com:acme",
        "org.slf4j:slf4j-api",
        "com.au.acme:x",
    ])
    def test_does_not_match(self, name):
        assert not _private("maven:au.com.acme").match("maven", name)

    def test_one_artifact(self):
        private = _private("maven:au.com.acme:platform-bom")
        assert private.match("maven", "au.com.acme:platform-bom")
        assert not private.match("maven", "au.com.acme:core")
        assert not private.match("maven", "au.com.acme:platform-bom-extra")

    def test_package_type_case_is_ignored(self):
        assert _private("maven:au.com.acme").match("Maven", "au.com.acme:core")

    def test_another_ecosystem_is_not_matched(self):
        assert not _private("maven:au.com.acme").match("go", "au.com.acme:core")

    def test_the_reason_names_the_entry(self):
        assert _private("maven:au.com.acme").match("maven", "au.com.acme:core") == \
            "KOSPEX_DEPS_PRIVATE maven:au.com.acme"


class TestGoMatching:
    @pytest.mark.parametrize("name", [
        "github.com/acme",
        "github.com/acme/lib",
        "github.com/acme/lib/v2",
        "GitHub.com/Acme/lib",
    ])
    def test_matches(self, name):
        assert _private("go:github.com/acme").match("go", name)

    @pytest.mark.parametrize("name", ["github.com/acmecorp/x", "github.com/pkg/errors",
                                      "github.com"])
    def test_does_not_match(self, name):
        assert not _private("go:github.com/acme").match("go", name)

    def test_a_trailing_slash_in_the_prefix(self):
        assert _private("go:github.com/acme/").match("go", "github.com/acme/lib")


class TestGoprivate:
    @pytest.mark.parametrize("patterns, name", [
        ("*.acme.internal", "git.acme.internal/x/y"),
        ("github.com/acme/*", "github.com/acme/lib"),
        ("github.com/acme/*", "github.com/acme/lib/v2"),
        ("github.com/acme", "github.com/acme/lib"),
        ("github.com/other,github.com/acme", "github.com/acme/lib"),
        ("GitHub.com/Acme", "github.com/acme/lib"),
    ])
    def test_matches(self, patterns, name):
        assert goprivate_match(patterns.split(","), name)

    @pytest.mark.parametrize("patterns, name", [
        ("*.acme.internal", "github.com/acme/lib"),
        ("github.com/acme/*", "github.com/acme"),
        ("github.com/acme", "github.com/acmecorp/x"),
        ("", "github.com/acme/lib"),
    ])
    def test_does_not_match(self, patterns, name):
        assert not goprivate_match(patterns.split(","), name)

    def test_applies_to_go_only(self):
        private = _private("", goprivate="github.com/acme")
        assert private.match("go", "github.com/acme/lib") == "GOPRIVATE github.com/acme"
        assert not private.match("maven", "github.com/acme/lib")


class TestLookupPermitted:
    def test_nothing_configured(self):
        assert KospexDependencies.lookup_permitted("maven", "au.com.acme:core") == (True, "")

    def test_a_private_name(self, monkeypatch):
        monkeypatch.setenv("KOSPEX_DEPS_PRIVATE", "maven:au.com.acme")
        assert KospexDependencies.lookup_permitted("maven", "au.com.acme:core") == (
            False, "KOSPEX_DEPS_PRIVATE maven:au.com.acme")

    def test_goprivate(self, monkeypatch):
        monkeypatch.setenv("GOPRIVATE", "github.com/acme")
        assert KospexDependencies.lookup_permitted("go", "github.com/acme/lib")[0] is False


# --------------------------------------------------------------------------
# Nothing private leaves the machine

POM = ('<project><modelVersion>4.0.0</modelVersion>'
       '<groupId>au.com.acme</groupId><artifactId>app</artifactId><version>1.0</version>'
       '<dependencyManagement><dependencies><dependency>'
       '<groupId>au.com.acme</groupId><artifactId>acme-bom</artifactId><version>1.0</version>'
       '<type>pom</type><scope>import</scope>'
       '</dependency></dependencies></dependencyManagement>'
       '<dependencies>'
       '<dependency><groupId>au.com.acme</groupId><artifactId>core</artifactId></dependency>'
       '<dependency><groupId>au.com.acme.platform</groupId><artifactId>auth</artifactId>'
       '<version>2.1</version></dependency>'
       '<dependency><groupId>org.slf4j</groupId><artifactId>slf4j-api</artifactId>'
       '<version>2.0.9</version></dependency>'
       '</dependencies></project>')

BUILD_GRADLE = """plugins {
    id 'au.com.acme.conventions' version '1.0'
}
dependencies {
    implementation platform('au.com.acme:acme-platform:1.0')
    implementation 'au.com.acme:core'
    implementation 'com.squareup.okio:okio:3.9.1'
}
"""

CATALOG = ('[libraries]\nokio = "com.squareup.okio:okio:3.9.1"\n'
           '[plugins]\nacme = { id = "au.com.acme.conventions", version = "1.0" }\n')

GO_MOD = ("module github.com/acme/svc\n\ngo 1.22\n\nrequire (\n"
          "  github.com/acme/lib v1.2.0\n  git.acme.internal/x/y v0.1.0\n"
          "  github.com/pkg/errors v0.9.1\n)\n")

# file name -> (content, public names that must still be looked up)
FILES = {
    "pom.xml": (POM, ["slf4j-api"]),
    "build.gradle": (BUILD_GRADLE, ["okio"]),
    "libs.versions.toml": (CATALOG, ["okio"]),
    "go.mod": (GO_MOD, ["errors"]),
}


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("KOSPEX_DEPS_PRIVATE", "maven:au.com.acme,go:github.com/acme")
    monkeypatch.setenv("GOPRIVATE", "*.acme.internal")


def _write(tmp_path, name):
    path = tmp_path / name
    path.write_text(FILES[name][0])
    return str(path)


def _kdeps():
    """Built the way the commands build it, so a lookup takes the real route
    to the network (and the url_cache)."""
    from kospex_query import KospexQuery
    db = KospexSchema.connect_or_create_kospex_db()
    return KospexDependencies(kospex_db=db, kospex_query=KospexQuery(kospex_db=db))


def _assess(kd, path, **kw):
    with contextlib.redirect_stdout(io.StringIO()):
        return kd.assess(path, **kw) or []


def _leaks(urls):
    return [u for u in urls if "acme" in u.lower()]


class TestNothingLeaves:
    @pytest.mark.parametrize("name", sorted(FILES))
    def test_sca(self, name, tmp_path, sent, configured):
        records = _assess(_kdeps(), _write(tmp_path, name), enrich=True)

        assert records
        assert _leaks(sent) == []
        for public in FILES[name][1]:
            assert any(public in u for u in sent), f"{public} was not looked up"

    @pytest.mark.parametrize("name", sorted(FILES))
    def test_osi(self, name, tmp_path, sent, configured):
        from krunner import enrich_dependency_records
        kd = _kdeps()
        records = _assess(kd, _write(tmp_path, name), enrich=False)
        sent.clear()

        enrich_dependency_records(records, kd, enrich=True)

        assert _leaks(sent) == []
        for public in FILES[name][1]:
            assert any(public in u for u in sent), f"{public} was not looked up"

    def test_control_without_the_setting_names_are_sent(self, tmp_path, sent):
        """With nothing configured the same file does send the names, so an
        empty _leaks() above means something."""
        _assess(_kdeps(), _write(tmp_path, "pom.xml"), enrich=True)
        assert _leaks(sent)


class TestGuards:
    """Each outbound method refuses a private name itself, so a caller that
    skips lookup_dependency() still sends nothing."""

    CALLS = [
        ("deps_dev", ("maven", "au.com.acme:core", "1.0")),
        ("deps_dev_status", ("maven", "au.com.acme:core", "1.0")),
        ("deps_dev_package", ("maven", "au.com.acme:core")),
        ("_published_versions", ("maven", "au.com.acme:core")),
        ("release_list", ("maven", "au.com.acme:core")),
        ("get_versions_behind", ("maven", "au.com.acme:core", "1.0")),
        ("_bom_managed_versions", ("au.com.acme:acme-bom", "1.0")),
        ("depsdev_record", ("maven", "au.com.acme:core", "1.0")),
        ("get_depsdev_info", ("maven", "au.com.acme:core", "1.0")),
        ("package_dependencies", ("au.com.acme:core", "1.0", "maven")),
        ("deps_dev_package", ("go", "github.com/acme/lib")),
        ("check_malware", ("maven", "au.com.acme:core", "key")),
    ]

    @pytest.mark.parametrize("method, args", CALLS, ids=[c[0] for c in CALLS])
    @pytest.mark.parametrize("with_query", [True, False])
    def test_refused(self, method, args, with_query, sent, configured):
        kd = _kdeps() if with_query else KospexDependencies()
        with contextlib.redirect_stdout(io.StringIO()):
            getattr(kd, method)(*args)
        assert sent == []

    def test_a_private_malware_check_is_not_reported_clean(self, sent, configured):
        assert KospexDependencies().check_malware("maven", "au.com.acme:core", "key") is None

    @pytest.mark.parametrize("method", ["get_pypi_source_repo", "get_pypi_package_info"])
    def test_pypi_is_checked_too(self, method, sent, monkeypatch):
        # No pypi prefixes in this release; the guard is checked through the
        # function every outbound method calls.
        monkeypatch.setattr(KospexDependencies, "lookup_permitted",
                            staticmethod(lambda t, n: (False, "test")))
        getattr(_kdeps(), method)("acme-internal")
        assert sent == []


# --------------------------------------------------------------------------
# Rows


class TestRows:
    def test_sca_rows(self, tmp_path, sent, configured):
        by_name = {r["package_name"]: r for r in _assess(
            _kdeps(), _write(tmp_path, "pom.xml"), enrich=True)}

        row = by_name["au.com.acme.platform:auth"]
        assert row["resolution"] == PRIVATE
        assert row["advisories"] is None
        assert row["versions_behind"] is None
        assert row["resolved_version"] == ""
        assert row.get("last_checked") is None
        assert row["package_version"] == "2.1"
        assert by_name["org.slf4j:slf4j-api"]["resolution"] != PRIVATE

    def test_sca_rows_with_lookups_off(self, tmp_path, sent, configured):
        by_name = {r["package_name"]: r for r in _assess(
            _kdeps(), _write(tmp_path, "pom.xml"), enrich=False)}

        assert by_name["au.com.acme.platform:auth"]["resolution"] == PRIVATE
        assert by_name["org.slf4j:slf4j-api"]["resolution"] == NOT_CHECKED

    @pytest.mark.parametrize("enrich", [True, False])
    def test_osi_rows(self, enrich, sent, configured):
        from krunner import enrich_dependency_records
        rows = [{"package_type": "go", "package_name": "github.com/acme/lib",
                 "package_version": "v1.2.0", "requirements_type": "direct"},
                {"package_type": "go", "package_name": "github.com/pkg/errors",
                 "package_version": "v0.9.1", "requirements_type": "direct"}]

        enrich_dependency_records(rows, _kdeps(), enrich=enrich)

        assert rows[0]["resolution"] == PRIVATE
        assert rows[0]["advisories"] is None
        assert rows[0]["resolved_version"] == ""
        assert rows[1]["resolution"] != PRIVATE


def _row(**over):
    row = {"_repo_id": "github.com~acme~a", "hash": "h1", "file_path": "pom.xml",
           "package_type": "maven", "package_name": "au.com.acme:core",
           "package_version": "1.0", "requirements_type": "direct"}
    row.update(over)
    return row


def _saved(db):
    return list(db.query(f"SELECT * FROM {KospexSchema.TBL_DEPENDENCY_DATA} WHERE latest = 1"))


class TestSave:
    def test_a_private_row_has_no_last_checked(self):
        kd = _kdeps()
        kd.save_dependencies([_row(**KospexDependencies.private_fields())])

        (row,) = _saved(kd.kospex_db)
        assert row["resolution"] == PRIVATE
        assert row["last_checked"] is None

    def test_a_private_row_does_not_keep_an_earlier_lookup(self):
        kd = _kdeps()
        kd.save_dependencies([_row(resolution="resolved", advisories=2, versions_behind=1,
                                   resolved_version="1.0")])
        kd.save_dependencies([_row(**KospexDependencies.private_fields())])

        (row,) = _saved(kd.kospex_db)
        assert row["resolution"] == PRIVATE
        assert row["advisories"] is None
        assert row["last_checked"] is None


# --------------------------------------------------------------------------
# kospex sca


class TestScaCommand:
    def test_a_bad_setting_is_reported(self, tmp_path, monkeypatch, sent):
        import kospex_cli
        monkeypatch.setenv("KOSPEX_DEPS_PRIVATE", "npm:@acme")
        result = CliRunner().invoke(kospex_cli.cli, ["sca", _write(tmp_path, "pom.xml")])

        assert result.exit_code != 0
        assert "KOSPEX_DEPS_PRIVATE" in result.stderr
        assert sent == []

    def test_says_how_many_packages_are_private(self, tmp_path, sent, configured):
        import kospex_cli
        result = CliRunner().invoke(kospex_cli.cli, ["sca", _write(tmp_path, "pom.xml")])

        assert result.exit_code == 0, result.output
        assert "3 packages are private (KOSPEX_DEPS_PRIVATE / GOPRIVATE) and were not " \
            "looked up." in result.stderr
        assert _leaks(sent) == []

    def test_says_nothing_when_none_are_private(self, tmp_path, sent):
        import kospex_cli
        result = CliRunner().invoke(kospex_cli.cli, ["sca", _write(tmp_path, "pom.xml")])

        assert result.exit_code == 0, result.output
        assert "private" not in result.stderr

    def test_package_lookup_of_a_private_name_is_refused(self, monkeypatch, sent, configured):
        import kospex_cli
        result = CliRunner().invoke(kospex_cli.cli,
                                    ["sca", "-package", "maven:au.com.acme:core"])

        assert result.exit_code != 0
        assert "private" in result.stderr.lower()
        assert _leaks(sent) == []


# --------------------------------------------------------------------------
# krunner osi

REPO = "github.com~acme~a"


@pytest.fixture
def osi_env(tmp_path, monkeypatch, _home, sent):
    """A file-backed kospex home with one repo carrying a pom.xml."""
    from kospex.db.migrator import Migrator

    repo_dir = tmp_path / "code" / "a"
    repo_dir.mkdir(parents=True)
    (repo_dir / "pom.xml").write_text(POM)
    monkeypatch.setenv("KOSPEX_CODE", str(tmp_path / "code"))
    HabitatConfig.reset_instance()

    db = KospexSchema.connect_or_create_kospex_db()
    Migrator(db).apply_pending()
    db[KospexSchema.TBL_REPOS].insert(
        {"_repo_id": REPO, "_git_server": "github.com", "_git_owner": "acme",
         "_git_repo": "a", "file_path": str(repo_dir)}, pk="_repo_id")
    db[KospexSchema.TBL_FILE_METADATA].insert(
        {"_repo_id": REPO, "Provider": "pom.xml", "Filename": "pom.xml",
         "hash": "h0", "latest": 1, "tech_type": "|dependencies|"},
        pk=["Provider", "hash", "_repo_id"])
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("COLUMNS", "400")
    return db


def _osi(*args):
    import importlib
    import krunner
    importlib.reload(krunner)
    return CliRunner().invoke(krunner.cli, ["osi", *args])


def _by_name(db):
    return {r["package_name"]: r for r in _saved(db)}


def _looked_up_before(db, name, when="2026-11-02T08:14:00+00:00"):
    db.execute(f"UPDATE {KospexSchema.TBL_DEPENDENCY_DATA} SET last_checked = ?, "
               "resolution = 'resolved' WHERE package_name = ?", [when, name])
    db.conn.commit()


class TestOsiCommand:
    @pytest.mark.parametrize("enrich", ["-enrich", "-no-enrich"])
    def test_private_rows_are_saved_private(self, osi_env, configured, enrich):
        result = _osi(enrich, "-all")

        assert result.exit_code == 0, result.output
        rows = _by_name(osi_env)
        assert rows["au.com.acme.platform:auth"]["resolution"] == PRIVATE
        assert rows["org.slf4j:slf4j-api"]["resolution"] != PRIVATE

    def test_reports_the_configured_prefixes(self, osi_env, configured):
        result = _osi("-enrich", "-all")

        assert result.exit_code == 0, result.output
        assert "Private packages: 2 prefixes (maven 1, go 1) from KOSPEX_DEPS_PRIVATE" \
            in result.output
        assert "GOPRIVATE set" in result.output

    def test_says_nothing_private_was_looked_up(self, osi_env, configured):
        result = _osi("-enrich", "-all")

        assert "No current rows matching a private prefix have been looked up." \
            in result.output

    @pytest.mark.parametrize("enrich", ["-enrich", "-no-enrich"])
    def test_warns_about_private_rows_looked_up_before(self, osi_env, monkeypatch, enrich):
        assert _osi("-no-enrich", "-all").exit_code == 0
        _looked_up_before(osi_env, "au.com.acme.platform:auth")
        monkeypatch.setenv("KOSPEX_DEPS_PRIVATE", "maven:au.com.acme")

        result = _osi(enrich, "-all")

        assert result.exit_code == 0, result.output
        assert ("Warning: 1 current dependency rows in this scope match a private prefix "
                "and were looked up before (most recently 2026-11-02T08:14:00+00:00 UTC)."
                in result.output)

    def test_the_warning_counts_only_private_rows(self, osi_env, monkeypatch):
        assert _osi("-no-enrich", "-all").exit_code == 0
        _looked_up_before(osi_env, "org.slf4j:slf4j-api")
        monkeypatch.setenv("KOSPEX_DEPS_PRIVATE", "maven:au.com.acme")

        result = _osi("-no-enrich", "-all")

        assert "No current rows matching a private prefix have been looked up." \
            in result.output

    def test_silent_when_nothing_is_configured(self, osi_env):
        result = _osi("-no-enrich", "-all")

        assert result.exit_code == 0, result.output
        assert "Private packages" not in result.output
        assert "private prefix" not in result.output

    def test_next_reports_too(self, osi_env, configured):
        result = _osi("-enrich", "-next", "5")

        assert result.exit_code == 0, result.output
        assert "Private packages: 2 prefixes" in result.output

    def test_a_bad_setting_stops_the_run(self, osi_env, monkeypatch):
        monkeypatch.setenv("KOSPEX_DEPS_PRIVATE", "npm:@acme")
        result = _osi("-enrich", "-all")

        assert result.exit_code != 0
        assert "KOSPEX_DEPS_PRIVATE" in result.output
        assert _saved(osi_env) == []


# --------------------------------------------------------------------------
# Web UI


class TestWebLabel:
    def test_dependencies_page_says_private(self):
        import kweb2
        html = kweb2.templates.get_template("dependencies.html").render({
            "data": [{"package_name": "au.com.acme:core", "package_type": "maven",
                      "package_version": "1.0", "resolution": PRIVATE,
                      "versions_behind": None, "advisories": None}],
            "request": None})

        assert "private" in html.lower()
        assert "Up to date" not in html

    def test_status_label(self):
        from kweb2 import UNRESOLVED_STATUS_LABELS
        assert UNRESOLVED_STATUS_LABELS[PRIVATE] == "Private"


class TestNoCheckDatePanel:
    """The "No check date recorded" panel on /dependencies/."""

    EXTRACTED = {"github.com~acme~a": {"examined_at": "2026-11-12T10:00:00",
                                       "files": {"pom.xml": "extracted"}}}

    def _reason(self, rows):
        import kospex_web as KospexWeb
        (entry,) = KospexWeb.never_checked_files(rows, self.EXTRACTED)
        return entry["reason"]

    def test_private_rows_say_private(self):
        reason = self._reason([_row(**KospexDependencies.private_fields())])

        assert "KOSPEX_DEPS_PRIVATE" in reason
        assert "lookups off" not in reason
        assert "did not refresh" not in reason

    def test_private_and_unchecked_rows_say_lookups_off(self):
        reason = self._reason([
            _row(**KospexDependencies.private_fields()),
            _row(package_name="org.slf4j:slf4j-api", **KospexDependencies.not_checked_fields())])

        assert "lookups off" in reason


class TestSupplyChainPage:
    def test_a_private_package_is_not_looked_up(self, sent, configured):
        from fastapi.testclient import TestClient
        import kweb2
        response = TestClient(kweb2.app, base_url="http://localhost").get(
            "/supply-chain/", params={"package": "go:github.com/acme/lib:v1.2.0"})

        assert response.status_code == 200
        assert "is private" in response.text
        assert _leaks(sent) == []

    def test_a_bad_setting_is_shown_not_a_server_error(self, sent, monkeypatch):
        from fastapi.testclient import TestClient
        import kweb2
        monkeypatch.setenv("KOSPEX_DEPS_PRIVATE", "npm:@acme")
        response = TestClient(kweb2.app, base_url="http://localhost").get(
            "/supply-chain/", params={"package": "go:github.com/pkg/errors:v0.9.1"})

        assert response.status_code == 200
        assert "KOSPEX_DEPS_PRIVATE" in response.text
        assert sent == []
