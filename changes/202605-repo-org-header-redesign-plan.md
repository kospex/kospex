# Repo Header Redesign + New Org View — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the raw `repo_id` `<h1>` on `/repo/{repo_id}` with a breadcrumb + title header, and add a new `/org/{org_key}` page that mirrors the repo view, sharing one header partial.

**Architecture:** A new Jinja2 partial `_entity_header.html` renders `server / org / repo` breadcrumb + bold title + sub-label and is `{% include %}`d by both `repo_view.html` and a new `org_view.html`. The `/repo/{repo_id}` handler is extended to parse the id (404 on malformed) and pass header context. A new `/org/{org_key}` handler reuses the org-capable `KospexQuery` methods (`commit_ranges`, `tech_landscape`, `developers`, `repos`, `active_devs`).

**Tech Stack:** FastAPI, Jinja2, TailwindCSS, pytest + `fastapi.testclient.TestClient`, jinja2 Environment for partial unit tests.

**Spec:** `changes/202605-repo-org-header-redesign.md`

---

## File Structure

| File | Responsibility |
|------|----------------|
| `src/templates/_entity_header.html` | **New.** Pure presentational partial: breadcrumb + title + sub-label. Reads `git_server`, `org`, `org_key`, `entity_type`, optional `repo`. |
| `src/templates/repo_view.html` | **Modify.** Replace the raw `<h1>{{ repo_id }}</h1>` (lines 39–41) with the partial. Nothing else changes. |
| `src/templates/org_view.html` | **New.** Org view mirroring `repo_view.html`: shared header, org Commit Summary, Developer Status, Tech Landscape, plus a "Repositories" table. No author-summary / email-domains / repo quick-links. |
| `src/kweb2.py` | **Modify** `repo()` (lines 692–733): parse repo_id, 404 on malformed, pass header context, don't let `except Exception` swallow the 404. **Add** new `org_view()` route + handler after it. |
| `tests/test_repo_org_header.py` | **New.** Jinja render tests for the partial + TestClient 404 tests for both routes. |

---

## Task 1: Shared `_entity_header.html` partial

**Files:**
- Create: `src/templates/_entity_header.html`
- Test: `tests/test_repo_org_header.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_repo_org_header.py`:

```python
"""Tests for the shared entity header partial and repo/org route guards."""
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader

TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "src" / "templates"


def render_entity_header(**ctx):
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
    return env.get_template("_entity_header.html").render(**ctx)


def test_entity_header_repo_mode_breadcrumb_and_links():
    html = render_entity_header(
        git_server="github.com",
        org="apache",
        org_key="github.com~apache",
        repo="kafka",
        entity_type="Repository",
    )
    # Server segment links to the existing /orgs/{server} route
    assert 'href="/orgs/github.com"' in html
    # Org segment links to the new /org/{org_key} route
    assert 'href="/org/github.com~apache"' in html
    # Title + sub-label present
    assert "kafka" in html
    assert "Repository" in html
    # The page never links to its own /repo/ page
    assert 'href="/repo/' not in html


def test_entity_header_org_mode_org_is_current_segment():
    html = render_entity_header(
        git_server="github.com",
        org="apache",
        org_key="github.com~apache",
        entity_type="Organisation",
    )
    assert 'href="/orgs/github.com"' in html
    assert "apache" in html
    assert "Organisation" in html
    # In org mode the org is the current (non-link) segment
    assert 'href="/org/github.com~apache"' not in html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_repo_org_header.py -v`
Expected: FAIL — `jinja2.exceptions.TemplateNotFound: _entity_header.html`

- [ ] **Step 3: Create the partial**

Create `src/templates/_entity_header.html`:

```html
{# Shared entity header. Context: git_server, org, org_key, entity_type;
   optional: repo (present => repo page, absent => org page). #}
<nav class="text-sm text-gray-500 mb-2" aria-label="Breadcrumb">
    <a href="/orgs/{{ git_server }}" class="text-blue-600 hover:text-blue-800 underline">{{ git_server }}</a>
    <span class="mx-2 text-gray-300">/</span>
    {% if repo %}
    <a href="/org/{{ org_key }}" class="text-blue-600 hover:text-blue-800 underline">{{ org }}</a>
    <span class="mx-2 text-gray-300">/</span>
    <span class="font-semibold text-gray-900">{{ repo }}</span>
    {% else %}
    <span class="font-semibold text-gray-900">{{ org }}</span>
    {% endif %}
</nav>
<h1 class="text-3xl font-bold text-gray-900">{{ repo if repo else org }}</h1>
<div class="mt-1 mb-6 text-xs font-semibold uppercase tracking-wider text-gray-500">{{ entity_type }}</div>
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_repo_org_header.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add tests/test_repo_org_header.py src/templates/_entity_header.html
git commit -m "Add shared _entity_header breadcrumb partial"
```

---

## Task 2: Wire repo_view to the partial + 404 on malformed repo_id

**Files:**
- Modify: `src/kweb2.py:692-733` (the `repo()` handler)
- Modify: `src/templates/repo_view.html:39-41`
- Test: `tests/test_repo_org_header.py`

- [ ] **Step 1: Add the failing 404 test**

Append to `tests/test_repo_org_header.py`:

```python
@pytest.fixture(scope="module")
def client():
    pytest.importorskip("httpx", reason="httpx required for FastAPI TestClient")
    from fastapi.testclient import TestClient
    from kweb2 import app

    return TestClient(app)


def test_repo_route_404_on_malformed_id(client):
    resp = client.get("/repo/notavalidrepoid")
    assert resp.status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_repo_org_header.py::test_repo_route_404_on_malformed_id -v`
Expected: FAIL — returns 500 (current handler builds `KospexQuery` and the generic `except` raises `HTTPException(500)`), not 404.

- [ ] **Step 3a: Add the parse guard to the `repo()` handler**

In `src/kweb2.py`, replace:

```python
        logger.info(f"Repository view requested for repo: {repo_id}")

        kospex = KospexQuery()
```

with:

```python
        logger.info(f"Repository view requested for repo: {repo_id}")

        parsed = KospexUtils.parse_repo_id(repo_id)
        if not parsed:
            raise HTTPException(status_code=404, detail=f"Invalid repo_id: {repo_id}")

        kospex = KospexQuery()
```

- [ ] **Step 3b: Pass header context to the template**

In `src/kweb2.py`, replace:

```python
            {
                "repo_id": repo_id,
                "ranges": commit_ranges,
```

with:

```python
            {
                "repo_id": repo_id,
                "git_server": parsed["git_server"],
                "org": parsed["org"],
                "org_key": parsed["org_key"],
                "repo": parsed["repo"],
                "entity_type": "Repository",
                "ranges": commit_ranges,
```

- [ ] **Step 3c: Stop the generic `except` from swallowing the 404**

In `src/kweb2.py`, replace (this exact block is unique — it is preceded by the `repo_view.html` response):

```python
                "summary": summary,
            },
        )
    except Exception as e:
        logger.error(f"Error in repo endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")
```

with:

```python
                "summary": summary,
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in repo endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")
```

- [ ] **Step 3d: Swap the header markup in `repo_view.html`**

In `src/templates/repo_view.html`, replace:

```html
                    <h1 class="text-3xl font-bold text-gray-900 mb-6">
                        {{ repo_id }}
                    </h1>
```

with:

```html
                    {% include '_entity_header.html' %}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_repo_org_header.py -v`
Expected: PASS (all 3 tests). The 404 test now returns 404.

- [ ] **Step 5: Commit**

```bash
git add tests/test_repo_org_header.py src/kweb2.py src/templates/repo_view.html
git commit -m "Repo view: breadcrumb header + 404 on malformed repo_id"
```

---

## Task 3: New `/org/{org_key}` route + `org_view.html`

**Files:**
- Modify: `src/kweb2.py` (add handler immediately after the `repo()` handler, i.e. after its final `raise HTTPException(status_code=500, ...)` line)
- Create: `src/templates/org_view.html`
- Test: `tests/test_repo_org_header.py`

- [ ] **Step 1: Add the failing 404 test**

Append to `tests/test_repo_org_header.py`:

```python
def test_org_route_404_on_malformed_key(client):
    resp = client.get("/org/notavalidorgkey")
    assert resp.status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_repo_org_header.py::test_org_route_404_on_malformed_key -v`
Expected: FAIL — 404 because the route does not exist yet (no `/org/{org_key}` handler).

- [ ] **Step 3a: Add the `org_view()` handler**

In `src/kweb2.py`, immediately after the `repo()` function (after its closing `raise HTTPException(status_code=500, detail="Internal server error")` and the blank line that follows), add:

```python
@app.get("/org/{org_key}", response_class=HTMLResponse)
async def org_view(request: Request, org_key: str):
    """Display individual organisation information (mirrors the repo view)."""
    try:
        logger.info(f"Organisation view requested for org_key: {org_key}")

        parsed = KospexUtils.parse_org_key(org_key)
        if not parsed:
            raise HTTPException(status_code=404, detail=f"Invalid org_key: {org_key}")

        kospex = KospexQuery()
        commit_ranges = kospex.commit_ranges(org_key=org_key)
        techs = kospex.tech_landscape(org_key=org_key)

        developers = kospex.developers(org_key=org_key)
        developer_status = KospexUtils.repo_stats(developers, "last_commit")

        org_repos = kospex.repos(org_key=org_key)
        active_devs = kospex.active_devs()
        for row in org_repos:
            row["active_devs"] = active_devs.get(row["_repo_id"], 0)

        # TODO - make generic function for radar graph (in repo/developer views too)
        labels = []
        datapoints = []
        count = 0
        for tech in techs:
            labels.append(tech["Language"])
            datapoints.append(tech["count"])
            count += 1
            if count > 10:
                break

        return templates.TemplateResponse(
            request, "org_view.html",
            {
                "org_key": org_key,
                "git_server": parsed["git_server"],
                "org": parsed["org"],
                "entity_type": "Organisation",
                "ranges": commit_ranges,
                "landscape": techs,
                "developer_status": developer_status,
                "labels": labels,
                "datapoints": datapoints,
                "repos": org_repos,
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in org_view endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")
```

- [ ] **Step 3b: Create `src/templates/org_view.html`**

Create `src/templates/org_view.html`:

```html
<!doctype html>
<html lang="en">
    <head>
        <meta charset="UTF-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1.0" />
        <title>Organisation View - Kospex Web</title>
        <link rel="stylesheet" href="/static/css/tailwind.css" />
        <style>
            .dataTables_wrapper .dataTables_length select,
            .dataTables_wrapper .dataTables_filter input {
                @apply border border-gray-300 rounded px-3 py-2 text-sm;
            }
            .dataTables_wrapper .dataTables_length,
            .dataTables_wrapper .dataTables_filter,
            .dataTables_wrapper .dataTables_info,
            .dataTables_wrapper .dataTables_paginate {
                @apply text-sm text-gray-700;
            }
            .dataTables_wrapper .dataTables_paginate .paginate_button {
                @apply px-3 py-2 border border-gray-300 text-gray-700 hover:bg-gray-50;
            }
            .dataTables_wrapper .dataTables_paginate .paginate_button.current {
                @apply bg-blue-600 text-white border-blue-600;
            }
        </style>
    </head>
    <body class="bg-white">
        {% include '_header.html' %}

        <div class="container mx-auto px-4 mt-12">
            <!-- Organisation Header -->
            <div class="bg-white border border-gray-200 rounded-lg shadow-sm mb-8">
                <div class="p-6">
                    {% include '_entity_header.html' %}

                    <h2 class="text-2xl font-bold text-gray-900 mb-4">
                        Commit Summary
                    </h2>

                    <div class="overflow-x-auto">
                        <table class="min-w-full divide-y divide-gray-200" id="commits_table">
                            <thead class="bg-gray-50">
                                <tr>
                                    <th class="px-6 py-3 text-center text-xs font-medium text-gray-500 uppercase tracking-wider">Active</th>
                                    <th class="px-6 py-3 text-center text-xs font-medium text-gray-500 uppercase tracking-wider">Aging</th>
                                    <th class="px-6 py-3 text-center text-xs font-medium text-gray-500 uppercase tracking-wider">Stale</th>
                                    <th class="px-6 py-3 text-center text-xs font-medium text-gray-500 uppercase tracking-wider">Unmaintained</th>
                                </tr>
                            </thead>
                            <tbody class="bg-white divide-y divide-gray-200">
                                <tr class="hover:bg-gray-50">
                                    <td class="px-6 py-4 whitespace-nowrap text-sm text-gray-900 text-center">{{ ranges['active'] }}</td>
                                    <td class="px-6 py-4 whitespace-nowrap text-sm text-gray-900 text-center">{{ ranges['aging'] }}</td>
                                    <td class="px-6 py-4 whitespace-nowrap text-sm text-gray-900 text-center">{{ ranges['stale'] }}</td>
                                    <td class="px-6 py-4 whitespace-nowrap text-sm text-gray-900 text-center">{{ ranges['unmaintained'] }}</td>
                                </tr>
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>

            <!-- Developer Status -->
            {% if developer_status %}
            <div class="bg-white border border-gray-200 rounded-lg shadow-sm mb-8">
                <div class="p-6">
                    <h2 class="text-2xl font-bold text-gray-900 mb-6">Developer Status</h2>
                    <div class="overflow-x-auto">
                        <table class="min-w-full divide-y divide-gray-200" id="developer_status_table">
                            <thead class="bg-gray-50">
                                <tr>
                                    <th class="px-6 py-3 text-center text-xs font-medium text-gray-500 uppercase tracking-wider">Active</th>
                                    <th class="px-6 py-3 text-center text-xs font-medium text-gray-500 uppercase tracking-wider">Aging</th>
                                    <th class="px-6 py-3 text-center text-xs font-medium text-gray-500 uppercase tracking-wider">Stale</th>
                                    <th class="px-6 py-3 text-center text-xs font-medium text-gray-500 uppercase tracking-wider">Unmaintained</th>
                                </tr>
                            </thead>
                            <tbody class="bg-white divide-y divide-gray-200">
                                <tr class="hover:bg-gray-50">
                                    <td class="px-6 py-4 whitespace-nowrap text-sm text-gray-900 text-center">{{ developer_status.get('Active') }}</td>
                                    <td class="px-6 py-4 whitespace-nowrap text-sm text-gray-900 text-center">{{ developer_status.get('Aging') }}</td>
                                    <td class="px-6 py-4 whitespace-nowrap text-sm text-gray-900 text-center">{{ developer_status.get('Stale') }}</td>
                                    <td class="px-6 py-4 whitespace-nowrap text-sm text-gray-900 text-center">{{ developer_status.get('Unmaintained') }}</td>
                                </tr>
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>
            {% endif %} {% import '_radar_macro.html' as radar %}

            <!-- Technology Landscape -->
            {% if landscape %}
            <div class="bg-white border border-gray-200 rounded-lg shadow-sm mb-8">
                <div class="p-6">
                    <h2 class="text-2xl font-bold text-gray-900 mb-6">Technology Landscape</h2>
                    <div class="overflow-x-auto">
                        <table class="min-w-full divide-y divide-gray-200" id="tech_table">
                            <thead class="bg-gray-50">
                                <tr>
                                    <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Technology</th>
                                    <th class="px-6 py-3 text-right text-xs font-medium text-gray-500 uppercase tracking-wider"># Files</th>
                                </tr>
                            </thead>
                            <tbody class="bg-white divide-y divide-gray-200">
                                {% for row in landscape %}
                                <tr class="hover:bg-gray-50">
                                    <td class="px-6 py-4 whitespace-nowrap text-sm">
                                        <a href="/tech/{{ row['Language'] }}?org_key={{ org_key }}" class="text-blue-600 hover:text-blue-800 underline">{{ row['Language'] }}</a>
                                    </td>
                                    <td class="px-6 py-4 whitespace-nowrap text-sm text-gray-900 text-right">{{ row['count'] }}</td>
                                </tr>
                                {% endfor %}
                            </tbody>
                        </table>
                    </div>
                    <div class="mt-6">
                        {{ radar.render_radar("radarChartOrg", labels, datapoints) }}
                    </div>
                </div>
            </div>
            {% else %}
            <div class="bg-white border border-gray-200 rounded-lg shadow-sm mb-8">
                <div class="p-6 text-center">
                    <p class="text-lg font-semibold text-gray-900">No Technology Landscape found for organisation</p>
                </div>
            </div>
            {% endif %}

            <!-- Repositories in this organisation -->
            <div class="bg-white border border-gray-200 rounded-lg shadow-sm">
                <div class="p-6">
                    <h2 class="text-2xl font-bold text-gray-900 mb-6">Repositories</h2>
                    <div class="overflow-x-auto">
                        <table class="min-w-full divide-y divide-gray-200" id="org_repos_table">
                            <thead class="bg-gray-50">
                                <tr>
                                    <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Repo</th>
                                    <th class="px-6 py-3 text-right text-xs font-medium text-gray-500 uppercase tracking-wider"># Authors</th>
                                    <th class="px-6 py-3 text-right text-xs font-medium text-gray-500 uppercase tracking-wider"># Committers</th>
                                    <th class="px-6 py-3 text-right text-xs font-medium text-gray-500 uppercase tracking-wider"># Commits</th>
                                    <th class="px-6 py-3 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">Last Seen (days)</th>
                                    <th class="px-6 py-3 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">Active Devs</th>
                                    <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Status</th>
                                </tr>
                            </thead>
                            <tbody class="bg-white divide-y divide-gray-200">
                                {% for row in repos %}
                                <tr class="hover:bg-gray-50">
                                    <td class="px-6 py-4 whitespace-nowrap text-sm">
                                        <a href="/repo/{{ row['_repo_id'] }}" class="text-blue-600 hover:text-blue-800 underline">{{ row['_git_repo'] }}</a>
                                    </td>
                                    <td class="px-6 py-4 whitespace-nowrap text-sm text-gray-900 text-right">{{ row['authors'] }}</td>
                                    <td class="px-6 py-4 whitespace-nowrap text-sm text-gray-900 text-right">{{ row['committers'] }}</td>
                                    <td class="px-6 py-4 whitespace-nowrap text-sm text-gray-900 text-right">{{ row['commits'] }}</td>
                                    <td class="px-6 py-4 whitespace-nowrap text-sm text-gray-900 text-right">{{ row['days_ago'] }}</td>
                                    <td class="px-6 py-4 whitespace-nowrap text-sm text-right">
                                        <a href="/developers/active/{{ row['_repo_id'] }}" class="text-blue-600 hover:text-blue-800 underline">{{ row['active_devs'] }}</a>
                                    </td>
                                    <td class="px-6 py-4 whitespace-nowrap text-sm text-gray-900">{{ row['status'] }}</td>
                                </tr>
                                {% endfor %}
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>
        </div>

        {% include '_footer_scripts.html' %} {% include '_datatable_scripts.html' %}

        <script>
            $(document).ready(function () {
                $("#tech_table").DataTable({
                    order: [[1, "desc"]],
                    responsive: true,
                    pageLength: 25,
                    lengthMenu: [
                        [10, 25, 50, 100, -1],
                        [10, 25, 50, 100, "All"],
                    ],
                    dom: '<"flex flex-col sm:flex-row sm:items-center sm:justify-between mb-4"lf>rt<"flex flex-col sm:flex-row sm:items-center sm:justify-between mt-4"ip>',
                });
                $("#org_repos_table").DataTable({
                    order: [[3, "desc"]],
                    responsive: true,
                    pageLength: 25,
                    lengthMenu: [
                        [10, 25, 50, 100, -1],
                        [10, 25, 50, 100, "All"],
                    ],
                    dom: '<"flex flex-col sm:flex-row sm:items-center sm:justify-between mb-4"lf>rt<"flex flex-col sm:flex-row sm:items-center sm:justify-between mt-4"ip>',
                });
            });
        </script>
    </body>
</html>
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_repo_org_header.py -v`
Expected: PASS (all 4 tests, including `test_org_route_404_on_malformed_key`).

- [ ] **Step 5: Commit**

```bash
git add tests/test_repo_org_header.py src/kweb2.py src/templates/org_view.html
git commit -m "Add /org/{org_key} view mirroring the repo view"
```

---

## Task 4: Full-suite verification + mark spec done

**Files:**
- Modify: `changes/202605-repo-org-header-redesign.md`

- [ ] **Step 1: Run the full test suite**

Run: `pytest`
Expected: PASS — no regressions; the 4 new tests in `tests/test_repo_org_header.py` pass. If `httpx` is unavailable, the 2 TestClient tests are skipped (not failed) via `importorskip`; the 2 Jinja render tests still run and pass.

- [ ] **Step 2: Manual smoke check (optional, needs a populated DB + running server)**

Run: `python kweb2.py` then visit `/repo/<a real repo_id>` and `/org/<its server~owner>`.
Expected: repo page shows `server / org / repo` breadcrumb (server + org clickable) with bold title and `Repository` label; clicking `org` lands on the new org page showing the same header style (server clickable, org current, `Organisation` label), org Commit Summary, Developer Status, Tech Landscape, and a Repositories table whose rows link back to `/repo/{repo_id}`.

- [ ] **Step 3: Update the spec status**

In `changes/202605-repo-org-header-redesign.md`, replace:

```
**Status:** Design / spec (no code yet)
```

with:

```
**Status:** Implemented (see changes/202605-repo-org-header-redesign-plan.md)
```

- [ ] **Step 4: Commit**

```bash
git add changes/202605-repo-org-header-redesign.md
git commit -m "Mark repo/org header redesign spec as implemented"
```

---

## Self-Review

**Spec coverage:**
- Shared breadcrumb header partial → Task 1. ✓
- Repo view uses partial, raw `repo_id` `<h1>` removed → Task 2 Step 3d. ✓
- Repo handler parses id, 404 on malformed, passes header context → Task 2 Steps 3a–3c. ✓
- New `/org/{org_key}` route + handler → Task 3 Step 3a. ✓
- Org page mirrors repo view (Commit Summary via `commit_ranges(org_key)`, Tech Landscape via `tech_landscape(org_key)`, Developer Status via `developers(org_key)` + `repo_stats`, Repos table via `repos(org_key)` + `active_devs`) → Task 3 Step 3b. ✓
- Server breadcrumb → `/orgs/{server}`; org breadcrumb/repo-row links → `/org/{org_key}` / `/repo/{repo_id}` → partial + org_view table. ✓
- Non-goals respected: org page omits author-summary, email-domains, repo quick-links. ✓
- Malformed `repo_id`/`org_key` → 404; unexpected → 500 (existing) → `except HTTPException: raise` ahead of `except Exception`. ✓
- Tests: breadcrumb hrefs + 404s → Task 1 & 2 & 3 tests. ✓

**Placeholder scan:** No TBD/TODO-as-instruction. The single `# TODO` string copied into the org handler is a verbatim copy of the existing repo-handler comment (intentional parity), not a plan placeholder. All code blocks are complete.

**Type consistency:**
- `parse_repo_id` → `{git_server, org, repo, repo_id, org_key}`; handler reads `git_server/org/org_key/repo`. ✓
- `parse_org_key` → `{git_server, org, org_key}`; handler reads `git_server/org`. ✓
- `commit_ranges(org_key=...)` returns lowercase `{active,aging,stale,unmaintained}`; `org_view.html` reads `ranges['active'...]` lowercase (matches `repo_view.html`). ✓
- `repo_stats(...)` returns capitalised keys; both templates read `developer_status.get('Active'...)`. ✓
- `repos(org_key=...)` rows expose `_repo_id,_git_repo,authors,committers,commits,days_ago,status`; handler adds `active_devs`; `org_view.html` reads exactly these. ✓
- Partial context names (`git_server, org, org_key, repo, entity_type`) identical across partial, both handlers, and tests. ✓
