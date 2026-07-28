# CODEX-PLAN — netmon memory leak

**Builder:** Codex `gpt-5.6-luna`, high reasoning effort.
**Diagnosis by:** Claude (Opus 5), 2026-07-27, from outside the repo.
**Goal:** netmon's resident memory grows without bound. Find the actual cause and fix it.

---

## 0. STOP — read this before touching anything

**This repo has uncommitted work in progress that is NOT yours and must survive untouched:**

```
 M CHANGELOG.md
 M ROADMAP.md
 M src/netmon/api/app.py
 M src/netmon/web/index.html
 M tests/unit/test_api_edges.py
?? design/
?? src/netmon/web/dashboard.css
?? src/netmon/web/dashboard.js
```

- **Do NOT** run `git stash`, `git checkout --`, `git reset`, `git clean`, or `git commit`.
- **Do NOT** revert, reformat, or "tidy" any of the files listed above. `app.py` is mid-edit by
  someone else — if your fix genuinely requires touching it, make the smallest possible
  addition and say so loudly in your report.
- A recovery point exists at tag `pre-codex-memory-fix` (tracked files only). Do not rely on it.
- Leave the tree dirty. Do not commit.

**netmon is RUNNING right now as pid 28028 on `127.0.0.1:8787`.** It is the user's highest
priority service.

- **Do NOT kill, stop, or restart pid 28028.**
- To profile, run **your own** instance on a **different port** (e.g. 8799) with its own
  temp data dir. Never bind 8787.

---

## 1. Measured evidence

All measured on the live pid 28028, which started 13:36 local.

| Observation | Value |
|---|---|
| Working set at ~53 min uptime | 474.5 MB |
| Private bytes at ~3 h uptime | 778.2 MB |
| Peak working set | **equals current** — memory is never released |
| Growth rate | **~4.4 MB/min ≈ 265 MB/hour, linear and constant** |
| Threads | 26 |
| Handles | 349 |
| `data/netmon.db` on disk | **3.6 MB** — so this is not data volume |

### Growth is NOT request-driven — this is measured, not assumed

A control experiment was run. An external client was polling `GET /health` every 5s; it was
then killed and the growth re-measured:

```
WITH   5s polling:  766.8 MB -> 772.2 MB   (+5.4 MB / 75s)
WITHOUT   polling:  772.5 MB -> 778.2 MB   (+5.7 MB / 75s)
```

**Identical.** The leak is in the background collector/scheduler path, not in request
handling, middleware, or the API layer. Do not spend time on FastAPI/uvicorn/request scope.

### Already ruled out by inspection — do not re-investigate

- **httpx clients** — every construction site uses `async with` and is properly closed:
  `probes/eero_cloud.py:46`, `probes/http.py:37`, `probes/speedtest.py:48`,
  `probes/upnp.py:92`, `probes/webhook.py:10`.
- **SQLite connections** — `db/store.py` is documented and implemented as
  "one short-lived connection per operation".
- **Scheduler tasks** — `scheduler.py` creates exactly one task per collector in `start()`
  and clears `_tasks` in `stop()`. Bounded.
- **The OUI table** — `probes/oui.py:21` reads the 1.2 MB `data/oui.csv` into a dict once at
  startup. That is a fixed baseline cost (tens of MB at most), not growth. Leave it alone
  unless profiling proves otherwise; it is load-bearing for device vendor identification.

---

## 2. Confirmed unbounded structures — fix these regardless

Found by inspection. **Arithmetic says neither is large enough to explain 265 MB/hour**, so
they are almost certainly *not* the main leak — but both are real correctness bugs that will
consume unbounded memory over weeks, and both should be fixed.

### 2.1 `OutageStateMachine._wan_history` — `outage.py:118`

```python
self._wan_history: list[_WanObservation] = []          # line 118
...
self._wan_history.append(_WanObservation(status, ts, tuple(positive_reasons)))   # line 186
```

**Appended on every WAN observation. Never popped, trimmed, or bounded.** The only reader is
line ~350-358, which filters:

```python
for item in self._wan_history
if item.positive_reasons
and (outage_since is None or item.ts >= outage_since)
and item.ts <= now
```

So the read path can **only ever** return entries that (a) have non-empty `positive_reasons`
and (b) are newer than the current outage start. Every entry with empty `positive_reasons` —
i.e. the healthy steady state, the overwhelming majority — is retained forever and can never
be read. Entries older than the current outage are equally dead.

**Fix:** either skip the append entirely when `positive_reasons` is empty, or prune to a
bounded window on append. Note `self._wan_stale_after_seconds` (line 119-121) already exists
as a retention concept — prefer reusing it over inventing a new knob. Preserve the existing
semantics of the `wan_evidence` API output exactly; there are tests over it.

### 2.2 `_event_details` — `outage.py:430`

```python
self._event_details: dict[int, dict[str, Any]] = {}
```

Written at lines 447 and 486, read at 458, 466, 484. **No entry is ever deleted.** One dict
per outage event, retained for the process lifetime. Bound it (evict on event close, or cap
with an LRU keyed by event id) without changing the details returned for the currently open
event.

---

## 3. Your actual job: find the 265 MB/hour

**Do not guess. Measure.** The candidates above are ruled out or too small; the real
allocator is unidentified and must be found empirically.

Suggested method — adapt freely, but the deliverable is *evidence*, not a hypothesis:

1. Run your own netmon instance on port 8799 with a temp data dir and the same
   `netmon.example.toml`-derived config the live one uses.
2. Enable `tracemalloc` at startup. Take a snapshot after warm-up (~2 min, so the OUI load
   and first collector pass settle), then again 10-15 min later.
3. `snapshot2.compare_to(snapshot1, 'lineno')[:25]` — the top allocation sites by growth.
   Also try `'traceback'` grouping for the top 3 to get full call chains.
4. Correlate against collector intervals. The rate is *linear and constant*, which points at
   a frequently-scheduled collector rather than a rare event. Config intervals present:
   `5`, `10`, `10`, `30`, `60`, `60`, `60`, `300`, `21600` seconds. A ~4.4 MB/min leak means
   roughly 0.4 MB per run of a 5s collector, or 4.4 MB per run of a 60s one.
5. Also check `gc.get_objects()` type histograms between snapshots, and whether
   `gc.garbage` is non-empty (reference cycles with `__del__`).

Areas worth a look once profiling points somewhere — **as leads, not conclusions**:
`collectors/latency.py`, `collectors/discovery.py`, `collectors/host_nic.py` (psutil
objects), `probes/arp.py`, `probes/icmp.py`, and the `alerts/` notifier path.

**26 threads for an asyncio service is high** — worth confirming that count is stable over
time and not itself growing. A thread leak would show as both memory and handle growth.

---

## 4. Definition of done

1. **A named root cause with profiler evidence in your report** — file, line, and the
   measured allocation growth attributed to it. If you cannot find it, say so plainly and
   report what you ruled out and how; a truthful negative is worth more than a guess.
2. The leak is fixed, and you have **demonstrated** the fix: same profiling procedure,
   before/after growth rate over a comparable interval, with numbers in the report.
3. §2.1 and §2.2 are bounded regardless of whether they were the main leak.
4. **A regression test** that fails on the old code and passes on the new — e.g. drive the
   state machine through N thousand observations and assert the retained structure stays
   bounded. Do the same for whatever §3 turns up, if it is testable without a live network.
5. `uv run pytest` fully green, `uv run ruff check .` clean. The existing tests over
   `wan_evidence` and outage classification must still pass unchanged — if a test needs
   modifying, that is a signal you changed behaviour, so justify it explicitly.
6. No behavioural change to the `/status` or `/events` API output. This is a memory fix, not
   a feature change.

## 5. Report back

State plainly: the root cause and the evidence for it; before/after growth numbers; what you
ruled out; whether you had to touch `app.py` or any other file with pre-existing
uncommitted changes; and anything in this brief that turned out to be wrong. I diagnosed
this from outside the repo without running a profiler — if my reasoning in §1 or §2 does not
survive contact with the code, say so rather than bending the fix to match it.
