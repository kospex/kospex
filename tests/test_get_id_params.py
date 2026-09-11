"""get_id_params() must tell a repo_id from an org_key.

Every {id} page in kweb2 (/tenure/, /landscape/, /dependencies/, /osi/, ...)
scopes its query by the key this returns. It tries parse_org_key before
parse_repo_id, so if parse_org_key accepts a repo_id the page is scoped to an
org that does not exist: the owner binds as 'acme~svc', the query matches no
rows, and the page renders empty -- or, for /tenure/, raises on the empty
result and returns HTTP 500.
"""
import pytest

import kospex_web as KospexWeb


@pytest.mark.parametrize("request_id, expected", [
    ("github.com~acme~svc", {"repo_id": "github.com~acme~svc"}),
    ("gitlab.com~group~~subgroup~repo", {"repo_id": "gitlab.com~group~~subgroup~repo"}),
    ("github.com~acme", {"org_key": "github.com~acme"}),
    ("gitlab.com~group~~subgroup", {"org_key": "gitlab.com~group~~subgroup"}),
    ("github.com", {"server": "github.com"}),
])
def test_get_id_params_classifies_each_id_shape(request_id, expected):
    assert KospexWeb.get_id_params(request_id) == expected
