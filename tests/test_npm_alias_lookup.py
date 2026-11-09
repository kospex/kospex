"""An npm alias is looked up as the package it installs, not by its alias (#350).

`"react-17": "npm:react@^17"` in a package.json installs react under the name
`react-17`. The alias name was looked up on deps.dev as if it were a package:

* nothing called `react-17` exists, so the row was `package_not_found`;
* `string-width-cjs` does exist, unrelated, so its row was `version_yanked`;
* `eslint-plugin-tailwindcss: npm:@hasparus/eslint-plugin-tailwindcss@3.17.5`
  resolved -- against the original package, not the fork that is installed.

All 111 alias rows on a development estate came from package.json. The lockfile
parsers already record the target as its own row.

The lookup now uses the target and its range, so #379's range handling applies
(`^17` -> 17.0.0). The row keeps the alias as package_name -- it is part of the
dependency_data key and is what the project imports -- and the declared
`npm:react@^17` as package_version, which names the target.

Both scan paths are driven (`kospex sca` and `krunner osi`), with the real
deps.dev react record from tests/data/depsdev/.
"""
import json
import pathlib
from urllib.parse import quote

import pytest

import krunner
from kospex.extractors.npmlock import npm_alias_target
from kospex.extractors.registry import REGISTRY
from kospex_dependencies import KospexDependencies

DATA = pathlib.Path(__file__).parent / "data" / "depsdev"
BASE = "https://api.deps.dev/v3alpha/systems/npm/packages/"


class _Query:
    """deps.dev from fixtures, recording every URL asked for.

    `packages` maps a name to the fixture serving it. Anything else 404s, as
    the real endpoint does for a package that does not exist.
    """

    def __init__(self, packages):
        self.listings = {
            name: (DATA / fixture).read_text() for name, fixture in packages.items()}
        self.urls = []

    def url_request_with_status(self, url, **_kwargs):
        self.urls.append(url)
        for name, listing in self.listings.items():
            package_url = BASE + quote(name, safe="")
            if url == package_url:
                return listing, 200
            if url.startswith(package_url + "/versions/"):
                version = url[len(package_url + "/versions/"):]
                if version in {v["versionKey"]["version"]
                               for v in json.loads(listing)["versions"]}:
                    return json.dumps({
                        "versionKey": {"system": "NPM", "name": name, "version": version},
                        "publishedAt": "2026-01-01T00:00:00Z"}), 200
        return None, 404

    def url_request(self, url, **_kwargs):
        return self.url_request_with_status(url)[0]

    def asked_about(self, name):
        package_url = BASE + quote(name, safe="")
        return [u for u in self.urls if u == package_url or u.startswith(package_url + "/")]


def _row(name, declared):
    return {"package_name": name, "package_version": declared, "package_type": "npm",
            "requirements_type": "direct", "file_path": "package.json",
            "_repo_id": "github.com~acme~app"}


def _osi(query, name, declared):
    kdeps = KospexDependencies(kospex_query=query)
    return krunner.enrich_dependency_records([_row(name, declared)], kdeps)[0]


def _sca(query, name, declared):
    kdeps = KospexDependencies(kospex_query=query)
    extractor = next(e for e in REGISTRY if e.package_type == "npm")
    return kdeps._enrich_dependency_records([_row(name, declared)], extractor)[0]


PATHS = pytest.mark.parametrize("scan", [_osi, _sca], ids=["osi", "sca"])


@PATHS
@pytest.mark.parametrize("alias, declared, expected", [
    # several majors side by side: a range, resolved at its lowest published version
    ("react-17", "npm:react@^17", "17.0.0"),
    # a pinned alias
    ("react-16-8", "npm:react@16.8.0", "16.8.0"),
])
def test_an_alias_is_looked_up_as_its_target(scan, alias, declared, expected):
    query = _Query({"react": "npm__react.json"})

    row = scan(query, alias, declared)

    assert row["resolution"] == "resolved"
    assert row["resolved_version"] == expected
    assert query.asked_about(alias) == []


@PATHS
def test_the_row_keeps_the_alias_and_the_declared_text(scan):
    """package_name and package_version are the dependency_data key, so they
    must stay what the manifest says, or a re-scan inserts a second row."""
    row = scan(_Query({"react": "npm__react.json"}), "react-17", "npm:react@^17")

    assert row["package_name"] == "react-17"
    assert row["package_version"] == "npm:react@^17"


@PATHS
def test_an_alias_named_like_an_unrelated_package_is_not_yanked(scan):
    """`string-width-cjs` exists on npm, unrelated, so the old lookup found the
    package, 404'd the version, and recorded version_yanked. Here an unrelated
    package is served under the alias name; it must not be consulted."""
    query = _Query({"react": "npm__react.json", "react-17": "npm___types_react.json"})

    row = scan(query, "react-17", "npm:react@^17")

    assert row["resolution"] == "resolved"
    assert query.asked_about("react-17") == []


@PATHS
def test_a_fork_under_the_original_name_is_looked_up_as_the_fork(scan):
    """`eslint-plugin-tailwindcss: npm:@hasparus/eslint-plugin-tailwindcss@...`
    resolved against the original package, so the row looked clean with the
    wrong package's advisories. Here the alias name is a real package; only the
    scoped target may be asked about."""
    query = _Query({"@types/react": "npm___types_react.json",
                    "react": "npm__react.json"})

    row = scan(query, "react", "npm:@types/react@^18")

    assert row["resolution"] == "resolved"
    assert query.asked_about("react") == []
    assert query.asked_about("@types/react") != []


@PATHS
def test_an_ordinary_dependency_is_unchanged(scan):
    query = _Query({"react": "npm__react.json"})

    row = scan(query, "react", "^17")

    assert row["resolution"] == "resolved"
    assert row["resolved_version"] == "17.0.0"


class TestNpmAliasTarget:

    @pytest.mark.parametrize("spec, expected", [
        ("npm:react@^17", ("react", "^17")),
        ("npm:string-width@^4.2.0", ("string-width", "^4.2.0")),
        ("npm:@hasparus/eslint-plugin-tailwindcss@3.17.5",
         ("@hasparus/eslint-plugin-tailwindcss", "3.17.5")),
        ("npm:typescript@5.0.x", ("typescript", "5.0.x")),
    ])
    def test_reads_the_target(self, spec, expected):
        assert npm_alias_target(spec) == expected

    @pytest.mark.parametrize("spec", [
        "^17", "react@^17", "workspace:*", "npm:", "npm:react", "npm:@scope/pkg",
        "npm:@^1", "npm:.hidden@1.0.0", None, 17,
    ])
    def test_anything_else_is_none(self, spec):
        assert npm_alias_target(spec) is None
