# kospex

Kospex is a CLI which aims to _"look at the guts of your code"_ to help gain insights into your developers and technology landscape.
It uses database structure from the excellent [Mergestat lite](https://github.com/mergestat/mergestat-lite) to model data from git repositories. 

## Step 1: Installation, setup and usage

kospex is currently a python module with commands. It works by analysing cloned repositories on the filesystem. 

**Optional but strongely recommended** - use a python virtual env. 

Installing using pip:

> pip install kospex

For complexity and file type analysis, kospex uses the scc binary. 
It is optional, but enables much better file type guessing and provide complexity metrics.
Follow the instructions for installing [scc](https://github.com/boyter/scc)

### Step 2: Initial kospex

kospex uses a git repositoriy layout for cloning repos to disk. 

The following structure is used \
/BASE/GIT_SERVER/ORG/REPO

If you are ok to use the ~/code directory for cloned repos, then run: 
> kospex init --default

See section "Git code layout for running analysis" below for more details. 


### Step 3: sync some data and play with some commands

For an existing repo on disk:
> kospex sync [GIT_REPO]

You can also use the _kgit_ command to clone and sync a repo you have access to

> kgit clone https://github.com/mergestat/mergestat-lite

The above command will clone into the KOSPEX_CODE/GIT_SERVER/ORG/REPO structure

Some commands to try:

> kospex developers -repo [GIT_REPO]
>
> kospex tech-landscape -metadata


## Git code layout for running analysis

One option, if you're inspecting code on your own laptop (or virtual machine), is to use use your home directory. 

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

## Key Use Cases and features

 - Identify technology landscape
 - Identify active developers (e.g. who's had their code committer in the last 90 days)
 - Identify key person or offboarding risk
 - Identify potential complexity challenges (or conceptual integrity concerns)
 - Aggregate repo metadata into a single database for easier and faster querying

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
- Provide a mechanism to better map author_emails to users

## Data extractions and assumptions

Most tables have a column, _repo_id, in the format of GIT_SERVER&tilde;OWNER&tilde;REPO  
So for the repository https://github.com/kospex/kospex the _repo_id would be _github.com&tilde;kospex&tilde;kospex_

Most queries use author_email from git to mean a "developer". 

## How do I develop and improve kospex?

The easiest way to do development on the kospex repo is to clone kospex and run in "dev mode" using the following commands:
> git clone https://github.com/kospex/kospex
> cd kospex
> pip install -e .

## What is a kospex?

We're aiming to [k]now your c[o]de by in[spe]cting the haruspe[x].
From Wikipedia, _The Latin terms haruspex and haruspicina are from an archaic word, hīra = "entrails, intestines"_

So we're going to help look at the "guts of your code" to gain an understanding of the applications, technology landscape (sprawl?) and developers.


