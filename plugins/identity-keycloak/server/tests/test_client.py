# SPDX-License-Identifier: Apache-2.0
"""The Admin REST client against mocked responses: token handling, error mapping, no body in a message."""

from __future__ import annotations

import httpx
import pytest
import respx

from tranibor_identity_keycloak.client import KeycloakClient
from tranibor_identity_keycloak.config import Settings
from tranibor_identity_keycloak.errors import Failure

BASE = "https://sso.example.com"
TOKEN_URL = f"{BASE}/realms/acme/protocol/openid-connect/token"
USERS = f"{BASE}/admin/realms/acme/users"


def settings(**extra: str) -> Settings:
    env = {
        "KEYCLOAK_BASE_URL": BASE,
        "KEYCLOAK_REALMS": "acme,other",
        "KEYCLOAK_CLIENT_ID": "tranibor-iam",
        "KEYCLOAK_CLIENT_SECRET": "shh-not-a-real-secret",
        "TRANIBOR_SERVER_TOKEN": "t",
        **extra,
    }
    return Settings.from_env(env)


def token(value: str = "tok-1", expires_in: int = 300) -> httpx.Response:
    return httpx.Response(200, json={"access_token": value, "expires_in": expires_in})


@respx.mock
async def test_token_is_fetched_once_cached_and_sent_as_a_bearer() -> None:
    issued = respx.post(TOKEN_URL).mock(return_value=token())
    users = respx.get(USERS).mock(return_value=httpx.Response(200, json=[]))
    client = KeycloakClient(settings())
    await client.get("acme", "/users")
    await client.get("acme", "/users")
    assert issued.call_count == 1
    assert users.calls.last.request.headers["Authorization"] == "Bearer tok-1"
    form = issued.calls.last.request.content.decode()
    assert "grant_type=client_credentials" in form
    assert "client_id=tranibor-iam" in form


@respx.mock
async def test_an_expiring_token_is_renewed() -> None:
    issued = respx.post(TOKEN_URL).mock(side_effect=[token("a", 10), token("b", 300)])
    respx.get(USERS).mock(return_value=httpx.Response(200, json=[]))
    client = KeycloakClient(settings())
    await client.get("acme", "/users")
    await client.get("acme", "/users")  # 10 s lifetime is inside the 20 s margin
    assert issued.call_count == 2


@respx.mock
async def test_a_401_renews_the_token_once_then_retries() -> None:
    issued = respx.post(TOKEN_URL).mock(side_effect=[token("old"), token("new")])
    respx.get(USERS).mock(side_effect=[httpx.Response(401), httpx.Response(200, json=[{"id": "x"}])])
    assert await KeycloakClient(settings()).get("acme", "/users") == [{"id": "x"}]
    assert issued.call_count == 2


@respx.mock
async def test_a_second_401_is_auth_failed() -> None:
    respx.post(TOKEN_URL).mock(side_effect=[token("a"), token("b")])
    respx.get(USERS).mock(return_value=httpx.Response(401))
    with pytest.raises(Failure) as error:
        await KeycloakClient(settings()).get("acme", "/users")
    assert error.value.code == "auth_failed"


@respx.mock
async def test_token_realm_names_the_realm_that_issues_the_token() -> None:
    url = f"{BASE}/realms/master/protocol/openid-connect/token"
    issued = respx.post(url).mock(return_value=token())
    respx.get(USERS).mock(return_value=httpx.Response(200, json=[]))
    await KeycloakClient(settings(KEYCLOAK_TOKEN_REALM="master")).get("acme", "/users")
    assert issued.called


@respx.mock
@pytest.mark.parametrize("status", [400, 401, 403])
async def test_refused_credentials(status: int) -> None:
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(status, json={"error": "unauthorized_client"}))
    with pytest.raises(Failure) as error:
        await KeycloakClient(settings()).get("acme", "/users")
    assert error.value.code == "auth_failed"
    assert "shh-not-a-real-secret" not in str(error.value)


@respx.mock
@pytest.mark.parametrize(
    ("status", "code"),
    [(403, "forbidden"), (404, "not_found"), (409, "conflict"), (429, "rate_limited"), (500, "upstream_error")],
)
async def test_error_mapping_never_echoes_a_response_body(status: int, code: str) -> None:
    respx.post(TOKEN_URL).mock(return_value=token())
    respx.get(USERS).mock(return_value=httpx.Response(status, json={"errorMessage": "SECRET-IN-BODY"}))
    with pytest.raises(Failure) as error:
        await KeycloakClient(settings()).get("acme", "/users")
    assert error.value.code == code
    assert "SECRET-IN-BODY" not in str(error.value)


@respx.mock
async def test_ids_are_left_out_of_messages() -> None:
    respx.post(TOKEN_URL).mock(return_value=token())
    uid = "0b1c2d3e-4f50-4617-8293-a4b5c6d7e8f9"
    respx.get(f"{USERS}/{uid}").mock(return_value=httpx.Response(404))
    with pytest.raises(Failure) as error:
        await KeycloakClient(settings()).get("acme", f"/users/{uid}")
    assert uid not in str(error.value)
    assert "/users/{id}" in str(error.value)


@respx.mock
async def test_unreachable_server_is_an_upstream_error() -> None:
    respx.post(TOKEN_URL).mock(side_effect=httpx.ConnectError("boom with the-host"))
    with pytest.raises(Failure) as error:
        await KeycloakClient(settings()).get("acme", "/users")
    assert error.value.code == "upstream_error"
    assert "the-host" not in str(error.value)


@respx.mock
async def test_redirects_are_not_followed() -> None:
    respx.post(TOKEN_URL).mock(return_value=token())
    respx.get(USERS).mock(return_value=httpx.Response(302, headers={"Location": "https://evil.example.com/"}))
    with pytest.raises(Failure) as error:
        await KeycloakClient(settings()).get("acme", "/users")
    assert error.value.code == "upstream_error"


@respx.mock
@pytest.mark.parametrize(("total", "limit", "more"), [(5, 5, False), (6, 5, True), (3, 5, False), (250, 100, True)])
async def test_get_page_says_whether_the_list_went_on(total: int, limit: int, more: bool) -> None:
    respx.post(TOKEN_URL).mock(return_value=token())
    rows = [{"id": str(n)} for n in range(total)]

    def page(request: httpx.Request) -> httpx.Response:
        first, size = int(request.url.params["first"]), int(request.url.params["max"])
        return httpx.Response(200, json=rows[first : first + size])

    respx.get(USERS).mock(side_effect=page)
    found, truncated = await KeycloakClient(settings()).get_page("acme", "/users", limit=limit)
    assert len(found) == min(total, limit)
    assert truncated is more


def test_settings_refuse_http_and_odd_realms_outside_the_fake() -> None:
    with pytest.raises(ValueError, match="https"):
        settings(KEYCLOAK_BASE_URL="http://sso.example.com")
    with pytest.raises(ValueError, match="realm name"):
        settings(KEYCLOAK_REALMS="staff,../master")
    with pytest.raises(ValueError, match="KEYCLOAK_CLIENT_SECRET"):
        settings(KEYCLOAK_CLIENT_SECRET="")


@respx.mock
async def test_the_client_refuses_a_realm_outside_its_allow_list_before_any_request() -> None:
    issued = respx.post(TOKEN_URL).mock(return_value=token())
    respx.get(f"{BASE}/admin/realms/private/users").mock(return_value=httpx.Response(200, json=[]))
    with pytest.raises(Failure) as error:
        await KeycloakClient(settings()).get("private", "/users")
    assert error.value.code == "invalid_arguments"
    assert not issued.called
    assert not respx.calls


@respx.mock
async def test_the_token_comes_from_the_first_realm_and_serves_every_listed_realm() -> None:
    issued = respx.post(TOKEN_URL).mock(return_value=token())
    respx.get(f"{BASE}/admin/realms/other/users").mock(return_value=httpx.Response(200, json=[]))
    client = KeycloakClient(settings())
    await client.get("other", "/users")
    assert issued.call_count == 1


def test_realms_are_a_comma_joined_list_without_blanks_or_repeats() -> None:
    assert settings(KEYCLOAK_REALMS=" acme , other ,").realms == ("acme", "other")
    with pytest.raises(ValueError, match="twice"):
        settings(KEYCLOAK_REALMS="acme,acme")
    with pytest.raises(ValueError, match="KEYCLOAK_REALMS"):
        settings(KEYCLOAK_REALMS="")
