"""File-type extractors for kospex.

This package holds pure, file-in / records-out extractors that pull
domain-specific records out of source files (GitHub Actions workflows,
dependency lockfiles, build configs, etc.).

Conventions:

- One module per ecosystem (e.g. ``workflows.py``, ``yarn.py``, ``maven.py``).
- Module-level functions, not classes — promote to a class only when
  shared state appears.
- Pure: take a file path, return records. No DB access, no CLI concerns,
  no I/O beyond reading the file under analysis.
- Return shape is ``list[dict]`` for record extractors (one record per
  discovered item) or ``dict`` for config extractors (single snapshot).
- On parse error or missing file: log a warning and return ``[]`` (or
  ``{}``). Do not raise — extractors run across many repos and one bad
  file should not crash the run.

This package is **not** an SBOM generator. SBOMs come from tools that
invoke the build system to get a fully resolved tree
(``cyclonedx-bom``, ``cyclonedx-maven-plugin``, etc.). The extractors
here give static cross-repo visibility into declared dependencies and
references — the two layers are complementary.
"""
