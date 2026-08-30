"""go.mod parsing and assessment.

Covers two defects:

- #177: `require <module> <version>` on a single line, outside a `require ( ... )`
  block, was never parsed. Both forms are valid go.mod syntax.
- #178: `// indirect` modules were parsed and then discarded, so transitive Go
  dependencies never reached dependency_data. They are now recorded as
  PACKAGE_USE_TRANSITIVE, with the same deps.dev enrichment as direct ones.
"""
import kospex_schema as KospexSchema
from kospex_dependencies import KospexDependencies


def _write(tmp_path, content):
    p = tmp_path / "go.mod"
    p.write_text(content)
    return str(p)


def _kdeps():
    """A KospexDependencies with deps.dev stubbed — these tests measure parsing
    and classification, not network lookups."""
    kd = KospexDependencies()
    calls = []

    def fake_record(package_type, package_name, package_version):
        calls.append((package_type, package_name, package_version))
        return {
            "package_name": package_name,
            "package_version": package_version,
            "package_type": package_type,
        }

    kd.depsdev_record = fake_record
    return kd, calls


# --- #177: single-line require ---------------------------------------------


def test_single_line_require_is_parsed(tmp_path):
    kd, _ = _kdeps()
    path = _write(tmp_path, "module x\ngo 1.21\nrequire github.com/spf13/cobra v1.8.0\n")

    deps = kd.parse_go_mod_from_file(path)

    assert [(d["module"], d["version"]) for d in deps] == [
        ("github.com/spf13/cobra", "v1.8.0")
    ]


def test_single_line_require_marked_indirect(tmp_path):
    kd, _ = _kdeps()
    path = _write(tmp_path, "module x\nrequire github.com/x/y v1.0.0 // indirect\n")

    deps = kd.parse_go_mod_from_file(path)

    assert len(deps) == 1
    assert deps[0]["indirect"] is True


def test_block_and_single_line_requires_both_parsed(tmp_path):
    kd, _ = _kdeps()
    path = _write(tmp_path, """module x
go 1.21

require (
    github.com/pkg/errors v0.9.1
)

require github.com/spf13/cobra v1.8.0
""")

    deps = kd.parse_go_mod_from_file(path)

    assert {d["module"] for d in deps} == {
        "github.com/pkg/errors", "github.com/spf13/cobra",
    }


def test_other_directives_are_not_parsed_as_requires(tmp_path):
    """exclude / replace / retract must stay out.

    They are currently skipped only incidentally, because nothing but
    `require (` sets the in-block flag — a naive fix for #177 could start
    ingesting them.
    """
    kd, _ = _kdeps()
    path = _write(tmp_path, """module x
go 1.21

exclude github.com/bad/pkg v1.0.0

replace github.com/old/pkg => github.com/new/pkg v2.0.0

retract v1.0.1

replace (
    github.com/a/b => github.com/c/d v1.2.3
)

require github.com/real/dep v1.0.0
""")

    deps = kd.parse_go_mod_from_file(path)

    assert [d["module"] for d in deps] == ["github.com/real/dep"]


# --- #178: indirect recorded as transitive ---------------------------------


def test_indirect_deps_are_recorded_as_transitive(tmp_path):
    kd, _ = _kdeps()
    path = _write(tmp_path, """module x
go 1.21

require (
    github.com/pkg/errors v0.9.1
    github.com/stretchr/testify v1.8.4 // indirect
)
""")

    records = kd.gomod_assess(path)

    by_name = {r["package_name"]: r for r in records}
    assert set(by_name) == {"github.com/pkg/errors", "github.com/stretchr/testify"}
    assert by_name["github.com/pkg/errors"]["package_use"] == KospexSchema.PACKAGE_USE_DIRECT
    assert by_name["github.com/stretchr/testify"]["package_use"] == KospexSchema.PACKAGE_USE_TRANSITIVE


def test_indirect_deps_are_enriched(tmp_path):
    """Unlike pnpm, go.mod indirect deps get deps.dev enrichment — a transitive
    dependency carrying a known advisory is the most valuable row of all."""
    kd, calls = _kdeps()
    path = _write(tmp_path, """module x
require (
    github.com/a/direct v1.0.0
    github.com/b/indirect v2.0.0 // indirect
)
""")

    kd.gomod_assess(path)

    assert ("go", "github.com/b/indirect", "v2.0.0") in calls


def test_comment_lines_inside_a_require_block_are_ignored(tmp_path):
    """A bare comment inside the block splits into >=2 parts, so without a guard
    it is recorded as a module named '//' with version 'grouped'."""
    kd, _ = _kdeps()
    path = _write(tmp_path, """module x
require (
    // grouped for clarity
    github.com/real/dep v1.0.0
)
""")

    deps = kd.parse_go_mod_from_file(path)

    assert [d["module"] for d in deps] == ["github.com/real/dep"]


def test_gomod_with_no_dependencies_returns_empty(tmp_path):
    kd, _ = _kdeps()
    path = _write(tmp_path, "module x\ngo 1.21\n")

    assert kd.gomod_assess(path) == []
