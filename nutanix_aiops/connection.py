"""Connection management for the Nutanix Prism Central v4 REST API.

Thin httpx wrapper with per-target session reuse and HTTP Basic auth, plus the
two things every hand-rolled Prism v4 client gets wrong:

  * **ETag / If-Match** — v4 requires an ``If-Match`` header carrying the entity's
    current ETag on *every* mutation (update / action / delete) or the call fails
    with a mid-air-collision error. ``get_with_etag`` fetches an entity and
    returns ``(body, etag)``; ``mutate`` sends the captured ETag back
    automatically. Callers never have to hand-manage ETags.
  * **Pagination** — v4 list endpoints default to 50 rows and page with
    ``$page`` / ``$limit``. ``list_all`` walks every page and returns the full
    ``data`` array (bounded by ``max_items`` so a runaway inventory can't OOM).

``base_url`` is just the Prism Central origin (``https://host:9440``); callers
pass the full versioned path, e.g. ``/api/vmm/v4.0/ahv/config/vms``.

All non-2xx responses are translated centrally into ``NutanixApiError`` with a
teaching message rather than leaking raw tracebacks.

The httpx client is injectable for tests: pass ``client=`` to
``NutanixConnection`` to substitute a mock implementing ``request`` / ``close``.
Mock responses must expose ``status_code``, ``headers``, ``content``, ``text``,
and ``json()``.
"""

from __future__ import annotations

from typing import Any

import httpx

from nutanix_aiops.config import AppConfig, TargetConfig, load_config

_TIMEOUT = 60.0

# v4 list pagination defaults / safety cap.
_PAGE_LIMIT = 50
_MAX_ITEMS = 5000


class NutanixApiError(Exception):
    """A Prism Central REST call failed; carries a teaching message + status code."""

    def __init__(self, message: str, *, status_code: int | None = None, path: str = "") -> None:
        self.status_code = status_code
        self.path = path
        super().__init__(message)


def _teaching_message(status: int, path: str, body: str) -> str:
    """Map a non-2xx status to an actionable, teaching error message."""
    snippet = body[:200].strip()
    if status in (401, 403):
        return (
            f"Authentication/authorization failed ({status}) on {path}. Check the "
            f"Prism Central username/password and that the account has REST API "
            f"rights (WebUI access does NOT imply REST access). {snippet}"
        )
    if status == 404:
        return (
            f"Resource not found (404) on {path}. The extId may be stale — list the "
            f"parent collection first to get a current extId. {snippet}"
        )
    if status == 409:
        return (
            f"Conflict (409) on {path}. The ETag is stale (someone else changed the "
            f"entity) — re-fetch it and retry the mutation with the fresh ETag. {snippet}"
        )
    if status == 412:
        return (
            f"Precondition failed (412) on {path}. The If-Match ETag did not match — "
            f"re-fetch the entity to get its current ETag before mutating. {snippet}"
        )
    if status == 422:
        return (
            f"Validation error (422) on {path}. Prism rejected the request body — "
            f"check required fields and value formats against the v4 schema. {snippet}"
        )
    if status in (500, 502, 503, 504):
        return (
            f"Prism Central server error ({status}) on {path}. The service may be "
            f"busy or a task backlog is draining; retry shortly. {snippet}"
        )
    return f"Prism Central API error ({status}) on {path}. {snippet}"


class NutanixConnection:
    """A single authenticated session against one Prism Central v4 target."""

    def __init__(self, target: TargetConfig, client: Any | None = None) -> None:
        self._target = target
        self._client = client or httpx.Client(
            base_url=target.base_url,
            verify=target.verify_ssl,
            timeout=_TIMEOUT,
            auth=httpx.BasicAuth(target.username, target.password),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )

    @property
    def target(self) -> TargetConfig:
        return self._target

    # ── low-level ────────────────────────────────────────────────────────
    def _raw(self, method: str, path: str, **kwargs: Any) -> Any:
        """Issue a request, translate non-2xx centrally, return the raw response."""
        try:
            resp = self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise NutanixApiError(
                f"Could not reach Prism Central at {self._target.base_url} "
                f"({method} {path}): {exc}. Check host/port 9440 and that the PC "
                f"v4 REST API is reachable.",
                path=path,
            ) from exc
        if not (200 <= resp.status_code < 300):
            raise NutanixApiError(
                _teaching_message(resp.status_code, path, resp.text),
                status_code=resp.status_code,
                path=path,
            )
        return resp

    @staticmethod
    def _json(resp: Any) -> Any:
        if not resp.content:
            return {}
        try:
            return resp.json()
        except ValueError:
            return {}

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        """Issue a request and return parsed JSON."""
        return self._json(self._raw(method, path, **kwargs))

    def get(self, path: str, **kwargs: Any) -> Any:
        return self.request("GET", path, **kwargs)

    # ── ETag-aware helpers (the v4 differentiator) ───────────────────────
    def get_with_etag(self, path: str, **kwargs: Any) -> tuple[Any, str]:
        """GET an entity and return ``(json, etag)``.

        The ETag is read from the response header (v4 sometimes echoes it as
        ``ETag`` and sometimes as the vendor-specific ``X-Nutanix-Entity-Tag``);
        both are checked. An empty string means the server sent no ETag, in which
        case ``mutate`` will proceed without ``If-Match``.
        """
        resp = self._raw("GET", path, **kwargs)
        headers = getattr(resp, "headers", {}) or {}
        etag = headers.get("ETag") or headers.get("X-Nutanix-Entity-Tag") or ""
        return self._json(resp), etag

    def mutate(
        self, method: str, path: str, *, etag: str | None = None, json: Any = None
    ) -> Any:
        """Issue a mutating call (PUT/POST-action/DELETE) with an optional If-Match ETag."""
        headers = {"If-Match": etag} if etag else None
        return self.request(method, path, headers=headers, json=json)

    def post(self, path: str, *, etag: str | None = None, json: Any = None) -> Any:
        return self.mutate("POST", path, etag=etag, json=json)

    def put(self, path: str, *, etag: str | None = None, json: Any = None) -> Any:
        return self.mutate("PUT", path, etag=etag, json=json)

    def delete(self, path: str, *, etag: str | None = None) -> Any:
        return self.mutate("DELETE", path, etag=etag)

    # ── auto-pagination ──────────────────────────────────────────────────
    def list_all(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        limit: int = _PAGE_LIMIT,
        max_items: int = _MAX_ITEMS,
    ) -> list[dict]:
        """Walk every v4 page of a list endpoint and return the full ``data`` array.

        Bounded by ``max_items`` (default 5000) so a huge inventory can't exhaust
        memory; when the cap is hit, paging stops and what was collected so far is
        returned (the caller sees a truncated-but-usable list, never an OOM).
        """
        collected: list[dict] = []
        page = 0
        while len(collected) < max_items:
            q = dict(params or {})
            q["$page"] = page
            q["$limit"] = limit
            body = self.get(path, params=q)
            rows = body.get("data", []) if isinstance(body, dict) else (body or [])
            rows = [r for r in rows if isinstance(r, dict)]
            collected.extend(rows)
            if len(rows) < limit:  # last page
                break
            page += 1
        return collected[:max_items]

    def close(self) -> None:
        self._client.close()


class ConnectionManager:
    """Manages connections to multiple Prism Central targets with session reuse."""

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._connections: dict[str, NutanixConnection] = {}

    @classmethod
    def from_config(cls, config: AppConfig | None = None) -> ConnectionManager:
        cfg = config or load_config()
        return cls(cfg)

    def connect(self, target_name: str | None = None) -> NutanixConnection:
        """Connect to a target by name, or the default target."""
        target = (
            self._config.get_target(target_name)
            if target_name
            else self._config.default_target
        )
        cached = self._connections.get(target.name)
        if cached is not None:
            return cached
        conn = NutanixConnection(target)
        self._connections[target.name] = conn
        return conn

    def disconnect(self, target_name: str) -> None:
        conn = self._connections.pop(target_name, None)
        if conn is not None:
            conn.close()

    def disconnect_all(self) -> None:
        for name in list(self._connections):
            self.disconnect(name)

    def list_targets(self) -> list[str]:
        return [t.name for t in self._config.targets]

    def list_connected(self) -> list[str]:
        return list(self._connections.keys())
