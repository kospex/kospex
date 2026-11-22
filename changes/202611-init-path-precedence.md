# init() no longer overrides the path settings

Fixes [#466](https://github.com/kospex/kospex/issues/466).

## Overview

`KospexUtils.init()` runs when every entry point loads (`kospex_cli`, `kgit`,
`krunner`, `kweb2`, `kospex_agent`, `KospexQuery.__init__`). It wrote the path
variables into `os.environ`, which is the top of `HabitatConfig`'s order
(environment, then `kospex.env`, then default). So:

- `KOSPEX_DB` and `KOSPEX_CONFIG` in the environment were replaced with their
  defaults: `KOSPEX_DB=copy.db kospex upgrade-db -apply` migrated
  `~/kospex/kospex.db`.
- `KOSPEX_DB` in `kospex.env` was ignored, because init set the variable before
  loading the file and dotenv does not replace a set variable.
- `KOSPEX_CODE=~/code` in `kospex.env` stayed a literal `~/code`, so init
  warned it did not exist and `validate_kospex_setup()` reported `exists: false`.
- The `KOSPEX_CODE` warning named the default path, not the one checked.
- A literal `~` in `KOSPEX_HOME` was used as given: init created `./~/...`
  in the working directory.

## What changed

- **`init()` exports no path variable.** Paths come from `HabitatConfig`. It
  still loads `kospex.env` into the environment, without replacing anything,
  for the settings read from `os.environ` (tokens, `REQUESTS_CA_BUNDLE`,
  `KOSPEX_URL_CACHE_SECONDS`, ...), and still expands `~` in the CA bundle
  variables. It creates the home (with a default `kospex.env`) and the logs
  directory, as before, from the `HabitatConfig` paths.
- **Path settings are not copied from `kospex.env` into `os.environ`.**
  `HabitatConfig` reads them from the file itself
  (`HabitatConfig.PATH_SETTINGS`). An exported copy would make a `kospex.env`
  value report as an environment one.
- **`KOSPEX_HOME` and `KOSPEX_CONFIG` are environment-only.** They locate
  `kospex.env`, so the file cannot set them. A file that tries is reported in
  `init_status["warnings"]` (`kospex init --verbose`). Before, the file's
  `KOSPEX_HOME` was ignored by the CLI but honoured by `HabitatConfig` when
  init had not run, depending on import order.
- **`HabitatConfig._path_setting()`**: every path property (`db_path`,
  `duckdb_path`, `logs_dir`, `krunner_dir`, `staging_dir`, `assessments_dir`)
  now checks override, environment, then `kospex.env`. Before, these skipped
  `kospex.env` and only worked from it because init had loaded it.
- **`HabitatConfig.source(key)`**: `override`, `environment`, `kospex.env` or
  `default`. `kospex init --validate` and `kospex system-status` print it next
  to each path.
- **One parser for `kospex.env`**: `habitat_config.read_env_file()`, which is
  python-dotenv's `dotenv_values`. `init()` and `HabitatConfig` used to parse
  it separately (dotenv vs. a `KEY=VALUE` splitter) and could disagree on an
  `export ` prefix or an inline comment.
- **Direct reads moved onto `HabitatConfig`**: `validate_kospex_setup()`,
  `KospexLogger` (which now also honours `KOSPEX_LOGS`; it used `home/logs`
  regardless) and `GitDuckDB._get_default_path()` (now `duckdb_path`, which
  honours `KOSPEX_DUCKDB`).
- `KOSPEX_CONFIG_ITEMS` no longer lists `KOSPEX_DB` twice; it is derived from
  `KOSPEX_CONFIG_PATHS`. `get_all_config()` reports the resolved values with
  their source instead of raw `os.getenv`.
- `validate_kospex_setup()["environment_vars"][VAR]` now holds the resolved
  value and gains `source`. `set` means "not the default".

## Files changed

- `src/kospex_utils.py`: `init()`, `load_config()`, `validate_kospex_setup()`,
  `get_all_config()`, `KOSPEX_CONFIG_PATHS`
- `src/kospex/habitat_config.py`: `read_env_file()`, `_path_setting()`,
  `source()`, `PATH_SETTINGS`, env-only `home`
- `src/kospex_logging.py`, `src/kospex/git_duckdb.py`: paths from `HabitatConfig`
- `src/kospex_cli.py`: `kospex init --validate` / `--verbose` output
- `tests/test_init_path_precedence.py`: new, unit level (`init()`, `HabitatConfig`)
- `tests/test_cli_path_precedence.py`: new, end to end. Each of `kospex`, `kgit`
  and `krunner` runs in a fresh interpreter and must open the DB that
  `KOSPEX_DB` names, from the environment or from `kospex.env`.
  `upgrade-db -apply` must migrate the copy, not the default DB. Also covers
  the `init --validate` / `--verbose` / `system-status` output and logs under
  `KOSPEX_LOGS`. An AST guard fails if any module except `habitat_config.py`
  reads or writes a path setting through `os.getenv` / `os.environ`.
  `kospex_agent` (loops) and kweb startup (`test_kweb_startup_db.py`) are not
  covered here.
- `docs/configuration.md`: new page, linked from index, getting-started,
  troubleshooting and data-schemas, and added to `sitemap.xml`

## Not done

- `init()` does not call `HabitatConfig.ensure_directories()`, as the issue
  suggested: that also creates `krunner/` and `assessments/`, and writes a
  config file at a `KOSPEX_CONFIG` path that does not exist. Every command
  runs `init()` at import, so it keeps creating only the home and logs.

## Follow-up: logging settings from kospex.env

Checked after #467 merged: `KOSPEX_LOG_LEVEL` in `kospex.env` was ignored by
`kospex`, `kgit`, `krunner` and `kweb2`. `KospexLogger._load_config()` caches on
the first `get_logger()`, which runs while modules are imported, before
`init()` loads `kospex.env`. It now reads `KOSPEX_LOG_LEVEL`,
`KOSPEX_LOG_RETENTION_DAYS` and `KOSPEX_CONSOLE_LOGGING` through the new
`HabitatConfig.setting()` (override, environment, then `kospex.env`), so it no
longer depends on `init()` having run. `config.json` still overrides all of them,
which is unchanged. Test: `test_log_level_from_kospex_env` in
`tests/test_cli_path_precedence.py`, which fails on the four `kospex.env` cases
without the fix.
