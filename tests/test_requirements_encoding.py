"""`detect_encoding` must actually consume the BOM it detects.

The function inspects the first four bytes for a byte-order mark and returns an
encoding name. For UTF-8 it returns `utf-8-sig`, which strips the BOM. For the
two UTF-16 cases it returned `utf-16-le` / `utf-16-be`, which **do not** -- the
BOM survives decoding as a leading U+FEFF, so the first declaration in the file
became `"\ufeffrequests==2.31.0"`, which is not a valid PEP 508 requirement and
was dropped.

A UTF-16 `requirements.txt` therefore lost its first dependency silently, and a
single-dependency file reported as having none at all. The function existed
specifically to handle BOMs and got two of its three cases wrong.

`utf-16` (no endianness suffix) reads the BOM, uses it to pick the byte order,
and consumes it -- which is what the detection was for.
"""

import codecs

import pytest

from kospex_dependencies import KospexDependencies


@pytest.fixture
def kdeps():
    return KospexDependencies()


LINE = "requests==2.31.0\n"


def _write_bytes(tmp_path, name, raw):
    path = tmp_path / name
    path.write_bytes(raw)
    return str(path)


class TestDetectEncoding:
    def test_no_bom_is_utf8(self, kdeps, tmp_path):
        path = _write_bytes(tmp_path, "r.txt", LINE.encode("utf-8"))
        assert kdeps.detect_encoding(path) == "utf-8"

    def test_utf8_bom_strips(self, kdeps, tmp_path):
        path = _write_bytes(tmp_path, "r.txt", codecs.BOM_UTF8 + LINE.encode("utf-8"))
        assert kdeps.detect_encoding(path) == "utf-8-sig"

    @pytest.mark.parametrize("bom,enc", [
        (codecs.BOM_UTF16_LE, "utf-16-le"),
        (codecs.BOM_UTF16_BE, "utf-16-be"),
    ], ids=["le", "be"])
    def test_utf16_encoding_consumes_the_bom(self, kdeps, tmp_path, bom, enc):
        """The returned encoding must not leave U+FEFF in the decoded text."""
        raw = bom + LINE.encode(enc)
        path = _write_bytes(tmp_path, "r.txt", raw)
        detected = kdeps.detect_encoding(path)

        text = raw.decode(detected)
        assert not text.startswith("\ufeff"), (
            f"detect_encoding returned {detected!r}, which leaves the BOM in "
            "the decoded text"
        )


class TestParsingBomEncodedRequirements:
    """The behaviour the encoding bug actually cost."""

    def test_plain_utf8(self, kdeps, tmp_path):
        path = _write_bytes(tmp_path, "r.txt", LINE.encode("utf-8"))
        records = kdeps.parse_pip_requirements_file(path)
        assert [r["package_name"] for r in records] == ["requests"]

    def test_utf8_with_bom(self, kdeps, tmp_path):
        path = _write_bytes(tmp_path, "r.txt", codecs.BOM_UTF8 + LINE.encode("utf-8"))
        records = kdeps.parse_pip_requirements_file(path)
        assert [r["package_name"] for r in records] == ["requests"]

    @pytest.mark.parametrize("bom,enc", [
        (codecs.BOM_UTF16_LE, "utf-16-le"),
        (codecs.BOM_UTF16_BE, "utf-16-be"),
    ], ids=["le", "be"])
    def test_utf16_with_bom_finds_the_dependency(self, kdeps, tmp_path, bom, enc):
        path = _write_bytes(tmp_path, "r.txt", bom + LINE.encode(enc))
        records = kdeps.parse_pip_requirements_file(path)
        assert [r["package_name"] for r in records] == ["requests"]

    @pytest.mark.parametrize("bom,enc", [
        (codecs.BOM_UTF16_LE, "utf-16-le"),
        (codecs.BOM_UTF16_BE, "utf-16-be"),
    ], ids=["le", "be"])
    def test_utf16_second_line_was_never_affected(self, kdeps, tmp_path, bom, enc):
        """Only the FIRST declaration was lost, which is why this was quiet.

        A file with several dependencies still returned most of them, so the
        symptom was an off-by-one in a count nobody was checking.
        """
        body = "requests==2.31.0\nflask==3.0.0\n"
        path = _write_bytes(tmp_path, "r.txt", bom + body.encode(enc))
        records = kdeps.parse_pip_requirements_file(path)
        assert [r["package_name"] for r in records] == ["requests", "flask"]


class TestUnreadableStillRaises:
    """Encoding handling must not turn an unreadable file into an empty one."""

    def test_undecodable_bytes_raise(self, kdeps, tmp_path):
        path = _write_bytes(tmp_path, "r.txt", b"requests==2.31.0\n# caf\xe9\n")
        with pytest.raises(UnicodeDecodeError):
            kdeps.parse_pip_requirements_file(path)

    def test_missing_file_raises(self, kdeps, tmp_path):
        with pytest.raises(OSError):
            kdeps.parse_pip_requirements_file(str(tmp_path / "nope.txt"))
