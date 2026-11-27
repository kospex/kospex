"""Query-string parsing, URLs and paging for kweb's /developers/ page."""
from urllib.parse import quote, urlencode

# Lists up to this long are shown whole; longer ones are paged, and a longer
# active list gives way to the overview.
LIST_THRESHOLD = 250
PAGE_SIZE = 50
BUSIEST_LIMIT = 10
MAX_PAGE = 1_000_000

STATUSES = ("active", "aging", "stale", "unmaintained", "all")
SORT_DEFAULT_DIRECTION = {
    "last_seen": "asc", "commits": "desc", "repos": "desc", "email": "asc"}


def parse_list_params(query_params):
    """{"status", "q", "sort", "dir", "page"} from a query string.

    Raises ValueError, with a message fit to show, for a value that isn't one
    the page offers.
    """
    status = (query_params.get("status") or "").strip().lower() or None
    if status and status not in STATUSES:
        raise ValueError(f"status must be one of: {', '.join(STATUSES)}.")

    sort = query_params.get("sort") or "last_seen"
    if sort not in SORT_DEFAULT_DIRECTION:
        raise ValueError(f"sort must be one of: {', '.join(SORT_DEFAULT_DIRECTION)}.")

    direction = query_params.get("dir") or SORT_DEFAULT_DIRECTION[sort]
    if direction not in ("asc", "desc"):
        raise ValueError("dir must be 'asc' or 'desc'.")

    page = query_params.get("page", "1")
    if not (page.isascii() and page.isdigit() and 1 <= int(page) <= MAX_PAGE):
        raise ValueError(f"page must be a whole number from 1 to {MAX_PAGE}.")

    return {"status": status, "q": (query_params.get("q") or "").strip() or None,
            "sort": sort, "dir": direction, "page": int(page)}


def developers_url(scope, **params):
    """A /developers/ URL: the scope in the path, everything else in the query.

    scope is {} or one of server, org_key, repo_id. Empty params are left out.
    """
    value = next(iter(scope.values()), "")
    query = urlencode({key: val for key, val in params.items() if val})
    url = f"/developers/{quote(value, safe='~')}"
    return f"{url}?{query}" if query else url


def page_window(total, page):
    """{"limit", "offset", "page", "pages"} for a list of total rows."""
    if total <= LIST_THRESHOLD:
        return {"limit": None, "offset": 0, "page": 1, "pages": 1}
    return {"limit": PAGE_SIZE, "offset": (page - 1) * PAGE_SIZE, "page": page,
            "pages": -(-total // PAGE_SIZE)}
