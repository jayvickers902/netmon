# Spike — Linux gateway, and where throttling could actually be enforced

**Date:** 2026-07-26
**Supersedes:** the `docs/spike-throttle.md` placeholder in ROADMAP v0.4. This document is that
spike; the enforcement analysis is in §5.

**Question:** can a Linux gateway be run on hardware already present — WSL2 or a VM on this
Windows machine — and if not, what is the cheapest thing that works?

**Short answer:** WSL2 is not viable, a Hyper-V VM is technically viable and still a bad idea,
and a dedicated fanless N100 box costs about **$150–200 and 8–12 W** — roughly $12/year to run.
Your instinct that "always on" kills the WSL idea is correct, and for a second reason beyond
uptime.

---

## 1. WSL2 — no

WSL2 is a Hyper-V utility VM with a virtual NIC. Two networking modes exist and neither makes
it a router:

- **NAT (default):** the distro sits behind a host-managed NAT on a private subnet. Nothing on
  the LAN can route *through* it. Reaching even a single service from another machine requires
  hand-written `netsh interface portproxy` rules. Forwarding a household's traffic through that
  is not a supported configuration, it is a stunt.
- **Mirrored (Win11 22H2+):** WSL mirrors the *host's* interfaces. Better for reaching services,
  but it makes the distro share Windows' network identity and **share ports with Windows** —
  which is the opposite of what a gateway needs. A gateway must own two separate interfaces
  (WAN and LAN) and forward between them. Mirrored mode gives it neither.

Beyond networking, the disqualifiers are operational:

- WSL2 does not survive host sleep, hibernate, or a Windows Update reboot. Every one of those
  takes the entire house offline, on Microsoft's schedule rather than yours.
- The distro is a subsystem of a desktop session, not a service. `wsl --shutdown` — or a GPU
  driver crash, or a game locking the machine — becomes a household internet outage.
- There is no second physical NIC to give it.

**Verdict: no. Not "hard", not "unsupported" — architecturally the wrong shape.**

## 2. Hyper-V VM — possible, still no

This one genuinely works, and is worth understanding because it is the tempting middle option.

You would add a second physical NIC (USB 2.5GbE, ~$25), create two **external** virtual
switches — one bound to the WAN-side NIC, one to the LAN-side — and run OPNsense or Debian in a
VM with both attached. Hyper-V does not route between switches itself; the guest does it. That
is a real, working router.

The reasons not to:

- **Everything hangs off Windows' uptime.** Windows Update reboots, driver installs, blue
  screens, and sleep all become full-network outages. A router is the one device in the house
  that should have boring uptime.
- **Boot ordering.** The network is down until Windows has booted *and* the Hyper-V guest has
  started. If the host ever fails to come up unattended, nobody in the house has internet and
  the fix is physical.
- **You lose the ability to freely experiment on your own PC.** Rebooting your desktop to try
  something now has a blast radius that includes everyone else.
- **Contention.** Enabling Hyper-V puts Windows itself under the hypervisor, which has a real
  (small, but non-zero) cost on a machine you also use for latency-sensitive things.

**Verdict: it works, and you will regret it.** The whole point of routing is that it is
infrastructure. Infrastructure shouldn't share a power button with a workstation.

## 3. Dedicated hardware — what it actually costs

| Option | Price | Ports | Idle draw | Notes |
|---|---|---|---|---|
| **N100/N150 fanless mini PC, 4× i226-V 2.5GbE** | **$150–200** | 4× 2.5GbE | 6–12 W | The sweet spot. CWWK, MOGINSOK, Topton, Glovary — all the same reference design. 8 GB DDR5 + 128–256 GB NVMe included |
| Same, 6-port or with SFP+ | $250–400 | 6× 2.5GbE / 10G | 10–15 W | Overkill unless you have 10G upstream |
| Raspberry Pi 5 + USB 2.5GbE NIC | $80–130 | 1 built-in + 1 USB | 4–8 W | Cheapest that's credible. Pi 5 routes ~1 Gbps easily, ~3 Gbps with a PCIe NIC HAT. More assembly, more fiddling |
| Used Dell/HP Micro (Optiplex 3050/7050) + USB NIC | $60–110 | 1 + 1 USB | 10–20 W | Cheapest overall, but gigabit-only onboard and noisier |

**Running cost:** roughly $1/year per idle watt at US rates. The N100 box is **~$8–12/year**.

**Buy 2.5GbE, not gigabit.** Your eero 7 has 2.5GbE ports and supports plans up to 2.5 Gbps;
putting a gigabit-only gateway in front of it caps the whole house at 1 Gbps forever. The price
difference is negligible now and irreversible later.

**Recommendation: the ~$160 four-port N100 box.** Fanless, silent, 8 W, and it has enough spare
capacity to host netmon itself — which removes this Windows machine from the critical path
entirely.

## 4. Operating system

| OS | Case for it | Case against |
|---|---|---|
| **Debian (plain)** | netmon can drive `nftables` and `tc` directly, with no second product's API in between. Per-device accounting comes free from nftables counters. Full control | You build the router yourself. No web UI |
| **OpenWrt** | Turnkey, excellent SQM packages, tiny, `uci` config is scriptable | netmon integration means shelling over SSH into someone else's config layer |
| **OPNsense / pfSense** | Polished UI, real REST API, mature | FreeBSD — `tc`/`cake` knowledge doesn't transfer. Heavier. netmon would be a client of its API rather than the owner |

**Recommendation: Debian.** This project's entire premise is that netmon owns the measurement
and (eventually) the enforcement. Debian gives netmon a `throttle/linux_tc.py` backend that
manipulates the kernel directly, rather than a `throttle/opnsense_api.py` that begs another
product to do it.

## 5. What a gateway unlocks — the enforcement analysis

This is the section ROADMAP v0.4 asked for.

### Topology

```
ONT / modem ──► [ gateway: WAN nic ]
                [ gateway: LAN nic ] ──► eero (bridge mode) ──► Wi-Fi
```

The eero keeps doing what it is genuinely excellent at — Wi-Fi and roaming — and stops doing
routing, DHCP, and NAT.

### Enforcement options, ranked

| Option | Real shaping? | Hardware | Verdict |
|---|---|---|---|
| **Linux gateway, `tc` + `cake`/HTB per client IP** | **Yes** — true per-device up/down rate limits, and `cake`'s host fairness solves bufferbloat properly | $150–200 | **The correct answer.** Everything else is a compromise |
| **nftables + IFB for ingress shaping** | Yes, paired with the above | same | Needed because ingress shaping requires a redirect device; not optional, just detail |
| **eero profiles + schedules** | **No** — binary pause only | $0 | **Use this today.** It is not throttling, but "internet off for this profile at 10pm" covers most of the actual intent |
| **eero device priority / SQM** | Partially — fair-shares, cannot target one device | $0 | Turn SQM on regardless. It is not enforcement |
| **eero cloud API-driven pause** | No — same binary pause, just automated | $0 | Possible via the reverse-engineered API. Fragile, and the app already does it |
| **ARP-spoof interception** | Yes, technically | $0 | **Rejected.** Degrades reliability for the target, breaks under IPv6 and 802.11 client isolation, and is hostile to debug. Documented so it isn't re-proposed |
| **Router web-UI automation** | n/a | $0 | **Not applicable** — the eero has no web UI at all. Confirmed: port 80 serves a block-page redirect and nothing else |

### What netmon gains from a gateway, beyond throttling

1. **Per-device bandwidth, passively and locally** — no cloud, no reverse-engineered API, no
   subscription. The gateway sees every packet by definition; nftables counters per client IP
   are exact and cost nothing.
2. **Real WAN interface telemetry** — link state, carrier, byte counters from the kernel,
   replacing the UPnP query the eero will no longer answer.
3. **Full DNS visibility** — every lookup on the network, if you want it.
4. **netmon stops depending on this desktop** and becomes a service on infrastructure.

### What it costs

1. **The eero must go into bridge mode**, which removes UPnP, SQM, Thread, upstream IPv6,
   custom DNS, local DNS caching, and eero Plus eligibility (see
   [`eero-capabilities.md`](eero-capabilities.md) §4). SQM is replaced by `cake`, which is
   better. Thread is a genuine loss if you have Matter/Thread devices — **check this before
   committing.**
2. **netmon's `wan_status` collector needs a second backend.** The UPnP one stops working the
   moment the eero is bridged. This is a v1.1 work item, not a free swap.
3. **You now own a router.** A misconfiguration takes the house offline and the fix is at a
   keyboard in a closet.

### Recommendation

**Not yet.** v0.4 and v0.5 do not need it, and nothing about this decision gets cheaper by
being made early. Revisit when either (a) you actually want rate-limiting rather than pausing,
or (b) the eero cloud API path proves too fragile to rely on for per-device data.

When you do: **$160 four-port N100, Debian, eero bridged.** That is the whole answer.

---

## Sources

- [WSL2 networking modes — NAT and mirrored](https://danielcosenza.com/posts/wsl-networking-modes/)
- [Mirrored mode port-sharing limitations](https://github.com/microsoft/WSL/issues/11034)
- [Hyper-V routing between virtual switches](https://woshub.com/hyper-v-enable-routing/)
- [N100/N150 firewall appliance pricing](https://www.amazon.com/MOGINSOK-Firewall-Appliance-Computer-OPNsense/dp/B0CY8FLFVR)
- [CWWK fanless firewall hardware](https://cwwkpc.com/products/cwwk-firewall-mini-pc-core-i3-n305-n100-octa-core-4-x-2-5-gbe-i226v-lan-fanless-router-computer-ddr5-8-gb-ram-256-gb-nvme-ssd-micro-firewall-appliance-support-windows-opnsense-tf-card-slot-copy-1)
- [N100/N150 idle power measurements](https://bishalkshah.com.np/blog/low-power-homelab-n100-mini-pc)
- [Raspberry Pi 5 as a router](https://pidiylab.com/how-to-build-raspberry-pi-router/)
- [10 GbE on Raspberry Pi 5 via PCIe](https://www.jiribrejcha.net/2024/03/10-gigabit-ethernet-on-raspberry-pi-5/)
