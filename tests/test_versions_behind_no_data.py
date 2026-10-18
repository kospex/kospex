"""One package deps.dev cannot answer must not end a batch of 374 repositories.

`get_versions_behind` called `data.get("versions", [])` on the result of
`get_url_json`, which returns None for any non-200 -- a 404, a 429, a 5xx or a
timeout. That `AttributeError` propagated out of `enrich_dependency_records`,
out of `_process_repo`, and killed `krunner osi -next` outright: observed on a
real sweep after 21 of 374 repositories, with the repository in flight left
unsaved.

The lookup this crashes on is the **package-level** endpoint
(`/packages/{name}`, no version), which is a second and separate request from
the exact-version lookup in `depsdev_record`. By the time it runs, the version
lookup has already succeeded and returned `resolution="resolved"`, so a failure
here is usually transient rather than "no such package".

`versions_behind` is already int-or-None throughout, and NULL means *unknown* --
`depsdev_record` does `versions_behind if isinstance(versions_behind, int) else
None`. So "deps.dev did not answer" has a correct representation already and
needs no new one. Nothing is fabricated: the row is still written, with the
advisory and publication data the version lookup did return, and
`versions_behind` left unknown.
"""
import pytest

from kospex_dependencies import KospexDependencies


class _StubQuery:
    """Minimal stand-in for KospexQuery: every request fails with `status`."""

    def __init__(self, status):
        self.status = status
        self.url_fetches = 0
        self.urls = []

    def url_request_with_status(self, url, cache=None, timeout=10, headers=None):
        self.urls.append(url)
        return None, self.status

    def url_request(self, url, cache=None, timeout=10, headers=None):
        content, _ = self.url_request_with_status(url)
        return content


@pytest.fixture
def kdeps():
    return KospexDependencies()


class TestGetVersionsBehindSurvivesNoData:

    @pytest.mark.parametrize("status", [404, 429, 500, 503, None])
    def test_it_does_not_raise(self, kdeps, status):
        """404 is "no such package"; 429/5xx/None are transient. None of them
        may be fatal, because the caller is a loop over an entire estate."""
        kdeps.kospex_query = _StubQuery(status)

        details = kdeps.get_versions_behind("npm", "no-such-package-xyz", "1.0.0")

        assert isinstance(details, dict)
        assert details.get("versions_behind") is None

    def test_the_unknown_reaches_the_record_as_none_not_zero(self, kdeps):
        """Zero would assert "up to date", which is a different claim."""
        kdeps.kospex_query = _StubQuery(429)

        details = kdeps.get_versions_behind("npm", "whatever", "1.0.0")

        assert details.get("versions_behind") is not 0  # noqa: F632 - identity is the point
        assert details.get("versions_behind") is None

    def test_a_package_name_needing_encoding_still_builds_one_url(self, kdeps):
        """A scoped npm name must not produce a path-traversing URL."""
        stub = _StubQuery(404)
        kdeps.kospex_query = stub

        kdeps.get_versions_behind("npm", "@scope/name", "1.0.0")

        assert len(stub.urls) == 1
        assert "%40scope%2Fname" in stub.urls[0]


class _VersionOkPackageFailsQuery:
    """The shape the real crash had: the exact-version lookup SUCCEEDS, and the
    separate package-level lookup fails.

    A stub that fails both requests does not reproduce it — `depsdev_record`
    returns early on the version miss and never reaches `get_versions_behind`.
    That version of this test passed before the fix, which is the definition of
    a test that proves nothing.
    """

    VERSION_BODY = (
        '{"versionKey": {"version": "1.0.0"}, "publishedAt": "2026-01-01T00:00:00Z",'
        ' "isDefault": true, "advisoryKeys": [{"id": "GHSA-x"}], "links": []}'
    )

    def __init__(self):
        self.url_fetches = 0
        self.urls = []

    def url_request_with_status(self, url, cache=None, timeout=10, headers=None):
        self.urls.append(url)
        if "/versions/" in url:
            return self.VERSION_BODY, 200
        return None, 429                      # the package-level call is throttled

    def url_request(self, url, cache=None, timeout=10, headers=None):
        content, _ = self.url_request_with_status(url)
        return content


class TestDepsdevRecordSurvivesIt:
    """The crash was reached THROUGH depsdev_record, so cover that path."""

    def test_the_row_is_still_enriched_with_versions_behind_unknown(self, kdeps):
        kdeps.kospex_query = _VersionOkPackageFailsQuery()

        record = kdeps.depsdev_record("npm", "whatever", "1.0.0")

        # Resolved, because the version lookup succeeded -- the row keeps the
        # data that call returned.
        assert record["resolution"] == "resolved"
        assert record["advisories"] == 1
        assert record["published_at"] == "2026-01-01T00:00:00Z"
        # Only this one field is unknown, and unknown is NULL, never 0.
        assert record["versions_behind"] is None

    def test_both_endpoints_were_called(self, kdeps):
        """Guards the test itself: if the package call stopped happening, the
        scenario would silently stop being the one that crashed."""
        stub = _VersionOkPackageFailsQuery()
        kdeps.kospex_query = stub

        kdeps.depsdev_record("npm", "whatever", "1.0.0")

        assert any("/versions/" in u for u in stub.urls)
        assert any("/versions/" not in u for u in stub.urls)
