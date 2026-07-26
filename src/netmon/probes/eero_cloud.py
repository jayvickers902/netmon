"""Small read-only client for the reverse-engineered eero cloud API."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import httpx


class EeroCloudError(Exception):
    """Sanitized cloud failure; credentials are deliberately absent from the message."""


@dataclass(frozen=True, slots=True)
class EeroDeviceRate:
    device_id: str
    upload_bytes_per_second: float
    download_bytes_per_second: float


class EeroTransport(Protocol):
    async def request(
        self,
        method: str,
        path: str,
        payload: dict[str, str] | None = None,
        token: str | None = None,
    ) -> dict[str, Any]: ...


class HttpxEeroTransport:
    def __init__(self, base_url: str, timeout_seconds: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    async def request(
        self,
        method: str,
        path: str,
        payload: dict[str, str] | None = None,
        token: str | None = None,
    ) -> dict[str, Any]:
        headers = {"Cookie": f"s={token}"} if token is not None else {}
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url, timeout=self.timeout_seconds, trust_env=False
            ) as client:
                response = await client.request(method, path, json=payload, headers=headers)
                response.raise_for_status()
                body = response.json()
        except (httpx.HTTPError, ValueError, OSError) as error:
            del error
            raise EeroCloudError("eero cloud request unavailable") from None
        if not isinstance(body, dict):
            raise EeroCloudError("eero cloud returned an invalid response")
        return body


class EeroCloudProbe:
    def __init__(self, transport: EeroTransport) -> None:
        self.transport = transport

    async def login(self, identifier: str) -> str:
        response = await self._request("POST", "/2.2/login", {"login": identifier})
        token = response.get("user_token")
        if not isinstance(token, str) or not token:
            raise EeroCloudError("eero cloud login response was invalid")
        return token

    async def verify(self, login_token: str, code: str) -> str:
        response = await self._request("POST", "/2.2/login/verify", {"code": code}, login_token)
        token = response.get("user_token", login_token)
        if not isinstance(token, str) or not token:
            raise EeroCloudError("eero cloud verification response was invalid")
        return token

    async def device_rates(self, token: str) -> list[EeroDeviceRate]:
        networks = await self._request("GET", "/2.2/networks", token=token)
        network_list = networks.get("networks")
        if not isinstance(network_list, list) or not network_list:
            raise EeroCloudError("eero cloud network response was invalid")
        network_id = network_list[0].get("id") if isinstance(network_list[0], dict) else None
        if not isinstance(network_id, (str, int)):
            raise EeroCloudError("eero cloud network response was invalid")
        devices = await self._request("GET", f"/2.2/networks/{network_id}/devices", token=token)
        device_list = devices.get("devices")
        if not isinstance(device_list, list):
            raise EeroCloudError("eero cloud device response was invalid")
        result: list[EeroDeviceRate] = []
        for device in device_list:
            if not isinstance(device, dict):
                raise EeroCloudError("eero cloud device response was invalid")
            device_id = device.get("mac") or device.get("id")
            upload = _number(device, "up_rate", "upload_rate", "upload")
            download = _number(device, "down_rate", "download_rate", "download")
            if not isinstance(device_id, (str, int)) or upload is None or download is None:
                # Offline and idle devices routinely carry no rate fields. Failing the whole
                # poll over one of them would blank per-device usage for the entire network
                # whenever a single laptop is asleep, so skip the device instead.
                continue
            result.append(EeroDeviceRate(str(device_id), upload, download))
        return result

    async def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, str] | None = None,
        token: str | None = None,
    ) -> dict[str, Any]:
        try:
            return await self.transport.request(method, path, payload, token)
        except EeroCloudError:
            raise
        except Exception:
            raise EeroCloudError("eero cloud request unavailable") from None


def _number(device: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = device.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    return None
