"""Shared run plumbing for `kgit github` per-repo exports (--properties, --dependabot).

A per-repo export makes one request (or more) per repo, so a large org takes a
while. Each repo's lines are written to `<name>.partial` as it finishes, and
only a finished run renames them, so:

- a file without `.partial` is always a complete run;
- a previous complete file is left alone until a new run completes;
- a stopped run (rate limit, Ctrl-C, a dropped session) keeps every repo it finished.
"""
import json
import os

PARTIAL_SUFFIX = ".partial"


class RunFiles:
    """One or more JSONL files, written one repo at a time to `.partial` names.

    add_repo(lines_0, lines_1, ...) writes each list of lines to the matching
    file and flushes them all, then counts the repo. finish() renames the
    partials to the final names; leaving the `with` block without it keeps them.
    """

    def __init__(self, *paths):
        if not paths:
            raise ValueError("RunFiles needs at least one path")
        self.paths = [os.fspath(p) for p in paths]
        self.partials = [p + PARTIAL_SUFFIX for p in self.paths]
        self.repos = 0
        self.lines = [0] * len(self.paths)
        self._files = None

    def __enter__(self):
        opened = []
        try:
            for partial in self.partials:
                opened.append(open(partial, "w", encoding="utf-8"))
        except BaseException:
            for f in opened:
                f.close()
            raise
        self._files = opened
        return self

    def add_repo(self, *line_lists):
        if len(line_lists) != len(self._files):
            raise ValueError(f"expected {len(self._files)} line lists, got {len(line_lists)}")
        for i, (f, lines) in enumerate(zip(self._files, line_lists)):
            for line in lines:
                f.write(json.dumps(line, ensure_ascii=False) + "\n")
            self.lines[i] += len(lines)
        for f in self._files:
            f.flush()
        self.repos += 1

    def _close(self):
        if self._files:
            for f in self._files:
                f.close()
            self._files = None

    def finish(self):
        self._close()
        for partial, path in zip(self.partials, self.paths):
            os.replace(partial, path)

    def __exit__(self, *exc):
        self._close()
        return False


def _and_join(items):
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def stop_message(reason, files, total, filename):
    """What a stopped run says: how many repos it kept, where, and that FILE wasn't written."""
    return (f"{reason}. Stopped after {files.repos} of {total} repos: those are in "
            f"{_and_join(files.partials)}. {filename} was not written. "
            "Rerun to get every repo.")
