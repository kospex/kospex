"""Extractor for GitHub Actions workflow YAML files.

Pulls every ``uses:`` reference (step actions and job-level reusable
workflows) out of a workflow file and returns them as records, each
enriched with a classification of the ``uses:`` string — owner, name,
pin type, pinned ref value, and whether the owner is a strict
GitHub-organization-owned namespace. See :func:`parse_action`.

Recognised ``uses:`` forms (per GitHub Actions workflow syntax):

- ``owner/repo@ref`` and ``owner/repo/path@ref`` — third-party or
  first-party actions, e.g. ``actions/checkout@v4`` or
  ``actions/cache/restore@v4``.
- ``./path/to/action`` — local action defined in the same repo at
  ``.github/actions/<name>/action.yml``. Three flavours exist:
  composite, JavaScript, and Docker — distinguished by the action's
  ``runs.using`` metadata, not by the ``uses:`` string. The leading
  ``./`` is required by GitHub for local refs.
- ``./.github/workflows/<name>.yml`` — local reusable workflow,
  referenced at job level (``jobs.<job>.uses``, not in ``steps``).
- ``docker://image[:tag]`` — Docker Hub image, not a GitHub action.

References:

- About custom actions:
  https://docs.github.com/en/actions/creating-actions/about-custom-actions
- Creating a composite action:
  https://docs.github.com/en/actions/creating-actions/creating-a-composite-action
- Reusing workflows:
  https://docs.github.com/en/actions/using-workflows/reusing-workflows
"""

import re

import yaml

from kospex_utils import get_kospex_logger

logger = get_kospex_logger("extractors.workflows")

# 40-character hex SHAs (case-insensitive — git stores lowercase but
# we don't care which form an audit target used).
_HEX40 = re.compile(r"[a-f0-9]{40}", re.IGNORECASE)

# Strict GitHub-organization-owned namespaces. Microsoft / Azure are
# parent-company-owned and intentionally excluded.
_GITHUB_ACTION_OWNERS = frozenset({"actions", "github"})


def parse_action(uses):
    """Classify a GitHub Actions ``uses:`` string.

    Args:
        uses: The full ``uses:`` value from a workflow step or job.

    Returns:
        dict with five keys:

        - ``action_owner`` — first ``/``-separated segment before ``@``.
          ``"docker"`` for ``docker://...`` references; empty for local
          ``./...`` references.
        - ``action_name`` — everything between owner and ``@``. For
          ``actions/cache/restore@v4`` this is ``"cache/restore"``; for
          local refs the full path; for docker refs the image string
          (including any ``:tag``).
        - ``pinned_version`` — the ref after ``@``, or empty when there
          is no pin.
        - ``pin_type`` — ``"HASH"`` for 40-char hex SHAs, ``"TAG"`` for
          anything else after ``@``, ``"NONE"`` when no ``@`` is present.
        - ``github_action`` — ``"yes"`` only when the owner is in the
          strict GitHub-owned allowlist (``actions``, ``github``);
          ``"no"`` otherwise.
    """
    if uses.startswith("./") or uses.startswith(".\\"):
        return {
            "action_owner": "",
            "action_name": uses,
            "pinned_version": "",
            "pin_type": "NONE",
            "github_action": "no",
        }

    if uses.startswith("docker://"):
        return {
            "action_owner": "docker",
            "action_name": uses[len("docker://"):],
            "pinned_version": "",
            "pin_type": "NONE",
            "github_action": "no",
        }

    if "@" in uses:
        pre_ref, ref = uses.rsplit("@", 1)
    else:
        pre_ref, ref = uses, ""

    parts = pre_ref.split("/", 1)
    owner = parts[0]
    name = parts[1] if len(parts) > 1 else ""

    if not ref:
        pin_type = "NONE"
    elif _HEX40.fullmatch(ref):
        pin_type = "HASH"
    else:
        pin_type = "TAG"

    return {
        "action_owner": owner,
        "action_name": name,
        "pinned_version": ref,
        "pin_type": pin_type,
        "github_action": "yes" if owner in _GITHUB_ACTION_OWNERS else "no",
    }


def extract_workflow_actions(filename):
    """Parse a GitHub Actions workflow YAML file and return a list of
    action references (``uses:`` values), each enriched with
    classification fields from :func:`parse_action`.

    Args:
        filename: Path to a workflow YAML file on disk.

    Returns:
        list[dict]: One record per ``uses:`` reference, with keys
        ``action`` (the full ``uses:`` string), ``job``, ``step_name``
        (or ``"(reusable workflow)"`` for job-level ``uses:``), plus the
        five classification keys from :func:`parse_action`. Returns an
        empty list on parse error, missing file, or a workflow with no
        actions.
    """
    try:
        with open(filename) as f:
            workflow = yaml.safe_load(f)
    except yaml.YAMLError as e:
        logger.warning("Error parsing YAML file %s: %s", filename, e)
        return []
    except FileNotFoundError:
        logger.warning("File not found: %s", filename)
        return []

    if not workflow or not isinstance(workflow, dict):
        return []

    actions = []
    jobs = workflow.get("jobs", {})
    if not isinstance(jobs, dict):
        return []

    for job_name, job_config in jobs.items():
        if not isinstance(job_config, dict):
            continue

        if "uses" in job_config:
            uses = job_config["uses"]
            actions.append({
                "action": uses,
                "job": job_name,
                "step_name": "(reusable workflow)",
                **parse_action(uses),
            })

        steps = job_config.get("steps", [])
        if not isinstance(steps, list):
            continue

        for step in steps:
            if not isinstance(step, dict):
                continue
            if "uses" in step:
                uses = step["uses"]
                actions.append({
                    "action": uses,
                    "job": job_name,
                    "step_name": step.get("name", ""),
                    **parse_action(uses),
                })

    return actions
