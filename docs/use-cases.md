
## Identifying developer and the technology landscape

For many organisations, some fundamental questions can be elusive to answer. Such as
- Who is a developer? (based on author or commiter in Git terminology)
- Who has developed code in a given timeframe (e.g. the last 90 days)?
- What languages and technologies are used by developers, a repository or an organisation?
- How many developers do we have so I can budget for licenses?
- Which code bases look stale or unmaintained?
- Identifying potential security test automation requirements  (e.g. must support SQL, Ruby, Java, rubygems and maven)
- Understand the (changing) technology landscape (what's current state and how does that compare to the previous 12 months)

## Identifying Key Person Risk and Managing off boarding knowledge Transfer

The approach for key person and offboarding is the same, one is proactive and one is reactive, but the data used is the same.

The idea is the files with the LEAST amount of "active other committers" is a starting point for handover or knowledge transfer. The code "value" depends on whether that code is still in the organisation, which could be identified using the "Identifying aging and unmaintaned code" process below.

If someone's leaving, what should we know, what do they need to hand over?
- What code (repos) have the contributed to during their tenure (all time commits)
- What have the got the most experience (measured by commits)
	- Repo level
	- File in a repo level (e.g. they may be the "only person who knows Class XYZ, core to product ABC")
- For files and repos they've been involved in, are there other collaborators and committers?
- Identify what capabilities they have and are leaving by perform a technology query on what they've committed such as:
	- What languages they've used (e.g. Ruby last year, Go in the last 6 months, previously Java)
	- Any IaC, Docker, Cloud
	- which repos they've worked on

## Identifying aging and unmaintained code

Older code is likely to be harder to maintain, invoke fear of change, and add to conceptual load because "it's something we should look at, but not now".

There are two ways of identifying aging and unmaintained code:
- Last commit age and how many committers to this repo still work here
- Age of dependencies in a repositories, number of newer versions and known vulnerabilities

Bearing in mind, older code can just mean "feature complete" rather than unused
(E.g. a datetime or file format utility may not need to change every year).
