
### Get the last author date of a file

By default, when you clone a repo, the file dates will be the clone date and not the last modfied. 

The following Git command returns the last author date of the file (NOT the Committer Date)

> git log -1 --format=%aD <file_path>

The date will be in the RFC2822 format.

### When is the first and last commit of a repo?

How old is the repo? (Date of the first commit)

> git log --reverse --format=%aD \| head -1

When was it last changed? (Date of last commit)

> git log --format=%aD -1

### How many authors (developers) are in this repo? (with how many commits)

This nifty command shows all the authors, their email and displays and sorts by number of commits.

> git short -sne

Description of switches from _git shortlog --help_ 

-s, --summary Suppress commit description and provide a commit count summary only.

-n, Sort output according to the number of commits per author instead of author alphabetic order.

-e, --email Show the email address of each author.



