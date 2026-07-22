# Kospex Web UI

`kweb` is the web interface for navigating the data kospex has synced into its database.
Where the CLI is built for scanning, scripting and reporting, the Web UI is built for
*exploring* — following a thread from an organisation, to a repo, to a file, to the
developer who last touched it.

## Starting the Web UI

```bash
kweb
```

Then open [http://127.0.0.1:8000](http://127.0.0.1:8000).

You'll need repositories synced into the database first — see
[Getting started](../getting-started) if you haven't done that yet.

> **The Web UI has no authentication.** It binds to `127.0.0.1` by default, so it's only
> reachable from your own machine. Don't expose it to a network or the internet without
> putting your own authentication in front of it — everything kospex knows about your
> code and developers is readable by anyone who can reach the port.

## Navigation

The main menu maps to the core views:

| Menu | Path | What it shows |
| ---- | ---- | ------------- |
| Summary | `/summary/` | High-level overview of everything synced |
| Repos | `/repos/` | Repository inventory, activity and health |
| Orgs | `/orgs/` | Organisations and git servers |
| Developers | `/developers/` | Contributors, activity and expertise |
| Opensource | `/osi/` | Open Source Inventory — dependency files and packages |
| Landscape | `/landscape/` | Technology landscape across your code |
| Metadata | `/metadata/` | File-level metadata and technology detail |
| Help | `/help/` | Context-sensitive help |

## Core views

### Summary

The starting point. Aggregate counts of repositories, developers and organisations,
with activity status so you can see how much of your estate is active versus aging.

### Repos

Repository inventory. For each repo you can see commit activity, contributor counts and
last-commit age, which drives the active / aging / stale / unmaintained status described
on the [home page](../).

Drilling into a single repo (`/repo/{repo_id}`) gives you its contributors, technologies,
files and commit history.

### Orgs

Organisation and git server level views (`/orgs/`, `/org/{org_key}`). Useful for
portfolio-level questions — which parts of the business have the most repos, the most
developers, or the most unmaintained code.

### Developers

Contributor analysis (`/developers/`, `/developer/{id}`). For an individual you can see
which repos they've committed to, the technologies they've used, and how that has changed
over time — the basis for offboarding and knowledge-transfer planning.

> Kospex measures commit activity, which reflects *where knowledge sits*, not how well
> someone does their job. It is not a productivity metric and shouldn't be used as one.

### Opensource (OSI)

The Open Source Inventory. Shows the dependency and lock files found across your repos,
when each was last updated, and the packages extracted from them.

Because a package manager file that hasn't been touched in a year is a strong signal on
its own, dependency files carry the same active / aging / stale / unmaintained status as
repositories.

### Landscape

The technology landscape — languages, infrastructure as code, package managers, pipelines
and linters detected across your code. `/tech/{tech}` drills into a single technology to
see which repos use it, and `/tech-change/` compares the landscape over time.

### Metadata

File-level metadata and technology detail, including the file types and complexity metrics
gathered via `scc`.

## Analysis views

These aren't all in the top menu, but are reachable by drilling down:

| View | Path | Purpose |
| ---- | ---- | ------- |
| Orphans | `/orphans/` | Repos with no active committer still contributing |
| Key person | `/key-person/{repo_id}` | Files and repos carrying single-contributor risk |
| Collaboration | `/collab/{repo_id}` | Who works alongside whom in a repo |
| File collaboration | `/file-collab/{repo_id}/` | Collaboration at the individual file level |
| Hotspots | `/hotspots/{repo_id}` | Files that change often and are hard to change |
| Files | `/files/repo/{repo_id}` | File inventory for a repo |
| Dependencies | `/dependencies/` | Declared dependencies and how far behind they are |
| Package check | `/package-check/` | Drag and drop a dependency file to analyse it ad hoc |
| Commits | `/commits/{repo_id}` | Commit history and individual commit detail |
| Tenure | `/tenure/` | How long contributors have been active |
| Observations | `/observations/` | Stored analysis results |
| Recent | `/recent/` | Recently synced repositories |

### Graphs and visualisations

- **Collaboration graphs** (`/graph/{org_key}`) — interactive network views of how
  developers and repositories connect
- **Bubble charts** (`/bubble/{id}`) — contribution volume and status at a glance
- **Treemaps** (`/treemap/{id}`) — proportional views of the same data

## Common workflows

- **Where is our knowledge concentrated?** Orgs → Repos → Key person → Collaboration graph
- **What are we built on?** Landscape → Opensource → Dependencies
- **What's been abandoned?** Summary → Orphans → individual repo views
- **What is this person taking with them?** Developers → individual developer → their repos and technologies

## Troubleshooting

- **No data showing** — the database may be empty. Sync at least one repo with
  `kgit clone` or `kospex sync`, and check `kospex summary` returns results from the CLI.
- **Missing repositories** — confirm the repo synced successfully; `/recent/` shows what
  was most recently ingested.
- **Missing file types or complexity metrics** — check that `scc` is installed, as it
  drives file type detection and complexity analysis.
- **Slow pages** — large estates produce large tables; filter by org or server rather than
  loading everything at once.

More general help is in [Troubleshooting](../troubleshooting).
