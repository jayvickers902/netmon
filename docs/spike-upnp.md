# UPnP WAN byte-counter spike

**Status:** answered · **Date:** 2026-07-26 · **Gates:** v0.4 bandwidth

## Question

Does the router expose UPnP IGD `WANCommonInterfaceConfig` byte counters
(`GetTotalBytesSent` / `GetTotalBytesReceived`)? The v0.4 WAN bandwidth design
assumed it would.

## Answer

**No.** The counters are not merely unauthorized — they are not implemented.
The v0.4 WAN bandwidth approach as originally planned is dead on this hardware.

A secondary and more useful result: `WANIPConnection` *does* work, and gives
router-reported WAN connection state. See [What is available](#what-is-available).

> **Superseded note — 2026-07-28.** The UPnP conclusion above remains correct for the eero:
> its byte counters are not implemented. A live survey of the bridged Hitron CODA found
> unauthenticated `dsoctets` counters that provide total downstream WAN throughput. Upstream
> throughput remains unavailable. See [`hitron-capabilities.md`](hitron-capabilities.md).

## Environment

| | |
|---|---|
| Router | eero 7 (`eero inc.`, `modelNumber 0.0.1`) |
| LAN | `192.168.4.0/24`, gateway `192.168.4.1` |
| Host adapter | Ethernet, `192.168.4.143` |
| Device description | `http://192.168.4.1:1900/igd.xml` |
| UDN | `uuid:fcdb9233-a63f-41da-b42c-7cfeb99c8adf` |

> **Note on the original assumption.** The project was planned against an
> "ISP-provided consumer router". The actual device is an eero 7 mesh system.
> This does not change the passive-visibility constraint (eero exposes no local
> management API, no SSH, no SNMP, no mirror port) but it does change the
> hardware options in the v1.1 throttling evaluation.

## Method

SSDP `M-SEARCH` on `239.255.255.250:1900`, then fetch each `SCPDURL` to read the
service's *declared* action list, then issue SOAP calls for the read-only `Get*`
actions only. No mutating action (`AddPortMapping`, `ForceTermination`,
`SetDefaultConnectionService`, …) was invoked.

### Gotcha worth keeping

The first run returned **zero** responses, including to `ssdp:all`. The cause was
not the router: this host runs NordVPN (NordLynx + OpenVPN DCO) and Tailscale
alongside the LAN adapter, and the multicast datagram followed the default route
into a tunnel. Binding the socket to the LAN address and setting
`IP_MULTICAST_IF` to the same address fixed it immediately.

```python
sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(lan_ip))
sock.bind((lan_ip, 0))
```

**`probes/upnp.py` must pin the egress interface.** A discovery probe that relies
on the default route will silently return nothing whenever a VPN is up, which on
this host is most of the time. This applies to mDNS and NetBIOS discovery too —
worth auditing `probes/hostname.py` for the same defect.

## Findings

The eero advertises the full IGD device tree, which is misleading — advertising a
service says nothing about which of its actions are implemented.

### `WANCommonInterfaceConfig:1` — stub, unusable

`SCPDURL /ifc.xml` declares exactly one action:

| Action | Declared | Result |
|---|---|---|
| `GetCommonLinkProperties` | yes | **HTTP 500** |
| `GetTotalBytesSent` | **no** | HTTP 500 |
| `GetTotalBytesReceived` | **no** | HTTP 500 |
| `GetTotalPacketsSent` / `Received` | **no** | not attempted |

The one action it does declare still fails. Treat this entire service as absent.

### `WANIPConnection:1` — works

`SCPDURL /ipc.xml` declares 45 names. Verified working:

| Action | Returned |
|---|---|
| `GetStatusInfo` | `ConnectionStatus=Connected`, `LastConnectionError=ERROR_NONE`, `Uptime=1469066` |
| `GetExternalIPAddress` | *(current WAN IP)* |
| `GetNATRSIPStatus` | `RSIPAvailable=0`, `NATEnabled=1` |
| `GetConnectionTypeInfo` | `ConnectionType=IP_Routed` |

`Uptime=1469066` s ≈ **17.0 days** of continuous WAN connection at time of test.

### `Layer3Forwarding:1`

Declares `GetDefaultConnectionService` / `SetDefaultConnectionService`. Not useful
here; the setter is mutating and must never be called.

## What is available

Unauthenticated, no router login, no admin rights:

- **WAN connection uptime** — a counter that resets when the WAN link
  re-establishes. This is *ground truth from the router* about whether the
  connection actually dropped, as opposed to inferring it from ping loss on this
  host. It cleanly separates "the internet went down" from "my Wi-Fi/NIC glitched"
  from "the far end was unreachable", which is the single most common ambiguity
  when troubleshooting.
- **`LastConnectionError`** — the router's own reason for the last failure.
- **External IP** — an ISP-side change often coincides with a reconnect, and is
  worth an event in its own right.
- **`ConnectionStatus`** — coarse, but free.

## Consequences

**v0.3 gains a collector.** `collectors/wan_status.py`, polling `GetStatusInfo`
and `GetExternalIPAddress`. Uptime going *backwards* is an unambiguous
`wan_reconnect` event and should be a first-class input to the outage state
machine, corroborating (or contradicting) the latency-derived signal. This is a
better outcome for the project's primary goal — troubleshooting — than the byte
counters would have been.

**v0.4 loses its primary source.** Remaining options for throughput, in order of
preference:

1. **Host NIC counters** via `psutil` — already planned, unaffected, but only
   covers this machine.
2. **Scheduled speedtest** — already planned. Measures capacity, not utilisation,
   and saturates the link, so it stays infrequent and opt-in.
3. **eero cloud API** (`api-user.e2ro.com`) — undocumented, unofficial, requires
   an auth token, and does expose per-device usage. Introduces a cloud dependency
   into a LAN tool and can break without notice. **Not recommended**; record as an
   option, do not build speculatively.
4. **Total WAN throughput** — the eero still has no local source, but the bridged Hitron
   now supplies verified total **downstream** throughput via `dsoctets`. **Upstream remains
   unsourced.** Accept that asymmetry, or resolve it with the v1.1 gateway hardware decision,
   which would supply upstream accounting, per-device attribution, and enforcement.

At the time of this spike, ROADMAP v0.4 was correctly amended to drop `probes/upnp.py`'s
byte-counter role and keep the probe for connection status. The Hitron survey now supersedes
only the topology-wide conclusion that total downstream WAN throughput is unavailable.

## Reproducing

The spike scripts are not part of the package (they touch the live network and are
excluded from the test suite by policy). To re-run after a firmware change:

```bash
python - <<'PY'
import socket, urllib.request, re
lan = "192.168.4.143"
msg = "\r\n".join(["M-SEARCH * HTTP/1.1","HOST: 239.255.255.250:1900",
    'MAN: "ssdp:discover"',"MX: 3",
    "ST: urn:schemas-upnp-org:device:InternetGatewayDevice:1","",""]).encode()
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(lan))
s.bind((lan, 0)); s.settimeout(4); s.sendto(msg, ("239.255.255.250", 1900))
try:
    while True:
        d, a = s.recvfrom(65535)
        loc = re.search(r"^LOCATION\s*:\s*(.+)$", d.decode(errors="replace"), re.I|re.M)
        if loc: print(a[0], loc.group(1).strip())
except TimeoutError: pass
PY
```

Then fetch each `SCPDURL` from the returned description and check whether
`GetTotalBytesSent` appears in the declared action list. If eero ever ships it,
re-open the v0.4 decision.
