# Changelog

The format of this changelog is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)

## 0.0.19 - 2025-07-09 

### Added
- Nothing

### Changed
- Nothing

### Fixed
- [Static files (js,css) not packaged for kweb](https://github.com/kospex/kospex/issues/37)

## [Unreleased]

## VERSION - DATE - IN PROGRESS

### Added
### Changed
### Fixed


## 0.0.18 - 2025-07-07

## Major Changes (non breaking)

This version did a refactor from Bootstrap 4 to [TailwindCSS](https://tailwindcss.com/) and
now manages the CSS and JS files programatically and stores local versions
[Documentation for TailwindCSS web and JS assets](/web-assets.md)

Refactored from [Flask to FastAPI](https://github.com/kospex/kospex/issues/33) 

### Added
- [Create local static css and js files and package management](https://github.com/kospex/kospex/issues/31)
- [Implemented Author Collaborator view](https://github.com/kospex/kospex/issues/32)
- Exposed the collaborator feature (/collab/) via the /repo/ page to show author / committer summary
- Exposed the tenure feature (/tenure/) via the /repo/ page to show stats on current commits and authors
- Better [single commit and commits per file view](https://github.com/kospex/kospex/issues/35) 

### Changed
- Removed broken hotspots link from repo view

### Fixed
- Probably a few bugs here and there

## 0.0.17 - 2025-04-20

### Added
  - Implemented a pretty_table in the kospex_git class
### Changed
  - kospex --version now uses the version number in kospex_core.pyg
  - kgit --version now uses the version number in kospex_core.py
  - [Implemented PyGithub to remove some bespoke code](https://github.com/kospex/kospex/issues/28)
  - Initial work on [SSH clone issue](https://github.com/kospex/kospex/issues/7) However, still a WIP
### Fixed
  - Cleanup the requirements.txt from some experimental uses
  - Removed commented code in Git changes
  - removed some unnecessary switches in kgit github



## 0.0.16 - 2025-04-06

### Added
  - Added integration in [malicious package API in SCA](https://github.com/kospex/kospex/issues/25)
  - Added MVP [db migrations feature](https://github.com/kospex/kospex/issues/23)
### Changed
  - kospex sca now has a cut down version of kospex deps, plus a malware flag for malicious packages
  - tests/run-kdocker.sh now mounts a data directory for output, and the tests folder for scripts to run
  -
### Fixed
  - Removed some commented out dead and test code

## 0.0.15 - 2025-02-08

### Added
  - a static parse_ssh_git_url method
  - MVP sca method to eventually replace kospex deps with kospex sca
  - an Initial End Point for dependencies queries
  - kreaper can now remove all rows with repo_id from a table
  - initial [orphans feature](https://github.com/kospex/kospex/issues/20)
  - tenure functions and pages to show how long developers have worked

### Changed
  - Improved tests for Git URLs
  - Removed references to pygit2 (mostly commented out) as no longer used

### Fixed
  - Parsing of ssh urls like git@github.com:kospex/panopticas.git
  - parsing of git URLs with trailing slash which failed e.g. https://github.com/kospex/kospex/
  - kreaper can now delete a repo_id out of all tables
  - Shell escaped filenames to handle spaces in Git commands
  - Improved SCC testing so that only Git managed files are added to Metadata table



## 0.0.14 - 2025-01-15

### Added
  - a switch to orphans to allow a targe list of repos to assess
### Changed
  - KospexGit now has safer (handles "/" in org) repo_id generation when setting a repo_url

### Fixed
  - Bug fix when kospex metadata is run and not in a Git dir.


## 0.0.13 - 2025-01-06

### Added
  - Initial End Point for OSI (Open Source Inventory) queries
  - Architecture Decision Record are in /docs/adr/
  - New kospex CLI metadata function using Panopticas
  - [Use panopticas for base file detection](https://github.com/kospex/kospex/issues/11)

### Changed
  - KospexGit now uses Panopticas for the get_repo_files function

### Fixed
  - commented out the experimental graph-api which broke the workflow build, kweb kospex_query
  - Link to GitHub from the developer page when we know their GitHub handle [16](https://github.com/kospex/kospex/issues/16)


## 0.0.12 - 2024-12-01

### Added
  - added Treemap graph which can be a toggled graph from bubble charts

### Changed
  - None

### Fixed
  - graph APIs for bubble and treemap dont dislay repos when showing developers
  - Fixed bug where landscape drilldown didn't work [Issue 15](https://github.com/kospex/kospex/issues/15)


## 0.0.11 - 2024-11-25

### Added
  - Added an <id> param to the /repos/ endpoint for easier linking
### Changed
### Fixed
 - Fixed commit slider so it works on bubble graph, removed reset button
 - Fixed bubble graph redraw overlap issue when commit slider is reduced.
 - Fixed npm parsing bug on absence of dependencies in a package.json
 - Fixed bug where repos by tech page didn't display

## 0.0.10 - 2024-11-04

### Added
  - a help section in the menu (available from /help/)
  - Intial start on header macro in jinja templates to make drilldown headings more repeatable
  - initial work on using panopticas for file type identification
  - added some static methods for generating and parsing repo_id in kospex_git
  - Implemented command on kospex CLI for feature request [kospex version command](https://github.com/kospex/kospex/issues/13)
  - Implemented [Krunner trufflehog capability to report only verified secrets](https://github.com/kospex/kospex/issues/10)

### Changed
  - Fixed how percentages and circles were created in summary view
  - added no_scc options to some commands



## VERSION - DATE

### Added
### Changed
### Fixed
