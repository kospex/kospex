## Prerequisites

For the best setup experience, we recommend using Homebrew to install the required dependencies on macOS:


```bash
# Install Git (if not already installed)
brew install git

# Install Git Credential Manager for secure authentication
brew install git-credential-manager

# Install scc for code complexity analysis
brew install scc
```


On other platforms:
- **Git**: Follow instructions at [https://git-scm.com/downloads](https://git-scm.com/downloads)
- **Git Credential Manager**: Follow instructions at [https://github.com/git-ecosystem/git-credential-manager](https://github.com/git-ecosystem/git-credential-manager)
- **scc**: Follow instructions at [https://github.com/boyter/scc](https://github.com/boyter/scc)

## Step 1: Installation, setup and usage

kospex is currently a python module with commands. It works by analysing cloned repositories on the filesystem.

**Optional but strongely recommended** - use a python virtual env.

Installing using pip:

> pip install kospex

For complexity and file type analysis, kospex uses the scc binary (installed in Prerequisites above).
It is optional, but enables much better file type guessing and provide complexity metrics.

### Step 2: Initial kospex setup

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

> kospex developers
>
> kospex tech-landscape -metadata

### Step 4: Use the Web UI to explore your repos and developers

You can run the Web UI using the following command:

> kweb

You can now navigate to http://127.0.0.1:5000


## Git code layout for running analysis

One option, if you're inspecting code on your own laptop is to use use your home directory.

~/kospex/ \
We'll place config files and the kospex DB (Sqlite3) in here for sync'ed data \
~/code/ \
This should have with a structure like \
GIT_SERVER/ORG/REPO \
 \
For example, in your ~/code it might look like: \
github.com/kospex/kospex \
github.com/mergestat/mergestat-lite

This way we have a nice deterministic way of separating different orgs, potentially different instances (e.g. you have an on premise bitbucket and use GitHub.com) as well.
