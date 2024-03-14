## Installation, setup and usage

kospex is currently a collection of tools, bound together by python in a docker container. 
It works by analysing cloned repositories on the filesystem. 

### Step 0: Prepare (clone) the repositories you want to assess

See section "Git code layout for running analysis" below. For simple "one off" analysis just clone some repo's into a directory as a starting point. 

Ideally, a structure like \
/BASE/GIT_SERVER/ORG/REPO

### Step 1: Clone kospex

> git clone https://github.com/kospex/kospex.git

### Step 2: Install the python dependencies (and scc)

**Optional but strongely recommended** - use a python virtual env. 

> pip install -r requirements.txt

Follow the instructions for installing [scc](https://github.com/boyter/scc)

> export PATH=$PATH:$PWD

Add this directory to your path, kospex toolkit is a collection on python executables.

If you are ok to use the ~/code directory for cloned repos, then run: 
> kospex init --default

### Step 3: sync some data and play with some commands

> kospex sync [GIT_REPO]
>
> kospex developers -repo [GIT_REPO]
>
> kospex tech-landscape -metadata

You can also use the _kgit_ command to clone and synx a repo (if you have access)

> kgit clone -sync -repo https://github.com/mergestat/mergestat-lite

The above command will clone into the KOSPEX_CODE/GIT_SERVER/ORG/REPO structure