"""Tests for the pure go.mod extractor (sub-project C1).

Mirrors the pnpm extractor's contract: pure (no DB, no CLI, no enrichment),
returns records shaped to KospexDependencies.get_package_template(), and
returns [] rather than raising on a missing or unreadable file.

Parsing rules themselves are covered by tests/test_gomod_parsing.py — these
cover the extractor shape and the direct/indirect classification that
krunner osi consumes.
"""
from kospex.extractors.gomod import _template, extract_gomod


def _write(tmp_path, content):
    p = tmp_path / "go.mod"
    p.write_text(content)
    return str(p)


class TestTemplateContract:
    def test_template_matches_get_package_template(self):
        from kospex_dependencies import KospexDependencies
        assert set(_template().keys()) == set(
            KospexDependencies().get_package_template().keys()
        )


class TestExtract:
    def test_records_direct_and_indirect(self, tmp_path):
        path = _write(tmp_path, """module x
go 1.21

require (
    github.com/pkg/errors v0.9.1
    github.com/stretchr/testify v1.8.4 // indirect
)
""")

        records = extract_gomod(path)

        by_name = {r["package_name"]: r for r in records}
        assert set(by_name) == {"github.com/pkg/errors", "github.com/stretchr/testify"}
        assert by_name["github.com/pkg/errors"]["requirements_type"] == "direct"
        assert by_name["github.com/stretchr/testify"]["requirements_type"] == "indirect"

    def test_records_carry_version(self, tmp_path):
        path = _write(tmp_path, "module x\nrequire github.com/spf13/cobra v1.8.0\n")

        records = extract_gomod(path)

        assert records[0]["package_name"] == "github.com/spf13/cobra"
        assert records[0]["package_version"] == "v1.8.0"

    def test_records_have_the_full_template_shape(self, tmp_path):
        """krunner osi consumes these unchanged, so every template key must exist."""
        path = _write(tmp_path, "module x\nrequire github.com/a/b v1.0.0\n")

        records = extract_gomod(path)

        assert set(records[0].keys()) == set(_template().keys())

    def test_missing_file_returns_empty(self, tmp_path):
        assert extract_gomod(str(tmp_path / "nope.mod")) == []

    def test_gomod_with_no_requires_returns_empty(self, tmp_path):
        assert extract_gomod(_write(tmp_path, "module x\ngo 1.21\n")) == []
