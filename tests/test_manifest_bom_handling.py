"""A leading byte order mark must not make a readable manifest unreadable.

A UTF-8 BOM is what a Windows editor writes and is never content, so whether
kospex may refuse a file carrying one is decided by the ecosystem that owns the
format, not by what our reader finds convenient. Each expectation below was
checked against the real tool:

* ``package.json`` -- **npm reads it.** `npm pkg get name` returns the name and
  `npm ls` resolves the tree from a BOM-prefixed file.
* ``package-lock.json`` -- **npm reads it.** `npm ls --package-lock-only`
  resolves the locked version from a BOM-prefixed lockfile.
* ``pnpm-lock.yaml`` -- the YAML spec permits a BOM and PyYAML consumes it.
* ``*.csproj`` -- the XML spec permits a BOM and ElementTree consumes it.
* ``requirements.txt`` -- already handled, via ``detect_encoding``.
* ``pyproject.toml`` -- **pip refuses it**, in
  ``pip/_internal/pyproject.py`` -> ``tomllib.loads``. A BOM-prefixed
  pyproject.toml cannot be installed, so refusing it is the honest answer.
* ``go.mod`` -- **Go refuses it.** ``golang.org/x/mod/modfile`` has no BOM
  handling, and its ``ModulePath`` tests ``bytes.HasPrefix(line, moduleStr)``,
  which a BOM defeats exactly as it defeated ours.

So the rule is not "strip every BOM" and not "refuse every BOM". Where the
format's own tooling reads the file, kospex must read it too, or it reports a
working repository as broken. Where the tooling refuses it, kospex refuses it as
well -- but must say *why*, because "no `module` directive" about a file whose
first directive is `module` sends the reader looking for the wrong defect.

UTF-16 is refused everywhere here, which is also what npm does: a UTF-16
package.json fails with EJSONPARSE.
"""
import codecs

import pytest

from kospex.extractors.errors import UnreadableManifest
from kospex.extractors.gomod import extract_gomod, parse_gomod
from kospex.extractors.npmlock import extract_package_lock
from kospex.extractors.nuget import extract_csproj
from kospex.extractors.pnpm import extract_pnpm_lock
from kospex_dependencies import KospexDependencies

PACKAGE_JSON = '{\n  "dependencies": {"lodash": "4.17.21"}\n}\n'

PACKAGE_LOCK = """{
  "name": "l",
  "version": "1.0.0",
  "lockfileVersion": 3,
  "packages": {
    "": {"name": "l", "version": "1.0.0"},
    "node_modules/lodash": {
      "version": "4.17.21",
      "resolved": "https://registry.npmjs.org/lodash/-/lodash-4.17.21.tgz"
    }
  }
}
"""

PNPM_LOCK = """lockfileVersion: '9.0'

importers:

  .:
    dependencies:
      lodash:
        specifier: 4.17.21
        version: 4.17.21

packages:

  lodash@4.17.21:
    resolution: {integrity: sha512-deadbeef}
"""

CSPROJ = """<Project Sdk="Microsoft.NET.Sdk">
  <ItemGroup>
    <PackageReference Include="Newtonsoft.Json" Version="13.0.3" />
  </ItemGroup>
</Project>
"""

REQUIREMENTS = "requests==2.31.0\n"

PYPROJECT = """[project]
name = "p"
version = "1.0.0"
dependencies = ["requests==2.31.0"]
"""

GOMOD = """module example.com/m

go 1.22

require github.com/pkg/errors v0.9.1
"""


def _write(tmp_path, name, text, prefix=b""):
    """Write `text` as UTF-8, optionally prefixed with raw `prefix` bytes."""
    path = tmp_path / name
    path.write_bytes(prefix + text.encode("utf-8"))
    return str(path)


@pytest.fixture
def kdeps():
    return KospexDependencies()


class TestUtf8BomIsReadWhereTheEcosystemReadsIt:
    """The parser must produce the same records with and without the BOM."""

    def test_package_json(self, tmp_path, kdeps):
        plain_dir = tmp_path / "plain"
        bom_dir = tmp_path / "bom"
        plain_dir.mkdir()
        bom_dir.mkdir()
        plain = _write(plain_dir, "package.json", PACKAGE_JSON)
        bommed = _write(bom_dir, "package.json", PACKAGE_JSON, codecs.BOM_UTF8)

        records = kdeps.parse_package_json(file_path=bommed)
        assert [r["package_name"] for r in records] == ["lodash"]

        # Byte-identical to the BOM-less file, field by field: reading it is
        # not enough if the BOM leaks into a value.
        def without_path(rows):
            return [{k: v for k, v in row.items() if k != "file_path"}
                    for row in rows]

        assert without_path(records) == without_path(
            kdeps.parse_package_json(file_path=plain))

    def test_package_lock_json(self, tmp_path):
        bommed = _write(tmp_path, "package-lock.json", PACKAGE_LOCK,
                        codecs.BOM_UTF8)
        records = extract_package_lock(bommed)
        assert [r["package_name"] for r in records] == ["lodash"]

    def test_pnpm_lock_yaml(self, tmp_path):
        """Already works -- a regression guard, since the YAML spec allows it."""
        bommed = _write(tmp_path, "pnpm-lock.yaml", PNPM_LOCK, codecs.BOM_UTF8)
        records = extract_pnpm_lock(bommed)
        assert [r["package_name"] for r in records] == ["lodash"]

    def test_csproj(self, tmp_path):
        """Already works -- a regression guard, since the XML spec allows it."""
        bommed = _write(tmp_path, "Acme.csproj", CSPROJ, codecs.BOM_UTF8)
        records = extract_csproj(bommed)
        assert [r["package_name"] for r in records] == ["Newtonsoft.Json"]

    def test_requirements_txt(self, tmp_path, kdeps):
        """Already works, via detect_encoding -- guard it from the same regression."""
        bommed = _write(tmp_path, "requirements.txt", REQUIREMENTS,
                        codecs.BOM_UTF8)
        records = kdeps.parse_pip_requirements_file(bommed)
        assert [r["package_name"] for r in records] == ["requests"]


class TestUtf8BomIsRefusedWhereTheEcosystemRefusesIt:
    """Refusing is right here -- but the message must name the BOM.

    A reader told "no `module` directive" about a file beginning with `module`
    goes looking for a defect that is not there. The outcome is the same either
    way (`parse_error`); the operator's next ten minutes are not.
    """

    def test_pyproject_toml_raises_and_says_why(self, tmp_path, kdeps):
        bommed = _write(tmp_path, "pyproject.toml", PYPROJECT, codecs.BOM_UTF8)
        with pytest.raises(UnreadableManifest) as exc:
            kdeps.parse_pyproject_file(bommed)
        assert "BOM" in str(exc.value), str(exc.value)

    def test_gomod_raises_and_says_why_not_no_module(self, tmp_path):
        bommed = _write(tmp_path, "go.mod", GOMOD, codecs.BOM_UTF8)
        with pytest.raises(UnreadableManifest) as exc:
            extract_gomod(bommed)
        message = str(exc.value)
        assert "BOM" in message, message
        assert "no `module` directive" not in message, message

    def test_gomod_raise_lives_in_parse_gomod_too(self, tmp_path):
        """parse_go_mod_from_file delegates here, so the contract holds here."""
        bommed = _write(tmp_path, "go.mod", GOMOD, codecs.BOM_UTF8)
        with pytest.raises(UnreadableManifest):
            parse_gomod(bommed)

    def test_a_real_go_mod_without_a_bom_still_parses(self, tmp_path):
        plain = _write(tmp_path, "go.mod", GOMOD)
        records = extract_gomod(plain)
        assert [r["package_name"] for r in records] == ["github.com/pkg/errors"]

    def test_a_go_mod_genuinely_missing_module_still_says_so(self, tmp_path):
        """The BOM check must not take over the gate it sits in front of."""
        path = _write(tmp_path, "go.mod", "require github.com/pkg/errors v0.9.1\n")
        with pytest.raises(UnreadableManifest) as exc:
            extract_gomod(path)
        assert "no `module` directive" in str(exc.value)


class TestUtf16IsRefused:
    """npm refuses a UTF-16 package.json (EJSONPARSE), so kospex may too.

    Pinned rather than fixed: no file on a development estate is UTF-16 except
    the requirements.txt that drove `detect_encoding`, and reading one as though
    the ecosystem would install it reports dependencies npm cannot see.
    """

    def test_package_json(self, tmp_path, kdeps):
        path = tmp_path / "package.json"
        path.write_bytes(PACKAGE_JSON.encode("utf-16"))
        with pytest.raises(Exception):
            kdeps.parse_package_json(file_path=str(path))

    def test_package_lock_json(self, tmp_path):
        path = tmp_path / "package-lock.json"
        path.write_bytes(PACKAGE_LOCK.encode("utf-16"))
        with pytest.raises(UnreadableManifest):
            extract_package_lock(str(path))


class TestNoSourceFileCarriesAnInvisibleBom:
    """A BOM in SOURCE must be written as an escape, never as the character.

    `startswith("\ufeff")` and `startswith("<the character>")` behave
    identically, so this is a readability rule rather than a correctness one --
    but it is the readability of a security check. The character is invisible in
    GitHub's diff view, in `git show` and in most editors, so the second form
    renders as `startswith("")`: a test for the empty string, which would be
    always true and therefore an obvious bug. A reviewer cannot tell the
    difference between right and catastrophically wrong.

    It reached `gomod.py` because writing the escape produced the character,
    which is the mistake this guards -- it is not a hypothetical. It is also
    fragile: anything that normalises the file can drop the character and
    silently change what the check compares against.

    Scoped to `.py` sources. Test *fixtures* that carry a real BOM are data and
    are exactly what the rest of this module needs.
    """

    def test_no_py_file_has_an_inline_bom(self):
        import pathlib

        root = pathlib.Path(__file__).resolve().parent.parent
        bom = b"\xef\xbb\xbf"
        offenders = []
        for directory in ("src", "tests"):
            for path in (root / directory).rglob("*.py"):
                raw = path.read_bytes()
                # A BOM at offset 0 is the file's own encoding mark, not an
                # inline literal, and is a different question.
                body = raw[3:] if raw.startswith(bom) else raw
                if bom in body:
                    offenders.append(str(path.relative_to(root)))

        assert not offenders, (
            "a U+FEFF character is written literally in these sources; it is "
            "invisible in review and renders as an empty string. Write the "
            'escape instead:\n  ' + "\n  ".join(offenders))
