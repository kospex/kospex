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

| Indicator                     | Description                                                       |
| -----------------             | ----                                                              |
| Last updated                  | Last commit to the repo                                           |
| Number of authors             | All time authors to a repo                                        |
| Number of active authors      | List of developers who've commited in the last X Days (e.g. 90)   |
| Age of repository             | How old is this repo? 1 month, 1 quarter, older than 1 year?      | 
| Days active                   | How many days was there developer? 1 day for a spike, 3 years     |
| Number of dependency files    | How many dependency files (e.g. package.json, go.mod) are there   |
| Number of direct dependencies | In a dependency/package file, how many libraries are we including |
| % upto date dependency files  | What percentage of dependency files are n-2 or newer              |
| % upto date dependencies      | What percentage of ALL dependency files are n-2 or newer          |


### Separate indicators per file




