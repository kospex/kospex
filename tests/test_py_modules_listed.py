"""Every flat module in src/ ships in the wheel.

pyproject.toml configures packages.find, which disables setuptools'
auto-discovery of py-modules, so each top-level src/*.py must be listed in
[tool.setuptools] py-modules by hand. A module missing from that list works in
an editable install and is absent from the built wheel, so the installed CLI
fails on import. That broke the 0.0.38 wheel.
"""
import pathlib
import tomllib

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_every_src_module_is_listed_in_py_modules():
    listed = set(tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["setuptools"]["py-modules"])
    on_disk = {p.stem for p in (ROOT / "src").glob("*.py")}
    assert on_disk - listed == set(), "add these to [tool.setuptools] py-modules"
    assert listed - on_disk == set(), "py-modules lists modules that no longer exist"
