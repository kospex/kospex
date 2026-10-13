"""Tests for the package-lock.json extractor.

Mirrors tests/test_extractors_pnpm.py: template contract, helper-level tests,
extract-level tests per lockfileVersion, then error handling.

The fixtures are built from dicts and json.dumps'd rather than written as JSON
string literals, so a key name appears once and cannot drift between the
fixture and the assertion.
"""
import json
import os

import pytest

from kospex.extractors.npmlock import (
    UnreadableLockfile,
    extract_package_lock,
    _package_name_from_key,
    _template,
)


def _write(tmp_path, doc, name="package-lock.json"):
    p = tmp_path / name
    p.write_text(json.dumps(doc))
    return str(p)


def _by_name(records):
    return {r["package_name"]: r for r in records}


# A v3 lockfile exercising every structural case at once: the root entry, a
# direct dep, a dev dep, a scoped name, and a nested transitive.
V3 = {
    "name": "acme-app",
    "lockfileVersion": 3,
    "requires": True,
    "packages": {
        "": {
            "name": "acme-app",
            "version": "1.0.0",
            "license": "MIT",
            "dependencies": {"lodash": "^4.17.21"},
            "devDependencies": {"mocha": "^10.0.0"},
        },
        "node_modules/lodash": {
            "version": "4.17.21",
            "resolved": "https://registry.npmjs.org/lodash/-/lodash-4.17.21.tgz",
            "integrity": "sha512-xxx",
            "license": "MIT",
        },
        "node_modules/mocha": {"version": "10.2.0", "dev": True},
        "node_modules/@babel/core": {"version": "7.21.3"},
        "node_modules/findup-sync/node_modules/glob": {"version": "7.1.6"},
    },
}


class TestTemplateContract:
    """The local record template must stay in sync with KospexDependencies."""

    def test_template_matches_get_package_template(self):
        from kospex_dependencies import KospexDependencies
        assert set(_template().keys()) == set(
            KospexDependencies().get_package_template().keys()
        )


class TestPackageNameFromKey:
    """packages: keys are install paths, not names."""

    def test_plain_dependency(self):
        assert _package_name_from_key("node_modules/lodash") == "lodash"

    def test_scoped_dependency_keeps_its_scope(self):
        assert _package_name_from_key("node_modules/@babel/core") == "@babel/core"

    def test_nested_key_yields_the_innermost_package(self):
        # A transitive installed under its parent. The name is the last
        # segment, not the path -- otherwise the row is named
        # "findup-sync/node_modules/glob" and matches nothing in deps.dev.
        assert _package_name_from_key(
            "node_modules/findup-sync/node_modules/glob"
        ) == "glob"

    def test_deeply_nested_scoped(self):
        assert _package_name_from_key(
            "node_modules/a/node_modules/@scope/b"
        ) == "@scope/b"

    def test_root_entry_has_no_package_name(self):
        assert _package_name_from_key("") is None

    def test_workspace_importer_has_no_package_name(self):
        # A local workspace dir, not an installed package: no node_modules/.
        assert _package_name_from_key("apps/desktop") is None


class TestAliasedDependencies:
    """`npm i foo@npm:bar` installs bar at the path node_modules/foo.

    npm records the real package in a `name` field on the entry, so taking the
    name from the install path records a package that does not exist. The
    deps.dev lookup then returns nothing and the row carries no advisories --
    the SCA result is silently clean for whatever is actually installed.

    This is not hypothetical: 30 entries across the reference estate are
    aliases, and every entry that carries a `name` has one that differs from
    its path.
    """

    def test_the_real_package_name_is_recorded_not_the_alias(self, tmp_path):
        doc = {
            "lockfileVersion": 3,
            "packages": {
                "": {"name": "root", "version": "1.0.0"},
                "node_modules/string-width-cjs": {
                    "name": "string-width",
                    "version": "4.2.3",
                    "resolved": "https://registry.npmjs.org/string-width/-/"
                                "string-width-4.2.3.tgz",
                },
            },
        }
        records = extract_package_lock(_write(tmp_path, doc))
        # The target is what matters: without it, deps.dev is never asked about
        # the package that is actually installed. The install path is recorded
        # too -- see TestEveryIdentityIsRecorded.
        assert "string-width" in {r["package_name"] for r in records}

    def test_a_scoped_alias_target_keeps_its_scope(self, tmp_path):
        doc = {
            "lockfileVersion": 3,
            "packages": {
                "": {"name": "root", "version": "1.0.0"},
                "node_modules/react-loadable": {
                    "name": "@docusaurus/react-loadable",
                    "version": "6.0.0",
                    "resolved": "https://registry.npmjs.org/@docusaurus/"
                                "react-loadable/-/react-loadable-6.0.0.tgz",
                },
            },
        }
        records = extract_package_lock(_write(tmp_path, doc))
        assert "@docusaurus/react-loadable" in {
            r["package_name"] for r in records}

    def test_a_name_matching_the_path_is_unchanged(self, tmp_path):
        doc = {
            "lockfileVersion": 3,
            "packages": {
                "": {"name": "root", "version": "1.0.0"},
                "node_modules/lodash": {"name": "lodash", "version": "4.17.21"},
            },
        }
        records = extract_package_lock(_write(tmp_path, doc))
        assert records[0]["package_name"] == "lodash"

    def test_a_useless_name_falls_back_to_the_path(self, tmp_path):
        # Don't trust the field blindly: the file is attacker-controlled, and an
        # empty or non-string name must not produce a nameless row, which would
        # be an unusable primary key.
        for bad in ("", None, 42, {"not": "a string"}):
            doc = {
                "lockfileVersion": 3,
                "packages": {
                    "": {"name": "root", "version": "1.0.0"},
                    "node_modules/lodash": {"name": bad, "version": "4.17.21"},
                },
            }
            records = extract_package_lock(_write(tmp_path, doc))
            assert [r["package_name"] for r in records] == ["lodash"], bad

    def test_an_alias_and_its_target_at_one_version_are_one_package(self, tmp_path):
        # node_modules/string-width-cjs and
        # node_modules/wrap-ansi-cjs/node_modules/string-width are both
        # string-width@4.2.3 -- the same package by two routes, so one row.
        doc = {
            "lockfileVersion": 3,
            "packages": {
                "": {"name": "root", "version": "1.0.0"},
                "node_modules/string-width-cjs": {
                    "name": "string-width", "version": "4.2.3", "dev": True,
                    "resolved": "https://registry.npmjs.org/string-width/-/"
                                "string-width-4.2.3.tgz"},
                "node_modules/wrap-ansi-cjs/node_modules/string-width": {
                    "version": "4.2.3"},
            },
        }
        records = _by_name(extract_package_lock(_write(tmp_path, doc)))
        assert records["string-width"]["is_dev"] is False, \
            "one non-dev route means it ships"

    def test_a_different_version_of_the_alias_target_stays_separate(self, tmp_path):
        doc = {
            "lockfileVersion": 3,
            "packages": {
                "": {"name": "root", "version": "1.0.0"},
                "node_modules/string-width-cjs": {
                    "name": "string-width", "version": "4.2.3",
                    "resolved": "https://registry.npmjs.org/string-width/-/"
                                "string-width-4.2.3.tgz"},
                "node_modules/string-width": {"version": "5.1.2"},
            },
        }
        records = extract_package_lock(_write(tmp_path, doc))
        pairs = {(r["package_name"], r["package_version"]) for r in records}
        assert ("string-width", "4.2.3") in pairs
        assert ("string-width", "5.1.2") in pairs, \
            "a different version is a different package, not a duplicate"

    def test_v1_records_an_alias_in_the_version_string(self, tmp_path):
        # v1 has no `name` field, so it encodes the alias as
        # version: "npm:<name>@<version>". Storing that verbatim gives a
        # package_version that is not a version at all.
        doc = {
            "lockfileVersion": 1,
            "dependencies": {
                "foo": {"version": "npm:left-pad@1.2.3"},
            },
        }
        records = extract_package_lock(_write(tmp_path, doc))
        assert ("left-pad", "1.2.3") in {
            (r["package_name"], r["package_version"]) for r in records}

    def test_v1_alias_to_a_scoped_package(self, tmp_path):
        doc = {
            "lockfileVersion": 1,
            "dependencies": {
                "helmet": {"version": "npm:@slorber/react-helmet-async@1.3.0"},
            },
        }
        records = extract_package_lock(_write(tmp_path, doc))
        assert ("@slorber/react-helmet-async", "1.3.0") in {
            (r["package_name"], r["package_version"]) for r in records}

    def test_v1_a_normal_version_is_untouched(self, tmp_path):
        doc = {
            "lockfileVersion": 1,
            "dependencies": {"lodash": {"version": "4.17.21"}},
        }
        records = extract_package_lock(_write(tmp_path, doc))
        assert [(r["package_name"], r["package_version"]) for r in records] == [
            ("lodash", "4.17.21"),
        ]

    def test_an_aliased_direct_dependency_is_still_direct(self, tmp_path):
        # The root declares the ALIAS ("string-width-cjs": "npm:string-width@…"),
        # so resolving the row to the real name must not lose the fact that the
        # project declared it. Membership is tested against both names.
        doc = {
            "lockfileVersion": 3,
            "packages": {
                "": {"name": "root", "version": "1.0.0",
                     "dependencies": {"string-width-cjs": "npm:string-width@^4.2.3"}},
                "node_modules/string-width-cjs": {
                    "name": "string-width", "version": "4.2.3",
                    "resolved": "https://registry.npmjs.org/string-width/-/"
                                "string-width-4.2.3.tgz"},
            },
        }
        records = _by_name(extract_package_lock(_write(tmp_path, doc)))
        assert records["string-width"]["requirements_type"] == "direct"

    def test_an_aliased_dev_dependency_is_still_dev(self, tmp_path):
        doc = {
            "lockfileVersion": 3,
            "packages": {
                "": {"name": "root", "version": "1.0.0",
                     "devDependencies": {"sw-cjs": "npm:string-width@^4.2.3"}},
                "node_modules/sw-cjs": {
                    "name": "string-width", "version": "4.2.3",
                    "resolved": "https://registry.npmjs.org/string-width/-/"
                                "string-width-4.2.3.tgz"},
            },
        }
        records = extract_package_lock(_write(tmp_path, doc))
        assert records[0]["requirements_type"] == "dev"

    def test_v1_a_malformed_npm_alias_is_still_reported(self, tmp_path):
        # "npm:" with nothing parseable after it names no package kospex can
        # look up. It is recorded anyway, under the name the file declares:
        # dropping it let `version: "npm:anything"` hide a dependency, and an
        # un-enrichable row is still visible to a reader.
        # See TestNothingIsHiddenByAFlag for the evasion this prevents.
        doc = {
            "lockfileVersion": 1,
            "dependencies": {
                "broken": {"version": "npm:"},
                "alsobroken": {"version": "npm:noversion"},
                "lodash": {"version": "4.17.21"},
            },
        }
        records = extract_package_lock(_write(tmp_path, doc))
        assert sorted(r["package_name"] for r in records) == [
            "alsobroken", "broken", "lodash",
        ]


class TestEveryIdentityIsRecorded:
    """No field of a lockfile vouches for another, so none is trusted over
    another: every identity an entry asserts is recorded.

    The previous cut corroborated `name` against the `resolved` URL. That was a
    conceptual error -- both fields come from the same untrusted document, so
    agreement between them is not evidence, and the URL's host was never checked
    either. Any rule that picks one field as authoritative is spoofable in
    whichever direction it picks.

    Recording all of them is not: a package cannot be hidden, because the
    install path is always recorded, and a real alias target is always recorded
    too, so deps.dev is asked about it. The cost is a row for a name that may
    not exist on the registry -- a visible, dismissible false positive instead
    of an invisible false negative.

    Measured cost on the reference estate: ~32 extra rows out of 13,707, because
    12,687 of 12,689 entries with a tarball URL already agree with their path.
    """

    def _doc(self, meta, path="node_modules/evil-pkg"):
        return {
            "lockfileVersion": 3,
            "packages": {
                "": {"name": "root", "version": "1.0.0"},
                path: dict(meta, version=meta.get("version", "1.2.3")),
            },
        }

    def test_agreement_produces_one_row(self, tmp_path):
        # The overwhelmingly common case: 12,687 entries in the estate.
        records = extract_package_lock(_write(tmp_path, self._doc(
            {"resolved":
             "https://registry.npmjs.org/evil-pkg/-/evil-pkg-1.2.3.tgz"})))
        assert [r["package_name"] for r in records] == ["evil-pkg"]

    def test_a_real_alias_records_both_the_path_and_the_target(self, tmp_path):
        # The alias target is what matters for advisories; the path is what is
        # on disk. Recording both means neither can be used to hide the other.
        records = extract_package_lock(_write(tmp_path, self._doc(
            {"name": "string-width",
             "resolved": "https://registry.npmjs.org/string-width/-/"
                         "string-width-1.2.3.tgz"},
            path="node_modules/string-width-cjs")))
        assert sorted(r["package_name"] for r in records) == [
            "string-width", "string-width-cjs",
        ]

    def test_a_url_naming_another_package_is_also_recorded(self, tmp_path):
        # Measured in the wild: two entries in malware test fixtures have
        # node_modules/express-exp resolving to the `exit` tarball. Recording
        # only the path would never ask deps.dev about `exit`.
        records = extract_package_lock(_write(tmp_path, self._doc(
            {"resolved": "https://registry.npmjs.org/exit/-/exit-1.2.3.tgz"},
            path="node_modules/express-exp")))
        assert sorted(r["package_name"] for r in records) == [
            "exit", "express-exp",
        ]

    def test_a_spoofed_name_cannot_hide_the_install_path(self, tmp_path):
        # Claim a benign package; the path is recorded regardless.
        records = extract_package_lock(_write(tmp_path, self._doc(
            {"name": "lodash",
             "resolved": "https://registry.npmjs.org/lodash/-/lodash-1.2.3.tgz"})))
        assert "evil-pkg" in {r["package_name"] for r in records}

    def test_an_untrusted_host_needs_no_allowlist(self, tmp_path):
        # The URL host was never validated, which made corroboration bypassable.
        # It no longer matters: the URL only ADDS an identity, so a hostile host
        # cannot displace the install path. This also keeps private registries
        # (Artifactory, GitHub Packages) working, which an allowlist would break.
        records = extract_package_lock(_write(tmp_path, self._doc(
            {"name": "lodash",
             "resolved": "https://evil.example.com/lodash/-/lodash-1.2.3.tgz"})))
        assert sorted(r["package_name"] for r in records) == ["evil-pkg", "lodash"]

    def test_an_implausible_name_is_not_recorded_as_an_identity(self, tmp_path):
        # Still not a free-for-all: package_name reaches a primary key and
        # exports, so a value that cannot be an npm package name is dropped as
        # an identity while the path survives.
        for bad in ("=cmd|' /C calc'!A0", "has space", "x" * 215, ".dot"):
            records = extract_package_lock(_write(tmp_path, self._doc(
                {"name": bad})))
            assert [r["package_name"] for r in records] == ["evil-pkg"], bad

    def test_an_aliased_direct_dependency_is_direct_under_both_names(self, tmp_path):
        doc = {
            "lockfileVersion": 3,
            "packages": {
                "": {"name": "root", "version": "1.0.0",
                     "dependencies": {"sw-cjs": "npm:string-width@^4.2.3"}},
                "node_modules/sw-cjs": {
                    "name": "string-width", "version": "4.2.3",
                    "resolved": "https://registry.npmjs.org/string-width/-/"
                                "string-width-4.2.3.tgz"},
            },
        }
        records = extract_package_lock(_write(tmp_path, doc))
        assert {r["requirements_type"] for r in records} == {"direct"}


class TestAMissingVersionCannotHideAnEntry:
    """Omitting one key removed a package from the scan entirely.

    `version` is part of the dependency_data primary key, so a version-less row
    cannot be written -- but silently skipping means `node_modules/evil-pkg`
    with its `version` deleted simply does not exist as far as kospex is
    concerned, while `resolved` and `integrity` still tell npm what to fetch.

    Measured: of the 13 version-less entries in the estate, all 13 are
    `link: true` symlinks to workspace directories. A non-link entry without a
    version does not occur naturally, so treating it as anomalous costs nothing.
    """

    def test_the_version_is_recovered_from_the_tarball_url(self, tmp_path):
        doc = {
            "lockfileVersion": 3,
            "packages": {
                "": {"name": "root", "version": "1.0.0"},
                "node_modules/evil-pkg": {
                    "resolved":
                        "https://registry.npmjs.org/evil-pkg/-/evil-pkg-1.2.3.tgz",
                    "integrity": "sha512-xxx"},
                "node_modules/another-pkg": {"version": "2.0.0"},
            },
        }
        records = _by_name(extract_package_lock(_write(tmp_path, doc)))
        assert "evil-pkg" in records
        assert records["evil-pkg"]["package_version"] == "1.2.3"

    def test_a_prerelease_version_is_recovered_whole(self, tmp_path):
        doc = {
            "lockfileVersion": 3,
            "packages": {
                "": {"name": "root", "version": "1.0.0"},
                "node_modules/evil-pkg": {
                    "resolved": "https://registry.npmjs.org/evil-pkg/-/"
                                "evil-pkg-1.0.0-beta.1.tgz"},
            },
        }
        records = extract_package_lock(_write(tmp_path, doc))
        assert records[0]["package_version"] == "1.0.0-beta.1"

    def test_v1_recovers_the_version_too(self, tmp_path):
        doc = {
            "lockfileVersion": 1,
            "dependencies": {
                "evil-pkg": {
                    "resolved":
                        "https://registry.npmjs.org/evil-pkg/-/evil-pkg-1.2.3.tgz"},
                "another-pkg": {"version": "2.0.0"},
            },
        }
        records = _by_name(extract_package_lock(_write(tmp_path, doc)))
        assert sorted(records) == ["another-pkg", "evil-pkg"]
        assert records["evil-pkg"]["package_version"] == "1.2.3"

    def test_a_real_link_entry_is_still_not_a_package(self, tmp_path):
        # All 13 in the estate: {link, resolved} with a relative path, no
        # version to recover. These are workspace symlinks, not fetched
        # packages.
        doc = {
            "lockfileVersion": 3,
            "packages": {
                "": {"name": "root", "version": "1.0.0"},
                "node_modules/@acme/shared": {"link": True,
                                              "resolved": "apps/shared"},
                "node_modules/lodash": {"version": "4.17.21"},
            },
        }
        records = extract_package_lock(_write(tmp_path, doc))
        assert [r["package_name"] for r in records] == ["lodash"]

    def test_an_entry_with_no_version_anywhere_is_reported(self, tmp_path):
        # Nothing to recover and nothing to install from. Logged rather than
        # written, because package_version is part of the primary key -- but the
        # file is flagged so the gap is not silent.
        doc = {
            "lockfileVersion": 3,
            "packages": {
                "": {"name": "root", "version": "1.0.0"},
                "node_modules/evil-pkg": {"integrity": "sha512-xxx"},
                "node_modules/lodash": {"version": "4.17.21"},
            },
        }
        records = extract_package_lock(_write(tmp_path, doc))
        assert [r["package_name"] for r in records] == ["lodash"]


class TestNothingIsHiddenByAFlag:
    """A single key must not remove a package from the scan.

    Each case below is a package that npm would install and that an earlier cut
    of this parser dropped silently. A dropped row is invisible; a row kospex
    cannot enrich is at least visible, so these record rather than skip.
    """

    def test_a_link_entry_that_has_a_version_is_still_recorded(self, tmp_path):
        # Real link: entries carry only {link, resolved} -- all 13 in the
        # reference estate have no version. One WITH a version is anomalous, and
        # skipping it would let `link: true` hide any dependency.
        doc = {
            "lockfileVersion": 3,
            "packages": {
                "": {"name": "root", "version": "1.0.0"},
                "node_modules/evil-pkg": {"version": "1.2.3", "link": True},
                "node_modules/lodash": {"version": "4.17.21"},
            },
        }
        records = extract_package_lock(_write(tmp_path, doc))
        assert sorted(r["package_name"] for r in records) == ["evil-pkg", "lodash"]

    def test_a_real_link_entry_is_still_skipped(self, tmp_path):
        # Unchanged: no version means no primary key, and these are symlinks to
        # workspace directories rather than fetched packages.
        doc = {
            "lockfileVersion": 3,
            "packages": {
                "": {"name": "root", "version": "1.0.0"},
                "node_modules/@acme/shared": {"link": True, "resolved": "apps/shared"},
                "node_modules/lodash": {"version": "4.17.21"},
            },
        }
        records = extract_package_lock(_write(tmp_path, doc))
        assert [r["package_name"] for r in records] == ["lodash"]

    def test_v1_an_unparseable_npm_alias_is_recorded_not_dropped(self, tmp_path):
        # Previously dropped, which let `version: "npm:anything"` hide a
        # package. Recorded under its declared name with the version npm wrote,
        # so it is visible even though it cannot be looked up.
        doc = {
            "lockfileVersion": 1,
            "dependencies": {
                "evil-pkg": {"version": "npm:whatever"},
                "lodash": {"version": "4.17.21"},
            },
        }
        records = _by_name(extract_package_lock(_write(tmp_path, doc)))
        assert sorted(records) == ["evil-pkg", "lodash"]
        assert records["evil-pkg"]["package_version"] == "npm:whatever"

    def test_v2_a_package_only_in_the_legacy_tree_is_not_lost(self, tmp_path):
        # npm 6 reads `dependencies:` from a v2 lockfile, so a package present
        # only there is installed by a real client but was invisible to kospex.
        doc = {
            "lockfileVersion": 2,
            "packages": {
                "": {"name": "root", "version": "1.0.0"},
                "node_modules/lodash": {"version": "4.17.21"},
            },
            "dependencies": {
                "lodash": {"version": "4.17.21"},
                "evil-pkg": {"version": "1.2.3"},
            },
        }
        records = extract_package_lock(_write(tmp_path, doc))
        assert sorted(r["package_name"] for r in records) == ["evil-pkg", "lodash"]

    def test_v2_the_legacy_tree_does_not_double_count_or_alter_flags(self, tmp_path):
        # The duplicate is still not merged: `packages:` owns the flags for any
        # package it lists, because the legacy tree cannot express
        # hasInstallScript and would flip an aggregate computed with all().
        doc = {
            "lockfileVersion": 2,
            "packages": {
                "": {"name": "root", "version": "1.0.0"},
                "node_modules/lodash": {
                    "version": "4.17.21", "dev": True, "hasInstallScript": True},
            },
            "dependencies": {
                "lodash": {"version": "4.17.21"},
            },
        }
        records = extract_package_lock(_write(tmp_path, doc))
        assert len(records) == 1
        assert records[0]["is_dev"] is True, "the legacy tree must not clear dev"
        assert records[0]["runs_install_script"] is True


class TestLockfileVersionIsReadLiberally:
    """npm writes an integer, but a parser that only accepts one fails open.

    `lockfileVersion: "3"` produced zero rows and an `empty` outcome -- a
    one-character edit that hid every dependency behind a result meaning
    "examined and genuinely had nothing to find".
    """

    def _doc(self, lv):
        return {
            "lockfileVersion": lv,
            "packages": {
                "": {"name": "root", "version": "1.0.0"},
                "node_modules/lodash": {"version": "4.17.21"},
            },
        }

    def test_a_string_version_is_accepted(self, tmp_path):
        assert len(extract_package_lock(_write(tmp_path, self._doc("3")))) == 1

    def test_a_string_version_one_selects_the_v1_reader(self, tmp_path):
        doc = {"lockfileVersion": "1",
               "dependencies": {"lodash": {"version": "4.17.21"}}}
        records = extract_package_lock(_write(tmp_path, doc))
        assert [r["package_name"] for r in records] == ["lodash"]
        assert records[0]["runs_install_script"] is None, "read as v1, not v3"

    def test_a_float_version_is_accepted(self, tmp_path):
        assert len(extract_package_lock(_write(tmp_path, self._doc(3.0)))) == 1

    def test_a_padded_string_version_is_accepted(self, tmp_path):
        assert len(extract_package_lock(_write(tmp_path, self._doc(" 3 ")))) == 1

    def test_a_genuinely_unknown_version_is_reported_as_unreadable(self, tmp_path):
        # Not guessed at, and not passed off as empty -- see
        # TestUnreadableFilesAreNotReportedAsEmpty.
        for lv in (4, "abc", None, True, 3.5):
            with pytest.raises(UnreadableLockfile):
                extract_package_lock(_write(tmp_path, self._doc(lv)))


class TestWhichEntriesBecomeRows:
    """Three kinds of packages: entry are not installed third-party packages."""

    def test_a_normal_entry_becomes_one_row(self, tmp_path):
        records = _by_name(extract_package_lock(_write(tmp_path, V3)))
        assert records["lodash"]["package_version"] == "4.17.21"
        assert records["lodash"]["ecosystem"] == "npm"

    def test_every_installed_package_becomes_a_row(self, tmp_path):
        records = extract_package_lock(_write(tmp_path, V3))
        assert sorted(r["package_name"] for r in records) == [
            "@babel/core", "glob", "lodash", "mocha",
        ]

    def test_the_root_entry_is_not_a_row(self, tmp_path):
        # The "" entry is the project itself. It carries a name and version and
        # would otherwise be recorded as a dependency on itself.
        records = extract_package_lock(_write(tmp_path, V3))
        assert "acme-app" not in {r["package_name"] for r in records}

    def test_a_workspace_importer_is_not_a_row(self, tmp_path):
        doc = {
            "lockfileVersion": 3,
            "packages": {
                "": {"name": "root", "version": "1.0.0"},
                "apps/desktop": {"name": "desktop", "version": "2.0.0",
                                 "dependencies": {"lodash": "^4.0.0"}},
                "node_modules/lodash": {"version": "4.17.21"},
            },
        }
        records = extract_package_lock(_write(tmp_path, doc))
        assert [r["package_name"] for r in records] == ["lodash"]

    def test_a_link_entry_is_not_a_row(self, tmp_path):
        # link:true entries are symlinks back at a workspace dir. Every one of
        # the 13 in the reference estate has no `version` key at all, and
        # package_version is part of the dependency_data primary key -- so a row
        # from one could never be updated, only duplicated.
        doc = {
            "lockfileVersion": 3,
            "packages": {
                "": {"name": "root", "version": "1.0.0"},
                "node_modules/@acme/shared": {"link": True, "resolved": "apps/shared"},
                "node_modules/lodash": {"version": "4.17.21"},
            },
        }
        records = extract_package_lock(_write(tmp_path, doc))
        assert [r["package_name"] for r in records] == ["lodash"]

    def test_an_entry_without_a_version_is_not_a_row(self, tmp_path):
        # Belt and braces for the same primary-key reason, for any future
        # version-less shape that is not flagged as a link.
        doc = {
            "lockfileVersion": 3,
            "packages": {
                "": {"name": "root", "version": "1.0.0"},
                "node_modules/mystery": {"resolved": "somewhere"},
                "node_modules/lodash": {"version": "4.17.21"},
            },
        }
        records = extract_package_lock(_write(tmp_path, doc))
        assert [r["package_name"] for r in records] == ["lodash"]


class TestPackageUse:
    """requirements_type -> the DB package_use value, as the scanners map it."""

    def test_a_root_dependency_is_direct(self, tmp_path):
        records = _by_name(extract_package_lock(_write(tmp_path, V3)))
        assert records["lodash"]["requirements_type"] == "direct"

    def test_a_root_dev_dependency_is_dev(self, tmp_path):
        records = _by_name(extract_package_lock(_write(tmp_path, V3)))
        assert records["mocha"]["requirements_type"] == "dev"

    def test_anything_else_is_resolved(self, tmp_path):
        records = _by_name(extract_package_lock(_write(tmp_path, V3)))
        assert records["@babel/core"]["requirements_type"] == "resolved"
        assert records["glob"]["requirements_type"] == "resolved"

    def test_a_workspace_importers_dependencies_count_as_direct(self, tmp_path):
        # Same rule as pnpm's _collect_direct_dev: a dep declared by any
        # importer is direct to the repository, not transitive.
        doc = {
            "lockfileVersion": 3,
            "packages": {
                "": {"name": "root", "version": "1.0.0"},
                "apps/desktop": {
                    "name": "desktop", "version": "2.0.0",
                    "dependencies": {"lodash": "^4.0.0"},
                    "devDependencies": {"mocha": "^10.0.0"},
                },
                "node_modules/lodash": {"version": "4.17.21"},
                "node_modules/mocha": {"version": "10.2.0"},
            },
        }
        records = _by_name(extract_package_lock(_write(tmp_path, doc)))
        assert records["lodash"]["requirements_type"] == "direct"
        assert records["mocha"]["requirements_type"] == "dev"


def _one(tmp_path, meta, lockfile_version=3):
    """Extract a single-package v2/v3 lockfile and return its one record."""
    doc = {
        "lockfileVersion": lockfile_version,
        "packages": {
            "": {"name": "root", "version": "1.0.0"},
            "node_modules/thing": dict(meta, version="1.0.0"),
        },
    }
    records = extract_package_lock(_write(tmp_path, doc))
    assert len(records) == 1, records
    return records[0]


class TestInstallStateV2V3:
    """packages: entries answer all three axes, so absence means no, not unknown.

    npm omits each of these keys when false, so a v2/v3 lockfile genuinely
    answers the question for every entry -- unlike v1, and unlike pnpm v9.
    """

    def test_no_flags_is_false_not_null(self, tmp_path):
        rec = _one(tmp_path, {})
        assert rec["is_dev"] is False
        assert rec["is_optional"] is False
        assert rec["runs_install_script"] is False
        assert rec["declared_scope"] is None

    def test_dev(self, tmp_path):
        rec = _one(tmp_path, {"dev": True})
        assert rec["is_dev"] is True
        assert rec["is_optional"] is False
        assert rec["declared_scope"] == "dev"

    def test_optional(self, tmp_path):
        rec = _one(tmp_path, {"optional": True})
        assert rec["is_optional"] is True
        assert rec["is_dev"] is False
        assert rec["declared_scope"] == "optional"

    def test_has_install_script(self, tmp_path):
        rec = _one(tmp_path, {"hasInstallScript": True})
        assert rec["runs_install_script"] is True
        assert rec["declared_scope"] == "hasInstallScript"

    def test_dev_and_optional_together(self, tmp_path):
        # Measured: this combination occurs 555 times across the reference
        # estate, so dev and optional are NOT mutually exclusive in npm.
        rec = _one(tmp_path, {"dev": True, "optional": True})
        assert rec["is_dev"] is True
        assert rec["is_optional"] is True
        assert rec["declared_scope"] == "dev,optional"


class TestDevOptional:
    """npm's third state, which is not the union of the other two.

    Measured across the reference estate: `devOptional` appears on 71 entries
    and never co-occurs with `dev` or `optional`, while `dev` and `optional` do
    co-occur 555 times. So npm sets exactly one of the three, and devOptional
    means "reachable only via a dev path OR an optional path" without
    resolving which.
    """

    def test_dev_optional_is_dev(self, tmp_path):
        # It definitely does not reach production.
        rec = _one(tmp_path, {"devOptional": True})
        assert rec["is_dev"] is True

    def test_dev_optional_leaves_optionality_unknown(self, tmp_path):
        # NULL, not False: npm has not said whether it may fail to install.
        # False would assert "we checked and it always installs".
        rec = _one(tmp_path, {"devOptional": True})
        assert rec["is_optional"] is None

    def test_dev_optional_records_the_raw_key(self, tmp_path):
        # The lossy projection above stays auditable: a reader can tell this
        # from a plain `dev` entry, which is a different claim.
        rec = _one(tmp_path, {"devOptional": True})
        assert rec["declared_scope"] == "devOptional"

    def test_dev_optional_still_answers_install_script(self, tmp_path):
        rec = _one(tmp_path, {"devOptional": True})
        assert rec["runs_install_script"] is False

    def test_dev_optional_with_an_install_script(self, tmp_path):
        # This exact combination occurs once in the reference estate.
        rec = _one(tmp_path, {"devOptional": True, "hasInstallScript": True})
        assert rec["runs_install_script"] is True
        assert rec["declared_scope"] == "devOptional,hasInstallScript"


class TestFlagsWithNoColumn:
    """peer and inBundle have no boolean column, so declared_scope carries them.

    Recording the raw key keeps them queryable without inventing a column per
    flag, and keeps declared_scope a complete audit trail of what npm said
    rather than only the part that projected cleanly.
    """

    def test_peer_is_recorded_in_declared_scope(self, tmp_path):
        rec = _one(tmp_path, {"peer": True})
        assert rec["declared_scope"] == "peer"

    def test_peer_does_not_become_a_package_use(self, tmp_path):
        # PACKAGE_USE_PEER exists but neither scanner's _REQ_TO_USE maps it,
        # and the two disagree on the fallback (direct vs ""). Routing new data
        # through that would mean silently wrong package_use values.
        rec = _one(tmp_path, {"peer": True})
        assert rec["requirements_type"] == "resolved"

    def test_in_bundle_is_recorded_in_declared_scope(self, tmp_path):
        rec = _one(tmp_path, {"inBundle": True})
        assert rec["declared_scope"] == "inBundle"

    def test_scope_keys_are_sorted_so_the_value_is_stable(self, tmp_path):
        rec = _one(tmp_path, {"peer": True, "dev": True, "optional": True})
        assert rec["declared_scope"] == "dev,optional,peer"


def _two_paths(tmp_path, meta_a, meta_b, name="chalk", version="5.6.2"):
    """One package at two install depths, as npm records a non-hoisted dep."""
    doc = {
        "lockfileVersion": 3,
        "packages": {
            "": {"name": "root", "version": "1.0.0"},
            "node_modules/%s" % name: dict(meta_a, version=version),
            "node_modules/parent/node_modules/%s" % name: dict(
                meta_b, version=version),
        },
    }
    records = extract_package_lock(_write(tmp_path, doc))
    return records


class TestSeveralPathsToOnePackage:
    """dependency_data's primary key has no install-path column.

    (_repo_id, hash, file_path, package_type, package_name, package_version) --
    so two packages: entries for the same name@version at different depths
    collapse to ONE row. Measured across the reference estate: 14,884 entries
    hold 13,729 distinct name@version, so 1,155 collapse, and of the 479
    duplicate groups the entries disagree on `dev` in 53, `optional` in 48,
    `inBundle` in 18, and `devOptional` and `peer` in 4 each.

    Left to the upsert, which value survives depends on JSON key order. So the
    parser aggregates instead, by the rule already settled for pnpm v9: a
    package is excluded only if EVERY path agrees it is.
    """

    def test_one_record_per_name_and_version(self, tmp_path):
        records = _two_paths(tmp_path, {}, {})
        assert len(records) == 1

    def test_a_single_production_path_means_it_is_not_dev_only(self, tmp_path):
        # chalk@5.6.2 in the reference estate: three non-dev paths and one dev.
        # It ships, so is_dev must be False -- `any()` would hide it from a
        # "what reaches production" query.
        records = _two_paths(tmp_path, {"dev": True}, {})
        assert records[0]["is_dev"] is False

    def test_dev_survives_when_every_path_agrees(self, tmp_path):
        records = _two_paths(tmp_path, {"dev": True}, {"dev": True})
        assert records[0]["is_dev"] is True

    def test_a_single_required_path_means_it_is_not_optional(self, tmp_path):
        # Same reasoning as pnpm v9: is_optional says "may not install", and
        # one required path means it installs.
        records = _two_paths(tmp_path, {"optional": True}, {})
        assert records[0]["is_optional"] is False

    def test_optional_survives_when_every_path_agrees(self, tmp_path):
        records = _two_paths(tmp_path, {"optional": True}, {"optional": True})
        assert records[0]["is_optional"] is True

    def test_an_install_script_on_any_path_counts(self, tmp_path):
        # Unlike dev/optional, this is a property of the package tarball rather
        # than of the path, so it aggregates with any(). Measured: zero
        # duplicate groups disagree on it, which is consistent with that.
        records = _two_paths(tmp_path, {"hasInstallScript": True}, {})
        assert records[0]["runs_install_script"] is True

    def test_declared_scope_describes_the_aggregate_not_one_path(self, tmp_path):
        # Recording "dev" here would contradict is_dev=False in the same row.
        records = _two_paths(tmp_path, {"dev": True}, {})
        assert records[0]["declared_scope"] is None

    def test_dev_optional_on_one_path_only_does_not_make_it_unknown(self, tmp_path):
        # tinyglobby@0.2.17 in the reference estate: devOptional on one path,
        # a plain production path under npm/ on the other. It installs, so
        # is_optional is False rather than NULL.
        records = _two_paths(tmp_path, {"devOptional": True}, {})
        assert records[0]["is_dev"] is False
        assert records[0]["is_optional"] is False
        assert records[0]["declared_scope"] is None

    def test_dev_optional_survives_when_every_path_agrees(self, tmp_path):
        records = _two_paths(tmp_path, {"devOptional": True}, {"devOptional": True})
        assert records[0]["is_dev"] is True
        assert records[0]["is_optional"] is None
        assert records[0]["declared_scope"] == "devOptional"

    def test_peer_on_any_path_is_recorded(self, tmp_path):
        # It is a peer dependency somewhere, which is what the flag records.
        records = _two_paths(tmp_path, {"peer": True}, {})
        assert records[0]["declared_scope"] == "peer"

    def test_the_first_paths_version_is_kept(self, tmp_path):
        # Different versions are different packages and must stay separate --
        # they are distinct primary keys, not duplicates.
        doc = {
            "lockfileVersion": 3,
            "packages": {
                "": {"name": "root", "version": "1.0.0"},
                "node_modules/chalk": {"version": "5.6.2"},
                "node_modules/parent/node_modules/chalk": {"version": "4.1.2"},
            },
        }
        records = extract_package_lock(_write(tmp_path, doc))
        assert sorted(r["package_version"] for r in records) == ["4.1.2", "5.6.2"]

    def test_requirements_type_is_kept_for_the_declared_copy(self, tmp_path):
        # A direct dependency that also appears nested stays direct.
        doc = {
            "lockfileVersion": 3,
            "packages": {
                "": {"name": "root", "version": "1.0.0",
                     "dependencies": {"chalk": "^5.0.0"}},
                "node_modules/chalk": {"version": "5.6.2"},
                "node_modules/parent/node_modules/chalk": {"version": "5.6.2"},
            },
        }
        records = extract_package_lock(_write(tmp_path, doc))
        assert len(records) == 1
        assert records[0]["requirements_type"] == "direct"


class TestV1SeveralPaths:
    """The v1 tree has the same problem, more so -- it nests by default."""

    def test_duplicates_collapse_in_v1_too(self, tmp_path):
        doc = {
            "lockfileVersion": 1,
            "dependencies": {
                "chalk": {"version": "5.6.2", "dev": True},
                "parent": {
                    "version": "1.0.0",
                    "dependencies": {"chalk": {"version": "5.6.2"}},
                },
            },
        }
        records = _by_name(extract_package_lock(_write(tmp_path, doc)))
        assert len(records) == 2
        assert records["chalk"]["is_dev"] is False


# A v1 lockfile: no packages: section, a nested dependencies tree, and only two
# of the install-state flags available.
V1 = {
    "name": "acme-app",
    "lockfileVersion": 1,
    "dependencies": {
        "lodash": {
            "version": "4.17.21",
            "resolved": "https://registry.npmjs.org/lodash/-/lodash-4.17.21.tgz",
            "integrity": "sha512-xxx",
        },
        "mocha": {
            "version": "10.2.0",
            "dev": True,
            "requires": {"glob": "7.1.6"},
            "dependencies": {
                "glob": {"version": "7.1.6", "dev": True},
            },
        },
        "fsevents": {"version": "2.3.2", "optional": True},
    },
}


class TestLockfileVersion1:
    """v1 answers two of the three axes, not none of them.

    docs/dependency-flags.md asserted v1 "records none of this" and a 493-entry
    file that does not exist. Re-measured: v1 records `dev` on every entry and
    `optional` on some. It carries no `hasInstallScript`, so only that axis is
    genuinely unknown.
    """

    def test_the_nested_tree_is_walked(self, tmp_path):
        records = extract_package_lock(_write(tmp_path, V1))
        assert sorted(r["package_name"] for r in records) == [
            "fsevents", "glob", "lodash", "mocha",
        ]

    def test_a_nested_entry_keeps_its_own_version(self, tmp_path):
        records = _by_name(extract_package_lock(_write(tmp_path, V1)))
        assert records["glob"]["package_version"] == "7.1.6"

    def test_dev_is_read(self, tmp_path):
        records = _by_name(extract_package_lock(_write(tmp_path, V1)))
        assert records["mocha"]["is_dev"] is True
        assert records["mocha"]["declared_scope"] == "dev"

    def test_dev_is_read_at_depth(self, tmp_path):
        records = _by_name(extract_package_lock(_write(tmp_path, V1)))
        assert records["glob"]["is_dev"] is True

    def test_optional_is_read(self, tmp_path):
        records = _by_name(extract_package_lock(_write(tmp_path, V1)))
        assert records["fsevents"]["is_optional"] is True
        assert records["fsevents"]["declared_scope"] == "optional"

    def test_absent_flags_are_false_because_v1_does_state_these_two(self, tmp_path):
        records = _by_name(extract_package_lock(_write(tmp_path, V1)))
        assert records["lodash"]["is_dev"] is False
        assert records["lodash"]["is_optional"] is False
        assert records["lodash"]["declared_scope"] is None

    def test_install_script_is_null_because_the_format_cannot_say(self, tmp_path):
        # The one axis v1 genuinely cannot answer. False would assert npm had
        # checked and found no install script.
        records = extract_package_lock(_write(tmp_path, V1))
        assert {r["runs_install_script"] for r in records} == {None}

    def test_nothing_is_called_direct_because_v1_cannot_tell(self, tmp_path):
        # v1's top level is the HOISTED install set, not the declared set, so a
        # transitive lifted to the root is indistinguishable from a direct
        # dependency. Claiming "direct" for all four would be a guess;
        # `is_dev` still carries what npm did state.
        records = extract_package_lock(_write(tmp_path, V1))
        assert {r["requirements_type"] for r in records} == {"resolved"}


class TestLockfileVersion2:
    """v2 carries both sections; only packages: is read."""

    def test_the_duplicate_dependencies_tree_does_not_double_count(self, tmp_path):
        # `packages:` is authoritative for anything it lists, so the tree's own
        # copy of lodash@4.17.21 adds nothing and does not disturb its flags.
        doc = {
            "lockfileVersion": 2,
            "packages": {
                "": {"name": "root", "version": "1.0.0",
                     "dependencies": {"lodash": "^4.17.21"}},
                "node_modules/lodash": {"version": "4.17.21"},
            },
            "dependencies": {
                "lodash": {"version": "4.17.21"},
            },
        }
        records = extract_package_lock(_write(tmp_path, doc))
        assert [(r["package_name"], r["package_version"]) for r in records] == [
            ("lodash", "4.17.21"),
        ]

    def test_a_version_only_the_legacy_tree_knows_is_also_reported(self, tmp_path):
        # The two sections CAN disagree, and npm 6 installs from the tree while
        # npm 7+ installs from packages:. Picking one silently would hide a
        # version a real client installs, so both are reported and the reader
        # decides. Previously this test asserted the tree was ignored, which is
        # what made the discrepancy invisible.
        doc = {
            "lockfileVersion": 2,
            "packages": {
                "": {"name": "root", "version": "1.0.0",
                     "dependencies": {"lodash": "^4.17.21"}},
                "node_modules/lodash": {"version": "4.17.21"},
            },
            "dependencies": {
                "lodash": {"version": "4.17.20"},
                "only-in-the-old-tree": {"version": "1.0.0"},
            },
        }
        records = extract_package_lock(_write(tmp_path, doc))
        assert sorted(
            (r["package_name"], r["package_version"]) for r in records) == [
            ("lodash", "4.17.20"),
            ("lodash", "4.17.21"),
            ("only-in-the-old-tree", "1.0.0"),
        ]

    def test_v2_reads_install_state_like_v3(self, tmp_path):
        rec = _one(tmp_path, {"hasInstallScript": True}, lockfile_version=2)
        assert rec["runs_install_script"] is True


class TestUnreadableFilesAreNotReportedAsEmpty:
    """A file kospex could not read must not look like one with nothing in it.

    This parser raises instead of returning [], which is a deliberate departure
    from the other extractors. `osi_extract` catches it and records
    `parse_error`; returning [] produces `empty`, whose documented meaning is
    "examined, nothing to find" -- the result that stops a repository being
    re-queued.

    The difference is an evasion. Before this, changing `lockfileVersion: 3` to
    `"3"` read zero packages out of a real lockfile and reported the repository
    as cleanly empty. Every case below hid every dependency in the file.
    """

    def test_missing_file(self, tmp_path):
        with pytest.raises(UnreadableLockfile):
            extract_package_lock(str(tmp_path / "nope.json"))

    def test_invalid_json(self, tmp_path):
        p = tmp_path / "package-lock.json"
        p.write_text("{not json at all")
        with pytest.raises(UnreadableLockfile):
            extract_package_lock(str(p))

    def test_a_truncated_file(self, tmp_path):
        # The shape a partial write or a tampered file takes.
        p = tmp_path / "package-lock.json"
        p.write_text('{"lockfileVersion": 3, "packages": {')
        with pytest.raises(UnreadableLockfile):
            extract_package_lock(str(p))

    def test_empty_file(self, tmp_path):
        p = tmp_path / "package-lock.json"
        p.write_text("")
        with pytest.raises(UnreadableLockfile):
            extract_package_lock(str(p))

    def test_json_that_is_not_an_object(self, tmp_path):
        p = tmp_path / "package-lock.json"
        p.write_text("[1, 2, 3]")
        with pytest.raises(UnreadableLockfile):
            extract_package_lock(str(p))

    def test_unknown_lockfile_version(self, tmp_path):
        doc = {"lockfileVersion": 4, "packages": {
            "node_modules/x": {"version": "1.0.0"}}}
        with pytest.raises(UnreadableLockfile):
            extract_package_lock(_write(tmp_path, doc))

    def test_missing_lockfile_version(self, tmp_path):
        doc = {"packages": {"node_modules/x": {"version": "1.0.0"}}}
        with pytest.raises(UnreadableLockfile):
            extract_package_lock(_write(tmp_path, doc))

    def test_v3_without_a_packages_section(self, tmp_path):
        # npm always writes `packages:` for v2/v3, even for a project with no
        # dependencies, so its absence is a malformed file rather than an empty
        # one. Contrast test_a_lockfile_with_only_a_root_entry below.
        with pytest.raises(UnreadableLockfile):
            extract_package_lock(_write(tmp_path, {"lockfileVersion": 3}))

    def test_the_error_names_the_file_and_the_reason(self, tmp_path):
        # The message reaches the operator through osi_extract's `echo`, so it
        # has to say which file and why.
        doc = {"lockfileVersion": 4, "packages": {}}
        path = _write(tmp_path, doc)
        with pytest.raises(UnreadableLockfile) as caught:
            extract_package_lock(path)
        assert "lockfileVersion" in str(caught.value)
        assert "4" in str(caught.value)


class TestBothScanPathsHandleAnUnreadableFile:
    """Raising is only honest if both scanners act on it.

    `osi_extract` already maps an exception to a `parse_error` outcome. The
    `kospex sca` path called the parser with no guard at all, so the same file
    would have crashed the command instead of being reported. Both are checked
    here, because a parser contract only one caller honours is not a contract.
    """

    def _bad(self, tmp_path):
        doc = {"lockfileVersion": 4,
               "packages": {"node_modules/evil-pkg": {"version": "1.2.3"}}}
        return _write(tmp_path, doc)

    def test_the_osi_path_propagates_so_the_outcome_is_parse_error(self, tmp_path):
        # extract_dependency_file is the osi loop body, reachable without a
        # synced database. osi_extract wraps it in `except Exception ->
        # PARSE_ERROR`, so propagating is what produces the honest outcome.
        from krunner import extract_dependency_file
        from kospex.extractors.registry import classify
        from kospex_dependencies import KospexDependencies

        extractor = classify("package-lock.json").extractor
        with pytest.raises(UnreadableLockfile):
            extract_dependency_file(
                extractor, self._bad(tmp_path), "s~o~r",
                "package-lock.json", "abc123", KospexDependencies(),
            )

    def test_an_unreadable_file_classifies_as_parse_error_not_empty(self, tmp_path):
        # The mapping that makes the raise worth doing.
        from kospex.osi_outcomes import EMPTY, PARSE_ERROR, classify_outcome

        assert classify_outcome("package-lock.json", row_count=0) == EMPTY
        assert classify_outcome(
            "package-lock.json", row_count=0,
            error=UnreadableLockfile("bad")) == PARSE_ERROR

    def test_the_sca_path_reports_rather_than_crashing(self, tmp_path):
        # Previously `parser(filename)` was unguarded here, so an unreadable
        # lockfile raised out of `kospex sca` entirely.
        from kospex_dependencies import KospexDependencies

        assert KospexDependencies().assess(self._bad(tmp_path)) is None


class TestGenuinelyEmptyFilesStillReadAsEmpty:
    """The other half of the distinction: nothing declared is not an error."""

    def test_v1_without_a_dependencies_tree(self, tmp_path):
        # v1 omits `dependencies` for a project with none, so this is empty
        # rather than malformed.
        assert extract_package_lock(_write(tmp_path, {"lockfileVersion": 1})) == []

    def test_v1_with_an_empty_dependencies_tree(self, tmp_path):
        assert extract_package_lock(
            _write(tmp_path, {"lockfileVersion": 1, "dependencies": {}})) == []

    def test_a_lockfile_with_only_a_root_entry(self, tmp_path):
        # A project with no dependencies at all. Distinct from a parse failure:
        # #148's vocabulary reports this as `empty`, not `parse_error`.
        doc = {"lockfileVersion": 3, "packages": {
            "": {"name": "root", "version": "1.0.0"}}}
        assert extract_package_lock(_write(tmp_path, doc)) == []


# --- against real lockfiles --------------------------------------------------
#
# Synthetic fixtures pin the rules; these guard against the parser silently
# reading nothing, or reading a whole file as one shape. Every count below was
# taken from the file independently of the parser, so agreement means something.
# Skipped when the clone is absent, as the pnpm suite does.

V1_REAL = os.path.expanduser("~/code/github.com/lodash/lodash/package-lock.json")
V2_REAL = os.path.expanduser(
    "~/code/github.com/facebook/react/fixtures/ssr2/package-lock.json")
V3_REAL = os.path.expanduser(
    "~/code/github.com/mergestat/mergestat/ui/package-lock.json")


@pytest.mark.skipif(not os.path.exists(V1_REAL),
                    reason="needs the lodash clone this was measured against")
def test_the_real_v1_lockfile_answers_two_axes_not_none():
    """The error docs/dependency-flags.md shipped, as a test.

    The page claimed a v1 package-lock.json "has no flags at all", which would
    have sent this branch to NULL for both axes the file does answer.

    Counts from the file: 673 install-path entries holding 602 distinct
    name@version, so 71 collapse. `dev` is set on every path of all 602.
    `optional` is set on 93 entries but on *every* path of only 55 -- the other
    38 are reachable by a required path too, so they install.
    """
    rows = extract_package_lock(V1_REAL)
    assert len(rows) == 602

    assert sum(1 for r in rows if r["is_dev"] is True) == 602, \
        "v1 records dev on every entry here; NULL would discard it"
    assert sum(1 for r in rows if r["is_optional"] is True) == 55, \
        "93 entries carry optional, but only 55 packages are optional by every path"
    assert all(r["runs_install_script"] is None for r in rows), \
        "v1 has no hasInstallScript; False would claim nothing executes"


@pytest.mark.skipif(not os.path.exists(V2_REAL),
                    reason="needs the react clone this was measured against")
def test_the_real_v2_lockfile_is_not_double_counted():
    """v2's two sections describe the same closure.

    The file has 819 `packages:` entries (818 real ones plus the root) and 818
    `dependencies:` tree entries. Reading both would report 1636. The 818
    install paths hold 715 distinct name@version.
    """
    rows = extract_package_lock(V2_REAL)
    assert len(rows) == 715


@pytest.mark.skipif(not os.path.exists(V3_REAL),
                    reason="needs the mergestat clone this was measured against")
def test_the_real_v3_lockfile_separates_dev_optional_from_optional():
    """The devOptional decision, against the file with the most of them.

    Counts from the file: 1284 install-path entries holding 1141 distinct
    name@version. Of those, 669 are dev by every path and 15 are devOptional by
    every path, so is_dev is True for 669 + 15 = 684. 14 are optional by every
    path, and `hasInstallScript` appears on 2.

    The 16th devOptional entry is the one this aggregation exists for:
    tinyglobby@0.2.17 is devOptional on one path and a plain production
    dependency under npm/ on the other, so it installs and is_optional is False
    rather than NULL.
    """
    rows = extract_package_lock(V3_REAL)
    assert len(rows) == 1141

    assert sum(1 for r in rows if r["is_dev"] is True) == 684
    assert sum(1 for r in rows if r["is_optional"] is True) == 14
    assert sum(1 for r in rows if r["is_optional"] is None) == 15
    assert sum(1 for r in rows if r["declared_scope"] == "devOptional") == 15
    assert sum(1 for r in rows if r["runs_install_script"] is True) == 2


ALIAS_REAL = os.path.expanduser(
    "~/code/github.com/NixOS/nixpkgs/ci/github-script/package-lock.json")


@pytest.mark.skipif(not os.path.exists(ALIAS_REAL),
                    reason="needs the nixpkgs clone this was measured against")
def test_a_real_aliased_dependency_resolves_to_the_installed_package():
    """The SCA-evasion case, against a real file that contains it.

    This lockfile holds three aliases — `string-width-cjs`, `strip-ansi-cjs`
    and `wrap-ansi-cjs` — each installing the plain package at an older major.
    Recording the alias would send three deps.dev lookups to packages that do
    not exist, and return no advisories for three that do.

    It also holds the genuine `string-width@5.1.2`, so the two must stay
    separate rows while `string-width@4.2.3` (reached as the alias AND nested
    under `wrap-ansi-cjs`) is one.
    """
    rows = extract_package_lock(ALIAS_REAL)
    pairs = {(r["package_name"], r["package_version"]) for r in rows}

    # Each alias target is recorded, which is the fix: previously deps.dev was
    # asked about `string-width-cjs`, which does not exist, and never about
    # string-width@4.2.3, which does. The install paths are recorded alongside
    # them -- see TestEveryIdentityIsRecorded for why neither field wins.
    assert ("string-width", "4.2.3") in pairs
    assert ("string-width", "5.1.2") in pairs
    assert ("strip-ansi", "6.0.1") in pairs
    assert ("wrap-ansi", "7.0.0") in pairs


@pytest.mark.skipif(not os.path.exists(V3_REAL),
                    reason="needs the mergestat clone this was measured against")
def test_a_real_lockfile_is_not_recorded_as_all_one_thing():
    """The shape of the pnpm v9 regression, as a guard for this parser.

    That bug wrote a confident False for every entry in a file the format could
    not speak for. The failure mode is a uniform column, so assert no column is
    uniform where the file is mixed.
    """
    rows = extract_package_lock(V3_REAL)

    assert len({r["is_dev"] for r in rows}) > 1, "is_dev is uniform"
    assert len({r["requirements_type"] for r in rows}) > 1, \
        "every package has the same package_use"
    assert any(r["declared_scope"] for r in rows), "no flags read at all"
    assert any(r["declared_scope"] is None for r in rows), \
        "every entry carries a flag, which no real lockfile does"
