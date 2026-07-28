# Investigation mode — build spec

**Status:** approved, ready to build. Lands under ROADMAP v0.6 ("on-demand diagnostics runner",
the last unchecked item). Not a new phase.

## The question this answers

The operator has a **highly asymmetric link: ~10 Mbps up, fast down**. When upload saturates,
everything lags. When the internet misbehaves they need to distinguish, within minutes:

1. **Congestion** — someone on the LAN is filling the 10 Mbps uplink (prime suspect: `owens-pc`)
2. **Local link** — Wi-Fi / mesh / NIC problem between this host and the eero ("hardware")
3. **Upstream** — the WAN link or the ISP

Today netmon collects every signal needed to tell these apart, but at cadences tuned for
24/7 background operation (latency 10s, eero 60s, managed hosts 30s) and with no way to ask it
"look hard, right now, for the next fifteen minutes, and then tell me who did it."

**Investigation mode** is that button.

## Shape

A time-boxed, in-memory state flag that (a) raises the poll rate of the collectors that carry
diagnostic signal, (b) suppresses the one collector that would corrupt the measurement, and
(c) on expiry runs a deterministic classifier over the samples in the window and stores a verdict.

Deliberately **not** a new data pipeline. Samples land in the existing `sample`/`metric` tables
exactly as they always do. An investigation is just a named time window plus a verdict computed
over it. That means the analysis is reproducible, re-derivable if the classifier improves, and
requires zero change to the persistence hot path.

---

## 1. Evidence sources (all already exist — nothing new is collected)

| Signal | Metric kind | Role in the verdict |
|---|---|---|
| Gateway RTT / loss | `latency_rtt_ms`, `latency_loss` @ `gateway` | **The hardware discriminator.** Bad gateway RTT = local Wi-Fi/NIC/mesh problem. Good gateway RTT while internet RTT is bad = the problem is not in this house. |
| Internet RTT / jitter / loss | same @ `1.1.1.1`, `8.8.8.8` | The symptom being explained. |
| WAN uptime / last error | `wan_uptime_seconds`, `wan_status` events | Ground truth from the router. Uptime moving **backwards** during the window = the link genuinely dropped → upstream, not congestion. |
| Per-device upload | `eero_device_upload_rate` (source `eero_cloud`) | The **who**. Free-tier eero cloud, 30s floor. |
| owens-pc NIC | `managed_host_nic_bytes_sent_rate` (source `managed_host_ssh`) | Independent, local, non-cloud corroboration for the prime suspect. |
| This host NIC | `host_nic_bytes_sent_rate` | Rules the operator's own machine in or out. |

**Total WAN upload is not directly measurable on this topology** (eero implements no UPnP byte
counters — `docs/spike-upnp.md`). Total upload is therefore **estimated as the sum of per-device
eero rates**, and every report must label it as an estimate with a stated coverage figure. Do not
present it as ground truth.

## 2. Non-negotiable constraints

These are existing project invariants. An implementation that violates one is wrong even if the
tests pass.

- **eero polls never go below 30s.** ROADMAP v0.4 non-negotiable, against an undocumented,
  unrate-limited endpoint. Burst interval for eero is **exactly 30s**, not lower. Enforce with a
  pydantic validator (`ge=30.0`), not a comment.
- **eero fails soft, always.** An expired token or a dead cloud degrades the report's confidence
  and coverage. It never raises, never stalls the scheduler, never blocks an investigation from
  running or concluding.
- **Speedtest is suppressed while an investigation is active.** It saturates the uplink by design.
  Letting it run inside the measurement window would manufacture the exact "upload saturation"
  verdict being tested for. This is a correctness requirement, not a nicety.
- **All I/O and all clock reads stay injected.** `AGENTS.md`, the rule that matters most.
  `InvestigationState` takes a `Clock`; nothing calls `time.time()`.
- **No new interception.** No ARP spoofing, no packet capture, no traffic shaping. Permanently
  out of scope per `AGENTS.md` §Scope discipline.
- **Samples are never tagged with an investigation id.** The window is `[started_at, ended_at]`;
  the analyser queries by time. Keeps the write path untouched.

## 3. Config — new `[investigation]` block

```toml
[investigation]
enabled = true
default_duration_seconds = 900        # 15 min
max_duration_seconds = 3600           # hard server-side ceiling
uplink_mbps = 10.0                    # the asymmetry that makes this whole feature necessary
downlink_mbps = 500.0                 # informational only
saturation_fraction = 0.8             # >= 8 Mbps of 10 counts as saturated
latency_degraded_ms = 50.0            # internet RTT above baseline to count as degraded
gateway_degraded_ms = 30.0            # gateway RTT above baseline => local link, not congestion
loss_degraded_fraction = 0.05
bucket_seconds = 10.0                 # correlation bucket width
suppress_speedtest = true
auto_arm = false                      # opt-in; default OFF
auto_arm_duration_seconds = 600
auto_arm_cooldown_seconds = 1800

[investigation.burst_intervals]
latency = 2.0                         # floor 1.0
dns = 30.0
http = 30.0
wan = 10.0
host_nic = 2.0
eero = 30.0                           # FLOOR — validator must reject anything lower
managed_hosts = 10.0                  # floor 5.0; SSH round-trip to Windows needs headroom
```

`uplink_mbps` falls back to the newest `speedtest_upload` sample when set to `0`. If neither
exists, saturation verdicts are unavailable and the report says so explicitly rather than guessing.

## 4. Runtime mechanics

### `InvestigationState`

A single injected, in-memory object. `Scheduler._run` re-reads `collector.interval` on every
loop iteration, so a dynamic `interval` property is picked up automatically with **no scheduler
change and no restart**.

```python
class InvestigationState:
    def __init__(self, clock: Clock) -> None: ...
    @property
    def active(self) -> bool: ...  # started and not yet expired
    def start(self, investigation_id: int, duration_seconds: float) -> None: ...
    def stop(self) -> None: ...
    def snapshot(self) -> InvestigationWindow | None: ...
```

Expiry is computed from `clock.now()` on read — no timer task, nothing to leak, and a frozen
clock in tests drives it exactly.

### Burst-aware collectors

Each participating collector gains two optional constructor arguments, both defaulting to `None`
so every existing call site and test keeps working unchanged:

```python
def __init__(self, ..., investigation: InvestigationState | None = None,
             burst_interval_seconds: float | None = None) -> None: ...

@property
def interval(self) -> float:
    if self.investigation is not None and self.investigation.active \
            and self.burst_interval_seconds is not None:
        return self.burst_interval_seconds
    return self.config.interval_seconds
```

Applies to: `latency`, `dns_probe`, `http_probe`, `wan_status`, `host_nic`, `eero_usage`,
`managed_host`. **Not** discovery (a 300s subnet sweep has no diagnostic value here and bursting
it would flood the LAN) and **not** retention or alerts.

`SpeedtestCollector` gets the inverse treatment: when `investigation.active` and
`suppress_speedtest`, `collect()` returns `[]` immediately and logs the skip.

**Known limitation, to be documented rather than engineered around:** a collector already sleeping
when an investigation starts finishes that sleep first. Burst cadence therefore takes effect
within one current interval — ≤10s for latency, ≤60s for eero. For a 15-minute window this is
acceptable. Do **not** add sleep-interruption machinery; racing an `asyncio.Event` against the
injected clock would break `FrozenClock` and the hermetic test suite.

## 5. Persistence

Forward-only migration, version 3, following the existing pattern in `db/migrations.py`:

```sql
CREATE TABLE investigation (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at   REAL NOT NULL,
  ends_at      REAL NOT NULL,      -- scheduled expiry
  ended_at     REAL,               -- actual end; NULL while running
  label        TEXT,
  note         TEXT,
  trigger      TEXT NOT NULL,      -- 'manual' | 'auto_arm'
  status       TEXT NOT NULL,      -- 'running' | 'complete' | 'cancelled'
  verdict_json TEXT                -- snapshot, written once on completion
);
CREATE INDEX investigation_started_at ON investigation (started_at DESC);
```

`verdict_json` is a **snapshot taken at completion**, not a cache. Raw samples are purged after
7 days by retention; without the snapshot every investigation older than a week would silently
lose its conclusion. Reports for still-live raw data are recomputed on read so classifier
improvements apply retroactively.

Store methods: `create_investigation`, `active_investigation`, `list_investigations`,
`get_investigation`, `complete_investigation`, `cancel_investigation`.

## 6. The classifier — `src/netmon/analysis/upload_contention.py`

**Pure functions. No I/O, no store, no clock.** Takes lists of samples and a config; returns a
dataclass. This is the heart of the feature and must be exhaustively unit-tested against
synthetic series.

### Baseline

Degradation is relative to a quiet reference. Because an investigation is usually started *while
things are already bad*, the baseline must **not** come from the window itself:

1. **Preferred:** p50 of `latency_rtt_ms` over the trailing 24h *excluding* the investigation window.
2. **Fallback:** p50 of the first 60s of the window.
3. **Neither available:** report `inconclusive` with `"no latency baseline"` in `caveats`.

### Degraded-window detection

Bucket the window into `bucket_seconds` (10s) buckets. A bucket is *degraded* when
`internet_rtt_p95 >= baseline + latency_degraded_ms` or `loss >= loss_degraded_fraction`.

### Verdict rules — evaluate strictly in this order

1. `wan_uptime_seconds` decreased during the window, or a `wan_reconnect` event landed in it
   → **`wan_upstream`**, confidence `high`. The router says the link actually dropped.
2. Gateway RTT p95 ≥ gateway baseline + `gateway_degraded_ms`, **or** gateway loss ≥
   `loss_degraded_fraction` → **`local_link`**, confidence `high`.
   This is the "it's your hardware/Wi-Fi" answer. It ranks above congestion because a broken
   first hop makes every downstream measurement untrustworthy.
3. Degraded buckets exist **and** estimated total upload ≥ `saturation_fraction × uplink_bps` in
   **at least half of the degraded buckets** → **`upload_saturation`**.
   The temporal-overlap requirement is essential: a large upload burst that happened at a
   *different* moment than the lag does not explain the lag, and must not be blamed for it.
4. Degraded buckets exist, upload was low, gateway was clean → **`wan_upstream`**,
   confidence `medium` (upstream congestion or a far-end problem).
5. No degraded buckets at all → **`healthy`** — "nothing was wrong during this window."
   A real and useful answer; say it plainly rather than dressing it up.
6. Otherwise → **`inconclusive`**, with the reasons it could not decide.

### Attribution — only computed for `upload_saturation`

Per candidate device (eero devices, managed hosts, this host):

- `mean_upload_bps`, `peak_upload_bps` within the window
- `share_of_upload` — fraction of estimated total upload **during degraded buckets only**
- `correlation_with_latency` — **Spearman** rank correlation between the device's upload series
  and the internet RTT series, bucket-aligned. Spearman, not Pearson: the RTT-vs-load relationship
  under bufferbloat is monotonic but sharply non-linear, and Pearson understates it badly.
- `seconds_covered` — how much of the window actually had samples for this device

Rank by `share_of_upload`, tie-break on correlation. Report the top contributors, not just one.

### Confidence and coverage — the honesty requirement

eero polls at 30s; degraded buckets are 10s. **Many degraded buckets will have no per-device
sample at all.** The report must carry:

```python
coverage = {
    "eero_available": bool,
    "managed_hosts_available": list[str],
    "degraded_buckets": int,
    "degraded_buckets_with_device_data": int,
    "device_data_fraction": float,
}
```

Confidence downgrades to `low` when `device_data_fraction < 0.5`, and attribution is reported as
*suggestive* rather than conclusive. A confident-sounding accusation built on two samples is worse
than no answer — this feature exists to settle an argument about a person in the house, and it has
to be right about how sure it is.

### Output

```python
@dataclass(frozen=True, slots=True)
class InvestigationReport:
    investigation_id: int
    started_at: datetime
    ended_at: datetime
    verdict: str  # upload_saturation|local_link|wan_upstream|healthy|inconclusive
    confidence: str  # high|medium|low
    headline: str  # one plain-English sentence
    reasons: list[str]  # evidence bullets, each independently checkable
    caveats: list[str]
    baseline_rtt_ms: float | None
    degraded_rtt_p95_ms: float | None
    gateway_rtt_p95_ms: float | None
    degraded_seconds: float
    total_seconds: float
    peak_upload_bps: float | None
    uplink_capacity_bps: float | None
    peak_utilisation: float | None
    attributions: list[DeviceAttribution]
    coverage: dict[str, Any]
```

`headline` is what the dashboard shows first. Target register:

> "Upload saturation. owens-pc accounted for 78% of upload during the 6 minutes your latency was
> bad. Not hardware — gateway latency stayed normal and the WAN link never dropped."

## 7. REST surface

| Method | Path | Behaviour |
|---|---|---|
| `POST` | `/investigations` | `{duration_seconds?, label?, note?}`. `409` if one is already active. `422` above `max_duration_seconds`. Returns the record. |
| `GET` | `/investigations` | Newest first, paginated by `limit`. |
| `GET` | `/investigations/active` | The running one or `null`. Dashboard polls this. |
| `GET` | `/investigations/{id}` | The record. |
| `GET` | `/investigations/{id}/report` | Computed report. Live for a running investigation; recomputed from raw samples while they exist; falls back to `verdict_json`. |
| `POST` | `/investigations/{id}/stop` | End early, compute and store the verdict. |

Auth follows the existing middleware — no special-casing. Update `tests/contract/openapi_snapshot.json`.

## 8. Dashboard

New `/investigate` route, following the existing `paths`/`NAV`/`screens` structure in
`dashboard.js`. Reuse `dashboard.css` primitives (`panel`, `chip`, `stat-note`, `status-dot`,
`reason-detail`) — no new design language, no external libraries.

- **Idle:** "Start investigation" with a 5 / 15 / 30 / 60 min duration picker, and a plain
  statement of what it will do (raise poll rates, pause the speedtest, watch for N minutes).
- **Running:** countdown, live internet-RTT sparkline against a total-upload trace with the
  10 Mbps capacity line drawn in, and a per-device upload list. Poll `/investigations/active`.
- **Complete:** verdict card led by `headline`, then `reasons`, then the attribution table, then
  `coverage` and `caveats` rendered honestly — greyed, but present and readable, never hidden
  behind a disclosure.
- **History:** past investigations with verdict and timestamp, click through to the report.

## 9. Auto-arm (opt-in, default off)

`collectors/investigation_arm.py` — watches the existing `OutageCoordinator` degradation signal
and opens a `trigger='auto_arm'` investigation when it fires. Reuses the outage state machine's
hysteresis; does **not** introduce a second detector. Respects `auto_arm_cooldown_seconds` and
never starts one while another is active.

Rationale: the same argument that put auto-traceroute in v0.3 — capture the evidence at the moment
things break, not twenty minutes later when someone finally opens a browser.

## 10. Testing

Per `AGENTS.md`: hermetic unit tests, real temporary SQLite for integration, OpenAPI snapshot for
contract, 80% coverage gate on `src/netmon/`, `mypy --strict` and `ruff` clean.

Cases that must exist:

- Burst interval applies while active and reverts on expiry, driven by `FrozenClock`
- eero burst below 30s is a config **validation error**
- Speedtest returns `[]` while an investigation is active
- Each of the six verdict branches, from synthetic sample series
- Upload burst that does **not** temporally overlap the lag → **not** `upload_saturation`
- eero unavailable → `low` confidence, coverage reflects it, no crash, verdict still produced
- Second `POST /investigations` while one is active → `409`
- Duration above the ceiling → `422`
- Expiry writes `verdict_json`; report survives raw-sample purge
- Migration v3 applies to an existing v2 database without data loss
