## Prerequisites

For the best setup experience, we recommend using Homebrew to install the required dependencies on macOS:

```bash
# Install Git (if not already installed)
brew install git

# Install Git Credential Manager for secure authentication (macOS only)
brew install git-credential-manager

# Install scc for code complexity analysis
brew install scc
```

On other platforms:
- **Git**: Follow instructions at [https://git-scm.com/downloads](https://git-scm.com/downloads)
- **Git Credential Manager**: Follow instructions at [https://github.com/git-ecosystem/git-credential-manager](https://github.com/git-ecosystem/git-credential-manager)
- **scc**: Follow instructions at [https://github.com/boyter/scc](https://github.com/boyter/scc)

### Linux (Ubuntu tested)

We've tested on ubuntu, you'll need:
- git (apt install git)
- homebrew (see instructions above)
- If you're using Github Only (Organisations with the same credentials, or SAML/SSO) - See below on using the Github CLI

### Using the GitHub CLI, classic tokens and SAML

You can configure your git CLI (which kospex wraps) using the GitHub CLI _gh_

If you're organisation is using SAML / Single Sign On (e.g. Office365), we've tested that with kospex given the following steps:
- Create a classic token (gh requires repo, read:org and workflow permissions)
- Authorise the token for your organisation in the web ui
- If you haven't done it in the GH WebUI, When doing gh auth (running through the steps to use a token), it will ask you to authorisate the token

confirm you can clone a repo using
```bash
gh repo clone ORG/REPO
```
If this works, you can configure your git using gh
'''bash
gh auth setup-git
'''

the kgit clone commands will now work with GitHub and your org SSO account that _gh_ uses. 

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

You can now navigate to http://127.0.0.1:8000


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
