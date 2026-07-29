# eero 7 — capability survey

**Date:** 2026-07-26 · **Hardware:** eero 7 (dual-band BE5000), gateway `192.168.4.1`
**Question:** what can netmon actually get out of this router, and what has to come from
somewhere else?

This supersedes the narrower conclusion in [`spike-upnp.md`](spike-upnp.md), which asked only
about UPnP byte counters. The answer there was "no". The answer here is more useful: the eero
has three separate interfaces, they expose very different things, and one of them turns out to
carry per-device bandwidth after all.

---

## 1. What the box exposes on the LAN

Probed directly against `192.168.4.1`:

| Port | State | What it is |
|---|---|---|
| 22 (SSH) | closed | No shell. Never has been on eero |
| 53 (DNS) | **open** | Local DNS resolver / cache |
| 80 (HTTP) | **open** | *Not* a management UI — returns `303 See Other` → `https://blocked.eero.com`. It is the content-filter block page and captive-portal handler |
| 443 (HTTPS) | closed | — |
| 161 (SNMP) | closed | No SNMP. Not supported on any eero model |
| 1900 (SSDP) | **open** | UPnP IGD — the one genuinely useful local interface |
| 8080 / 5000 / 7000 | closed | — |

**There is no local management API.** This is a deliberate design decision by eero, not a
missing feature: the units are cloud-managed and hold no local admin surface at all. Port 80
looking open is a red herring — it serves a redirect to a block page.

### UPnP IGD — what works, what doesn't

Confirmed live (see `spike-upnp.md` for the transcript):

- ✅ `WANIPConnection:GetStatusInfo` — connection status, **uptime**, last connection error
- ✅ `WANIPConnection:GetExternalIPAddress` — the WAN IP
- ❌ `WANCommonInterfaceConfig` — *advertised but unimplemented*. Every action returns HTTP 500.
  `GetTotalBytesSent` / `GetTotalBytesReceived` are not declared. This is the byte-counter dead
  end. The bridged Hitron is a separate local source for downstream bytes; see
  [`hitron-capabilities.md`](hitron-capabilities.md). It does not provide upstream octets.

WAN uptime is the valuable one and netmon already consumes it: uptime moving backwards is
ground truth that the *link* dropped, as opposed to local Wi-Fi trouble or a dead far end.

**Caveat: UPnP is disabled in bridge mode.** If the eero is ever bridged behind another router,
netmon loses its only local WAN signal. That matters for the gateway decision — see
[`spike-gateway.md`](spike-gateway.md).

---

## 2. What the eero app exposes

This is where most of the capability lives, and much of it is not reachable by netmon at all —
it is enforcement and configuration, not telemetry. Worth cataloguing because several items
answer "can I just do this in the app instead of building it?" with **yes**.

### Free tier (no subscription)

| Capability | Where | Useful to you |
|---|---|---|
| **Live per-device data usage, refreshed ~every 5s** | Activity tab | **Yes — this is the big one.** See §3 |
| Weekly per-device usage totals | Activity tab | Yes |
| Per-device and whole-network speed tests | Home / device detail | Yes |
| **Profiles** — group devices by person, **pause internet** per profile or per device | Profiles tab | **This is your throttling stopgap.** Binary on/off, not rate-limiting, but it needs no hardware |
| **Schedules** — pause a profile on a recurring timetable | Profiles tab | Yes — covers most "limit the kids" intent without shaping |
| Device grouping and priority / bandwidth policy per group | Settings | Partial — coarse, and not exposed anywhere netmon can read |
| Guest network | Settings | Yes |
| IP reservations (static leases) | Settings → Reservations & Port Forwarding | **Yes — do this for netmon's own host** |
| Port forwarding (IPv4) and IPv6 firewall rules | same | Situational |
| Custom DNS (IPv4 + IPv6) | Settings → DNS | Yes |
| DHCP & NAT — manual prefix and lease range | Settings → Advanced | Yes |
| IPv6 toggle | Settings → Advanced | Yes |
| Bridge mode | Settings → Advanced | See §4 |
| Thread / Matter / Zigbee border router | built in | Not netmon's problem |

### eero Labs (free, opt-in beta toggles)

| Feature | What it does | Recommendation |
|---|---|---|
| **Smart Queue Management (SQM)** | Fair-shares bandwidth across devices automatically — *not* priority-based QoS. It is fq_codel-style bufferbloat control | **Turn it on.** This is the single best fix for "my connection is up and down" when the real symptom is latency spikes under load. It costs some peak throughput and caps you below ~500 Mbps on some models — worth it |
| **Local DNS caching** | Caches at the network level | Turn it on. Cuts DNS lookup time, and netmon's DNS collector will show the difference |
| Optimize for conferencing/gaming | Traffic prioritisation heuristics | Optional; measure with netmon before and after |

Both SQM and local DNS caching are **unavailable in bridge mode**.

### eero Plus ($9.99/mo) — what you'd actually be buying

- **Historical data usage** — daily and monthly per-device graphs. *Live usage is free; history
  is not.*
- Content filtering / ad blocking / threat blocking (encrypted DNS)
- Activity insights, weekly reports
- Internet backup (fail over to a phone hotspot during an outage)
- Bundled 1Password, Malwarebytes, Guardian VPN

**Recommendation: don't buy it for netmon's sake.** netmon stores its own history in SQLite —
that is the entire point of the project. The only Plus feature netmon cannot replicate is
Internet Backup, and that is a convenience, not telemetry. If you want the security filtering
for its own sake, fine, but it is not a data unlock.

---

## 3. The cloud API — the per-device bandwidth unlock

**This changes a roadmap decision.** The previous roadmap listed the eero cloud API under
"rejected sources". That rejection was made on the assumption that per-device usage was a
subscription feature. It is not: **live per-device usage is free tier**, and it is served over
the same API the mobile app uses.

### How it works

- **Base URL:** `https://api-user.e2ro.com` — cloud only, there is no local equivalent
- **Auth:** POST `/2.2/login` with an email or phone → returns a `user_token` and emails/SMSes a
  verification code → POST `/2.2/login/verify` with the code → thereafter send
  `Cookie: s=<user_token>` on every request. **Tokens are long-lived**, so this is a one-time
  interactive step, not a per-poll login.
- **Data:** the account response discovers accessible networks and their resource URLs; the
  selected network's device resource carries instantaneous `usage.up_mbps` / `usage.down_mbps`
  values. netmon follows those advertised URLs instead of constructing endpoints from guesses.
- **Selection:** set `eero.network_id` when the account can access multiple networks. Automatic
  selection is limited to the unambiguous single-network case; the login command prints the
  available names and IDs after verification.
- **Session lifetime:** netmon preserves the existing token when verification or refresh returns
  no replacement, and persists a new token when refresh supplies one. Authentication failures
  still fail soft rather than interrupting other collectors.
- **Prior art:** `schmittx/home-assistant-eero` (actively maintained, HA 2025.2+),
  `fulviofreitas/eero-api`, `yepher/eeroMonitor`. All reverse-engineered; none endorsed by eero.

### The blocker you will hit

**Accounts that sign in with Amazon cannot use this API.** eero 7 units bought through Amazon
are usually Amazon-linked, so this probably applies to you. The established workaround, used by
the Home Assistant integration, is:

1. Create a *separate* eero account with an email address and password (not Amazon SSO)
2. Invite it to your network as an admin from the app
3. Authenticate netmon as that account

That is a real, supported account operation — not a hack — but it is a step you have to take by
hand, and I am not going to create an account on your behalf.

### The honest downsides

- **Reverse-engineered.** eero can change or break it in any firmware or app release, with no
  notice and no deprecation window.
- **Cloud dependency in a LAN tool.** If your internet is down — precisely when netmon matters
  most — this collector goes dark. It must fail soft and must never block the scheduler.
- **Rate limits are undocumented.** Poll conservatively (30–60s); configuration enforces a
  30-second minimum, never the app's 5s cadence.
- **It puts your eero credentials in netmon's config.** Hence: separate account, admin-only,
  and the token stored rather than the password.
- **Read-only by design.** Although the private API may expose control operations, netmon uses
  only login/refresh and account, network, and device reads. It does not pause devices or alter
  router configuration.

### There is also an *official* path

eero runs a **Data Portability / third-party onboarding** programme: email `privacy@eero.com`,
sign an NDA, and they send full API documentation. Worth doing if you want this to be durable
rather than best-effort. Slow, but it is the difference between an integration that survives
firmware updates and one that doesn't.

### Verdict

Build it as an **opt-in collector, disabled by default**, that fails soft and degrades to
"unavailable" rather than erroring. It is the only source of per-device bandwidth that exists
without changing hardware, and it is free. But it is not turned on unless you deliberately turn
it on, and the roadmap must keep saying so.

---

## 4. Bridge mode — the cost of putting something in front

If a Linux gateway is ever installed upstream, the eero goes into bridge mode. Confirmed losses:

**Lost:** Smart Queue Management · Thread border router · upstream IPv6 · custom DNS · **UPnP**
· local DNS caching · HomeKit Secure Router · eligibility to subscribe to eero Plus at all ·
most Plus features (advanced security, ad blocking, content filters, historical usage, hotspot
backup).

**Kept:** all Wi-Fi performance and TrueMesh roaming · the app for Wi-Fi settings · guest
network · device list · and, for existing Plus subscribers, 1Password / Malwarebytes / Guardian
VPN / DDNS / radio analytics.

**Two consequences for netmon specifically:**

1. **Losing UPnP means losing the WAN uptime signal** that the v0.3 outage classifier uses to
   distinguish "the ISP dropped" from "something local broke". A gateway replaces this with a
   *better* signal — it owns the WAN interface and sees link state directly — but netmon's
   `wan_status` collector would need a second backend. That is real work, not a free swap.
2. **Losing SQM is a genuine regression** unless the gateway re-implements it, which it would
   (`cake`/`fq_codel` on Linux is what SQM is imitating, and does it better). So this is only a
   loss if the gateway is set up carelessly.

---

## 5. What to do right now, before writing any more code

Zero-cost, five minutes in the app, and they measurably improve the thing you originally
complained about:

1. **Turn on Smart Queue Management** (eero Labs). Bufferbloat under load is the most common
   cause of "the connection is up and down" on an otherwise healthy line. netmon's latency
   collector will show you whether it helped — you now have before/after instrumentation, so
   use it.
2. **Turn on local DNS caching** (eero Labs). The DNS collector will quantify it.
3. **Reserve a static IP for this machine** (Settings → Reservations & Port Forwarding). netmon
   binds to a LAN address for UPnP multicast; a moving DHCP lease is a self-inflicted outage.
4. **Set custom DNS to 1.1.1.1 / 8.8.8.8** if you want DNS behaviour to be predictable rather
   than dependent on your ISP's resolvers — which is worth doing given you are measuring them.
5. **Use profiles + schedules** for the "limit the kids" requirement in the meantime. It is
   on/off rather than rate-limiting, but it works today and needs no hardware.

---

## Sources

- [eero 7 specifications](https://support.eero.com/hc/en-us/articles/32571518316699-Introducing-eero-7)
- [Advanced networking settings](https://support.eero.com/hc/en-us/articles/360036385311-What-are-the-Advanced-networking-settings)
- [Smart Queue Management](https://support.eero.com/hc/en-us/articles/360000709886-Optional-Advanced-Features)
- [Local DNS caching](https://support.eero.com/hc/en-us/articles/46052784608667-What-is-Local-DNS-caching)
- [Features lost in bridge mode](https://support.eero.com/hc/en-us/articles/115000825206-What-features-do-I-lose-if-I-put-my-eeros-in-bridge-mode)
- [Monitoring device bandwidth](https://support.eero.com/hc/en-us/articles/115000696666-How-do-I-see-how-much-bandwidth-my-devices-are-using)
- [What is eero Plus](https://support.eero.com/hc/en-us/articles/115002766063-What-is-eero-Plus)
- [eero Data Portability — third-party onboarding](https://support.eero.com/hc/en-us/articles/40180105962139-eero-Data-Portability-Third-Party-Onboarding)
- [yepher/eeroMonitor — API endpoint notes](https://github.com/yepher/eeroMonitor/blob/master/README.md)
- [schmittx/home-assistant-eero](https://github.com/schmittx/home-assistant-eero)
