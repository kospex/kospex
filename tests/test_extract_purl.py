"""KospexDependencies.extract_purl must parse namespaced purls (#244).

A purl is pkg:type/namespace/name@version: the namespace may contain one or
more "/" (every golang module, maven coordinate, scoped npm package), and the
version follows the LAST "@" so a scoped npm name starting with "@" survives.
Percent-encoded namespace/name segments are decoded.

The tests only need the pure function, so modules that extract_purl never
touches are stubbed only when the real import fails; in a full dev environment
the real modules load untouched.
"""
import importlib
import sys
import types

import pytest


def _import_or_stub(name, attrs):
    try:
        importlib.import_module(name)
    except ImportError:
        module = types.ModuleType(name)
        for attr in attrs:
            setattr(module, attr, object)
        sys.modules[name] = module


def _load_kospex_dependencies():
    sys.path.insert(0, "src")
    _import_or_stub("sqlite_utils", ["Database"])
    _import_or_stub("prettytable", ["PrettyTable"])
    _import_or_stub("dotenv", ["load_dotenv"])
    _import_or_stub("panopticas", [])
    import kospex_dependencies

    return kospex_dependencies.KospexDependencies


def _kd():
    cls = _load_kospex_dependencies()
    return cls.__new__(cls)  # extract_purl touches no instance state


def test_unnamespaced_purl_unchanged():
    assert _kd().extract_purl("pkg:pypi/requests@2.31.0") == {
        "ecosystem": "pypi",
        "package_name": "requests",
        "package_version": "2.31.0",
    }


@pytest.mark.parametrize("purl,name,version", [
    # deps.dev emits these shapes for -ecosystem go / maven / scoped npm (#244)
    ("pkg:golang/github.com/gorilla/mux@v1.8.0",
     "github.com/gorilla/mux", "v1.8.0"),
    ("pkg:maven/org.apache.commons/commons-lang3@3.0",
     "org.apache.commons/commons-lang3", "3.0"),
    ("pkg:npm/%40types/uuid@10.0.0", "@types/uuid", "10.0.0"),
    # the unencoded scope that used to answer package_name "" / version "types"
    ("pkg:npm/@types/uuid@9.0.0", "@types/uuid", "9.0.0"),
])
def test_namespaced_purls(purl, name, version):
    result = _kd().extract_purl(purl)
    assert result["package_name"] == name
    assert result["package_version"] == version
    assert result["ecosystem"] == purl.split(":")[1].split("/")[0]


def test_percent_encoded_namespace_is_decoded():
    # purl-spec example: some producers percent-encode the "/" of a golang path
    result = _kd().extract_purl("pkg:golang/github.com%2Fgorilla%2Fmux@v1.8.0")
    assert result == {
        "ecosystem": "golang",
        "package_name": "github.com/gorilla/mux",
        "package_version": "v1.8.0",
    }


def test_percent_encoded_version_is_decoded():
    result = _kd().extract_purl("pkg:pypi/dulwich@0.22%2B1")
    assert result["package_version"] == "0.22+1"


@pytest.mark.parametrize("purl", [
    "pkg:pypi/requests",           # versionless purl
    "pkg:npm/@types/uuid",         # versionless scoped purl
    "pkg:golang/github.com/gorilla/mux",  # versionless namespaced purl
])
def test_versionless_purl_raises_value_error(purl):
    with pytest.raises(ValueError):
        _kd().extract_purl(purl)
