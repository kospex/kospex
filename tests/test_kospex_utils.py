""" Tests for Kospex Utils """
from pathlib import Path
import kospex_utils as KospexUtils

HERE = Path(__file__).parent

# Tests

def test_developer_tenure():
    """
    Test the tenure default ordered dict for status checking
    """
    assert "Single day" == KospexUtils.get_status(0)
    assert "< 3 months" == KospexUtils.get_status(15)

    assert KospexUtils.DEFAULT_MAX_STATUS == KospexUtils.get_status(731)

def test_mailmap():
    """
    Test mailmap parsing
    """

    results = KospexUtils.parse_mailmap(f"{HERE}/mailmap/github.com/adamtornhill/code-maat/.mailmap")
    print(results)

def test_get_status_legend():
    """get_status_legend returns a rich Table describing the 4 statuses."""
    from rich.table import Table

    legend = KospexUtils.get_status_legend()
    assert isinstance(legend, Table)

    from rich.console import Console
    console = Console(width=120)
    with console.capture() as capture:
        console.print(legend)
    text = capture.get()
    for status in ["Active", "Aging", "Stale", "Unmaintained"]:
        assert status in text
    # thresholds appear in the descriptions
    assert "90" in text
    assert "180" in text
    assert "365" in text

def test_get_status_table_rich():
    """get_status_table returns a rich Table with counts and percentages."""
    from rich.table import Table
    from rich.console import Console

    status = {"Active": 3, "Aging": 1, "Stale": 0, "Unmaintained": 1}
    table = KospexUtils.get_status_table(status)
    assert isinstance(table, Table)

    console = Console(width=120)
    with console.capture() as capture:
        console.print(table)
    text = capture.get()
    for header in ["Active", "Aging", "Stale", "Unmaintained", "Total"]:
        assert header in text
    assert "5" in text       # total count
    assert "%" in text       # percentage row present


# --- DB health in validate_kospex_setup() -----------------------------------


def _fresh_home_util(tmp_path, monkeypatch):
    from kospex.habitat_config import HabitatConfig
    monkeypatch.setenv("KOSPEX_HOME", str(tmp_path))
    HabitatConfig.reset_instance()


def _behind_db_at(tmp_path):
    """Build the DB, then rewind it to look like migrations never ran."""
    import kospex_schema as KospexSchema
    db = KospexSchema.connect_or_create_kospex_db()
    db.execute("DELETE FROM schema_migrations")
    db.execute(
        "UPDATE kospex_config SET value = '2' WHERE key = ?",
        [KospexSchema.KOSPEX_DB_VERSION_KEY],
    )
    db.conn.commit()
    return db


def test_validate_includes_a_database_section(tmp_path, monkeypatch):
    import kospex_utils as KospexUtils
    _fresh_home_util(tmp_path, monkeypatch)
    _behind_db_at(tmp_path)

    validation = KospexUtils.validate_kospex_setup()

    assert "database" in validation
    assert validation["database"]["pending_count"] == 4


def test_behind_db_is_not_healthy(tmp_path, monkeypatch):
    """The regression: a clean install reported HEALTHY over a DB that would
    crash kgit pull."""
    import kospex_utils as KospexUtils
    _fresh_home_util(tmp_path, monkeypatch)
    _behind_db_at(tmp_path)

    validation = KospexUtils.validate_kospex_setup()

    assert validation["overall_status"] != "healthy"
    assert any("migration" in issue for issue in validation["critical_issues"])
    assert any("upgrade-db" in rec for rec in validation["recommendations"])


def test_current_db_is_healthy(tmp_path, monkeypatch):
    import kospex_schema as KospexSchema
    import kospex_utils as KospexUtils
    _fresh_home_util(tmp_path, monkeypatch)
    KospexSchema.connect_or_create_kospex_db()

    validation = KospexUtils.validate_kospex_setup()

    assert validation["database"]["pending_count"] == 0
    assert validation["overall_status"] == "healthy"


# --- repo_id / org_key parsing: nested orgs (#94 Step 1) ---------------------
# A '/' in the org is encoded as '~~' by KospexGit.generate_repo_id -- GitLab
# subgroups, and Azure DevOps org/project. A plain split on '~' therefore sees
# more than three segments and previously returned None.

def test_parse_repo_id_flat():
    got = KospexUtils.parse_repo_id("github.com~acme~svc")
    assert got["git_server"] == "github.com"
    assert got["org"] == "acme"
    assert got["repo"] == "svc"
    assert got["org_key"] == "github.com~acme"


def test_parse_repo_id_nested_org_is_decoded():
    """org comes back with a real '/'; it is what _git_owner holds."""
    got = KospexUtils.parse_repo_id("gitlab.com~group~~subgroup~repo")
    assert got["git_server"] == "gitlab.com"
    assert got["org"] == "group/subgroup"
    assert got["repo"] == "repo"


def test_parse_repo_id_org_key_stays_encoded():
    """org_key is a URL path segment (/org/{org_key}), so it must not contain
    a '/' -- that would split the route into two segments."""
    got = KospexUtils.parse_repo_id("gitlab.com~group~~subgroup~repo")
    assert got["org_key"] == "gitlab.com~group~~subgroup"
    assert "/" not in got["org_key"]


def test_parse_repo_id_deeply_nested_org():
    got = KospexUtils.parse_repo_id("gitlab.com~a~~b~~c~repo")
    assert got["org"] == "a/b/c"
    assert got["repo"] == "repo"


def test_parse_repo_id_round_trips_generate_repo_id():
    from kospex_git import KospexGit
    for server, org, repo in [
        ("github.com", "acme", "svc"),
        ("gitlab.com", "group/subgroup", "repo"),
        ("dev.azure.com", "myorg/MyProject", "MyRepo"),
    ]:
        rid = KospexGit.generate_repo_id(server, org, repo)
        got = KospexUtils.parse_repo_id(rid)
        assert (got["git_server"], got["org"], got["repo"]) == (server, org, repo)


def test_parse_repo_id_rejects_junk():
    for bad in [None, "", "no-tildes-here", "onlyone~two"]:
        assert KospexUtils.parse_repo_id(bad) is None


def test_parse_org_key_flat_and_nested():
    assert KospexUtils.parse_org_key("github.com~acme")["org"] == "acme"
    nested = KospexUtils.parse_org_key("gitlab.com~group~~subgroup")
    assert nested["git_server"] == "gitlab.com"
    assert nested["org"] == "group/subgroup"
    assert nested["org_key"] == "gitlab.com~group~~subgroup"


def test_parse_org_key_rejects_junk():
    for bad in [None, "", "no-tilde"]:
        assert KospexUtils.parse_org_key(bad) is None
