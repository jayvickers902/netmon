"""Small read-only client for the reverse-engineered eero cloud API."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx

from netmon.probes.arp import normalize_mac


class EeroCloudError(Exception):
    """Sanitized cloud failure; credentials are deliberately absent from the message."""


class EeroAuthenticationError(EeroCloudError):
    """The cloud session is absent or expired."""


class EeroTransportError(EeroCloudError):
    """The cloud service could not be reached or decoded."""


class EeroSchemaError(EeroCloudError):
    """The cloud returned a response shape netmon cannot safely interpret."""


@dataclass(frozen=True, slots=True)
class EeroDeviceRate:
    device_id: str
    upload_bytes_per_second: float
    download_bytes_per_second: float


@dataclass(frozen=True, slots=True)
class EeroNetwork:
    id: str
    name: str
    resource_url: str | None = None


class EeroTransport(Protocol):
    async def request(
        self,
        method: str,
        path: str,
        payload: dict[str, str] | None = None,
        token: str | None = None,
    ) -> dict[str, Any]: ...

    async def aclose(self) -> None: ...


class HttpxEeroTransport:
    def __init__(
        self,
        base_url: str,
        timeout_seconds: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = httpx.URL(base_url.rstrip("/"))
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=timeout_seconds,
            trust_env=False,
            transport=transport,
        )

    async def request(
        self,
        method: str,
        path: str,
        payload: dict[str, str] | None = None,
        token: str | None = None,
    ) -> dict[str, Any]:
        headers = {"Cookie": f"s={token}"} if token is not None else {}
        try:
            request_url = httpx.URL(path)
            if request_url.is_absolute_url and (
                request_url.scheme,
                request_url.host,
                request_url.port,
            ) != (
                self._base_url.scheme,
                self._base_url.host,
                self._base_url.port,
            ):
                raise EeroSchemaError("eero cloud returned an invalid resource URL")
            response = await self._client.request(
                method, request_url, json=payload, headers=headers
            )
            response.raise_for_status()
            body = response.json()
        except httpx.HTTPStatusError as error:
            if error.response.status_code in (401, 403):
                raise EeroAuthenticationError("eero cloud authentication failed") from None
            raise EeroTransportError("eero cloud request unavailable") from None
        except (httpx.HTTPError, ValueError, OSError):
            raise EeroTransportError("eero cloud request unavailable") from None
        if not isinstance(body, dict):
            raise EeroSchemaError("eero cloud returned an invalid response")
        return body

    async def aclose(self) -> None:
        await self._client.aclose()

    async def close(self) -> None:
        await self.aclose()

    async def __aenter__(self) -> HttpxEeroTransport:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object,
    ) -> None:
        await self.aclose()


class EeroCloudProbe:
    def __init__(self, transport: EeroTransport) -> None:
        self.transport = transport

    async def login(self, identifier: str) -> str:
        response = await self._request("POST", "/2.2/login", {"login": identifier})
        return _token(response, "eero cloud login response was invalid")

    async def verify(self, login_token: str, code: str) -> str:
        response = await self._request("POST", "/2.2/login/verify", {"code": code}, login_token)
        token = response.get("user_token", login_token)
        if not isinstance(token, str) or not token:
            raise EeroSchemaError("eero cloud verification response was invalid")
        return token

    async def refresh(self, token: str) -> str:
        response = await self._request("POST", "/2.2/login/refresh", token=token)
        refreshed = response.get("user_token", token)
        if not isinstance(refreshed, str) or not refreshed:
            raise EeroSchemaError("eero cloud refresh response was invalid")
        return refreshed

    async def aclose(self) -> None:
        await self.transport.aclose()

    async def list_networks(self, token: str) -> list[EeroNetwork]:
        account = await self._request("GET", "/2.2/account", token=token)
        raw_networks = account.get("networks")
        if isinstance(raw_networks, dict):
            embedded = raw_networks.get("data")
            resource = _resource_url(raw_networks)
            if embedded is None and resource is not None:
                network_response = await self._request("GET", resource, token=token)
                embedded = network_response.get("networks", network_response)
                if isinstance(embedded, dict):
                    embedded = embedded.get("data")
            raw_networks = embedded
        if not isinstance(raw_networks, list):
            raise EeroSchemaError("eero cloud network response was invalid")

        networks: list[EeroNetwork] = []
        for raw_network in raw_networks:
            if not isinstance(raw_network, dict):
                raise EeroSchemaError("eero cloud network response was invalid")
            resource_url = _resource_url(raw_network)
            raw_id = raw_network.get("id")
            network_id = (
                str(raw_id)
                if isinstance(raw_id, (str, int)) and not isinstance(raw_id, bool)
                else _id_from_url(resource_url)
            )
            if not network_id:
                raise EeroSchemaError("eero cloud network response was invalid")
            raw_name = raw_network.get("name")
            name = raw_name if isinstance(raw_name, str) and raw_name else network_id
            networks.append(EeroNetwork(network_id, name, resource_url))
        return networks

    async def device_rates(self, token: str, network_id: str | None = None) -> list[EeroDeviceRate]:
        networks = await self.list_networks(token)
        selected_network: EeroNetwork | None
        if network_id is None:
            if len(networks) != 1:
                raise EeroCloudError("eero cloud network selection is ambiguous")
            selected_network = networks[0]
        else:
            selected_network = next((item for item in networks if item.id == network_id), None)
            if selected_network is None:
                raise EeroCloudError("configured eero network was not found")
        if selected_network is None:
            raise EeroCloudError("configured eero network was not found")

        device_path = f"/2.2/networks/{selected_network.id}/devices?thread=true"
        if selected_network.resource_url is not None:
            network_response = await self._request(
                "GET", selected_network.resource_url, token=token
            )
            devices_reference = network_response.get("devices")
            if devices_reference is None:
                resources = network_response.get("resources")
                if isinstance(resources, dict):
                    devices_reference = resources.get("devices")
            if isinstance(devices_reference, dict):
                resource = _resource_url(devices_reference)
                if resource is not None:
                    device_path = resource
                elif isinstance(devices_reference.get("data"), list):
                    return _device_rates(devices_reference["data"])
            elif isinstance(devices_reference, str) and devices_reference:
                device_path = devices_reference
            elif isinstance(devices_reference, list):
                return _device_rates(devices_reference)

        devices = await self._request("GET", device_path, token=token)
        device_list: object = devices.get("devices", devices)
        if isinstance(device_list, dict):
            device_list = device_list.get("data")
        if not isinstance(device_list, list):
            raise EeroSchemaError("eero cloud device response was invalid")
        return _device_rates(device_list)

    async def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, str] | None = None,
        token: str | None = None,
    ) -> dict[str, Any]:
        try:
            response = await self.transport.request(method, path, payload, token)
            return _unwrap_response(response)
        except EeroCloudError:
            raise
        except Exception:
            raise EeroTransportError("eero cloud request unavailable") from None


def _unwrap_response(response: dict[str, Any]) -> dict[str, Any]:
    if "meta" not in response:
        raise EeroSchemaError("eero cloud returned an invalid response")
    meta = response.get("meta")
    if not isinstance(meta, dict):
        raise EeroSchemaError("eero cloud returned an invalid response")
    code = meta.get("code")
    if isinstance(code, bool) or (code is not None and not isinstance(code, int)):
        raise EeroSchemaError("eero cloud returned an invalid response")
    if (
        (isinstance(code, int) and code >= 400)
        or meta.get("success") is False
        or bool(meta.get("error"))
    ):
        if _is_authentication_failure(meta):
            raise EeroAuthenticationError("eero cloud authentication failed")
        raise EeroTransportError("eero cloud request failed")
    data = response.get("data")
    if isinstance(data, dict):
        return data
    if isinstance(data, list):
        # Collection endpoints put their array directly in ``data`` while account and
        # authentication endpoints put an object there. Keep one internal mapping shape.
        return {"data": data}
    raise EeroSchemaError("eero cloud returned an invalid response")


def _token(response: dict[str, Any], error_message: str) -> str:
    token = response.get("user_token")
    if not isinstance(token, str) or not token:
        raise EeroSchemaError(error_message)
    return token


def _resource_url(value: dict[str, Any]) -> str | None:
    for key in ("url", "resource_url"):
        resource = value.get(key)
        if isinstance(resource, str) and resource:
            return resource
    return None


def _id_from_url(resource_url: str | None) -> str | None:
    if resource_url is None:
        return None
    path = urlparse(resource_url).path.rstrip("/")
    candidate = path.rsplit("/", 1)[-1]
    return candidate or None


def _device_rates(device_list: list[object]) -> list[EeroDeviceRate]:
    result: list[EeroDeviceRate] = []
    for device in device_list:
        if not isinstance(device, dict):
            raise EeroSchemaError("eero cloud device response was invalid")
        device_id = _device_identifier(device)
        usage = device.get("usage")
        if device_id is None:
            continue
        if not isinstance(usage, dict):
            continue
        upload = _non_negative_number(usage.get("up_mbps"))
        download = _non_negative_number(usage.get("down_mbps"))
        if upload is None or download is None:
            continue
        result.append(
            EeroDeviceRate(
                device_id,
                upload * 125_000.0,
                download * 125_000.0,
            )
        )
    return result


def _device_identifier(device: dict[str, Any]) -> str | None:
    mac = device.get("mac")
    if isinstance(mac, str) and mac.strip():
        return normalize_mac(mac.strip())
    raw_id = device.get("id")
    if isinstance(raw_id, bool) or not isinstance(raw_id, (str, int)):
        return None
    device_id = str(raw_id).strip()
    return device_id or None


def _non_negative_number(value: object) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    number = float(value)
    if not math.isfinite(number) or number < 0:
        return None
    return number


def _is_authentication_failure(meta: dict[str, Any]) -> bool:
    code = meta.get("code")
    if code in (401, 403):
        return True
    error = meta.get("error")
    values: tuple[object, ...] = tuple(error.values()) if isinstance(error, dict) else (error,)
    return any(
        isinstance(value, str)
        and any(marker in value.lower() for marker in ("auth", "token", "session"))
        for value in values
    )
