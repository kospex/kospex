# Metrics

## Introduction

Here are some commons questions people are about their code and repositories:
- "what does good look like?"
- "is my code base healthy?"
- "is it secure?"
- "is it maintained?"

Here's are some indicators that are easily measurable:
- Date of last commit
- Age of dependency files (e.g. how long since they've been updated)
- Commits in the last 90 days (what we call 'Active' for a repo)
- Number of branches (which may still be app and team dependent)

When looking at dependency file, we can also provide more granular indications.
For example, for each depepdency we can also identify:
- When was the last update (e.g. is is recent?)
- how many versions behind are we? (are we aiming at being n-2)
- Are there known vulnerabilities in this library?

## Predictive maintainence

Trying to identify where we should apply predictive maintenance needs to take into consideration indicators and the organisation, app or team context.

## Our code versus opensource

While we may known if our repo is feature complete, still in active development, scheduled for decommission we won't really know decisions an opensource project has made.

## Easy(ish) indicators to collect

The following are some suggestions which might give an indication that a repo is healthier than another, 
or simply start categorising. 

| Indicator                     | Description                                                       | Indicator   |
| -----------------             | ----                                                              | -----       |
| Last updated                  | Last commit to the repo                                           | Maintenance |
| Number of authors             | All time authors to a repo                                        | Complexity  |
| Number of active authors      | List of developers who've commited in the last X Days (e.g. 90)   | Maintenance |
| Age of repository             | How old is this repo? 1 month, 1 quarter, older than 1 year?      | Maintenance |
| Days active                   | How many days was there developer? 1 day for a spike, 3 years     | Info        |
| Number of dependency files    | How many dependency files (e.g. package.json, go.mod) are there   | Complexity  |
| Number of direct dependencies | In a dependency/package file, how many libraries are we including | Complexity  |
| % upto date dependency files  | What percentage of dependency files are n-2 or newer              | Maintenance |
| % upto date dependencies      | What percentage of ALL dependency files are n-2 or newer          | Maintenance |


### Separate indicators per file

*Dependency Files*

For dependency or package manager file (e.g. package.json, go.mod, requirements.txt), 
its possibly to deep dive into specific details as highlighted in the table below

| Detail                     | Description                                                       | Indicator   |
| -----------------          | ----                                                              | -----       |
| Name                       | Name of the package                                               | Info        |
| Version                    | Version of the package                                            | Maintenance |
| version type               | was it semantic, exact pinning, no version etc                    | Practices   |
| # versions behind          | Useful in looking at how "maintained" something is                | Maintenance |
| Date version published     | When was this published? Correlate with # versions behind         | Maintenance |
| # vulnerabilities          | If there are known vulnerabilities                                | Risk        |


