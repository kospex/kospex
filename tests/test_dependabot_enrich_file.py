"""enrich_file: the JSONL runner for `kospex sca -dependabot`.

Separate from test_dependabot_enrich.py (which covers the Enricher); the stub
and alert helpers are reused from there. Drives the production Enricher.
"""
import json
import os

import pytest

from kospex.dependabot_enrich import (
    SCA_FIELDS, DependabotInputError, Enricher, enrich_file,
)
from test_dependabot_enrich import StubDeps, _alert


def _write(path, text):
    path.write_bytes(text.encode("utf-8"))
    return path


def _lines(alerts):
    return "".join(json.dumps(a) + "\n" for a in alerts)


def _enricher():
    return Enricher(StubDeps())


def test_enrich_file_round_trip(tmp_path):
    alerts = [_alert(package_name="a"), _alert(package_name="b", patched_version=None),
              _alert(package_name="c", package_ecosystem="cobol")]
    src = _write(tmp_path / "in.jsonl", _lines(alerts))
    out = tmp_path / "out.jsonl"
    seen = []
    counts = enrich_file(src, out, _enricher(),
                         on_progress=lambda done, st: seen.append((done, st)))
    text = out.read_text(encoding="utf-8")
    assert text.endswith("\n")
    rows = [json.loads(line) for line in text.splitlines()]
    assert [r["package_name"] for r in rows] == ["a", "b", "c"]
    for r in rows:
        assert all(f in r for f in SCA_FIELDS)
    assert [r["sca_status"] for r in rows] == [
        "ok", "no_patched_version", "unsupported_ecosystem"]
    assert dict(counts) == {"ok": 1, "no_patched_version": 1,
                            "unsupported_ecosystem": 1}
    assert seen == [(1, "ok"), (2, "no_patched_version"),
                    (3, "unsupported_ecosystem")]


def test_non_ascii_written_as_utf8(tmp_path):
    src = _write(tmp_path / "in.jsonl", _lines([_alert(package_name="café")]))
    out = tmp_path / "out.jsonl"
    enrich_file(src, out, _enricher())
    assert "café".encode("utf-8") in out.read_bytes()


def _assert_fails(tmp_path, text, line_no):
    src = _write(tmp_path / "in.jsonl", text)
    out = tmp_path / "out.jsonl"
    with pytest.raises(DependabotInputError) as exc:
        enrich_file(src, out, _enricher())
    assert exc.value.line_no == line_no
    assert not out.exists()
    return exc.value


def test_blank_line_is_error_with_line_number(tmp_path):
    good = json.dumps(_alert())
    _assert_fails(tmp_path, f"{good}\n\n{good}\n", 2)


def test_non_object_json_is_error(tmp_path):
    good = json.dumps(_alert())
    _assert_fails(tmp_path, f"{good}\n[1, 2]\n", 2)


def test_malformed_json_names_line(tmp_path):
    good = json.dumps(_alert())
    err = _assert_fails(tmp_path, f"{good}\n{good}\n{{not json\n", 3)
    assert "3" in str(err)


def test_out_equals_in_refused(tmp_path):
    src = _write(tmp_path / "in.jsonl", _lines([_alert()]))
    before = src.read_bytes()
    with pytest.raises(ValueError):
        enrich_file(src, src, _enricher())
    link = tmp_path / "link.jsonl"
    os.symlink(src, link)
    with pytest.raises(ValueError):
        enrich_file(src, link, _enricher())
    assert src.read_bytes() == before


def test_error_leaves_no_complete_looking_output(tmp_path):
    good = json.dumps(_alert())
    src = _write(tmp_path / "in.jsonl", f"{good}\n{good}\nnope\n")
    out = tmp_path / "out.jsonl"
    out.write_text("old complete-looking content\n")
    with pytest.raises(DependabotInputError):
        enrich_file(src, out, _enricher())
    assert not out.exists()


def test_unexpected_exception_deletes_output_and_reraises(tmp_path):
    class Boom(Enricher):
        def enrich(self, alert):
            if alert["package_name"] == "b":
                raise RuntimeError("boom")
            return super().enrich(alert)

    src = _write(tmp_path / "in.jsonl",
                 _lines([_alert(package_name="a"), _alert(package_name="b")]))
    out = tmp_path / "out.jsonl"
    with pytest.raises(RuntimeError):
        enrich_file(src, out, Boom(StubDeps()))
    assert not out.exists()


def test_invalid_utf8_is_input_error_with_line_number(tmp_path):
    good = json.dumps(_alert()).encode("utf-8")
    src = tmp_path / "in.jsonl"
    src.write_bytes(good + b"\n" + good + b"\n" + b'{"package": "\xff\xfe"}\n')
    out = tmp_path / "out.jsonl"
    with pytest.raises(DependabotInputError) as exc:
        enrich_file(src, out, _enricher())
    assert exc.value.line_no == 3
    assert "UTF-8" in str(exc.value)
    assert not out.exists()


def test_missing_input_leaves_no_output(tmp_path):
    out = tmp_path / "out.jsonl"
    with pytest.raises(FileNotFoundError):
        enrich_file(tmp_path / "nope.jsonl", out, _enricher())
    assert not out.exists()


# A line that is valid JSON but the wrong type ends the run as an input error
# naming the line, not as a traceback from inside a lookup.
@pytest.mark.parametrize("over", [
    {"package_name": None},
    {"package_name": ""},
    {"package_name": 42},
    {"package_name": ["cookie"]},
    {"current_version": 1.5},
    {"current_version": ["0.4.0"]},
    {"patched_version": 7},
    {"patched_version": {"v": "0.7.0"}},
])
def test_wrong_typed_alert_fields_are_input_errors(tmp_path, over):
    good = json.dumps(_alert())
    bad = json.dumps({**_alert(), **over})
    err = _assert_fails(tmp_path, f"{good}\n{bad}\n", 2)
    assert "line 2" in str(err)


def test_missing_package_name_is_input_error(tmp_path):
    good = json.dumps(_alert())
    bad = _alert()
    del bad["package_name"]
    _assert_fails(tmp_path, f"{good}\n{good}\n{json.dumps(bad)}\n", 3)


def test_input_error_does_not_echo_the_value(tmp_path):
    bad = json.dumps({**_alert(), "current_version": 123456789})
    err = _assert_fails(tmp_path, bad + "\n", 1)
    assert "123456789" not in str(err)


def test_null_versions_and_ecosystem_stay_legal(tmp_path):
    alerts = [_alert(current_version=None), _alert(patched_version=None),
              _alert(package_ecosystem=None)]
    src = _write(tmp_path / "in.jsonl", _lines(alerts))
    out = tmp_path / "out.jsonl"
    counts = enrich_file(src, out, _enricher())
    assert dict(counts) == {"no_current_version": 1, "no_patched_version": 1,
                            "unsupported_ecosystem": 1}
