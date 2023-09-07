# kospex

Kospex is a CLI which aims to _"look at the guts of your code"_ to help gain insights into your developers and technology landscape.  
It uses the excellent [Mergestat lite](https://github.com/mergestat/mergestat-lite) to extract data from git repositories. 

## Installation, setup and usage

kospex is currently a collection of tools, bound together by python in a docker container. 
It works by analysing cloned repositories on the filesystem. 

Step 0: Prepare (clone) the repositories you want to assess

See section "Git code layout for running analysis" below. For simple "one off" analysis \\
just clone some repo's into a directory as a starting point. 

Ideally, a structure like
/BASE/GIT_SERVER/ORG/REPO

Step 1: Clone kospex and Build the docker image

> ./build-image.sh

Step 2 : Run the kospex shell

> shell-kospex.sh [GIT_DATA_DIRECTORY] [KOSPEX_DATA_DIR]

Step 3: 


## Git code layout for running analysis



## Key Use Cases and features

 - Identify technology landscape
 - Identify active developers (e.g. who's had their code committer in the last 90 days)
 - Identify key person or offboarding risk
 - Identify potential complexity challenges (or conceptual integrity concerns)
 - Aggregate repo metadata into a single database for easier and faster querying


## Queries to try

List the developers in the given repo (no sync required)
> kospex developers -repo=./REPO

List the developers in the given repo_id (sync'ed data in the kospex DB)
> kospex developers -repo_id=github.com&tilde;ORG&tilde;REPO

use -days NUM for seen in the last # of days (e.g. 90 or 365)

### Identify technology landscape

List the overall tech stack for a repo (using scc) (no sync required)
> kospex tech-landscape -repo=./REPO

List the tech stack in the given repo_id (sync'ed data in the kospex DB)
> kospex tech-landscape -repo_id=github.com&tilde;ORG&tilde;REPO


## Design principles

- Precompute data where possible and useful
- Flatten tables, data warehouse style, to enabled easier querying and slicing by git server, owner and repo
- Be as agnostic to the git provider (i.e. GitHub, BitBucket, GitLab) for base use cases
- Be mindful that "there is no perfect", only indicators
- Separate cloning and pull updates from the analysis

## Roadmap

- Build out automated functional and regressions testing (Currently manual)
- Build the ability to identify key person or offboarding risk



## Data extractions and assumptions

Most tables have a column, _repo_id, in the format of GIT_SERVER&tilde;OWNER&tilde;REPO  
So for the repository https://github.com/sabbatics/kospex the _repo_id would be _github.com&tilde;sabbaticas&tilde;kospex_

## What is a kospex?

We're aiming to [k]now your c[o]de by in[spe]cting the haruspe[x].
From Wikipedia, _The Latin terms haruspex and haruspicina are from an archaic word, hīra = "entrails, intestines"_

So we're going to help look at the "guts of your code" to gain an understanding of the applications, technology lanscape (sprawl?) and developers.


