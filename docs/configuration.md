# Configuration

kospex is configured with environment variables. Any of them can also go in a
`kospex.env` file, so you don't have to export them in every shell. This page
lists where kospex keeps its files, how it decides which value to use, and the
settings you can change.

## Where kospex keeps things

| What | Default | Setting |
|---|---|---|
| kospex home (config, database, logs) | `~/kospex` | `KOSPEX_HOME` |
| Config file | `~/kospex/kospex.env` | `KOSPEX_CONFIG` |
| SQLite database | `~/kospex/kospex.db` | `KOSPEX_DB` |
| Cloned repositories | `~/code` | `KOSPEX_CODE` |
| Log files | `~/kospex/logs` | `KOSPEX_LOGS` |
| Logging config (JSON) | `~/kospex/config.json` | follows `KOSPEX_HOME` |

The database, config file and logs live under the kospex home by default, so
setting `KOSPEX_HOME` alone moves all of them. Setting `KOSPEX_DB`, `KOSPEX_CONFIG`
or `KOSPEX_LOGS` moves just that one.

Repositories are cloned into `KOSPEX_CODE` as `SERVER/OWNER/REPO`, for example
`~/code/github.com/kospex/kospex` (see [kgit](kgit)).

A leading `~` is expanded to your home directory in every path setting, both in the
environment and in `kospex.env`.

## Which value wins

For each setting, kospex uses the first of these that has a value:

1. **The environment**: `export KOSPEX_DB=...`, or `KOSPEX_DB=... kospex ...` for one command.
2. **`kospex.env`**: a `KEY=value` line in the config file.
3. **The default** shown in the table above.

An empty value (`KOSPEX_DB=`) counts as unset.

So a value in the environment always wins over `kospex.env`. This is what lets you
point a single command at a copy of the database without editing anything:

```bash
cp ~/kospex/kospex.db /tmp/copy.db
KOSPEX_DB=/tmp/copy.db kospex upgrade-db -apply   # migrates the copy, not ~/kospex/kospex.db
```

Two settings are read from the environment only: `KOSPEX_HOME` and `KOSPEX_CONFIG`.
They tell kospex where `kospex.env` is, so the file cannot set them. If it contains
either one, kospex ignores it, and `kospex init --verbose` warns.

> Releases up to 0.4.2 always replaced `KOSPEX_DB` and `KOSPEX_CONFIG` with
> their defaults at startup, and ignored `KOSPEX_DB` in `kospex.env`, so these
> settings had no effect. A literal `~` in `kospex.env` paths or in `KOSPEX_HOME`
> was also not expanded.

### Checking what kospex is using

`kospex init --validate` prints each path, and where its value came from:

```
Paths (environment > kospex.env > default):
  KOSPEX_HOME: /Users/you/kospex (default)
  KOSPEX_CONFIG: /Users/you/kospex/kospex.env (default)
  KOSPEX_DB: /Volumes/data/kospex.db (kospex.env)
  KOSPEX_CODE: /Users/you/src (environment)
  KOSPEX_LOGS: /Users/you/kospex/logs (default)
```

`kospex system-status` shows the same paths in its "Environment configuration" table.

## The kospex.env file

When kospex first creates its home directory (for example on the first
`kospex init --create`), it writes a `kospex.env` there with every line commented
out. The format is one `KEY=value` per line:

```bash
# Comments start with #
KOSPEX_CODE=~/src
KOSPEX_DB=/Volumes/data/kospex.db
GITHUB_TOKEN="ghp_..."          # quotes are optional
export KOSPEX_DEPS_ENRICH=false # an export prefix is allowed
```

When a command starts, kospex reads this file. It also copies the non-path settings
(tokens, `REQUESTS_CA_BUNDLE`, the preferences below) into its own environment,
unless they are already set there. So a value you export in your shell still
wins. Once the file exists, the only command that changes it is
`kospex connectivity -save`, which adds a CA bundle line (see
[Troubleshooting](troubleshooting)).

To keep separate setups, for example a test database alongside your real one,
give each its own home:

```bash
KOSPEX_HOME=~/kospex-test kospex init --create
KOSPEX_HOME=~/kospex-test kgit clone https://github.com/kospex/kospex
```

Each home gets its own `kospex.env`, database and logs.

## Preferences

These all follow the same order: environment, then `kospex.env`, then the default.

| Setting | Default | What it does |
|---|---|---|
| `KOSPEX_DEPS_ENRICH` | `true` | `false` makes `kospex sca` and `krunner osi` parse dependency files without looking packages up on deps.dev or PyPI. `-enrich` / `-no-enrich` override it per run. See [kospex](kospex) and [krunner](krunner). |
| `KOSPEX_DEPS_PRIVATE` | none | Package name prefixes that are never looked up, such as `maven:com.example,go:github.com/example`. `GOPRIVATE` is also honoured for Go, but only from the environment. See [krunner](krunner). |
| `KOSPEX_CLONE_FORKS_DEFAULT` | `false` | `true` makes `kgit sync --org` / `--user` sync forks. See [kgit](kgit). |
| `KOSPEX_ORG_SYNC_REFRESH_DAYS` | `2` | How old an org sync listing can be before `kgit sync ... -active` lists the owner again. `0` means every time. |
| `KOSPEX_URL_CACHE_SECONDS` | `86400` | How long cached deps.dev and PyPI responses are kept, in seconds. See [kospex cache queries](kospex-cache-queries). |

## Credentials and network

| Setting | Used by |
|---|---|
| `GITHUB_AUTH_TOKEN`, `GH_TOKEN`, `GITHUB_TOKEN` | GitHub API calls (`kgit github`, `kgit sync --org`). The first non-empty one wins, in that order. See [GitHub rate limits](github-rate-limits). |
| `BITBUCKET_API_TOKEN` with `BITBUCKET_EMAIL` or `BITBUCKET_USERNAME` | `kgit bitbucket` |
| `SNYK_TOKEN` | `kospex snyk` |
| `REQUESTS_CA_BUNDLE`, `CURL_CA_BUNDLE` | A CA bundle for HTTPS lookups behind a TLS-inspecting proxy. See [Troubleshooting](troubleshooting). |

Tokens in `kospex.env` are stored in plain text. Keep the file readable only by you
(`chmod 600 ~/kospex/kospex.env`).

## Logging

| Setting | Default | What it does |
|---|---|---|
| `KOSPEX_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING` or `ERROR` |
| `KOSPEX_LOG_RETENTION_DAYS` | `30` | Days of rotated log files to keep |
| `KOSPEX_CONSOLE_LOGGING` | `false` | Also print log messages to the console |

Set these in the environment, or in `~/kospex/config.json`, which can also set a
level per tool:

```json
{
  "logging": {
    "log_level": "INFO",
    "retention_days": 60,
    "modules": {
      "kgit": {"level": "DEBUG"}
    }
  }
}
```

For a single `kospex` command, the global options `--debug`, `--verbose`, `--quiet`
and `--log-console` override these, as in `kospex --debug summary`.
