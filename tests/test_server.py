"""Unit tests for the client and tools against a mocked MiniBrew API.

Fixture shapes follow live responses (Oct 2026), with made-up ids and names.
"""

from __future__ import annotations

import copy
import json
import os

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from mcp_server_minibrew import client, server


def _ingredient(iid, kind, name, amount="10.00", **extra):
    return {
        "ingredient_id": iid,
        "ingredient_type": kind,
        "ingredient_name": name,
        "amount": amount,
        "amount_units": "GR",
        "duration": 0,
        "addition_step": "0-0",
        **extra,
    }


RECIPE = {
    "id": 2002,
    "beer_id": 300,
    "beer_name": "Test IPA",
    "shared": False,
    "public_note": "",
    "private_note": "",
    "times_brewed": 2,
    "serving_temperature": "5.00",
    "chilling_temperature": "18.00",
    "water_amount": "7.59",
    "kettle_water": "6.40",
    "mashing": [
        {
            "mash_in_water": "1.19",
            "sparge_in_water": None,
            "steps": [
                {"duration": 45, "temperature": "68.0", "end_temperature": "68.0", "order": 0}
            ],
            "ingredient_additions": [_ingredient(1, "FERM", "Pilsner Malt", "1900.00")],
            "order": 0,
            "name": "Mash stage: 0",
        }
    ],
    "boiling": [
        {
            "duration": 30,
            "hops": [_ingredient(130, "HOP", "MB - Citra", "5.00", duration=10)],
            "other_ingredients": [],
            "order": 0,
        }
    ],
    "fermenting": [
        {
            "fermentation_stage_type": "PRIM",
            "yeast": [_ingredient(1129, "YEAST", "Verdant IPA", "5.50")],
            "steps": [
                {"duration": 2.0, "temperature": "18.0", "end_temperature": "18.0", "order": 0}
            ],
            "order": 0,
            "name": "Ferm stage: 0",
            "pressure_relief": 1,
        }
    ],
    "while_fermenting": {
        "hops": [_ingredient(130, "HOP", "MB - Citra", duration=2.0, addition_step="1-0")],
        "other_ingredients": [],
    },
    "version_name": "14",
    "hop_filter": 0,
    "srm": "7.00",
    "abv": "5.79",
    "ibu": 0,
    "kcal": "174.00",
    "og": "1.056",
    "fg": "1.013",
    "modified_timestamp": 1790747877.5,
    "brewable": True,
    "carbonation": "2.40",
}


def _device(**extra):
    return {
        "uuid": "BASE-1",
        "serial_number": "BASE-1",
        "custom_name": "Home MiniBrew",
        "device_type": 0,
        "current_state": 2,
        "process_type": 0,
        "process_state": 31,
        "user_action": 0,
        "connection_status": 1,
        "last_time_online": "2026-10-01T08:00:00Z",
        **extra,
    }


def _session(sid, status=1, session_type=0, beer="Test IPA", **extra):
    return {
        "id": sid,
        "status": status,
        "session_type": session_type,
        "beer": {"id": 300, "name": beer, "style_name": "NEIPA"} if beer else None,
        "beer_recipe_id": 2002 if beer else None,
        "beer_recipe_version": "14" if beer else None,
        "device": _device(),
        "created": "2026-09-30T05:57:57Z",
        "modified": "2026-09-30T08:00:00Z",
        "original_gravity": None,
        "pending_command_error": 0,
        "milestones": [],
        "actual_step_timestamps": {},
        **extra,
    }


# --------------------------------------------------------------------------- #
# Client
# --------------------------------------------------------------------------- #
def test_client_sends_portal_header_and_bearer(fake_api):
    api = fake_api({("GET", "v1/devices/"): []})
    client.get_client().get("v1/devices/")
    req = api.requests[0]
    assert req.headers["client"] == "Breweryportal"
    assert req.headers["Authorization"] == "Bearer tok"


def test_client_explains_expired_token(fake_api):
    fake_api({("GET", "v1/recipes/2002/"): httpx.Response(401, json={"detail": "expired"})})
    with pytest.raises(ToolError, match="rejected the token.*MINIBREW_EMAIL"):
        server.get_recipe(2002)


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("PUT", "v1/sessions/1/"),
        ("DELETE", "v1/sessions/1/"),
        ("POST", "v1/sessions/"),
        ("POST", "v1/recipes/1/"),
        ("POST", "v1/beers/1/"),
        ("PATCH", "v1/beers/1/"),
        ("DELETE", "v1/beers/1/"),
        ("DELETE", "v1/recipes/1/"),
        ("PUT", "v1/recipes/1/steps/"),
        ("PUT", "v1/recipes/../sessions/1/"),
        ("POST", "v1/kegs/x/"),
        ("PUT", "v1/hops/1/"),
        ("DELETE", "v1/fermentables/1/"),
        ("POST", "v1/laboratories/"),
    ],
)
def test_client_refuses_writes_other_than_saving_a_recipe(fake_api, method, path):
    api = fake_api({})
    with pytest.raises(ToolError, match="read-only"):
        client.get_client()._request(method, path, json={})
    assert api.requests == []


def test_token_is_reloaded_when_env_file_changes(tmp_path):
    env = tmp_path / ".env"
    env.write_text("MINIBREW_TOKEN=Bearer first\n")
    tokens = client.TokenSource(str(env))
    assert tokens.get() == "first"
    env.write_text("# comment\nMINIBREW_TOKEN='second'\n")
    os.utime(env, (1, 1))  # force an mtime change even within the same tick
    assert tokens.get() == "second"


def test_missing_token_is_reported():
    with pytest.raises(ToolError, match="no MiniBrew token"):
        client.TokenSource().get()


class _LoginAPI:
    """v2/token/ hands out tok1, tok2, ...; v1/devices/ accepts only the newest token."""

    def __init__(self, login_status=200, exp=3600, revoke_first=False):
        self.login_status, self.exp, self.revoke_first = login_status, exp, revoke_first
        self.logins: list[httpx.Request] = []
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v2/token/":
            self.logins.append(request)
            if self.login_status != 200:
                return httpx.Response(self.login_status, json={"detail": "bad credentials"})
            return httpx.Response(200, json={"token": f"tok{len(self.logins)}", "exp": self.exp})
        self.requests.append(request)
        if self.revoke_first and request.headers["Authorization"] == "Bearer tok1":
            return httpx.Response(401, json={"detail": "revoked"})
        return httpx.Response(200, json=[])


def _login_client(api, **creds):
    tokens = client.TokenSource(**({"email": "me@x.dk", "password": "s3cret!"} | creds))
    return client.MiniBrewClient(tokens, transport=httpx.MockTransport(api))


def test_login_posts_credentials_with_app_key_and_reuses_the_token():
    api = _LoginAPI()
    mb = _login_client(api)
    mb.get("v1/devices/")
    mb.get("v1/devices/")
    [login] = api.logins
    assert login.method == "POST"
    assert login.headers["Authorization"] == f"TOKEN {client.APP_KEY}"
    assert login.headers["client"] == "Breweryportal"
    assert json.loads(login.content) == {"email": "me@x.dk", "password": "s3cret!"}
    assert [r.headers["Authorization"] for r in api.requests] == ["Bearer tok1"] * 2


def test_login_renews_before_expiry(monkeypatch):
    api = _LoginAPI(exp=3600)
    mb = _login_client(api)
    now = 1_000_000.0
    monkeypatch.setattr(client.time, "time", lambda: now)
    mb.get("v1/devices/")
    now += 3600 - client.RENEW_MARGIN + 1
    mb.get("v1/devices/")
    assert [r.headers["Authorization"] for r in api.requests] == ["Bearer tok1", "Bearer tok2"]


def test_login_retries_once_when_the_token_is_rejected():
    api = _LoginAPI(revoke_first=True)
    _login_client(api).get("v1/devices/")
    assert len(api.logins) == 2
    assert [r.headers["Authorization"] for r in api.requests] == ["Bearer tok1", "Bearer tok2"]


def test_failed_login_hides_the_password_and_is_not_retried():
    api = _LoginAPI(login_status=400)
    mb = _login_client(api)
    for _ in range(2):
        with pytest.raises(ToolError, match="login failed \\(HTTP 400\\)") as e:
            mb.get("v1/devices/")
        assert "s3cret" not in str(e.value)
    assert len(api.logins) == 1  # bad credentials aren't retried until they change
    assert api.requests == []


def test_failed_login_falls_back_to_the_pasted_token():
    api = _LoginAPI(login_status=401)
    _login_client(api, token="pasted").get("v1/devices/")
    assert api.requests[0].headers["Authorization"] == "Bearer pasted"


def test_credentials_are_read_from_the_env_file(tmp_path):
    env = tmp_path / ".env"
    env.write_text("MINIBREW_EMAIL=me@x.dk  # mine\nMINIBREW_PASSWORD='p=ss #word'   # quoted\n")
    assert client.TokenSource(str(env)).credentials() == ("me@x.dk", "p=ss #word")
    env.write_text("MINIBREW_EMAIL=me@x.dk\nMINIBREW_PASSWORD=p=ss#word\n")
    os.utime(env, (1, 1))
    assert client.TokenSource(str(env)).credentials() == ("me@x.dk", "p=ss#word")
    assert client.TokenSource(str(env), email="other@x.dk").credentials()[0] == "me@x.dk"
    assert client.TokenSource(email="me@x.dk").credentials() is None  # needs both


def test_get_all_follows_drf_pagination():
    next_url = "https://api.minibrew.io/v1/hops/?limit=1&offset=1"
    pages = iter(
        [
            {"next": next_url, "results": [{"id": 1}]},
            {"next": None, "results": [{"id": 2}]},
        ]
    )
    requests = []

    def route(request):
        requests.append(request)
        return httpx.Response(200, json=next(pages))

    mb = client.MiniBrewClient(client.TokenSource(token="t"), transport=httpx.MockTransport(route))
    assert mb.get_all("v1/hops/", {"limit": 1}) == [{"id": 1}, {"id": 2}]
    assert requests[1].url.params["offset"] == "1"
    assert requests[1].headers["client"] == "Breweryportal"


# --------------------------------------------------------------------------- #
# Devices and sessions
# --------------------------------------------------------------------------- #
def test_overview_labels_and_merges_live_state(fake_api):
    overview = {
        "brew_clean_idle": [],
        "fermenting": [
            {
                "uuid": "BASE-1",
                "title": "Home MiniBrew",
                "device_type": 0,
                "online": True,
                "stage": "Brewing",
                "session_id": 82486,
                "session_status": 1,
                "beer_name": "Test IPA",
                "current_temp": 67.5,
                "target_temp": 68.0,
                "gravity": "1.00",
                "user_action": 0,
            }
        ],
        "serving": [],
    }
    fake_api(
        {
            ("GET", "v1/breweryoverview/"): overview,
            ("GET", "v1/devices/"): [
                _device(
                    process_state=31,
                    process_type=1,
                    connection_status=2,
                    software_version="3.2.3, idf-v4.2-50-g11005797d",
                    last_process_state_change="2026-10-01T07:30:00Z",
                    updating=False,
                )
            ],
        }
    )
    [dev] = server.get_brewery_overview()
    assert dev["type"] == "brewer"
    assert dev["bucket"] == "fermenting"
    assert dev["process_state"] == "Mashing"
    assert dev["phase"] == "brewing"
    assert dev["session_status"] == "active"
    assert "gravity" not in dev  # 1.00 placeholder dropped
    assert "user_action" not in dev
    assert dev["process"] == "brewing"
    assert dev["connection"] == "not responding"
    assert dev["firmware"] == "3.2.3"
    assert dev["state_since"] == "2026-10-01T07:30:00Z"
    assert "updating" not in dev


def test_list_sessions_filters_sorts_and_limits(fake_api):
    sessions = [
        _session(10, status=2),
        _session(12, status=1),
        _session(11, status=2, session_type=2, beer=None),
        _session(13, status=2, beer="Stout"),
    ]
    fake_api({("GET", "v1/sessions/"): sessions})
    out = server.list_sessions(status="completed", limit=0)
    assert [s["id"] for s in out] == [13, 11, 10]
    assert server.list_sessions(type="clean")[0]["type"] == "clean"
    assert [s["id"] for s in server.list_sessions(beer="ipa", limit=1)] == [12]


def test_list_sessions_rejects_unknown_status(fake_api):
    api = fake_api({})
    with pytest.raises(ToolError, match="unknown status"):
        server.list_sessions(status="fermenting")
    assert api.requests == []


def test_get_session_adds_instructions_and_dedupes_milestones(fake_api):
    milestone = {"stage": "RINSE_START", "step_index": None, "created_at": "2026-09-30T05:57:57Z"}
    raw = _session(
        82486,
        device=_device(user_action=26, process_state=75),
        milestones=[
            milestone,
            dict(milestone, created_at="2026-09-30T05:58:00Z"),
            {"stage": "MASH_START", "created_at": "2026-09-30T07:13:50Z"},
        ],
        actual_step_timestamps={"1": 1790747877808},
        brew_timestamp=1790747877.0,
    )
    ua = {
        "user_action": 26,
        "title": "Prepare fermentation",
        "action_steps": [
            {"order": 2, "title": "Second", "description": "b"},
            {"order": 1, "title": "First", "description": "a", "image": "https://x/img.png"},
        ],
    }
    fake_api(
        {
            ("GET", "v1/sessions/82486/"): raw,
            ("GET", "v1/sessions/82486/user_actions/26/"): ua,
        }
    )
    out = server.get_session(82486)
    assert out["device_state"]["user_action"] == "Prepare fermentation"
    assert out["device_state"]["phase"] == "fermentation"
    assert [s["title"] for s in out["instructions"]["steps"]] == ["First", "Second"]
    assert [m["stage"] for m in out["milestones"]] == ["RINSE_START", "MASH_START"]
    assert out["step_timestamps"]["1"].endswith("Z")
    assert out["brew_started"].startswith("2026-")


def test_get_session_rejects_non_integer_id(fake_api):
    api = fake_api({})
    with pytest.raises(ToolError, match="session_id"):
        server.get_session("../kegs")  # type: ignore[arg-type]
    assert api.requests == []


# --------------------------------------------------------------------------- #
# Recipes
# --------------------------------------------------------------------------- #
def test_list_recipes_returns_latest_version_per_beer(fake_api):
    older = dict(RECIPE, id=2001, version_name="13", modified_timestamp=1700000000)
    other = dict(RECIPE, id=3001, beer_id=301, beer_name="Stout", modified_timestamp=1800000000)
    fake_api({("GET", "v1/recipes/"): [older, RECIPE, other]})
    out = server.list_recipes()
    assert [(r["id"], r["version"]) for r in out] == [(3001, "14"), (2002, "14")]
    assert out[1]["og"] == 1.056
    assert len(server.list_recipes(beer="ipa", all_versions=True)) == 2


UNBREWED = dict(RECIPE, times_brewed=0)


def test_update_recipe_puts_full_recipe_with_stored_identity(fake_api):
    api = fake_api(
        {
            ("GET", "v1/recipes/2002/"): UNBREWED,
            ("PUT", "v1/recipes/2002/"): UNBREWED,
        }
    )
    edited = copy.deepcopy(UNBREWED)
    edited["boiling"][0]["hops"][0]["amount"] = "8.00"
    # Identity fields from the caller are ignored in favour of the stored ones.
    edited.update(beer_id=999, version_name="99", times_brewed=0, beer_name="Other")
    out = server.update_recipe(2002, edited)
    assert out["id"] == 2002
    [body] = api.bodies("PUT")
    assert body["boiling"][0]["hops"][0]["amount"] == "8.00"
    assert (body["id"], body["beer_id"], body["version_name"], body["beer_name"]) == (
        2002,
        300,
        "14",
        "Test IPA",
    )
    assert api.requests[-1].method == "GET"  # result is re-read after saving


def test_update_recipe_refuses_brewed_versions(fake_api):
    api = fake_api({("GET", "v1/recipes/2002/"): RECIPE})  # times_brewed=2
    with pytest.raises(ToolError, match="brewed 2 time"):
        server.update_recipe(2002, copy.deepcopy(RECIPE))
    assert [r.method for r in api.requests] == ["GET"]


def test_update_recipe_rejects_mismatched_id(fake_api):
    api = fake_api({})
    with pytest.raises(ToolError, match="doesn't match"):
        server.update_recipe(2003, copy.deepcopy(UNBREWED))
    assert api.requests == []


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda r: r.update(bogus=1), "unknown recipe keys"),
        (lambda r: r.update(mashing=[]), "mashing"),
        (
            lambda r: r["mashing"][0]["ingredient_additions"][0].pop("ingredient_id"),
            "ingredient_id",
        ),
        (lambda r: r["boiling"][0]["hops"][0].update(ingredient_type="HOPS"), "ingredient_type"),
        (lambda r: r["fermenting"][0]["yeast"][0].update(amount="0"), "amount"),
        (lambda r: r["fermenting"][0].update(steps=[{"temperature": "18"}]), "duration"),
        (lambda r: r["while_fermenting"]["hops"][0].update(amount="lots"), "amount"),
    ],
)
def test_update_recipe_validates_before_sending(fake_api, mutate, message):
    api = fake_api({})
    recipe = copy.deepcopy(UNBREWED)
    mutate(recipe)
    with pytest.raises(ToolError, match=message):
        server.update_recipe(2002, recipe)
    assert api.requests == []


def test_search_ingredients_filters_and_drops_nested_fields(fake_api):
    hops = {
        "count": 2,
        "next": None,
        "results": [
            {"id": 130, "name": "MB - Citra", "alpha_acid": "12.5", "image": "x", "origin": {}},
            {"id": 131, "name": "Mosaic", "alpha_acid": "11.0"},
        ],
    }
    fake_api({("GET", "v1/hops/"): hops})
    assert server.search_ingredients("hops", "citra") == [
        {"id": 130, "name": "MB - Citra", "alpha_acid": "12.5"}
    ]
    with pytest.raises(ToolError, match="unknown kind"):
        server.search_ingredients("malts")


# --------------------------------------------------------------------------- #
# Creating beers and recipes
# --------------------------------------------------------------------------- #
ALTBIER = {
    "id": 19,
    "name": "Altbier",
    "category": {"id": 1, "name": "Ale"},
    "og_min": "1.044",
    "og_max": "1.052",
    "carbonation_min": "4.20",
    "carbonation_max": "5.00",
}
STYLES = {"count": 1, "next": None, "results": [ALTBIER]}


def test_list_beer_styles_shows_ranges(fake_api):
    fake_api({("GET", "v1/styles/"): STYLES})
    [style] = server.list_beer_styles("alt")
    assert style == {
        "id": 19,
        "name": "Altbier",
        "category": "Ale",
        "og": "1.044–1.052",
        "carbonation": "4.20–5.00",
    }


def test_create_beer_sends_portal_body_with_style_default_carbonation(fake_api):
    api = fake_api(
        {
            ("GET", "v1/styles/"): STYLES,
            ("POST", "v1/beers/"): {"id": 18894, "name": "Test Beer", "style": 19},
        }
    )
    out = server.create_beer("Test Beer", 19)
    assert out["id"] == 18894 and out["style"] == "Altbier"
    assert api.bodies("POST") == [
        {"name": "Test Beer", "styleName": "Altbier", "style": 19, "carbonation": "5.00"}
    ]


def test_create_beer_rejects_unknown_style(fake_api):
    api = fake_api({("GET", "v1/styles/"): STYLES})
    with pytest.raises(ToolError, match="unknown style_id"):
        server.create_beer("X", 20)
    assert [r.method for r in api.requests] == ["GET"]


def test_create_recipe_posts_without_server_fields_and_string_beer_id(fake_api):
    api = fake_api(
        {
            ("GET", "v1/recipes/"): [],
            ("POST", "v1/recipes/"): dict(UNBREWED, id=3000, beer_id=18894, version_name="1"),
        }
    )
    out = server.create_recipe(18894, copy.deepcopy(UNBREWED))
    assert out["id"] == 3000 and out["version"] == "1"
    [body] = api.bodies("POST")
    assert body["beer_id"] == "18894"
    for key in ("id", "beer_name", "version_name", "times_brewed", "modified_timestamp"):
        assert key not in body
    assert body["mashing"] == UNBREWED["mashing"]
    assert api.requests[0].url.params["beer"] == "18894"


def test_create_recipe_refuses_beer_that_has_a_recipe(fake_api):
    api = fake_api({("GET", "v1/recipes/"): [dict(UNBREWED, beer_id=18894)]})
    with pytest.raises(ToolError, match="already has a recipe"):
        server.create_recipe(18894, copy.deepcopy(UNBREWED))
    assert [r.method for r in api.requests] == ["GET"]


def test_check_recipe_posts_to_v2_and_returns_answer(fake_api):
    api = fake_api({("POST", "v2/check_recipes/"): {"brewable": False, "errors": ["mash"]}})
    out = server.check_recipe(18894, copy.deepcopy(UNBREWED))
    assert out == {"valid": True, "result": {"brewable": False, "errors": ["mash"]}}
    assert api.bodies("POST")[0]["beer_id"] == "18894"


def test_check_recipe_returns_validation_errors_instead_of_failing(fake_api):
    errors = {"mashing": [{"steps": [{"temperature": [{"code": "1008", "message": "Temp."}]}]}]}
    fake_api({("POST", "v2/check_recipes/"): httpx.Response(400, json={"errors": errors})})
    assert server.check_recipe(18894, copy.deepcopy(UNBREWED)) == {
        "valid": False,
        "errors": errors,
    }


def test_client_sends_portal_version_header(fake_api):
    api = fake_api({("GET", "v1/devices/"): []})
    client.get_client().get("v1/devices/")
    assert api.requests[0].headers["version"] == client.PORTAL_VERSION


FERMENTABLES = [{"id": 112, "name": "Maris Otter Pale Ale malt", "fermentable_type": "GRA"}]


def test_create_fermentable_sends_portal_body(fake_api):
    api = fake_api(
        {
            ("GET", "v1/fermentables/"): FERMENTABLES,
            ("POST", "v1/fermentables/"): {
                "id": 4405,
                "name": "Naked Barley",
                "fermentable_type": "GRA",
                "dry_yield": "75.00",
                "moisture": "5.00",
                "srm_color_min": None,
            },
        }
    )
    out = server.create_fermentable(" Naked Barley ", "GRA", 75, 5)
    assert out == {
        "id": 4405,
        "name": "Naked Barley",
        "fermentable_type": "GRA",
        "dry_yield": "75.00",
        "moisture": "5.00",
    }
    assert api.bodies("POST") == [
        {"name": "Naked Barley", "fermentable_type": "GRA", "dry_yield": 75, "moisture": 5}
    ]


@pytest.mark.parametrize(
    ("args", "match"),
    [
        (("", "GRA", 75, 5), "needs a name"),
        (("X", "MALT", 75, 5), "fermentable_type"),
        (("X", "GRA", 120, 5), "dry_yield"),
        (("X", "GRA", 75, 69), "moisture"),
        (("maris otter pale ale MALT", "GRA", 80, 5), "already exists .id 112"),
    ],
)
def test_create_fermentable_validates_before_posting(fake_api, args, match):
    api = fake_api({("GET", "v1/fermentables/"): FERMENTABLES})
    with pytest.raises(ToolError, match=match):
        server.create_fermentable(*args)
    assert api.bodies("POST") == []


def test_create_hop_sends_portal_body(fake_api):
    api = fake_api(
        {
            ("GET", "v1/hops/"): [{"id": 1, "name": "Citra", "usage": "ALL"}],
            ("POST", "v1/hops/"): {"id": 3517, "name": "Nectaron", "usage": "ARFLA"},
        }
    )
    out = server.create_hop("Nectaron", "ARFLA")
    assert out == {"id": 3517, "name": "Nectaron", "usage": "ARFLA"}
    assert api.bodies("POST") == [{"name": "Nectaron", "usage": "ARFLA"}]


@pytest.mark.parametrize(
    ("name", "usage", "match"), [("Citra", "ALL", "already exists"), ("X", "DRY", "usage")]
)
def test_create_hop_validates_before_posting(fake_api, name, usage, match):
    api = fake_api({("GET", "v1/hops/"): [{"id": 1, "name": "Citra", "usage": "ALL"}]})
    with pytest.raises(ToolError, match=match):
        server.create_hop(name, usage)
    assert api.bodies("POST") == []


LABS = [{"id": 1, "name": "Fermentis"}, {"id": 10, "name": "White Labs"}]


def test_create_yeast_sends_portal_body_with_lab_id_as_string(fake_api):
    api = fake_api(
        {
            ("GET", "v1/yeasts/"): [{"id": 1, "name": "Nottingham"}],
            ("GET", "v1/laboratories/"): LABS,
            ("POST", "v1/yeasts/"): {"id": 3289, "name": "US-05", "laboratory": 1},
        }
    )
    out = server.create_yeast("US-05", "ALE", 81, "fermentis", "US-05")
    assert out == {"id": 3289, "name": "US-05", "laboratory": 1}
    assert api.bodies("POST") == [
        {
            "name": "US-05",
            "yeast_form": "ALE",
            "attenuation": 81,
            "laboratory": "1",
            "product_id": "US-05",
        }
    ]


@pytest.mark.parametrize(
    ("args", "match"),
    [
        (("Nottingham", "ALE", 77, "Fermentis", "x"), "already exists"),
        (("X", "WHEAT", 77, "Fermentis", "x"), "yeast_form"),
        (("X", "ALE", 0, "Fermentis", "x"), "attenuation"),
        (("X", "ALE", 77, "Fermentis", " "), "product_id"),
        (("X", "ALE", 77, "Lallemand", "x"), "unknown laboratory.*White Labs"),
    ],
)
def test_create_yeast_validates_before_posting(fake_api, args, match):
    api = fake_api(
        {
            ("GET", "v1/yeasts/"): [{"id": 1, "name": "Nottingham"}],
            ("GET", "v1/laboratories/"): LABS,
        }
    )
    with pytest.raises(ToolError, match=match):
        server.create_yeast(*args)
    assert api.bodies("POST") == []


def test_create_adjunct_sends_portal_body(fake_api):
    api = fake_api(
        {
            ("GET", "v1/adjuncts/"): [{"id": 1, "name": "Irish Moss", "adjunct_type": "FINI"}],
            ("POST", "v1/adjuncts/"): {"id": 1965, "name": "Whirlfloc", "adjunct_type": "FINI"},
        }
    )
    assert server.create_adjunct("Whirlfloc", "FINI")["id"] == 1965
    assert api.bodies("POST") == [{"name": "Whirlfloc", "adjunct_type": "FINI"}]


@pytest.mark.parametrize(
    ("name", "kind", "match"), [("irish moss", "FINI", "already exists"), ("X", "SALT", "type")]
)
def test_create_adjunct_validates_before_posting(fake_api, name, kind, match):
    api = fake_api({("GET", "v1/adjuncts/"): [{"id": 1, "name": "Irish Moss"}]})
    with pytest.raises(ToolError, match=match):
        server.create_adjunct(name, kind)
    assert api.bodies("POST") == []
