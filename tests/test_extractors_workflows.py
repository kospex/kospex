"""Tests for kospex.extractors.workflows.

Covers the edge cases listed in deploy-kospex/feature/find-actions/SPEC.md
and planning/find-actions-migration-plan.md §5.
"""

from pathlib import Path

from kospex.extractors.workflows import extract_workflow_actions, parse_action


def _write(tmp_path: Path, content: str) -> str:
    """Write a workflow file to tmp_path and return the string path."""
    path = tmp_path / "ci.yml"
    path.write_text(content)
    return str(path)


class TestStandardActions:
    """Standard action references: owner/repo@ref and owner/repo/path@ref."""

    def test_owner_repo_at_ref(self, tmp_path):
        content = """
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
"""
        actions = extract_workflow_actions(_write(tmp_path, content))
        assert len(actions) == 1
        assert actions[0]["action"] == "actions/checkout@v4"
        assert actions[0]["job"] == "build"

    def test_owner_repo_path_at_ref(self, tmp_path):
        content = """
jobs:
  build:
    steps:
      - uses: actions/cache/restore@v4
"""
        actions = extract_workflow_actions(_write(tmp_path, content))
        assert actions[0]["action"] == "actions/cache/restore@v4"

    def test_step_name_captured(self, tmp_path):
        content = """
jobs:
  build:
    steps:
      - name: Check out code
        uses: actions/checkout@v4
"""
        actions = extract_workflow_actions(_write(tmp_path, content))
        assert actions[0]["step_name"] == "Check out code"

    def test_step_without_name_yields_empty_string(self, tmp_path):
        content = """
jobs:
  build:
    steps:
      - uses: actions/checkout@v4
"""
        actions = extract_workflow_actions(_write(tmp_path, content))
        assert actions[0]["step_name"] == ""


class TestSpecialActionForms:
    """docker://, local ./, reusable workflows."""

    def test_docker_action(self, tmp_path):
        content = """
jobs:
  build:
    steps:
      - uses: docker://alpine:3.18
"""
        actions = extract_workflow_actions(_write(tmp_path, content))
        assert actions[0]["action"] == "docker://alpine:3.18"

    def test_local_action(self, tmp_path):
        content = """
jobs:
  build:
    steps:
      - uses: ./.github/actions/my-action
"""
        actions = extract_workflow_actions(_write(tmp_path, content))
        assert actions[0]["action"] == "./.github/actions/my-action"

    def test_job_level_reusable_workflow(self, tmp_path):
        content = """
jobs:
  call-shared:
    uses: ./.github/workflows/shared.yml
"""
        actions = extract_workflow_actions(_write(tmp_path, content))
        assert len(actions) == 1
        assert actions[0]["action"] == "./.github/workflows/shared.yml"
        assert actions[0]["job"] == "call-shared"
        assert actions[0]["step_name"] == "(reusable workflow)"

    def test_external_reusable_workflow(self, tmp_path):
        content = """
jobs:
  call-external:
    uses: octo-org/example-repo/.github/workflows/reusable.yml@main
"""
        actions = extract_workflow_actions(_write(tmp_path, content))
        assert actions[0]["action"] == (
            "octo-org/example-repo/.github/workflows/reusable.yml@main"
        )


class TestSkippedSteps:
    """Steps without `uses:` should be skipped silently."""

    def test_run_only_step_skipped(self, tmp_path):
        content = """
jobs:
  build:
    steps:
      - name: Echo something
        run: echo hello
      - uses: actions/checkout@v4
"""
        actions = extract_workflow_actions(_write(tmp_path, content))
        assert len(actions) == 1
        assert actions[0]["action"] == "actions/checkout@v4"

    def test_workflow_with_only_run_steps_yields_empty(self, tmp_path):
        content = """
jobs:
  build:
    steps:
      - run: echo hello
      - run: make test
"""
        actions = extract_workflow_actions(_write(tmp_path, content))
        assert actions == []


class TestMultipleJobsAndSteps:
    """Multiple jobs and multi-step jobs all surface their actions."""

    def test_multiple_jobs(self, tmp_path):
        content = """
jobs:
  lint:
    steps:
      - uses: actions/checkout@v4
  test:
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
"""
        actions = extract_workflow_actions(_write(tmp_path, content))
        assert len(actions) == 3
        jobs = {a["job"] for a in actions}
        assert jobs == {"lint", "test"}


class TestErrorHandling:
    """Invalid YAML, missing file, malformed structure."""

    def test_missing_file_returns_empty_list(self, tmp_path):
        actions = extract_workflow_actions(str(tmp_path / "does-not-exist.yml"))
        assert actions == []

    def test_invalid_yaml_returns_empty_list(self, tmp_path):
        path = tmp_path / "broken.yml"
        path.write_text("jobs:\n  build:\n    steps:\n  - uses: actions/checkout@v4\n  bad: : :")
        actions = extract_workflow_actions(str(path))
        assert actions == []

    def test_empty_file_returns_empty_list(self, tmp_path):
        path = tmp_path / "empty.yml"
        path.write_text("")
        actions = extract_workflow_actions(str(path))
        assert actions == []

    def test_yaml_with_no_jobs_returns_empty_list(self, tmp_path):
        content = """
name: My workflow
on: push
"""
        actions = extract_workflow_actions(_write(tmp_path, content))
        assert actions == []

    def test_non_dict_root_returns_empty_list(self, tmp_path):
        path = tmp_path / "list.yml"
        path.write_text("- one\n- two\n")
        actions = extract_workflow_actions(str(path))
        assert actions == []

    def test_non_dict_jobs_returns_empty_list(self, tmp_path):
        content = """
jobs:
  - this is a list, not a dict
"""
        actions = extract_workflow_actions(_write(tmp_path, content))
        assert actions == []

    def test_non_list_steps_skipped(self, tmp_path):
        content = """
jobs:
  weird:
    steps: not-a-list
"""
        actions = extract_workflow_actions(_write(tmp_path, content))
        assert actions == []


class TestParseAction:
    """Classify uses: strings into owner/name/pin/github_action."""

    def test_standard_action_with_tag(self):
        assert parse_action("actions/checkout@v4") == {
            "action_owner": "actions",
            "action_name": "checkout",
            "pinned_version": "v4",
            "pin_type": "TAG",
            "github_action": "yes",
        }

    def test_standard_action_with_sha(self):
        sha = "de0fac2e4500dabe0009e672147c4adc9a8b35d8"  # 40 hex chars
        result = parse_action(f"actions/checkout@{sha}")
        assert result["pin_type"] == "HASH"
        assert result["pinned_version"] == sha
        assert result["action_owner"] == "actions"
        assert result["github_action"] == "yes"

    def test_action_with_subpath(self):
        result = parse_action("actions/cache/restore@v4")
        assert result["action_owner"] == "actions"
        assert result["action_name"] == "cache/restore"
        assert result["github_action"] == "yes"

    def test_third_party_action(self):
        result = parse_action("docker/build-push-action@v5")
        assert result["action_owner"] == "docker"
        assert result["action_name"] == "build-push-action"
        assert result["github_action"] == "no"

    def test_github_org_action(self):
        result = parse_action("github/codeql-action/init@v3")
        assert result["action_owner"] == "github"
        assert result["action_name"] == "codeql-action/init"
        assert result["github_action"] == "yes"

    def test_microsoft_org_is_not_github_action(self):
        # Microsoft is GitHub's parent — deliberately excluded from
        # the strict allowlist.
        result = parse_action("microsoft/setup-msbuild@v2")
        assert result["github_action"] == "no"

    def test_local_action(self):
        assert parse_action("./.github/actions/my-action") == {
            "action_owner": "",
            "action_name": "./.github/actions/my-action",
            "pinned_version": "",
            "pin_type": "NONE",
            "github_action": "no",
        }

    def test_local_reusable_workflow(self):
        result = parse_action("./.github/workflows/shared.yml")
        assert result["pin_type"] == "NONE"
        assert result["pinned_version"] == ""
        assert result["github_action"] == "no"

    def test_docker_image(self):
        assert parse_action("docker://alpine:3.18") == {
            "action_owner": "docker",
            "action_name": "alpine:3.18",
            "pinned_version": "",
            "pin_type": "NONE",
            "github_action": "no",
        }

    def test_no_at_ref_is_none(self):
        result = parse_action("actions/checkout")
        assert result["pin_type"] == "NONE"
        assert result["pinned_version"] == ""

    def test_short_hex_is_tag_not_hash(self):
        # 39 hex chars — not a valid commit SHA
        result = parse_action("actions/checkout@de0fac2e4500dabe0009e672147c4adc9a8b35d")
        assert result["pin_type"] == "TAG"

    def test_uppercase_hex_still_hash(self):
        sha_upper = "DE0FAC2E4500DABE0009E672147C4ADC9A8B35D8"
        result = parse_action(f"actions/checkout@{sha_upper}")
        assert result["pin_type"] == "HASH"

    def test_branch_pin_classified_as_tag(self):
        # main / master are technically branches; binary schema treats
        # them as TAG (not-a-HASH).
        for branch in ("main", "master", "develop"):
            result = parse_action(f"actions/checkout@{branch}")
            assert result["pin_type"] == "TAG"
            assert result["pinned_version"] == branch

    def test_external_reusable_workflow(self):
        result = parse_action("octo-org/example-repo/.github/workflows/reusable.yml@main")
        assert result["action_owner"] == "octo-org"
        assert result["action_name"] == "example-repo/.github/workflows/reusable.yml"
        assert result["pinned_version"] == "main"
        assert result["pin_type"] == "TAG"
        assert result["github_action"] == "no"


class TestExtractIntegration:
    """extract_workflow_actions enriches each record with parse_action output."""

    def test_record_has_classification_fields(self, tmp_path):
        content = """
jobs:
  build:
    steps:
      - uses: actions/checkout@v4
"""
        actions = extract_workflow_actions(_write(tmp_path, content))
        assert len(actions) == 1
        rec = actions[0]
        assert rec["action"] == "actions/checkout@v4"
        assert rec["job"] == "build"
        assert rec["action_owner"] == "actions"
        assert rec["action_name"] == "checkout"
        assert rec["pinned_version"] == "v4"
        assert rec["pin_type"] == "TAG"
        assert rec["github_action"] == "yes"

    def test_reusable_workflow_record_has_classification(self, tmp_path):
        content = """
jobs:
  call-shared:
    uses: ./.github/workflows/shared.yml
"""
        actions = extract_workflow_actions(_write(tmp_path, content))
        rec = actions[0]
        assert rec["step_name"] == "(reusable workflow)"
        assert rec["action_owner"] == ""
        assert rec["pin_type"] == "NONE"
        assert rec["github_action"] == "no"
