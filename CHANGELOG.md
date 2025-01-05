# Changelog

The format of this changelog is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)

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

## [Unreleased]

### Added
  - Initial End Point for OSI (Open Source Inventory) queries
  - Architecture Decision Record are in /docs/adr/
  - New kospex CLI metadata function using Panopticas


### Changed
  - KospexGit now uses Panopticas for the get_repo_files function

### Fixed
  - commented out the experimental graph-api which broke the workflow build, kweb kospex_query
  - Link to GitHub from the developer page when we know their GitHub handle

## VERSION - DATE

### Added
### Changed

### Fixed
