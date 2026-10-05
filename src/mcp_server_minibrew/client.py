"""MiniBrew REST API client.

A thin wrapper over httpx for the unofficial API the MiniBrew web portal uses.
Every request carries ``client: Breweryportal`` (without it the API answers 426)
and a bearer token.

With ``MINIBREW_EMAIL`` and ``MINIBREW_PASSWORD`` set, the client logs in itself
(POST v2/token/, as MiniBrew's apps do) and renews the token before it expires.
Otherwise, or if that login fails, it uses ``MINIBREW_TOKEN``, copied from a
logged-in portal session. Each setting is read from the environment or from the
file named by ``MINIBREW_ENV_FILE``; the file wins and is re-read whenever it
changes, so settings can be fixed without restarting the server. The password
and tokens never appear in error messages.

Writes are allowlisted here, below the tools, and mirror requests captured from
the portal: POST v1/beers/ (create a beer), POST v2/check_recipes/ (validate a
recipe), POST v1/recipes/ (create a beer's first recipe) and PUT v1/recipes/<id>/
(save a recipe), plus the login POST v2/token/. Nothing can start, stop or command a
device or session, and nothing is ever deleted.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any

import httpx
from mcp.server.mcpserver.exceptions import ToolError

BASE_URL = "https://api.minibrew.io/"
# The portal sends its build number as a `version` header (seen in Oct 2026 captures).
PORTAL_VERSION = "1.73.51"
# MiniBrew's apps authenticate the login request with this fixed client key (the same
# for every user; taken from stuartp44/pymbrewclient). If logins start failing with
# 401, it has probably changed; MINIBREW_TOKEN still works.
APP_KEY = "4c433da015985d17669c604a5a4e2c906083e815"
LOGIN_PATH = "v2/token/"
# Renew a login token this many seconds before it expires.
RENEW_MARGIN = 60
ALLOWED_WRITES = {
    "POST": re.compile(
        r"v1/(beers|recipes|fermentables|hops|yeasts|adjuncts)/|v2/(check_recipes|token)/"
    ),
    "PUT": re.compile(r"v1/recipes/\d+/"),
}

TOKEN_HELP = (
    "Set MINIBREW_EMAIL and MINIBREW_PASSWORD in the server's .env to log in automatically. "
    "Or get a token: log in at https://pro.minibrew.io, open DevTools → Network, pick a "
    "request to api.minibrew.io and copy its Authorization header without 'Bearer ' into "
    "MINIBREW_TOKEN. No restart needed either way."
)

_client: MiniBrewClient | None = None


class MiniBrewError(ToolError):
    """An API call failed; the message is meant to be read by the model.

    A ToolError so mcp passes the message through instead of a generic error.
    ``status`` is the HTTP status code when the API answered with an error.
    """

    def __init__(self, message: str, status: int | None = None, body: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.body = body  # the parsed JSON error body, when there was one


class TokenSource:
    """Credentials and/or a pasted token, from the environment or a reloadable .env file."""

    KEYS = ("MINIBREW_TOKEN", "MINIBREW_EMAIL", "MINIBREW_PASSWORD")

    def __init__(
        self,
        env_file: str | None = None,
        token: str | None = None,
        email: str | None = None,
        password: str | None = None,
    ) -> None:
        self._file = Path(env_file) if env_file else None
        values = (_clean(token), _unquote(email), _unquote(password))
        self._env = dict(zip(self.KEYS, values, strict=True))
        self._values = dict(self._env)
        self._mtime: float | None = None

    def _reload(self) -> None:
        if self._file and self._file.exists():
            mtime = self._file.stat().st_mtime
            if mtime != self._mtime:
                self._mtime = mtime
                found = _read_env(self._file)
                self._values = {k: found.get(k) or self._env[k] for k in self.KEYS}

    def get(self) -> str:
        """The pasted token (MINIBREW_TOKEN)."""
        self._reload()
        if not self._values["MINIBREW_TOKEN"]:
            raise MiniBrewError(f"no MiniBrew token set. {TOKEN_HELP}")
        return self._values["MINIBREW_TOKEN"]

    def credentials(self) -> tuple[str, str] | None:
        """(email, password) when both are set."""
        self._reload()
        email, password = self._values["MINIBREW_EMAIL"], self._values["MINIBREW_PASSWORD"]
        return (email, password) if email and password else None


def _unquote(value: str | None) -> str | None:
    value = (value or "").strip()
    if len(value) > 1 and value[0] == value[-1] and value[0] in "'\"":
        value = value[1:-1]
    return value or None


def _clean(token: str | None) -> str | None:
    token = _unquote(token) or ""
    return token.removeprefix("Bearer ").strip() or None


def _env_value(raw: str) -> str:
    """A .env value: up to the closing quote if quoted, else without a ' #' comment."""
    raw = raw.strip()
    if raw[:1] in ("'", '"'):
        end = raw.find(raw[0], 1)
        if end > 0:
            return raw[: end + 1]
    return re.split(r"\s#", raw, maxsplit=1)[0].strip()


def _read_env(path: Path) -> dict[str, str | None]:
    found = {}
    for line in path.read_text().splitlines():
        key, _, value = line.removeprefix("export ").partition("=")
        key, value = key.strip(), _env_value(value)
        if key == "MINIBREW_TOKEN":
            found[key] = _clean(value)
        elif key in TokenSource.KEYS:
            found[key] = _unquote(value)
    return found


class MiniBrewClient:
    def __init__(self, tokens: TokenSource, transport: httpx.BaseTransport | None = None) -> None:
        self._tokens = tokens
        self._login_token: str | None = None
        self._login_expiry = 0.0
        self._login_for: tuple[str, str] | None = None  # credentials the token belongs to
        self._login_error: tuple[tuple[str, str], str] | None = None  # don't retry bad ones
        self._http = httpx.Client(
            base_url=BASE_URL,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "client": "Breweryportal",
                "version": PORTAL_VERSION,
            },
            timeout=30.0,
            transport=transport,
        )

    def token(self) -> str:
        """The current bearer token (the MQTT broker takes it as password).

        Logs in with MINIBREW_EMAIL / MINIBREW_PASSWORD when they are set, reusing the
        token until shortly before it expires; falls back to MINIBREW_TOKEN.
        """
        creds = self._tokens.credentials()
        if not creds:
            return self._tokens.get()
        if (
            self._login_token
            and self._login_for == creds
            and time.time() < self._login_expiry - RENEW_MARGIN
        ):
            return self._login_token
        if self._login_error and self._login_error[0] == creds:
            failure = self._login_error[1]
        else:
            try:
                return self._login(creds)
            except MiniBrewError as e:
                failure = str(e)
                if e.status is not None and 400 <= e.status < 500:
                    self._login_error = (creds, failure)  # until the credentials change
        try:
            return self._tokens.get()
        except MiniBrewError:
            raise MiniBrewError(failure) from None

    def _login(self, creds: tuple[str, str]) -> str:
        self._check_allowed("POST", LOGIN_PATH)
        email, password = creds
        try:
            resp = self._http.post(
                LOGIN_PATH,
                json={"email": email, "password": password},
                headers={"Authorization": f"TOKEN {APP_KEY}"},
            )
        except httpx.HTTPError as e:
            raise MiniBrewError(f"MiniBrew login failed: {type(e).__name__}") from None
        if resp.is_error:
            raise MiniBrewError(
                f"MiniBrew login failed (HTTP {resp.status_code}): check MINIBREW_EMAIL and "
                "MINIBREW_PASSWORD in the server's .env",
                resp.status_code,
            )
        try:
            data = resp.json()
            token, exp = data["token"], float(data.get("exp") or 3600)
        except (ValueError, KeyError, TypeError):
            raise MiniBrewError("MiniBrew login answered without a token") from None
        # exp is seconds of validity; accept an absolute epoch too, in case it is one.
        self._login_expiry = exp if exp > 1e9 else time.time() + exp
        self._login_token, self._login_for, self._login_error = token, creds, None
        return token

    def get(self, path: str, params: dict | None = None) -> Any:
        return self._request("GET", path, params=params)

    def post(self, path: str, body: dict) -> Any:
        return self._request("POST", path, json=body)

    def put(self, path: str, body: dict) -> Any:
        return self._request("PUT", path, json=body)

    @staticmethod
    def _check_allowed(method: str, path: str) -> None:
        allowed = ALLOWED_WRITES.get(method)
        if method != "GET" and not (allowed and allowed.fullmatch(path)):
            raise MiniBrewError(f"{method} {path} is not allowed: this server is read-only")

    def _request(self, method: str, path: str, *, retry: bool = True, **kwargs) -> Any:
        self._check_allowed(method, path)
        token = self.token()
        try:
            resp = self._http.request(
                method, path, headers={"Authorization": f"Bearer {token}"}, **kwargs
            )
        except httpx.HTTPError as e:
            raise MiniBrewError(f"{method} {path} failed: {type(e).__name__}: {e}") from e
        if resp.status_code == 401:
            if retry and token == self._login_token:
                self._login_token = None  # revoked early: log in again, once
                return self._request(method, path, retry=False, **kwargs)
            raise MiniBrewError(f"MiniBrew rejected the token (expired?). {TOKEN_HELP}", 401)
        if resp.is_error:
            try:
                body = resp.json()
            except ValueError:
                body = None
            raise MiniBrewError(
                f"{method} {path} failed: HTTP {resp.status_code} {resp.text[:300]}",
                resp.status_code,
                body,
            )
        if not resp.content:
            return None
        try:
            return resp.json()
        except ValueError:
            return resp.text

    def get_all(self, path: str, params: dict | None = None, max_pages: int = 20) -> list:
        """GET a list endpoint, following DRF ``next`` links if it is paginated."""
        data = self.get(path, params)
        if isinstance(data, list):
            return data
        if not isinstance(data, dict) or "results" not in data:
            raise MiniBrewError(f"GET {path}: unexpected response shape")
        out = list(data["results"])
        for _ in range(max_pages - 1):
            nxt = data.get("next")
            if not nxt:
                break
            data = self._request("GET", httpx.URL(nxt).raw_path.decode().lstrip("/"))
            out.extend(data.get("results", []))
        return out


def get_client() -> MiniBrewClient:
    """Return a cached client built from the environment (creates it on first call)."""
    global _client
    if _client is None:
        _client = MiniBrewClient(
            TokenSource(
                os.environ.get("MINIBREW_ENV_FILE"),
                os.environ.get("MINIBREW_TOKEN"),
                os.environ.get("MINIBREW_EMAIL"),
                os.environ.get("MINIBREW_PASSWORD"),
            )
        )
    return _client
