"""Thin async client for the Intergas Orion (ARCH_2_0) cloud API.

The API is a FastAPI backend that returns an ES256 JWT in the ``jwt`` field of
the authenticate response. The JWT lasts about an hour; this client simply
re-authenticates with email/password on a 401/403 instead of depending on the
refresh-token contract.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

import aiohttp

from .const import API_BASE

_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=20)

# Never log these exchanges: they carry the email, password and JWT.
_UNLOGGED_PATHS = ("/v1/accounts/authenticate", "/v1/accounts/platform_check")

# Identifiers that tie a log to one installation. They are removed from
# everything this client logs or puts in an exception, so debug logs can be
# shared in an issue as they are.
_ID_IN_PATH = re.compile(r"/(accounts|heaters|gateways|presets)/(?!pair-)[^/]+")
_PRIVATE_KEYS = {"email", "name", "serial_number", "mac", "mac_address", "jwt", "refresh_token"}


def redact_path(path: str) -> str:
    """Replace the account, heater, gateway and preset ids in an API path."""
    return _ID_IN_PATH.sub(r"/\1/<id>", path)


def redact(value: Any) -> Any:
    """Replace ids, serial numbers, names and tokens in decoded API JSON."""
    if isinstance(value, dict):
        return {
            k: "<redacted>"
            if v is not None and isinstance(k, str) and (k in _PRIVATE_KEYS or k == "id" or k.endswith("_id"))
            else redact(v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


def _redact_body(body: bytes) -> Any:
    try:
        return redact(json.loads(body))
    except ValueError:
        return f"<non-JSON body, {len(body)} bytes>"


class IntergasAuthError(Exception):
    """Raised when authentication fails (bad credentials)."""


class IntergasApiError(Exception):
    """Raised for any other API failure."""


class IntergasOrionClient:
    """Minimal client for one Intergas Comfort Touch account."""

    def __init__(self, session: aiohttp.ClientSession, email: str, password: str):
        self._session = session
        self._email = email
        self._password = password
        self._jwt: str | None = None
        self._account_id: str | None = None
        self._auth_lock = asyncio.Lock()
        self.last_error: str | None = None

    @property
    def account_id(self) -> str | None:
        return self._account_id

    async def _raw(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, Any]:
        """Perform one HTTP request and return (status, parsed JSON or None)."""
        url = f"{API_BASE}{path}"
        try:
            async with self._session.request(
                method, url, json=json_body, headers=headers, timeout=REQUEST_TIMEOUT
            ) as resp:
                body = await resp.read()
                logged = path not in _UNLOGGED_PATHS
                if logged and _LOGGER.isEnabledFor(logging.DEBUG):
                    _LOGGER.debug(
                        "%s %s %s -> HTTP %s: %s",
                        method,
                        redact_path(path),
                        json_body,
                        resp.status,
                        _redact_body(body) if body else "",
                    )
                if resp.status >= 400:
                    # A validation error can echo the request; the login
                    # request carries the password, so keep nothing from it.
                    self.last_error = (
                        str(_redact_body(body))[:500] if logged else None
                    )
                    return resp.status, None
                if not body:
                    return resp.status, None
                return resp.status, await resp.json(content_type=None)
        except (TimeoutError, aiohttp.ClientError, ValueError) as err:
            raise IntergasApiError(
                f"{method} {redact_path(path)} failed: {type(err).__name__}"
            ) from err

    async def authenticate(self) -> None:
        """Log in and cache the JWT and account_id."""
        status, data = await self._raw(
            "POST",
            "/v1/accounts/authenticate",
            json_body={"email": self._email, "password": self._password},
        )
        if status in (401, 403):
            raise IntergasAuthError("Invalid credentials")
        if status != 200 or not isinstance(data, dict):
            raise IntergasApiError(f"authenticate failed: HTTP {status}")
        self._jwt = data.get("jwt")
        self._account_id = data.get("account_id")
        if not self._jwt or not self._account_id:
            raise IntergasApiError("authenticate response missing jwt/account_id")

    async def platform(self) -> str:
        """Return 'ARCH_1_0' or 'ARCH_2_0' for this account."""
        status, data = await self._raw(
            "POST", "/v1/accounts/platform_check", json_body={"email": self._email}
        )
        if status != 200 or not isinstance(data, dict):
            raise IntergasApiError(f"platform_check failed: HTTP {status}")
        return data.get("platform", "")

    async def _ensure_auth(self, stale_jwt: str | None = None) -> None:
        """Authenticate if there is no token, or if the token in use went stale."""
        async with self._auth_lock:
            if self._jwt is None or self._jwt == stale_jwt:
                self._jwt = None
                await self.authenticate()

    async def _request(self, method: str, path: str, *, json_body: Any = None) -> Any:
        """Authenticated request; ``{aid}`` in path is replaced by the account id."""
        await self._ensure_auth()
        path = path.format(aid=self._account_id)
        jwt = self._jwt
        status, data = await self._raw(
            method, path, json_body=json_body, headers={"Authorization": f"Bearer {jwt}"}
        )
        if status in (401, 403):
            # Token most likely expired: re-authenticate once and retry.
            await self._ensure_auth(stale_jwt=jwt)
            status, data = await self._raw(
                method,
                path,
                json_body=json_body,
                headers={"Authorization": f"Bearer {self._jwt}"},
            )
        if status in (401, 403):
            # The login itself just succeeded, so the credentials are fine: the
            # cloud rejected its own fresh token (seen during a server-side key
            # rotation). Drop the token and let the next poll retry instead of
            # starting a reauth flow, which would stop polling for good.
            self._jwt = None
            raise IntergasApiError(
                f"{method} {redact_path(path)}: HTTP {status} after re-auth"
            )
        if status >= 400:
            raise IntergasApiError(f"HTTP {status}: {self.last_error}")
        return data

    # --- resource helpers -------------------------------------------------
    async def get_heaters(self) -> list[dict]:
        return await self._request("GET", "/v1/accounts/{aid}/heaters") or []

    async def get_heater(self, heater_id: str) -> dict:
        return await self._request(
            "GET", f"/v1/accounts/{{aid}}/heaters/{heater_id}"
        )

    async def get_zones(self, heater_id: str) -> list[dict]:
        return (
            await self._request(
                "GET", f"/v1/accounts/{{aid}}/heaters/{heater_id}/zones"
            )
            or []
        )

    async def set_zone_temperature(
        self, heater_id: str, zone_number: int, temperature: float
    ) -> None:
        """Set a zone's target temperature (ZoneUpdate.temperature_heater_setpoint).

        The API also answers 202 to unknown fields and ignores them, so the
        field name has to match exactly.
        """
        await self._request(
            "PATCH",
            f"/v1/accounts/{{aid}}/heaters/{heater_id}/zones/{zone_number}",
            json_body={"temperature_heater_setpoint": round(float(temperature), 1)},
        )

    async def get_statistics(self, heater_id: str) -> dict:
        return await self._request(
            "GET", f"/v1/accounts/{{aid}}/heaters/{heater_id}/statistics"
        ) or {}

    async def get_weather(self, heater_id: str) -> dict:
        return await self._request(
            "GET", f"/v1/accounts/{{aid}}/heaters/{heater_id}/weather"
        ) or {}

    async def get_presets(self, heater_id: str) -> list[dict]:
        return await self._request(
            "GET", f"/v1/accounts/{{aid}}/heaters/{heater_id}/presets"
        ) or []

    async def get_week_scheme(self, heater_id: str, zone_number: int) -> dict:
        return await self._request(
            "GET",
            f"/v1/accounts/{{aid}}/heaters/{heater_id}/zones/{zone_number}/scheme/week",
        ) or {}

    async def patch_week_scheme(
        self, heater_id: str, zone_number: int, **fields: Any
    ) -> None:
        """Update WeekSchemeSettings; every field is optional.

        schedule_enabled switches between the week scheme and the manual
        setpoint (the API has no separate "mode" request).
        """
        await self._request(
            "PATCH",
            f"/v1/accounts/{{aid}}/heaters/{heater_id}/zones/{zone_number}/scheme/week",
            json_body=fields,
        )

    async def set_preset_temperature(
        self, heater_id: str, preset_id: str, temperature: float
    ) -> None:
        """Change a preset's temperature (PresetEdit; every field is optional)."""
        await self._request(
            "PATCH",
            f"/v1/accounts/{{aid}}/heaters/{heater_id}/presets/{preset_id}",
            json_body={"temperature": round(float(temperature), 1)},
        )

    async def get_parameters(self, heater_id: str) -> list[dict]:
        """Boiler parameters the app may change (APIParameter: title, value, range, display_code, status)."""
        return await self._request(
            "GET", f"/v1/accounts/{{aid}}/heaters/{heater_id}/parameters"
        ) or []

    async def set_parameter(self, heater_id: str, title: str, value: int) -> None:
        """Change one boiler parameter (APIParameterUpdate: title + desired_value)."""
        await self._request(
            "PATCH",
            f"/v1/accounts/{{aid}}/heaters/{heater_id}/parameters",
            json_body={"title": title, "desired_value": int(value)},
        )
