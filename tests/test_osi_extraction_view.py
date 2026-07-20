"""Tests for kospex_web.osi_extraction_view (sub-project B)."""
import kospex_web as KospexWeb


def _f(repo, provider, filename=None):
    return {"_repo_id": repo, "Provider": provider,
            "Filename": filename or provider.rsplit("/", 1)[-1]}


def test_marks_extracted_and_buckets_the_rest():
    files = [
        _f("s~o~r", "requirements.txt"),              # extracted (supported)
        _f("s~o~r", "package.json"),                  # NOT extracted, supported -> not_scanned
        _f("s~o~r", "yarn.lock"),                     # NOT extracted, no parser -> no_parser[package]
        _f("s~o~r", "sub/uv.lock", "uv.lock"),        # NOT extracted, no parser -> no_parser[package]
        _f("s~o~r", ".github/Dockerfile", "Dockerfile"),  # NOT extracted -> no_parser[container]
    ]
    extracted = {("s~o~r", "requirements.txt")}

    out_files, commentary = KospexWeb.osi_extraction_view(files, extracted)

    by_provider = {f["Provider"]: f["extracted"] for f in out_files}
    assert by_provider["requirements.txt"] is True
    assert by_provider["package.json"] is False
    assert by_provider["yarn.lock"] is False

    assert commentary["no_parser"]["package"] == {"yarn.lock": 1, "uv.lock": 1}
    assert commentary["no_parser"]["container"] == {"Dockerfile": 1}
    assert commentary["not_scanned"] == 1  # the package.json


def test_all_extracted_gives_empty_commentary():
    files = [_f("s~o~r", "requirements.txt"), _f("s~o~r", "package.json")]
    extracted = {("s~o~r", "requirements.txt"), ("s~o~r", "package.json")}
    out_files, commentary = KospexWeb.osi_extraction_view(files, extracted)
    assert all(f["extracted"] for f in out_files)
    assert commentary == {"no_parser": {}, "not_scanned": 0}
