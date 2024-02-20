
### Get the last author date of a file

By default, when you clone a repo, the file dates will be the clone date and not the last modfied. 

The following Git command returns the last author date of the file (NOT the Commit Date)

git log -1 --format=%aD <file_path>

The date will be in the RFC2822 format.


