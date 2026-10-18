"""Thin synchronous client for the (unofficial) TrainHeroic API."""

from __future__ import annotations

import json
import logging
import os
import re
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import httpx

from . import __version__

log = logging.getLogger(__name__)

BASE_URL = "https://api.trainheroic.com"
TIMEOUT = 60.0
LIBRARY_TTL_SECONDS = 24 * 3600

# The API rejects requests carrying httpx's default user agent (403) and requests
# with browser Origin/Referer headers, so send a plain identifying user agent only.
DEFAULT_HEADERS = {
    "user-agent": f"trainheroic-mcp/{__version__} (+https://github.com/IBazylchuk/trainheroic-mcp)",
    "accept": "application/json",
}


class TrainHeroicError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def default_config_dir() -> Path:
    return Path(
        os.environ.get("TRAINHEROIC_CONFIG_DIR", Path.home() / ".config" / "trainheroic-mcp")
    )


def _summarize_body(text: str) -> str:
    """Error bodies are often full HTML pages; keep a short, readable hint."""
    if "<html" in text[:200].lower():
        title = re.search(r"<title>(.*?)</title>", text, re.S | re.I)
        return f"HTML error page{': ' + title.group(1).strip() if title else ''}"
    return text[:300]


class TrainHeroicAPI:
    """Authenticated access to api.trainheroic.com.

    Auth uses either a session token or email + password (the token obtained by
    logging in is cached in ``config_dir/token.json`` and refreshed on 401).
    """

    def __init__(
        self,
        *,
        email: str | None = None,
        password: str | None = None,
        token: str | None = None,
        config_dir: Path | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._email = email
        self._password = password
        self._config_dir = config_dir or default_config_dir()
        self._token = token or self._load_cached_token()
        if not self._token and not (email and password):
            raise TrainHeroicError(
                "Set TRAINHEROIC_SESSION_TOKEN or TRAINHEROIC_EMAIL + TRAINHEROIC_PASSWORD"
            )
        self._http = httpx.Client(
            base_url=BASE_URL,
            headers=DEFAULT_HEADERS,
            timeout=TIMEOUT,
            follow_redirects=True,
            transport=transport,
        )
        self._profile: dict | None = None
        self._teams: list[dict] | None = None
        self._context = self._load_json("context.json") or {}

    @classmethod
    def from_env(cls) -> TrainHeroicAPI:
        return cls(
            email=os.environ.get("TRAINHEROIC_EMAIL"),
            password=os.environ.get("TRAINHEROIC_PASSWORD"),
            token=os.environ.get("TRAINHEROIC_SESSION_TOKEN"),
        )

    # ── persistence ──────────────────────────────────────────────────────────

    def _load_json(self, name: str) -> Any:
        path = self._config_dir / name
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError):
            return None

    def _save_json(self, name: str, data: Any) -> None:
        path = self._config_dir / name
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.write_text(json.dumps(data))
        path.chmod(0o600)

    def _load_cached_token(self) -> str | None:
        cached = self._load_json("token.json")
        return cached.get("session_token") if isinstance(cached, dict) else None

    # ── auth ─────────────────────────────────────────────────────────────────

    def login(self) -> str:
        if not (self._email and self._password):
            raise TrainHeroicError(
                "Session token was rejected and no email/password is configured", 401
            )
        resp = self._http.post(
            "/auth",
            params={"t": int(time.time() * 1000)},
            json={"email": self._email, "password": self._password},
        )
        if resp.status_code >= 400:
            raise TrainHeroicError(f"Login failed ({resp.status_code})", resp.status_code)
        token = resp.json().get("session_id")
        if not token:
            raise TrainHeroicError("Login response did not contain a session id")
        self._token = token
        self._save_json("token.json", {"session_token": token})
        return token

    # ── HTTP ─────────────────────────────────────────────────────────────────

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json_body: Any = None,
    ) -> Any:
        if not self._token:
            self.login()
        resp = self._send(method, path, params, json_body)
        # The API also answers 401 for resources of a team the athlete cannot see,
        # so only re-login when the token itself has expired.
        if resp.status_code == 401 and self._email and self._password and not self._token_valid():
            self.login()
            resp = self._send(method, path, params, json_body)
        log.debug("%s %s -> %s (%d bytes)", method, path, resp.status_code, len(resp.content))
        if resp.status_code >= 400:
            raise TrainHeroicError(
                f"{method} {path} failed with {resp.status_code}: {_summarize_body(resp.text)}",
                resp.status_code,
            )
        if not resp.content:
            return None
        try:
            return resp.json()
        except ValueError:
            # Some endpoints (e.g. DELETE) answer with plain text.
            return resp.text.strip()

    def _send(self, method: str, path: str, params: dict | None, json_body: Any) -> httpx.Response:
        return self._http.request(
            method,
            path,
            params=params,
            json=json_body,
            headers={"session-token": self._token or ""},
        )

    def _token_valid(self) -> bool:
        return self._send("GET", "/v5/user", None, None).status_code < 400

    def get(self, path: str, **params: Any) -> Any:
        return self.request("GET", path, params=params or None)

    def post(self, path: str, body: Any = None) -> Any:
        return self.request("POST", path, json_body=body)

    def put(self, path: str, body: Any = None) -> Any:
        return self.request("PUT", path, json_body=body)

    def delete(self, path: str) -> Any:
        return self.request("DELETE", path)

    # ── account context ──────────────────────────────────────────────────────

    @property
    def profile(self) -> dict:
        if self._profile is None:
            self._profile = self.get("/v5/user")
        return self._profile

    @property
    def user_id(self) -> int:
        return int(self.profile["id"])

    @property
    def full_name(self) -> str:
        p = self.profile
        return f"{p.get('nameFirst', '')} {p.get('nameLast', '')}".strip()

    @property
    def teams(self) -> list[dict]:
        """Coach teams the athlete belongs to (licensed or not)."""
        if self._teams is None:
            data = self.get("/1.0/athlete/userlicense/") or {}
            licenses = data.get("licenses", {})
            teams: list[dict] = []
            for kind in ("team", "team_share", "org", "unlicensed"):
                for t in licenses.get(kind, []):
                    teams.append(
                        {
                            "team_id": int(t["id"]),
                            "program_id": int(t["programId"]) if t.get("programId") else None,
                            "title": t.get("title"),
                            "coaches": [c.get("title") for c in t.get("coaches", [])],
                            "licensed": kind != "unlicensed",
                        }
                    )
            self._teams = teams
        return self._teams

    @property
    def personal_team_id(self) -> int | None:
        value = self._context.get("personal_team_id")
        return int(value) if value else None

    def remember_team(self, program_id: Any, team_id: Any, *, personal: bool = False) -> None:
        """Record which team owns a program; personal calendars are not listed in licenses."""
        if not (program_id and team_id):
            return
        programs = self._context.setdefault("program_teams", {})
        changed = programs.get(str(program_id)) != int(team_id)
        programs[str(program_id)] = int(team_id)
        if personal and self._context.get("personal_team_id") != int(team_id):
            self._context["personal_team_id"] = int(team_id)
            self._context["personal_program_id"] = int(program_id)
            changed = True
        if changed:
            self._save_json("context.json", self._context)

    def candidate_teams(self, program_id: int | None = None) -> list[int]:
        """Teams to try, most likely first, when an endpoint needs a team id."""
        ordered: list[int] = []
        if program_id:
            known = self._context.get("program_teams", {}).get(str(program_id))
            if known:
                ordered.append(int(known))
            ordered += [t["team_id"] for t in self.teams if t["program_id"] == program_id]
        if self.personal_team_id:
            ordered.append(self.personal_team_id)
        ordered += [t["team_id"] for t in self.teams]
        return list(dict.fromkeys(ordered))

    # ── cached library ───────────────────────────────────────────────────────

    def cached_list(self, path: str, cache_name: str, *, refresh: bool = False) -> list[dict]:
        cache_file = f"cache/{cache_name}.json"
        cached = None if refresh else self._load_json(cache_file)
        if cached and time.time() - cached.get("fetched_at", 0) < LIBRARY_TTL_SECONDS:
            return cached["data"]
        params: dict[str, Any] = {"userId": self.user_id}
        team_ids = self.candidate_teams()
        if team_ids:
            params["teamId"] = team_ids[0]
        data = self.get(path, **params) or []
        self._save_json(cache_file, {"fetched_at": time.time(), "data": data})
        return data

    def exercise_library(self, *, refresh: bool = False) -> list[dict]:
        return self.cached_list("/v5/users/exercises", "exercises", refresh=refresh)

    def circuit_library(self, *, refresh: bool = False) -> list[dict]:
        return self.cached_list("/v5/users/circuits", "circuits", refresh=refresh)

    def first_success(self, attempts: Iterable[int], fetch) -> tuple[int, Any]:
        """Call ``fetch(team_id)`` for each team until one is not rejected (401/403/404)."""
        last: TrainHeroicError | None = None
        for team_id in attempts:
            try:
                return team_id, fetch(team_id)
            except TrainHeroicError as e:
                if e.status_code not in (401, 403, 404):
                    raise
                last = e
        raise last or TrainHeroicError("No team available for this request")
