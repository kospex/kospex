"""Tests for scope resolution in KospexData.set_params_by_id().

Callers routinely pass their whole kwargs dict, which mixes scope keys
(repo_id / org_key / server) with unrelated ones. Two of those unrelated keys
used to break the query:

- `list-repos` defines `-repo_id` as an is_flag option meaning "add the Repo ID
  column to the output". Passing the flag put `repo_id=True` in the dict, which
  was read as a repo id and emitted `WHERE _repo_id = 1` - matching nothing, so
  `kospex list-repos -db -repo_id` returned zero rows.
- A display-only key such as `db=True` made the old `any(id_params.values())`
  all-scope test false, so an unscoped call fell through to an error branch that
  printed `ERROR: can't identify {...}` to stdout while still (correctly)
  applying no filter.

Only non-empty *string* scope keys count. Anything else means "all scope".
"""
import pytest
from sqlite_utils import Database

from kospex_query import KospexData


def new_kospex_data():
    """KospexData needs a DB handle; scope resolution never touches it."""
    return KospexData(kospex_db=Database(memory=True))


def where_clauses(**id_params):
    """Resolve id_params to the SQL where clauses it produces."""
    kd = new_kospex_data()
    kd.set_params_by_id(id_params or None)
    return kd.where_clause


def test_string_repo_id_scopes_the_query():
    clauses = where_clauses(repo_id="github.com~kospex~kospex")
    assert len(clauses) == 1
    assert "_repo_id" in clauses[0]


def test_org_key_and_server_still_scope():
    assert where_clauses(org_key="github.com~kospex")
    assert any("_git_server" in c for c in where_clauses(server="github.com"))


def test_boolean_repo_id_flag_is_not_treated_as_a_repo_id():
    """The regression: -repo_id is a display flag, not a scope value."""
    assert where_clauses(db=True, repo_id=True, server=None, email=None) == []


def test_false_repo_id_flag_is_not_a_scope():
    assert where_clauses(db=True, repo_id=False, server=None, email=None) == []


def test_display_only_keys_mean_all_scope_not_an_error(capsys):
    clauses = where_clauses(db=True, repo_id=False, server=None, email=None)
    assert clauses == []
    assert "can't identify" not in capsys.readouterr().out


def test_scope_still_applies_alongside_display_flags():
    clauses = where_clauses(db=True, repo_id=True, server="github.com", email=None)
    assert len(clauses) == 1
    assert "_git_server" in clauses[0]


def test_empty_string_scope_is_ignored():
    assert where_clauses(repo_id="", org_key="", server="") == []


def test_no_params_is_all_scope():
    assert where_clauses() == []


@pytest.mark.parametrize("unknown", [{"author_email": "dev@example.com"}, {"days": 90}])
def test_unrecognised_keys_are_all_scope_without_error(unknown, capsys):
    """get_id_params() can return author_email; it is not a repos-table scope."""
    kd = new_kospex_data()
    kd.set_params_by_id(unknown)
    assert kd.where_clause == []
    assert "can't identify" not in capsys.readouterr().out
