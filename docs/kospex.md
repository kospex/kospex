# kospex

This is the main command to run analysis, sync directories, find orphaned repos, check dependencies, manage groups, health checks and more!

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
 



