# Changelog

The format of this changelog is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)

## 0.0.27 - 2025-XX-XX

### Fixed

### Changed
- added a tenure field to the key_person function in KospexQuery

### Added

## 0.0.26 - 2025-09-18

### Fixed
- [Missed a few more places to lowercase the email address](https://github.com/kospex/kospex/issues/55)

## 0.0.25 - 2025-09-17

### Added
- [Krunner file-metadata command to re-run metadata](https://github.com/kospex/kospex/issues/58)
This enables re-running metadata for files in Krunner, when there's new features in panopticas.

### Changed
- [Bumped panopticas to 0.0.12 for pipeline and CI file types](https://github.com/kospex/kospex/issues/59 )

### Fixed
- [Missed a couple of places to lowercase the email address](https://github.com/kospex/kospex/issues/55)

## 0.0.24 - 2025-09-16

### Changed
[Bumped panopticas to 0.0.11](https://github.com/kospex/kospex/issues/52)

### Added
- [Add branch observation in krunner](https://github.com/kospex/kospex/issues/53)
- [add repo size observation in krunner](https://github.com/kospex/kospex/issues/57)

### Fixed
- [Issues handling mixed cased emails in some queries](https://github.com/kospex/kospex/issues/55)
- [years active now displayed on repos page](https://github.com/kospex/kospex/issues/54)
- [Committed some files missed in email bot detection](https://github.com/kospex/kospex/issues/47)

## 0.0.23 - 2025-09-03

### Added
- [Handle Bitbucket on premise URLs for repo_id generation](https://github.com/kospex/kospex/issues/51)

### Changed
Nothing

### Fixed
Nothing

## 0.0.22 - 2025-09-02

### Added
- [Email analysis and bot detection](https://github.com/kospex/kospex/issues/47)
- [Add port and host binding for kweb, add docker awarenes](https://github.com/kospex/kospex/issues/48)
- [Minimal docker image for testing](https://github.com/kospex/kospex/issues/49)
- [Handle Azure DevOps clone urls](https://github.com/kospex/kospex/issues/50)
### Changed
Nothing

### Fixed
Nothing

## 0.0.21 - 2025-08-14

### Added
- [MVP version of logging](https://github.com/kospex/kospex/issues/22)
- [Add a collab graph link from the collab page](https://github.com/kospex/kospex/issues/42)
- [Added initial kweb testing](https://github.com/kospex/kospex/issues/46)
### Changed
- Added a leavers tenure view where we show how long they have been in an org or repo
- Removed kgit pull as we don't really need it anymore
### Fixed
- [Removed old Flask Kweb](https://github.com/kospex/kospex/issues/41)
- [Improve Sync time](https://github.com/kospex/kospex/issues/39)


## 0.0.20 - 2025-07-21

### Added
- Added a [network graph view of commit collaborators ](https://github.com/kospex/kospex/issues/38)
- Added metadata repos view to see last commit, last sync of the data
- [key person risk view in kweb based on the commit percentages](https://github.com/kospex/kospex/issues/40
### Changed
- Single developer view now displays the years active in repos and the technologies used.
- Updated [HTML views to use src/templates/_header.html](/changes/202507-template-header-updates.md)
### Fixed
- [kospex CLI exited with error code 2](/changes/click-exit-2-error.md) when no arguments are provided

## 0.0.19 - 2025-07-09

### Added
- Nothing

### Changed
- Nothing

### Fixed
- [Static files (js,css) not packaged for kweb](https://github.com/kospex/kospex/issues/37)

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
