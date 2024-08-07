# kospex

This is the main command to run analysis, sync directories, find orphaned repos, check dependencies, manage groups, health checks and more!

## developers

List all the developers in the kospex directory, by default, only those with active commits in the last 90 days. 

> kospex developers

If you want developers for "all time", use the -all flag

> kospex developers -all

If you need to restrict the search to only in a given domain name / server

> kospex developers -server [DOMAIN_NAME]


## sync

The sync command takes all the commit history and adds it to the kospex database.

> kospex sync [DIRECTORY]

Most likely, you'll cd to a repo and then run the following command

> kospex sync .


*Parameters*

'--no-scc', is_flag=True, default=False, help="Skip scc analysis."

## orphans

Orphans occur when a repo no longer has someone working in the organisation or team who's active. 

All you need to do is run the following command

> kospex orphans

This will display a table with the following columns
 - _repo_id
 - committers
 - active
 - Orphaned
 - % Here

*Parameters*

'-days', type=int, default=90, help='Committed in X days is considered active.(default 90)'
'-window', type=int, default=365, help='Days to consider for orphaned repos. (default 365)'
 

## list-repos

This command either lists all the known repos that have been sync'ed to the database or \
list the repos found searching a directory path.

*By directory*

> kospex list-repos [DIRECTORY]

*From the database*

> kospex list-repos -db

If you want to restrict the search to a specific server or domain name, use the -server flag \

> kospex list-repos -db -server [DOMAIN_NAME]

E.g.

> kospex list-repos -db -server bitbucket.org

*Including the Repo ID*

Both -db and the filepath option support the -repo_id flag, which returns the repo_id. \
The repo_id is used as a key in the format of GIT_SERVER&tilde;OWNER&tilde;REPO \  
So for the repository https://github.com/kospex/kospex the _repo_id would be _github.com&tilde;kospex&tilde;kospex_