"""Manual live check: crawl a running kweb and report every page that breaks.

Starts from the main pages, plus every route that takes an id, requested with
each kind of id: a repo, its org, its server, a GitLab nested-group repo (if
the DB has one) and a base64 developer id. It then follows the links on each
page. Not part of CI: the name does not match test_*.py, so pytest never
collects it.

    kweb                                              # in another terminal
    python tests/manual/web_crawl.py                  # seed repo picked from the DB
    python tests/manual/web_crawl.py --repo github.com~kospex~kospex
    python tests/manual/web_crawl.py --per-route 5 --max-urls 800
    python tests/manual/web_crawl.py --base-url http://127.0.0.1:8017

The crawl is bounded by route shape, not by depth: at most --per-route URLs of
each shape (/repo/{id}, /developer/?author_email={v}, ...) are fetched. So it
reaches every route the pages link to, and still finishes in minutes on a DB
with tens of thousands of developers.

The seed repo decides which ids get exercised. By default it is a repo with a
handful of developers, so its pages are quick and all its developer links get
followed. Base64 developer ids only appear in JavaScript-built links, which
the crawler cannot see, so it builds them itself from each author_email found.

A failure is a 5xx, a timeout or a connection error, and makes the exit status
1. A 4xx is listed for review but does not fail the run, because some are
deliberate (a summary page for a developer id is a 404 by design).

What it cannot catch: a page that returns 200 with wrong or empty data.
"""
import argparse
import sys
import time
from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit

import requests

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

import kospex_utils as KospexUtils  # noqa: E402
from kospex_query import KospexQuery  # noqa: E402

DEFAULT_BASE_URL = "http://127.0.0.1:8000"

# Never requested: side effects, or not pages.
SKIP_PREFIXES = ("/clear-cache", "/static/", "/docs", "/redoc", "/openapi.json")

# Main pages to start from.
START_PAGES = [
    "/", "/summary/", "/repos/", "/orgs/", "/servers/", "/developers/",
    "/landscape/", "/tenure/", "/osi/", "/dependencies/", "/commits/",
    "/metadata/", "/metadata/repos/", "/orphans/", "/recent/", "/tags/",
    "/graph/", "/files/repo/", "/supply-chain/", "/observations/",
    "/tech-change/", "/meta/author-domains", "/help/",
]

# Routes that take a scope id. Each is tried with every kind of id below; a
# combination a route does not support should be a 4xx, never a 5xx.
ID_ROUTES = [
    "/summary/{}", "/repos/{}", "/orgs/{}", "/osi/{}", "/landscape/{}",
    "/tenure/{}", "/dependencies/{}", "/orphans/{}", "/bubble/{}",
    "/treemap/{}", "/graph/{}", "/metadata/repos/{}",
]
REPO_ROUTES = [
    "/repo/{}", "/commits/{}", "/key-person/{}", "/hotspots/{}",
    "/files/repo/{}", "/collab/{}", "/developers/active/{}",
]  # /file-collab/ needs ?file_path=; the crawl finds real links to it
DEVELOPER_B64_ROUTES = ["/developer/{}", "/bubble/{}", "/treemap/{}"]

# Path segments that are part of a route rather than an id. Anything else
# after the first segment (a repo id, a language, a tag, a hash) is an id.
ROUTE_WORDS = {
    seg
    for route in START_PAGES + ID_ROUTES + REPO_ROUTES + DEVELOPER_B64_ROUTES
    for seg in route.split("/")
    if seg and seg != "{}"
} | {"api", "focus", "graph", "active", "repo", "repos", "author-domains"}


@dataclass
class Result:
    url: str
    shape: str
    status: object  # int, or "timeout" / "error"
    seconds: float
    found_via: str
    detail: str = ""

    @property
    def failed(self):
        return not isinstance(self.status, int) or self.status >= 500


class LinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            for name, value in attrs:
                if name == "href" and value:
                    self.links.append(value)


def shape_of(path_and_query):
    """Route shape of a URL: id segments become {id}, query values {v}."""
    parts = urlsplit(path_and_query)
    segments = [
        seg if i <= 1 or not seg or seg in ROUTE_WORDS else "{id}"
        for i, seg in enumerate(parts.path.split("/"))
    ]
    shape = "/".join(segments)
    if parts.query:
        keys = sorted({k for k, _ in parse_qsl(parts.query, keep_blank_values=True)})
        shape += "?" + "&".join(f"{k}={{v}}" for k in keys)
    return shape


def pick_seed_repo(kquery):
    """A plain three-part repo with a handful of developers."""
    rows = kquery.kospex_db.query(
        """SELECT _repo_id, COUNT(*) AS devs FROM developer_stats
           WHERE stat_type = 'ALL_TIME' AND _repo_id NOT LIKE '%~~%'
           GROUP BY _repo_id HAVING devs BETWEEN 3 AND 15
           ORDER BY devs DESC, _repo_id LIMIT 1"""
    )
    row = next(iter(rows), None)
    return row["_repo_id"] if row else None


def nested_group_repo(kquery):
    rows = kquery.kospex_db.query(
        "SELECT DISTINCT _repo_id FROM developer_stats WHERE _repo_id LIKE '%~~%' LIMIT 1"
    )
    row = next(iter(rows), None)
    return row["_repo_id"] if row else None


def top_developer(kquery, repo_id):
    rows = kquery.kospex_db.query(
        """SELECT author_email FROM developer_stats
           WHERE _repo_id = ? AND stat_type = 'ALL_TIME'
           ORDER BY total_commits DESC LIMIT 1""",
        [repo_id],
    )
    row = next(iter(rows), None)
    return row["author_email"] if row else None


def seed_urls(repo_id, nested_id, developer_email):
    """(url, found_via) pairs to start the crawl from."""
    server, owner, _ = repo_id.split("~")  # seed is never a nested-group id
    org_key = f"{server}~{owner}"
    seeds = [(p, "start page") for p in START_PAGES]

    scope_ids = [("repo", repo_id), ("org", org_key), ("server", server)]
    if nested_id:
        scope_ids.append(("nested-group repo", nested_id))
    if developer_email:
        scope_ids.append(("developer b64", KospexUtils.encode_base64(developer_email)))

    for kind, value in scope_ids:
        for route in ID_ROUTES:
            seeds.append((route.format(value), f"seed: {kind} id"))
    for value in filter(None, [repo_id, nested_id]):
        for route in REPO_ROUTES:
            seeds.append((route.format(value), "seed: repo route"))
    seeds.append((f"/org-graph/{org_key}", "seed: org id"))
    return seeds


def developer_b64_urls(link):
    """Base64 forms of a /developer(s)/?author_email= link."""
    parts = urlsplit(link)
    if not parts.path.startswith(("/developer/", "/developers/")):
        return []
    email = dict(parse_qsl(parts.query)).get("author_email")
    if not email:
        return []
    encoded = KospexUtils.encode_base64(email)
    return [route.format(encoded) for route in DEVELOPER_B64_ROUTES]


def fetch(session, base_url, path, timeout):
    started = time.perf_counter()
    try:
        response = session.get(base_url + path, timeout=timeout)
        elapsed = time.perf_counter() - started
        return response.status_code, elapsed, response
    except requests.exceptions.Timeout:
        return "timeout", time.perf_counter() - started, None
    except requests.exceptions.RequestException as exc:
        return "error", time.perf_counter() - started, exc


def internal_links(base_url, page_path, html):
    parser = LinkParser()
    parser.feed(html)
    base_host = urlsplit(base_url).netloc
    links = []
    for href in parser.links:
        if href.startswith(("#", "mailto:", "javascript:")) or "${" in href or "{{" in href:
            continue
        absolute = urlsplit(urljoin(base_url + page_path, href))
        if absolute.netloc != base_host:
            continue
        path = absolute.path or "/"
        if absolute.query:
            path += "?" + urlencode(parse_qsl(absolute.query, keep_blank_values=True))
        links.append(path)
    return links


def crawl(base_url, seeds, per_route, max_urls, timeout, verbose):
    session = requests.Session()
    queue = deque()
    seen = set()
    per_shape = Counter()  # queued or fetched, per route shape
    capped = Counter()
    results = []

    def enqueue(path, found_via, seed=False):
        if path in seen or path.startswith(SKIP_PREFIXES):
            return
        seen.add(path)
        shape = shape_of(path)
        # Seeds are never capped: the five kinds of id share shapes such as
        # /summary/{id}, and each kind is the point of seeding.
        if not seed and per_shape[shape] >= per_route:
            capped[shape] += 1
            return
        per_shape[shape] += 1
        queue.append((path, found_via))

    for path, found_via in seeds:
        enqueue(path, found_via, seed=True)

    while queue and len(results) < max_urls:
        path, found_via = queue.popleft()
        shape = shape_of(path)

        status, seconds, response = fetch(session, base_url, path, timeout)
        detail = ""
        if isinstance(status, int) and status >= 400 and response is not None:
            detail = response.text[:120].replace("\n", " ")
        elif not isinstance(status, int):
            detail = str(response)[:120] if response is not None else ""
        result = Result(path, shape, status, seconds, found_via, detail)
        results.append(result)
        if verbose or result.failed:
            print(f"[{len(results):4}] {status!s:>7} {seconds:6.2f}s  {path}", flush=True)

        if (isinstance(status, int) and status == 200 and response is not None
                and "text/html" in response.headers.get("content-type", "")):
            for link in internal_links(base_url, path, response.text):
                enqueue(link, f"link on {path}")
                for extra in developer_b64_urls(link):
                    enqueue(extra, f"b64 of a developer link on {path}")

    return results, capped, len(queue)


def report(results, capped, unvisited, coverage, slow_seconds):
    failed = [r for r in results if r.failed]
    client_errors = [r for r in results if isinstance(r.status, int) and 400 <= r.status < 500]
    slow = sorted((r for r in results if not r.failed and r.seconds >= slow_seconds),
                  key=lambda r: -r.seconds)

    print("\n== Coverage")
    for label, value in coverage:
        print(f"  {label:22} {value}")
    b64 = [r for r in results if "b64" in r.found_via]
    print(f"  {'base64 URLs fetched':22} {len(b64)} "
          f"({sum(1 for r in b64 if r.status == 200)} returned 200)")

    print(f"\n== Fetched {len(results)} URLs across {len({r.shape for r in results})} route shapes")
    statuses = Counter(str(r.status) for r in results)
    print("  " + ", ".join(f"{s}: {n}" for s, n in sorted(statuses.items())))
    if unvisited:
        print(f"  {unvisited} URLs still queued when --max-urls was reached")
    if capped:
        print(f"  {sum(capped.values())} URLs skipped by --per-route, over {len(capped)} shapes")

    if failed:
        print(f"\n== FAILED ({len(failed)})")
        for r in failed:
            print(f"  {r.status!s:>7}  {r.url}\n           via {r.found_via}")
            if r.detail:
                print(f"           {r.detail}")

    if client_errors:
        print(f"\n== 4xx, review ({len(client_errors)})")
        by_shape = defaultdict(list)
        for r in client_errors:
            by_shape[r.shape].append(r)
        for shape, rows in sorted(by_shape.items()):
            print(f"  {rows[0].status}  {shape}  (e.g. {rows[0].url}, via {rows[0].found_via})")

    if slow:
        print(f"\n== Slow (>= {slow_seconds:g}s)")
        for r in slow[:15]:
            print(f"  {r.seconds:6.2f}s  {r.url}")

    print("\nRESULT:", "FAIL" if failed else "OK",
          f"- {len(failed)} failed, {len(client_errors)} 4xx, {len(results)} fetched")
    return 1 if failed else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--repo", help="Seed REPO_ID (default: a repo with 3-15 developers)")
    parser.add_argument("--per-route", type=int, default=3,
                        help="Max URLs fetched per route shape (default 3)")
    parser.add_argument("--max-urls", type=int, default=500,
                        help="Stop after this many fetches (default 500)")
    parser.add_argument("--timeout", type=float, default=60.0,
                        help="Seconds per request (default 60)")
    parser.add_argument("--slow", type=float, default=5.0,
                        help="Report pages slower than this many seconds (default 5)")
    parser.add_argument("-v", "--verbose", action="store_true", help="Print every fetch")
    args = parser.parse_args()
    base_url = args.base_url.rstrip("/")

    try:
        requests.get(base_url + "/health", timeout=10).raise_for_status()
    except requests.exceptions.RequestException as exc:
        sys.exit(f"kweb is not answering at {base_url}: {exc}")

    kquery = KospexQuery()
    repo_id = args.repo or pick_seed_repo(kquery)
    if not repo_id:
        sys.exit("No repo with 3-15 developers in the DB; pass --repo REPO_ID.")
    if "~~" in repo_id or repo_id.count("~") != 2:
        sys.exit("--repo must be a plain SERVER~OWNER~REPO id (nested groups are found automatically).")
    nested_id = nested_group_repo(kquery)
    developer = top_developer(kquery, repo_id)

    coverage = [
        ("kweb", base_url),
        ("seed repo", repo_id),
        ("nested-group repo", nested_id or "none in this DB, not tested"),
        ("base64 developer", "seeded from the repo's top committer, plus each author_email link"
                             if developer else "none found"),
    ]
    print(f"Crawling {base_url} from {repo_id} "
          f"(per-route {args.per_route}, max {args.max_urls} URLs)\n", flush=True)

    started = time.perf_counter()
    results, capped, unvisited = crawl(base_url, seed_urls(repo_id, nested_id, developer),
                                       args.per_route, args.max_urls, args.timeout, args.verbose)
    coverage.append(("elapsed", f"{time.perf_counter() - started:.0f}s"))
    sys.exit(report(results, capped, unvisited, coverage, args.slow))


if __name__ == "__main__":
    main()
