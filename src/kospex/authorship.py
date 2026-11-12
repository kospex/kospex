"""Per-author commit aggregates turned into key-person and bus-factor rows.

`krunner key-person` and `krunner bus-factor` share one grouped query
(KospexQuery.author_commit_aggregates). Everything after that query is in this
module as pure functions over its rows, so the ranking, percentages and bus
factor are testable without a database.

Each aggregate row is a dict with: author, commits, merges, active_commits,
first_commit, last_commit. ``commits`` excludes merges (AUTHORSHIP_COMMIT, #170),
and ``active_commits`` counts the authorship commits in the active window.

The output is numbers only: counts, percentages and the bus factor at a
stated threshold. There are no risk bands or labels here, by design.

Design: changes/202611-krunner-key-person-bus-factor.md
"""

import kospex_utils as KospexUtils

MAX_THRESHOLDS = 3

KEY_PERSON_FIELDS = [
    "repo_id", "git_server", "git_owner", "git_repo",
    "rank", "author", "is_bot",
    "commits", "merges", "pct_commits",
    "active_commits", "pct_active",
    "in_top_all_time", "in_top_active",
    "first_commit", "last_commit", "tenure_years",
    "repo_commits", "repo_active_commits",
    "active_days", "generated_at",
]


def parse_thresholds(value):
    """Parse '50' or '50,80,90' into a sorted, de-duplicated list of ints.

    1 to MAX_THRESHOLDS integers, each in 1..100. Raises ValueError otherwise.
    """
    parts = [p.strip() for p in str(value).split(",")]
    if any(p == "" for p in parts):
        raise ValueError(f"Invalid threshold list '{value}': empty value.")
    thresholds = set()
    for p in parts:
        if not p.isdigit():
            raise ValueError(f"Invalid threshold '{p}': must be a whole number from 1 to 100.")
        t = int(p)
        if not 1 <= t <= 100:
            raise ValueError(f"Invalid threshold '{p}': must be from 1 to 100.")
        thresholds.add(t)
    if len(thresholds) > MAX_THRESHOLDS:
        raise ValueError(
            f"At most {MAX_THRESHOLDS} thresholds, got {len(thresholds)} ('{value}').")
    return sorted(thresholds)


def bus_factors(counts, thresholds):
    """Smallest n whose top-n counts reach each threshold % of the total.

    Returns {threshold: n}. With no commits the bus factor is undefined, so each
    value is None (not 0, which would read as "nobody is needed").
    """
    total = sum(counts)
    if total == 0:
        return {t: None for t in thresholds}
    result = {}
    pending = sorted(thresholds)
    running = 0
    for n, c in enumerate(sorted(counts, reverse=True), start=1):
        running += c
        # running * 100 >= total * t, in integers, so 50% of 3 needs 2 not 1.5
        while pending and running * 100 >= total * pending[0]:
            result[pending.pop(0)] = n
        if not pending:
            break
    return result


def _pct(part, total):
    """part as a percentage of total, 2 dp, or '' when total is 0."""
    if not total:
        return ""
    return round(100 * part / total, 2)


def _date_part(iso):
    return iso.split("T")[0] if iso else iso


def _tenure_years(first, last):
    """Years between the first and last commit dates, as `kospex key-person` does.

    Computed on the date parts, min one day, so the value matches the existing
    key-person table exactly.
    """
    if not first or not last:
        return ""
    days = KospexUtils.days_between_datetimes(_date_part(first), _date_part(last), min_one=True)
    return round(days / 365, 3)


def _repo_columns(repo):
    return {
        "repo_id": repo["_repo_id"],
        "git_server": repo.get("_git_server") or "",
        "git_owner": repo.get("_git_owner") or "",
        "git_repo": repo.get("_git_repo") or "",
    }


def _by_commits(authors, key="commits"):
    """Sorted by key descending, ties by author, so output is deterministic."""
    return sorted(authors, key=lambda a: (-a[key], a["author"]))


def key_person_rows(repo, authors, top, active_days, generated_at, is_bot):
    """CSV rows (KEY_PERSON_FIELDS) for one repo's key people.

    top=None returns every author. Otherwise it returns the top `top` authors of
    all time, then any of the top `top` active authors not already included,
    which is what `kospex key-person` shows. An active author needs at least one
    authorship commit in the window.
    """
    ranked = _by_commits(authors)
    if not ranked:
        return []
    repo_commits = sum(a["commits"] for a in ranked)
    repo_active = sum(a["active_commits"] for a in ranked)
    rank = {a["author"]: i for i, a in enumerate(ranked, start=1)}

    if top is None:
        top_all, top_active = ranked, [a for a in ranked if a["active_commits"] > 0]
        selected = ranked
    else:
        top_all = ranked[:top]
        top_active = [a for a in _by_commits(authors, "active_commits")
                      if a["active_commits"] > 0][:top]
        selected = list(top_all)
        seen = {a["author"] for a in top_all}
        selected.extend(a for a in top_active if a["author"] not in seen)

    in_all = {a["author"] for a in top_all}
    in_active = {a["author"] for a in top_active}
    base = _repo_columns(repo)
    rows = []
    for a in selected:
        row = dict(base)
        row.update({
            "rank": rank[a["author"]],
            "author": a["author"],
            "is_bot": 1 if is_bot(a["author"]) else 0,
            "commits": a["commits"],
            "merges": a["merges"],
            "pct_commits": _pct(a["commits"], repo_commits),
            "active_commits": a["active_commits"],
            "pct_active": _pct(a["active_commits"], repo_active),
            "in_top_all_time": 1 if a["author"] in in_all else 0,
            "in_top_active": 1 if a["author"] in in_active else 0,
            "first_commit": a["first_commit"] or "",
            "last_commit": a["last_commit"] or "",
            "tenure_years": _tenure_years(a["first_commit"], a["last_commit"]),
            "repo_commits": repo_commits,
            "repo_active_commits": repo_active,
            "active_days": active_days,
            "generated_at": generated_at,
        })
        rows.append(row)
    return rows


def bus_factor_fields(thresholds):
    """The bus-factor CSV header for these thresholds, in a fixed order."""
    def each(prefix):
        return [f"{prefix}_{t}" for t in thresholds]

    return (
        ["repo_id", "git_server", "git_owner", "git_repo", "active_days",
         "authors", "commits"]
        + each("bus_factor") + each("human_bus_factor")
        + ["bot_commits", "bot_commit_pct", "top_author", "top_author_pct",
           "active_authors", "active_commits"]
        + each("active_bus_factor") + each("active_human_bus_factor")
        + ["active_top_author", "active_top_author_pct", "last_commit", "generated_at"]
    )


def _top(authors, key, total):
    """(author, pct) of the author with most `key`, or ('', '') with none."""
    candidates = [a for a in authors if a[key] > 0]
    if not candidates:
        return "", ""
    first = _by_commits(candidates, key)[0]
    return first["author"], _pct(first[key], total)


def bus_factor_row(repo, authors, thresholds, active_days, generated_at, is_bot):
    """One CSV row (bus_factor_fields(thresholds)) for a repo.

    The human_* variants leave bots out of both the top-n and the total. Bots
    stay in the all-authors variant and are reported as bot_commits, because a
    repo maintained mostly by bots is itself worth seeing.
    """
    def none_to_blank(values):
        return {t: ("" if v is None else v) for t, v in values.items()}

    humans = [a for a in authors if not is_bot(a["author"])]
    commits = sum(a["commits"] for a in authors)
    active_commits = sum(a["active_commits"] for a in authors)
    bot_commits = commits - sum(a["commits"] for a in humans)

    groups = {
        "bus_factor": bus_factors([a["commits"] for a in authors], thresholds),
        "human_bus_factor": bus_factors([a["commits"] for a in humans], thresholds),
        "active_bus_factor": bus_factors([a["active_commits"] for a in authors], thresholds),
        "active_human_bus_factor": bus_factors(
            [a["active_commits"] for a in humans], thresholds),
    }

    top_author, top_pct = _top(authors, "commits", commits)
    active_top, active_top_pct = _top(authors, "active_commits", active_commits)
    last_commits = [a["last_commit"] for a in authors if a["last_commit"]]

    row = _repo_columns(repo)
    row.update({
        "active_days": active_days,
        "authors": sum(1 for a in authors if a["commits"] > 0),
        "commits": commits,
        "bot_commits": bot_commits,
        "bot_commit_pct": _pct(bot_commits, commits),
        "top_author": top_author,
        "top_author_pct": top_pct,
        "active_authors": sum(1 for a in authors if a["active_commits"] > 0),
        "active_commits": active_commits,
        "active_top_author": active_top,
        "active_top_author_pct": active_top_pct,
        "last_commit": max(last_commits) if last_commits else "",
        "generated_at": generated_at,
    })
    for prefix, values in groups.items():
        for t, v in none_to_blank(values).items():
            row[f"{prefix}_{t}"] = v
    return {f: row[f] for f in bus_factor_fields(thresholds)}
