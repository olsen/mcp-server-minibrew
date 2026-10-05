"""Shared fixtures: a MiniBrew API mocked with httpx.MockTransport.

Tests never touch the network. ``fake_api`` patches ``client.get_client`` with a
real MiniBrewClient wired to a FakeAPI, so headers, auth errors and the write
allowlist are exercised too.
"""

from __future__ import annotations

import json

import httpx
import pytest


class FakeAPI:
    """Routes ``(method, path)`` to canned bodies and records every request.

    Paths are as the server sends them, without the leading slash or query string
    (e.g. ``v1/sessions/``). A route value may be an ``httpx.Response``.
    """

    def __init__(self, routes: dict):
        self.routes = routes
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        route = self.routes.get((request.method, request.url.path.lstrip("/")))
        if route is None:
            return httpx.Response(404, json={"detail": "Not found."})
        if isinstance(route, httpx.Response):
            return route
        return httpx.Response(200, json=route)

    def bodies(self, method: str = "PUT") -> list[dict]:
        return [json.loads(r.content) for r in self.requests if r.method == method]


@pytest.fixture
def fake_api(monkeypatch):
    from mcp_server_minibrew import client

    def _install(routes: dict) -> FakeAPI:
        api = FakeAPI(routes)
        mb = client.MiniBrewClient(
            client.TokenSource(token="tok"), transport=httpx.MockTransport(api)
        )
        monkeypatch.setattr(client, "get_client", lambda: mb)
        return api

    return _install
