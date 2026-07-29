"""Read-only DOCSIS status probe for the Hitron CODA cable modem.

The modem serves JSON at ``/data/*.asp`` with no authentication, and stays reachable on
192.168.100.1 even while bridged. See ``docs/hitron-capabilities.md`` for the survey.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Any, cast

import httpx

#: Fields the firmware leaves unpopulated. Any of these means "no value", not data.
PLACEHOLDERS = frozenset({"TODO", "NA", "Unknow", "Unknown", ""})


@dataclass(frozen=True, slots=True)
class DownstreamChannel:
    channel_id: int
    frequency_hz: int
    snr_db: float | None
    power_dbmv: float | None
    corrected: int | None
    uncorrectable: int | None
    octets: int | None
    channel_type: str = "qam"


@dataclass(frozen=True, slots=True)
class UpstreamChannel:
    channel_id: int
    frequency_hz: int
    bandwidth_hz: int
    modulation: str | None
    power_dbmv: float | None


@dataclass(frozen=True, slots=True)
class DocsisEventLogEntry:
    modem_time: str
    type_code: str
    priority: str
    message: str


@dataclass(frozen=True, slots=True)
class DocsisStatus:
    """One poll of the modem. Every field is optional; the modem omits plenty."""

    downstream: tuple[DownstreamChannel, ...]
    upstream: tuple[UpstreamChannel, ...]
    #: Sum of ``dsoctets`` over QAM and OFDM channels. Monotonic, 32-bit, wraps.
    downstream_octets_total: int | None
    uptime_seconds: int | None
    link_speed_mbps: int | None
    link_up: bool | None
    software_version: str | None
    #: True when every stage in getCMInit.asp reports Success.
    registered: bool | None
    event_log: tuple[DocsisEventLogEntry, ...] = ()
    event_log_available: bool = False


class HitronProbe:
    """Fetches and parses the modem's unauthenticated status endpoints."""

    def __init__(
        self,
        base_url: str = "http://192.168.100.1",
        timeout_seconds: float = 5.0,
        transport: httpx.AsyncBaseTransport | httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.transport = transport
        self._client: httpx.AsyncClient | None = None

    async def status(self) -> DocsisStatus | None:
        """Return the current DOCSIS status, or None if the modem is unreachable.

        Never raises for network or parse failure: an absent modem is a missing reading,
        not an outage signal.
        """
        endpoints = (
            "dsinfo.asp",
            "dsofdminfo.asp",
            "usinfo.asp",
            "getSysInfo.asp",
            "getLinkStatus.asp",
            "getCMInit.asp",
            "status_log.asp",
        )
        try:
            responses = await asyncio.gather(
                *(self._fetch_json(endpoint) for endpoint in endpoints),
                return_exceptions=True,
            )
        except (httpx.HTTPError, OSError, ValueError, KeyError, TypeError):
            return None

        payloads = [
            response if not isinstance(response, Exception) else None for response in responses
        ]
        downstream, qam_octets, downstream_learned = _parse_downstream(payloads[0])
        ofdm, ofdm_octets, ofdm_learned = _parse_ofdm(payloads[1])
        upstream, upstream_learned = _parse_upstream(payloads[2])
        uptime, software_version, system_learned = _parse_system(payloads[3])
        link_speed, link_up, link_learned = _parse_link(payloads[4])
        registered, registration_learned = _parse_registration(payloads[5])
        event_log, event_log_available = _parse_event_log(payloads[6])

        if not any(
            (
                downstream_learned,
                ofdm_learned,
                upstream_learned,
                system_learned,
                link_learned,
                registration_learned,
                event_log_available,
            )
        ):
            return None

        octet_values = [value for value in (qam_octets, ofdm_octets) if value is not None]
        return DocsisStatus(
            downstream=downstream + ofdm,
            upstream=upstream,
            downstream_octets_total=sum(octet_values) if octet_values else None,
            uptime_seconds=uptime,
            link_speed_mbps=link_speed,
            link_up=link_up,
            software_version=software_version,
            registered=registered,
            event_log=event_log,
            event_log_available=event_log_available,
        )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _fetch_json(self, endpoint: str) -> Any | None:
        try:
            if self._client is None:
                self._client = httpx.AsyncClient(
                    timeout=httpx.Timeout(self.timeout_seconds),
                    trust_env=False,
                    verify=not self.base_url.lower().startswith("https://"),
                    transport=cast(httpx.AsyncBaseTransport | None, self.transport),
                )
            response = await self._client.get(f"{self.base_url}/data/{endpoint}")
            response.raise_for_status()
            return response.json()
        except json.JSONDecodeError:
            return None
        except (httpx.HTTPError, OSError, ValueError, KeyError, TypeError):
            return None


_STAGES = (
    "hwInit",
    "findDownstream",
    "ranging",
    "dhcp",
    "timeOfDay",
    "downloadCfg",
    "registration",
)
_STAGE_ALIASES = {"timeOfDay": ("timeOfDay", "timeOfday")}
_UPTIME_RE = re.compile(r"(?:(\d+)d:)?(\d+)h:(\d+)m:(\d+)s")
_MBPS_RE = re.compile(r"([+-]?\d+(?:\.\d+)?)\s*Mbps", re.IGNORECASE)
_PADDED_ZERO_RE = re.compile(r"[+-]?0(?:\.0+)?")
_EVENT_METADATA_RE = re.compile(r";CM(?:TS)?-(?:MAC|QOS|VER)=.*$", re.IGNORECASE)


def _records(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        return [payload]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def _clean(value: Any) -> str | None:
    if isinstance(value, str):
        value = value.strip()
        return None if value in PLACEHOLDERS else value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return None


def _number(value: Any, parser: type[int] | type[float]) -> int | float | None:
    cleaned = _clean(value)
    if cleaned is None:
        return None
    if isinstance(value, str) and value != value.strip() and _PADDED_ZERO_RE.fullmatch(cleaned):
        return None
    try:
        return parser(cleaned)
    except (TypeError, ValueError):
        return None


def _integer(value: Any) -> int | None:
    parsed = _number(value, int)
    return parsed if isinstance(parsed, int) else None


def _decimal(value: Any) -> float | None:
    parsed = _number(value, float)
    return parsed if isinstance(parsed, float) else None


def _text(value: Any) -> str | None:
    return _clean(value)


def _parse_downstream(
    payload: Any,
) -> tuple[tuple[DownstreamChannel, ...], int | None, bool]:
    channels: list[DownstreamChannel] = []
    octets: list[int] = []
    learned = False
    for record in _records(payload):
        channel_id = _integer(record.get("channelId"))
        frequency = _integer(record.get("frequency"))
        channel_octets = _integer(record.get("dsoctets"))
        if channel_octets is not None:
            octets.append(channel_octets)
            learned = True
        if channel_id is None or frequency is None:
            continue
        channels.append(
            DownstreamChannel(
                channel_id=channel_id,
                frequency_hz=frequency,
                snr_db=_decimal(record.get("snr")),
                power_dbmv=_decimal(record.get("signalStrength")),
                corrected=_integer(record.get("correcteds")),
                uncorrectable=_integer(record.get("uncorrect")),
                octets=channel_octets,
            )
        )
        learned = True
    return tuple(channels), sum(octets) if octets else None, learned


def _parse_ofdm(
    payload: Any,
) -> tuple[tuple[DownstreamChannel, ...], int | None, bool]:
    channels: list[DownstreamChannel] = []
    octets: list[int] = []
    for record in _records(payload):
        channel_octets = _integer(record.get("dsoctets"))
        if channel_octets is not None:
            octets.append(channel_octets)
        channel_id = _integer(record.get("receive"))
        frequency = _integer(record.get("Subcarr0freqFreq"))
        if channel_id is None or frequency is None:
            continue
        channels.append(
            DownstreamChannel(
                channel_id=channel_id,
                frequency_hz=frequency,
                snr_db=_decimal(record.get("SNR")),
                power_dbmv=_decimal(record.get("plcpower")),
                corrected=_integer(record.get("correcteds")),
                uncorrectable=_integer(record.get("uncorrect")),
                octets=channel_octets,
                channel_type="ofdm",
            )
        )
    learned = bool(channels or octets)
    return tuple(channels), sum(octets) if octets else None, learned


def _parse_upstream(payload: Any) -> tuple[tuple[UpstreamChannel, ...], bool]:
    channels: list[UpstreamChannel] = []
    learned = False
    for record in _records(payload):
        channel_id = _integer(record.get("channelId"))
        frequency = _integer(record.get("frequency"))
        bandwidth = _integer(record.get("bandwidth"))
        if channel_id is None or frequency is None or bandwidth is None:
            continue
        channels.append(
            UpstreamChannel(
                channel_id=channel_id,
                frequency_hz=frequency,
                bandwidth_hz=bandwidth,
                modulation=_text(record.get("modtype")),
                power_dbmv=_decimal(record.get("signalStrength")),
            )
        )
        learned = True
    return tuple(channels), learned


def _parse_system(payload: Any) -> tuple[int | None, str | None, bool]:
    records = _records(payload)
    if not records:
        return None, None, False
    record = records[0]
    uptime_raw = _text(record.get("systemUptime"))
    uptime = _uptime_seconds(uptime_raw)
    software_version = _text(record.get("swVersion"))
    return uptime, software_version, uptime is not None or software_version is not None


def _parse_link(payload: Any) -> tuple[int | None, bool | None, bool]:
    records = _records(payload)
    if not records:
        return None, None, False
    record = records[0]
    status = _text(record.get("LinkStatus"))
    speed = _link_speed(record.get("LinkSpeed"))
    link_up = None if status is None else status == "Up"
    return speed, link_up, status is not None or speed is not None


def _parse_registration(payload: Any) -> tuple[bool | None, bool]:
    records = _records(payload)
    if not records:
        return None, False
    record = records[0]
    present_stages = [
        stage for stage in _STAGES if any(alias in record for alias in _stage_keys(stage))
    ]
    if not present_stages:
        return None, False
    registered = len(present_stages) == len(_STAGES) and all(
        _text(_stage_value(record, stage)) == "Success" for stage in _STAGES
    )
    return registered, True


def _parse_event_log(payload: Any) -> tuple[tuple[DocsisEventLogEntry, ...], bool]:
    if not isinstance(payload, list):
        return (), False
    events: list[DocsisEventLogEntry] = []
    for record in _records(payload):
        modem_time = _text(record.get("time"))
        type_code = _text(record.get("type"))
        priority = _text(record.get("priority"))
        message = _text(record.get("event"))
        if None in (modem_time, type_code, priority, message):
            continue
        assert modem_time is not None
        assert type_code is not None
        assert priority is not None
        assert message is not None
        sanitized = _EVENT_METADATA_RE.sub("", message).strip().rstrip(";")
        if not sanitized:
            continue
        events.append(
            DocsisEventLogEntry(
                modem_time=modem_time,
                type_code=type_code,
                priority=priority.lower(),
                message=sanitized,
            )
        )
    return tuple(events), True


def _stage_keys(stage: str) -> tuple[str, ...]:
    return _STAGE_ALIASES.get(stage, (stage,))


def _stage_value(record: dict[str, Any], stage: str) -> Any:
    for key in _stage_keys(stage):
        if key in record:
            return record[key]
    return None


def _uptime_seconds(value: str | None) -> int | None:
    if value is None:
        return None
    match = _UPTIME_RE.fullmatch(value)
    if match is None:
        return None
    days, hours, minutes, seconds = (int(part or 0) for part in match.groups())
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def _link_speed(value: Any) -> int | None:
    value = _clean(value)
    if value is None:
        return None
    match = _MBPS_RE.fullmatch(value)
    if match is not None:
        try:
            return int(float(match.group(1)))
        except ValueError:
            return None
    return _integer(value)
