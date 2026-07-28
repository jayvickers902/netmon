# CODEX-PLAN — netmon memory leak, round 2

**Builder:** Codex `gpt-5.6-luna`, high reasoning effort.
**Author:** Claude (Opus 5), 2026-07-27.
**Predecessor:** `CODEX-PLAN-memory-leak.md` — your round-1 fix. It was real but incomplete.

---

## 0. Safety constraints — unchanged from round 1, still binding

Uncommitted work in this repo is NOT yours and must survive untouched:
`CHANGELOG.md`, `ROADMAP.md`, `src/netmon/api/app.py`, `src/netmon/web/index.html`,
`tests/unit/test_api_edges.py`, and untracked `design/`, `src/netmon/web/dashboard.css`,
`src/netmon/web/dashboard.js`.

- **No** `git stash`, `checkout --`, `reset`, `clean`, or `commit`.
- **Do not** touch `app.py`. Round 1 correctly avoided it; keep that record.
- Round 1's fixes in `discovery.py` and `outage.py` are **correct — keep them.**
- **netmon is RUNNING as pid 31968 on 127.0.0.1:8787.** Do not kill, stop, or restart it.
  Do not bind 8787. Profile your own instance on 8799 with a temp data dir.
- Leave the tree dirty.

---

## 1. What round 1 achieved, and what it missed

Your `asyncio.gather()` fix was real and is not in question. Peak memory dropped from
**474 MB at 53 minutes** to **63 MB at startup**. Keep it.

But the process still grows **~4 MB/min, linearly, indefinitely**. Measured on the live
process, twice, five one-minute samples each:

```
before subnet scoping:  +3.76  +2.65  +3.88  +4.09  +4.43   MB/min
after  subnet scoping:  +4.67  +4.11  +3.88  +4.26  +3.43   MB/min
```

At that rate netmon reaches ~5.7 GB/day. The previous process hit **1,537 MB** before it was
restarted.

### The decisive evidence — read this carefully

I scoped discovery from **132,090 hosts to 1,022** (a 99.2% reduction) by adding
`subnets = ["192.168.4.0/22"]` to `netmon.toml`. Auto-detection had been enumerating the
NordLynx `10.5.0.0/16` and the link-local `169.254.0.0/16` from the Tailscale and OpenVPN
adapters — 131,068 addresses that can never respond.

Config load is **verified**, not assumed:

```
subnets     = ['192.168.4.0/22']
interval    = 300.0
concurrency = 32
```

**Growth did not change at all.** Therefore **the residual leak is not in discovery** and is
not proportional to host count.

Your own round-1 report contains the same signal, and it was misread — including by me:

> Four-host control: +38.28 MB.
> After fix, full auto-discovery: +38.78 MB, matching the control.

The four-host control was leaking at essentially the same rate as a full sweep. "Matches the
control" was taken as proof of success, when it actually proved the leak is independent of
discovery scale. **The control itself was leaking.** Do not repeat that inference: a
post-fix number is only meaningful against a baseline known to be flat.

---

## 2. Your job

Find and fix the ~4 MB/min allocator. **Profile. Do not reason from the outside — two
plausible hypotheses have already been wrong (mine, then mine again).**

Method:

1. Own instance on port 8799, temp data dir, `tracemalloc` enabled at startup.
2. Snapshot after warm-up (~3 min), again 15+ min later. `compare_to(..., 'lineno')[:25]`,
   then `'traceback'` grouping on the top few for full call chains.
3. **Establish a flat baseline first.** Run with collectors disabled (or an empty collector
   set) and confirm growth is ~0. If it is not flat with nothing collecting, the leak is in
   the scheduler, the store, the API layer, or startup — and that is a very different search.
4. Then enable collectors **one at a time** and measure each. The rate is linear and
   constant, so it correlates with a scheduled interval. Live intervals:
   `latency=10s` (3 targets x 5 probes), `discovery=300s`, plus dns/http/wan/host_nic/
   speedtest/eero/retention/alerts — read `config.py` for the real defaults.
5. Also diff `gc.get_objects()` type histograms between snapshots, and check `gc.garbage`.

Suspicion worth testing early, given the rate is unchanged across a 130x change in discovery
work: something per-*sample* or per-*probe-cycle* rather than per-host — e.g. samples
retained after `persist_samples`, an accumulating structure in the store, alert/notifier
state, or observation sinks holding references. `OutageStateMachine._states` and `._latest`
are keyed by target and should be bounded — verify that they actually are in practice, and
that `_recovery_started_at` and `_aggregate_targets` cannot grow.

---

## 3. Definition of done

1. **Named root cause with profiler evidence** — file, line, measured growth attributed to it.
2. **A flat baseline demonstrated**, then the fix demonstrated against it. Report the actual
   numbers. A fix is only proven if post-fix growth approaches the flat baseline, not merely
   "matches a control" — see §1.
3. Regression test that fails on old code and passes on new.
4. `uv run pytest` green, `uv run ruff check .` clean, `uv run mypy src` clean.
5. No behavioural change to the `/status` or `/events` API.
6. **Also fix:** `local_subnets()` must never auto-detect link-local `169.254.0.0/16`. It is
   never a valid scan target, and any machine with a VPN or virtual adapter hits this. Add a
   test. This is a genuine bug independent of the leak.

## 4. Report back

The root cause and its evidence; the flat-baseline number and the before/after; what you
ruled out and how; whether you touched any file with pre-existing uncommitted changes; and
anything in this brief that is wrong. **If you cannot find it, say so plainly** and report
the per-collector measurements from §2.4 so the search is narrowed for whoever picks it up.
A truthful negative with data beats a confident guess — round 1 shipped a real fix alongside
a wrong conclusion about whether the problem was solved, and that cost a cycle.
