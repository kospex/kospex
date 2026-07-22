# Getting started

This guide walks through installing kospex, setting up your directory structure,
syncing your first repository and exploring the results in the Web UI.

## Prerequisites

For the best setup experience, we recommend using Homebrew to install the required dependencies on macOS:

```bash
# Install Git (if not already installed)
brew install git

# Install Git Credential Manager for secure authentication (macOS only)
brew install git-credential-manager

# Install scc for code complexity analysis
brew install scc
```

On other platforms:

- **Git** — follow the instructions at [git-scm.com/downloads](https://git-scm.com/downloads)
- **Git Credential Manager** — follow the instructions at [git-ecosystem/git-credential-manager](https://github.com/git-ecosystem/git-credential-manager)
- **scc** — follow the instructions at [boyter/scc](https://github.com/boyter/scc)

### Linux (Ubuntu tested)

We've tested on Ubuntu. You'll need:

- git (`apt install git`)
- homebrew (see instructions above)
- If you're using GitHub only (organisations with the same credentials, or SAML/SSO), see the section below on using the GitHub CLI

### Using the GitHub CLI, classic tokens and SAML

You can configure the git CLI (which kospex wraps) using the GitHub CLI, `gh`.

If your organisation uses SAML / Single Sign On (e.g. Office 365), we've tested that with kospex using the following steps:

1. Create a classic token (`gh` requires `repo`, `read:org` and `workflow` permissions)
2. Authorise the token for your organisation in the web UI
3. If you haven't done it in the GitHub web UI, `gh auth` will ask you to authorise the token as you work through the steps to use a token

Confirm you can clone a repo:

```bash
gh repo clone ORG/REPO
```

If this works, you can configure git using `gh`:

```bash
gh auth setup-git
```

The `kgit clone` commands will now work with GitHub and the org SSO account that `gh` uses.

## Step 1: Installation

kospex is a Python package with commands. It works by analysing cloned repositories on the filesystem.

**Optional but strongly recommended** — use a Python virtual environment.

Install using pip:

```bash
pip install kospex
```

For complexity and file type analysis, kospex uses the `scc` binary (installed in Prerequisites above).
It is optional, but enables much better file type guessing and provides complexity metrics.

## Step 2: Initial kospex setup

kospex uses a git repository layout for cloning repos to disk, with the following structure:

```
/BASE/GIT_SERVER/ORG/REPO
```

If you're happy to use the `~/code` directory for cloned repos, run:

```bash
kospex init --default
```

See [Git code layout for running analysis](#git-code-layout-for-running-analysis) below for more details.

## Step 3: Sync some data and try some commands

Use the `kgit` command to clone and sync a repo you have access to:

```bash
kgit clone https://github.com/mergestat/mergestat-lite
```

This clones into the `KOSPEX_CODE/GIT_SERVER/ORG/REPO` structure.

Some commands to try:

```bash
kospex developers
kospex tech-landscape -metadata
```

## Step 4: Use the Web UI to explore your repos and developers

Start the Web UI:

```bash
kweb
```

You can now navigate to [http://127.0.0.1:8000](http://127.0.0.1:8000).

See the [Web UI guide](kweb/) for what each view shows you.

## Git code layout for running analysis

If you're inspecting code on your own laptop, one option is to use your home directory:

| Directory | Purpose |
| --------- | ------- |
| `~/kospex/` | Config files and the kospex DB (SQLite3) for synced data |
| `~/code/` | Cloned repositories, in a `GIT_SERVER/ORG/REPO` structure |

For example, your `~/code` might look like:

```
~/code/
  github.com/
    kospex/kospex
    mergestat/mergestat-lite
```

This gives a deterministic way of separating different orgs, and different instances as well
(e.g. you have an on-premise Bitbucket and also use GitHub.com).
