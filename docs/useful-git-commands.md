# Useful Git commands

### Get the last author date of a file

By default, when you clone a repo, the file dates will be the clone date and not the last modified.

The following Git command returns the last author date of the file (NOT the committer date):

```bash
git log -1 --format=%aD FILENAME
```

The date will be in the RFC2822 format.

### When is the first and last commit of a repo?

How old is the repo? (Date of the first commit)

```bash
git log --reverse --format=%aD | head -1
```

When was it last changed? (Date of last commit)

```bash
git log --format=%aD -1
```

### How many authors (developers) are in this repo? (with how many commits)

This nifty command shows all the authors, their email and displays and sorts by number of commits.

```bash
git shortlog -sne
```

Description of switches (from `git shortlog --help`):

- `-s`, `--summary` — suppress commit description and provide a commit count summary only.
- `-n` — sort output according to the number of commits per author instead of author alphabetic order.
- `-e`, `--email` — show the email address of each author.
