# kgit

The kgit command is a helper for some git commands and utilities on repos.

## clone

The clone function is a wrapper around the CLI git installed on your machine.
It will authenticate with your credentials as it's running in your shell.

```bash
kgit clone https://github.com/OWNER/REPO
```

By default, the clone command clones the repo to the `KOSPEX_CODE/SERVER/ORG/REPO`
file structure and syncs the entire git history to the kospex database.

You can confirm the sync by running the following kospex command:

```bash
kospex list-repos -db
```

### Cloning a list of repos in a file

To clone and sync in bulk you can use the `-filename FILENAME` switch:

```bash
kgit clone -filename FILENAME
```

For example, the contents of the file might look like:

```
https://github.com/kospex/kospex
# This is a comment
https://github.com/mergestat/mergestat-lite
```

## bitbucket

All bitbucket commands require the credentials to be set. The following environment
variables are required:

- `BITBUCKET_USERNAME`
- `BITBUCKET_APP_PASSWORD`

```bash
kgit bitbucket -test-auth
```

This flag will check if the credentials work, display the status and exit.

```bash
kgit bitbucket -workspace WORKSPACE
```

If you want to write all the clone URLs to a file you can use the `-out-repo-list FILENAME` switch:

```bash
kgit bitbucket -workspace WORKSPACE -out-repo-list FILENAME
```

This can be used with the `kgit clone -filename` switch as shown above.

## github

The github function can interact with both authenticated and public orgs and users.

For authenticated requests the `GITHUB_AUTH_TOKEN` must be set in the environment.

```bash
kgit github -test-auth
```

This `-test-auth` flag will check if the token (GitHub PAT) works, display the status and exit.

To list the repos for a user:

```bash
kgit github -user USERNAME
```

For an organisation:

```bash
kgit github -org ORG_OR_TEAM
```
