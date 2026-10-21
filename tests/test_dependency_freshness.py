"""Tests for KospexWeb.dependency_freshness() - the "Last checked" line on
/dependencies/, built from dependency_data.last_checked."""
import datetime

import kospex_web as KospexWeb

NOW = datetime.datetime(2026, 10, 20, 12, 0, 0, tzinfo=datetime.timezone.utc)


def _rows(*stamps):
    return [{"last_checked": s} for s in stamps]


def test_no_rows_has_no_freshness():
    assert KospexWeb.dependency_freshness([], now=NOW) is None


def test_recent_check_is_not_stale():
    f = KospexWeb.dependency_freshness(_rows("2026-10-19 12:00:00"), now=NOW)
    assert f["oldest"] == "2026-10-19 12:00:00"
    assert f["stale"] is False


def test_older_than_two_days_is_stale():
    f = KospexWeb.dependency_freshness(_rows("2026-10-18 11:59:59"), now=NOW)
    assert f["stale"] is True


def test_reports_oldest_so_one_stale_file_cannot_hide():
    f = KospexWeb.dependency_freshness(
        _rows("2026-10-20 09:00:00", "2026-10-10 09:00:00"), now=NOW)
    assert (f["oldest"], f["newest"]) == ("2026-10-10 09:00:00", "2026-10-20 09:00:00")
    assert f["stale"] is True


def test_null_means_never_checked_and_is_stale():
    # Per migration 0006: NULL renders as "never checked", not "checked long ago"
    f = KospexWeb.dependency_freshness(_rows("2026-10-20 09:00:00", None, ""), now=NOW)
    assert f["oldest"] == "2026-10-20 09:00:00"
    assert f["never_checked"] == 2
    assert f["stale"] is True

    f = KospexWeb.dependency_freshness(_rows(None), now=NOW)
    assert f["oldest"] is None and f["stale"] is True


def _dep(file_path, last_checked=None, created_at="2026-07-15 23:59:08", repo_id="s~o~r"):
    return {"_repo_id": repo_id, "file_path": file_path,
            "last_checked": last_checked, "created_at": created_at}


def test_never_checked_files_groups_rows_and_skips_checked_ones():
    rows = [_dep("a.txt"), _dep("a.txt", created_at="2026-05-01 00:00:00"),
            _dep("b.txt", last_checked="2026-10-19 10:00:00")]
    files = KospexWeb.never_checked_files(rows, {"s~o~r": None})
    assert [(f["file_path"], f["rows"], f["first_seen"]) for f in files] == [
        ("a.txt", 2, "2026-05-01 00:00:00")]


def test_never_checked_reason_comes_from_latest_extraction_outcome():
    outcome = {"examined_at": "2026-10-17T19:35:44Z",
               "files": {"empty.toml": "empty", "bad.toml": "parse_error"}}
    rows = [_dep("empty.toml"), _dep("bad.toml"), _dep("gone.toml")]
    reasons = {f["file_path"]: f["reason"]
               for f in KospexWeb.never_checked_files(rows, {"s~o~r": outcome})}
    assert reasons["empty.toml"] == "Latest extraction (2026-10-17) found no packages in this file"
    assert reasons["bad.toml"] == "Latest extraction (2026-10-17) could not read this file"
    assert reasons["gone.toml"].startswith("Not in the latest extraction (2026-10-17)")


def test_never_checked_reason_without_any_extraction_record():
    files = KospexWeb.never_checked_files([_dep("a.txt")], {"s~o~r": None})
    assert files[0]["reason"].startswith("No extraction outcome recorded")
