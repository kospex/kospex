# Metrics

## Introduction

A lot of people ask "what does good look like?", is my code base health? is it secure?

Here's are some indicators that measurable:
- Date of last commit
- Age of dependency files (e.g. how long since they've been updated)
- Commits in the last 90 days (what we call 'Active')

When looking at dependency file, we can also provide more granular indications.
For example, for each depepdency we can also identify:
- When was the last update (e.g. is is recent?)
- how many versions behind are we? (are we aiming at being n-2)
- Are there known vulnerabilities in this library?

## Predictive maintainence

