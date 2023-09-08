# kospex

Kospex is a CLI which aims to _"look at the guts of your code"_ to help gain insights into your developers and technology landscape.
It uses the excellent [Mergestat lite](https://github.com/mergestat/mergestat-lite) to extract data from git repositories. 

## Installation, setup and usage

kospex is currently a collection of tools, bound together by python in a docker container. 
It works by analysing cloned repositories on the filesystem. 

### Step 0: Prepare (clone) the repositories you want to assess

See section "Git code layout for running analysis" below. For simple "one off" analysis just clone some repo's into a directory as a starting point. 

Ideally, a structure like \
/BASE/GIT_SERVER/ORG/REPO

### Step 1: Clone kospex and Build the docker image

> ./build-image.sh

### Step 2 : Run the kospex shell

> shell-kospex.sh [GIT_DATA_DIRECTORY] [KOSPEX_DATA_DIR]

### Step 3: sync some data and play with some commands

> kospex sync [GIT_REPO]
>
> kospex developers -repo [GIT_REPO]
>
> kospex tech-landscape -metadata


## Git code layout for running analysis

One option, if you're inspecting code on your own laptop is to use use your home directory. 

~/kospex/ \
We'll place config files and the kospex DB (Sqlite3) in here for sync'ed data \
~/code/ \
This should be your GIT_DATA_DIRECTORY with a structure like \
GIT_SERVER/ORG/REPO \
 \
For example, in your ~/code it might look like: \
github.com/kospex/kospex \
github.com/mergestat/mergestat-lite

This way we have a nice deterministic way of separating different orgs, potentially different instances (e.g. you have an on premise bitbucket and use GitHub.com) as well. 

using the command from Step 2, running the kospex shell should look like this 

> shell-kospex.sh ~/code ~/kospex

While this might seem a little odd, it means all our git creds and apps are outside the running docker container, and we just mount the local filesystem to do analysis.

## Key Use Cases and features

 - Identify technology landscape
 - Identify active developers (e.g. who's had their code committer in the last 90 days)
 - Identify key person or offboarding risk
 - Identify potential complexity challenges (or conceptual integrity concerns)
 - Aggregate repo metadata into a single database for easier and faster querying

## Inside the kospex shell

All of your mounted code will be in the /repos direcory and that's where your shell will start. Proving you used a similar format for the GIT_DATA_DIRECTORY above, you should see everythign like: \
/repos/GIT_SERVER/ORG/REPO \
/repos/github.com/kospex/kospex \
etc

Useful tools installed:
- MergeStat (https://github.com/mergestat/mergestat-lite) - for extracting Git data
- SCC (https://github.com/boyter/scc/) - used for file metadata, Lines of Code etc

## Queries to try

### sync a repo

> kospex sync PATH/TO/GIT_REPO

_Most functions require the data to be synced._ 

### Show recent developers who've committed in X days

List the active developers (90 days) in the given repo (sync required)
> kospex developers -repo PATH/TO/REPO

List the developers in the given repo_id (sync'ed data in the kospex DB)
> kospex developers -repo_id=github.com&tilde;ORG&tilde;REPO

use -days NUM for seen in the last # of days (e.g. 90 or 365)

### Identify technology landscape

List the overall tech stack, based on file extension, for all sync'ed repos
> kospex tech-landscape

List the overall tech stack for all sync'ed repos (using scc)
> kospex tech-landscape -metadata

List the overall tech stack for a repo (using scc)
> kospex tech-landscape -repo PATH/TO/REPO

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
- Improve use case documentation 
- Build a docker image and publish to dockerhub (save building one yourself)
- Provide a mechanism to better map author_emails to users

## Data extractions and assumptions

Most tables have a column, _repo_id, in the format of GIT_SERVER&tilde;OWNER&tilde;REPO  
So for the repository https://github.com/kospex/kospex the _repo_id would be _github.com&tilde;kospex&tilde;kospex_

Most queries use author_email from git to mean a "developer". This is not always accurate as GitHub 

## What is a kospex?

We're aiming to [k]now your c[o]de by in[spe]cting the haruspe[x].
From Wikipedia, _The Latin terms haruspex and haruspicina are from an archaic word, hīra = "entrails, intestines"_

So we're going to help look at the "guts of your code" to gain an understanding of the applications, technology lanscape (sprawl?) and developers.


