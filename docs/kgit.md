# kgit

The kgit command is helper for some git commands and utilities on repos

## clone

The clone function is a wrapper around the CLI git installed in your machine.
It will authenticate with your credentials as it's runing in your shell. 

> kgit clone https://github.com/

By default, the clone command: \
clones the repo to the KOSPEX_CODE/SERVER/ORG/REPO file structure and \
syncs the entire git history to the kospex database. 

You can confirm the sync by running the following kospex command
> kospex list-repos -db

### Cloning a list of repos in a file

To clone and sync in bulk you can use the _-filename FILENAME_ switch like: \
> kgit clone -filename FILENAME

For example, the contents of the file might look like: \
https://github.com/kospex/kospex \
\# This is a comment \
https://github.com/mergestat/mergestat-lite


## bitbucket

All bitbucket commands require the credentials to be set
The following environment variables are required: \
BITBUCKET_USERNAME \
BITBUCKET_APP_PASSWORD

> kgit bitbucket -test-auth

This flag will check if the credentials work, display the status and exit.

> kgit bitbucket -workspace _WORKSPACE_

If you want to write all the clone URLs to a file you can use the _-out-repo-list FILENAME_ switch
> kgit bitbucket -workspace _WORKSPACE_ -out-repo-list FILENAME

This can be used with the kgit clone _-filename_ switch as shown above. 

## github

The _github_ function can interact with both authenticated and public orgs and users. 

For authenticated requests the GITHUB_AUTH_TOKEN must be set in the environment. 

> kgit github -test-auth 

This _-test-auth_ flag will check if the token (Github PAT) works, display the status and exit.

To list the repos for a user:

> kgit github -user _USERNAME_

For an organisation:

> kgit gihub -org _ORG OR TEAM_





