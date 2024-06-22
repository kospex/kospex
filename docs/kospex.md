# kospex

This is the main command to run analysis, sync directories, find orphaned repos, check dependencies, manage groups, health checks and more!

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
 



