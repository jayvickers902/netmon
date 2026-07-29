"""Small read-only client for the reverse-engineered eero cloud API."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime
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


@dataclass(frozen=True, slots=True)
class EeroDevice:
    """Full per-device info; every field but ``device_id`` is best-effort."""

    device_id: str
    name: str | None = None
    connection_type: str | None = None
    signal_dbm: float | None = None
    band: str | None = None
    node: str | None = None
    ip: str | None = None
    connected: bool | None = None
    upload_bytes_per_second: float | None = None
    download_bytes_per_second: float | None = None


@dataclass(frozen=True, slots=True)
class EeroNode:
    location: str | None = None
    serial: str | None = None
    model: str | None = None
    status: str | None = None
    connected_clients: int | None = None

    @property
    def target(self) -> str | None:
        return self.location or self.serial


@dataclass(frozen=True, slots=True)
class EeroSpeedTest:
    measured_at: datetime
    upload_mbps: float
    download_mbps: float


@dataclass(frozen=True, slots=True)
class EeroNetworkSnapshot:
    network_id: str
    devices: list[EeroDevice]
    speed: EeroSpeedTest | None


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
        """Backwards-compatible view over :meth:`network_snapshot`: rate pairs only."""
        snapshot = await self.network_snapshot(token, network_id)
        return [
            EeroDeviceRate(
                device.device_id,
                device.upload_bytes_per_second,
                device.download_bytes_per_second,
            )
            for device in snapshot.devices
            if device.upload_bytes_per_second is not None
            and device.download_bytes_per_second is not None
        ]

    async def network_snapshot(
        self, token: str, network_id: str | None = None
    ) -> EeroNetworkSnapshot:
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
        speed: EeroSpeedTest | None = None
        if selected_network.resource_url is not None:
            network_response = await self._request(
                "GET", selected_network.resource_url, token=token
            )
            speed = _parse_speed_test(network_response.get("speed"))
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
                    return EeroNetworkSnapshot(
                        selected_network.id, _parse_devices(devices_reference["data"]), speed
                    )
            elif isinstance(devices_reference, str) and devices_reference:
                device_path = devices_reference
            elif isinstance(devices_reference, list):
                return EeroNetworkSnapshot(
                    selected_network.id, _parse_devices(devices_reference), speed
                )

        devices = await self._request("GET", device_path, token=token)
        device_list: object = devices.get("devices", devices)
        if isinstance(device_list, dict):
            device_list = device_list.get("data")
        if not isinstance(device_list, list):
            raise EeroSchemaError("eero cloud device response was invalid")
        return EeroNetworkSnapshot(selected_network.id, _parse_devices(device_list), speed)

    async def node_health(self, token: str, network_id: str) -> list[EeroNode]:
        # A single direct GET; unlike devices, node status is always addressable by
        # network_id so no resource_url indirection hop is needed.
        response = await self._request("GET", f"/2.2/networks/{network_id}/eeros", token=token)
        node_list: object = response.get("eeros", response)
        if isinstance(node_list, dict):
            node_list = node_list.get("data")
        if not isinstance(node_list, list):
            raise EeroSchemaError("eero cloud node response was invalid")
        return _parse_nodes(node_list)

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


def _parse_devices(device_list: list[object]) -> list[EeroDevice]:
    result: list[EeroDevice] = []
    for device in device_list:
        if not isinstance(device, dict):
            raise EeroSchemaError("eero cloud device response was invalid")
        parsed = _parse_device(device)
        if parsed is not None:
            result.append(parsed)
    return result


def _parse_device(device: dict[str, Any]) -> EeroDevice | None:
    device_id = _device_identifier(device)
    if device_id is None:
        return None
    usage = device.get("usage")
    return EeroDevice(
        device_id=device_id,
        name=_parse_name(device),
        connection_type=_optional_str(device.get("connection_type")),
        signal_dbm=_parse_signal_dbm(device.get("connectivity")),
        band=_parse_band(device.get("interface")),
        node=_parse_node_location(device.get("source")),
        ip=_optional_str(device.get("ip")),
        connected=device.get("connected") if isinstance(device.get("connected"), bool) else None,
        upload_bytes_per_second=_parse_rate_bytes(usage, "up_mbps"),
        download_bytes_per_second=_parse_rate_bytes(usage, "down_mbps"),
    )


def _parse_name(device: dict[str, Any]) -> str | None:
    for key in ("nickname", "display_name", "hostname"):
        value = _optional_str(device.get(key))
        if value is not None:
            return value
    return None


def _optional_str(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


_SIGNAL_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")


def _parse_signal_dbm(connectivity: object) -> float | None:
    if not isinstance(connectivity, dict):
        return None
    signal = connectivity.get("signal")
    if not isinstance(signal, str):
        return None
    match = _SIGNAL_NUMBER_RE.search(signal)
    if match is None:
        return None
    try:
        return float(match.group())
    except ValueError:
        return None


def _parse_band(interface: object) -> str | None:
    if not isinstance(interface, dict):
        return None
    frequency = interface.get("frequency")
    if isinstance(frequency, bool) or not isinstance(frequency, (int, float)):
        return None
    if not math.isfinite(frequency):
        return None
    unit = _optional_str(interface.get("frequency_unit"))
    if unit is None:
        return None
    number = int(frequency) if float(frequency).is_integer() else frequency
    return f"{number} {unit}"


def _parse_node_location(source: object) -> str | None:
    if not isinstance(source, dict):
        return None
    return _optional_str(source.get("location"))


def _parse_rate_bytes(usage: object, key: str) -> float | None:
    if not isinstance(usage, dict):
        return None
    number = _non_negative_number(usage.get(key))
    if number is None:
        return None
    return number * 125_000.0


def _parse_nodes(node_list: list[object]) -> list[EeroNode]:
    result: list[EeroNode] = []
    for node in node_list:
        if not isinstance(node, dict):
            raise EeroSchemaError("eero cloud node response was invalid")
        clients = node.get("connected_clients_count")
        client_count = (
            int(clients)
            if isinstance(clients, (int, float)) and not isinstance(clients, bool)
            else None
        )
        result.append(
            EeroNode(
                location=_optional_str(node.get("location")),
                serial=_optional_str(node.get("serial")),
                model=_optional_str(node.get("model")),
                status=_optional_str(node.get("status")),
                connected_clients=client_count,
            )
        )
    return result


_SPEED_UNITS_TO_MBPS = {"bps": 1e-6, "kbps": 1e-3, "mbps": 1.0, "gbps": 1_000.0}


def _parse_speed_test(value: object) -> EeroSpeedTest | None:
    if not isinstance(value, dict):
        return None
    measured_at = _parse_iso8601(value.get("date"))
    if measured_at is None:
        return None
    upload_mbps = _parse_speed_leg(value.get("up"))
    download_mbps = _parse_speed_leg(value.get("down"))
    if upload_mbps is None or download_mbps is None:
        return None
    return EeroSpeedTest(measured_at, upload_mbps, download_mbps)


def _parse_speed_leg(leg: object) -> float | None:
    if not isinstance(leg, dict):
        return None
    number = _non_negative_number(leg.get("value"))
    if number is None:
        return None
    unit = _optional_str(leg.get("units"))
    if unit is None:
        return None
    factor = _SPEED_UNITS_TO_MBPS.get(unit.lower())
    if factor is None:
        return None
    return number * factor


def _parse_iso8601(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


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
