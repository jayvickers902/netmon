(() => {
  "use strict";

  const config = window.NETMON_CONFIG || {};
  const app = document.querySelector("#app");
  const paths = {
    "/": "now", "/timeline": "timeline", "/devices-view": "devices",
    "/diagnostics": "diagnostics", "/bandwidth": "bandwidth",
    "/alerts-view": "alerts", "/settings": "settings", "/investigate": "investigate",
  };
  const nav = [
    ["now", "◉", "Now", "/"], ["timeline", "⌁", "Timeline", "/timeline"],
    ["devices", "⌬", "Devices", "/devices-view"], ["diagnostics", "≈", "Diagnostics", "/diagnostics"],
    ["bandwidth", "⇅", "Bandwidth", "/bandwidth"], ["alerts", "◬", "Alerts", "/alerts-view"],
    ["settings", "⊟", "Settings", "/settings"], ["investigate", "!", "Investigate", "/investigate"],
  ];
  const colors = {gateway:"#4af6c3","1.1.1.1":"#d08c60","8.8.8.8":"#58b8f0"};
  const state = {
    route: paths[location.pathname] || "now", status: null, devices: [], events: [],
    metrics: [], alerts: [], health: false, errors: {}, loaded: false, editingDeviceId: null,
    investigation: null, investigationReport: null, investigations: [], investigationDuration: 900,
  };
  const chartRegistry = new Map();
  let chartSerial = 0;

  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, char => (
    {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char]
  ));
  const finite = value => Number.isFinite(Number(value));
  const fmt = (value, digits = 1) => finite(value) ? Number(value).toFixed(digits) : "—";
  const stamp = seconds => {
    if (!finite(seconds)) return "—";
    return new Date(Number(seconds) * 1000).toLocaleTimeString([], {hour:"2-digit", minute:"2-digit", second:"2-digit"});
  };
  const ago = value => {
    if (!value) return "never";
    const seconds = Math.max(0, (Date.now() - new Date(value).getTime()) / 1000);
    if (seconds < 60) return `${Math.floor(seconds)}s`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)}h`;
    return `${Math.floor(seconds / 86400)}d`;
  };
  const duration = seconds => {
    if (!finite(seconds)) return "—";
    const n = Number(seconds);
    if (n < 60) return `${Math.round(n)}s`;
    if (n < 3600) return `${Math.floor(n / 60)}m ${Math.round(n % 60)}s`;
    return `${Math.floor(n / 3600)}h ${Math.floor((n % 3600) / 60)}m`;
  };
  const bytes = value => {
    if (!finite(value)) return "—";
    const units = ["B/s","KB/s","MB/s","GB/s"]; let n = Number(value), i = 0;
    while (n >= 1000 && i < units.length - 1) { n /= 1000; i += 1; }
    return `${n.toFixed(i > 1 ? 2 : 0)} ${units[i]}`;
  };
  const latest = (kind, target) => {
    const rows = state.metrics.filter(row => row.kind === kind && (!target || row.target === target));
    return rows.at(-1);
  };
  const hitronSeries = (kind, target) => state.metrics.filter(row =>
    row.source === "hitron" && row.kind === kind && (!target || row.target === target) && finite(row.value)
  );
  const latestHitron = (kind, target) => hitronSeries(kind, target).at(-1);
  const series = (kind, target, limit = 180) =>
    state.metrics.filter(row => row.kind === kind && (!target || row.target === target)).slice(-limit);
  const percentile = (rows, p) => {
    const values = rows.map(row => Number(row.value)).filter(Number.isFinite).sort((a,b) => a-b);
    if (!values.length) return null;
    return values[Math.min(values.length - 1, Math.floor((values.length - 1) * p))];
  };
  const chartTime = (seconds, span = 0) => {
    if (!finite(seconds)) return "—";
    const date = new Date(Number(seconds) * 1000);
    const time = date.toLocaleTimeString([], {hour:"2-digit", minute:"2-digit", second:"2-digit"});
    return span >= 86400
      ? `${date.toLocaleDateString([], {month:"short", day:"numeric"})} ${time}`
      : time;
  };
  const sumByTimestamp = rows => [...rows.reduce((buckets, row) => {
    const ts = Number(row.ts), value = Number(row.value);
    if (Number.isFinite(ts) && Number.isFinite(value)) buckets.set(ts, (buckets.get(ts) || 0) + value);
    return buckets;
  }, new Map()).entries()].map(([ts,value]) => ({ts,value})).sort((a,b) => a.ts - b.ts);
  const nearestRow = (rows, ts) => rows.reduce((nearest, row) =>
    !nearest || Math.abs(Number(row.ts) - ts) < Math.abs(Number(nearest.ts) - ts) ? row : nearest
  , null);
  const interactiveChart = ({
    datasets, unit = "", valueFormatter = value => `${fmt(value)}${unit ? ` ${unit}` : ""}`,
    floor = null, ceiling = null, height = 120, reference = null, ariaLabel = "Metric history",
  }) => {
    const clean = datasets.map(dataset => ({
      ...dataset,
      rows: dataset.rows.filter(row => finite(row.ts) && finite(row.value)),
    })).filter(dataset => dataset.rows.length);
    const allRows = clean.flatMap(dataset => dataset.rows);
    if (!allRows.length) return `<div class="chartbox chart-empty">No samples in this window.</div>`;
    const times = allRows.map(row => Number(row.ts));
    const values = allRows.map(row => Number(row.value));
    const minTs = Math.min(...times), maxTs = Math.max(...times), timeRange = Math.max(maxTs - minTs, 1);
    const min = floor ?? Math.min(...values);
    const observedMax = ceiling ?? Math.max(...values, Number(reference?.value || -Infinity));
    const max = observedMax > min ? observedMax : min + Math.max(Math.abs(min) * .1, 1);
    const valueRange = Math.max(max - min, .001);
    const width = 600, padLeft = 58, padRight = 12, padTop = 10, padBottom = 24;
    const plotWidth = width - padLeft - padRight, plotHeight = height - padTop - padBottom;
    const x = ts => padLeft + (Number(ts) - minTs) / timeRange * plotWidth;
    const y = value => padTop + (max - Number(value)) / valueRange * plotHeight;
    const paths = clean.map(dataset => {
      const d = dataset.rows.map((row,index) =>
        `${index ? "L" : "M"}${x(row.ts).toFixed(1)} ${y(row.value).toFixed(1)}`
      ).join("");
      return `<path d="${d}" fill="none" stroke="${dataset.color}" stroke-width="${dataset.width || 1.7}" vector-effect="non-scaling-stroke"/>`;
    }).join("");
    const referenceLine = reference && finite(reference.value)
      ? `<path d="M${padLeft} ${y(reference.value).toFixed(1)}H${width-padRight}" stroke="${reference.color || "#d8a657"}" stroke-width="1.3" stroke-dasharray="5 4" vector-effect="non-scaling-stroke"/>`
      : "";
    const id = `chart-${++chartSerial}`;
    const timestamps = [...new Set(allRows.map(row => Number(row.ts)))].sort((a,b) => a-b);
    chartRegistry.set(id, {datasets:clean, minTs, maxTs, timeRange, valueFormatter, timestamps});
    const middleTs = minTs + timeRange / 2;
    const yTicks = [max, min + valueRange / 2, min];
    return `<div class="chart-wrap">
      <div class="chart-legend">${clean.map(dataset => `<span><i style="background:${dataset.color}"></i>${esc(dataset.label)}</span>`).join("")}${reference && finite(reference.value) ? `<span><i class="reference-swatch" style="--reference-color:${reference.color || "#d8a657"}"></i>${esc(reference.label || "Reference")}</span>` : ""}<span class="chart-hint">hover or focus to inspect</span></div>
      <div class="chartbox interactive-chart" id="${id}" data-chart-id="${id}" tabindex="0" role="img" aria-label="${esc(ariaLabel)}" style="--chart-height:${height}px">
        <svg class="chart" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" aria-hidden="true">
          <path d="M${padLeft} ${padTop}H${width-padRight}M${padLeft} ${yTicks[1] === min ? padTop : y(yTicks[1]).toFixed(1)}H${width-padRight}M${padLeft} ${height-padBottom}H${width-padRight}" stroke="#1b2430" vector-effect="non-scaling-stroke"/>
          ${referenceLine}${paths}<line class="chart-crosshair" x1="${padLeft}" x2="${padLeft}" y1="${padTop}" y2="${height-padBottom}"/>
        </svg>
        <div class="chart-y-axis">${yTicks.map(value => `<span>${esc(valueFormatter(value))}</span>`).join("")}</div>
        <div class="chart-x-axis"><span>${esc(chartTime(minTs,timeRange))}</span><span>${esc(chartTime(middleTs,timeRange))}</span><span>${esc(chartTime(maxTs,timeRange))}</span></div>
        <div class="chart-tooltip" role="status"></div>
      </div>
    </div>`;
  };
  const metricTargets = kind => [...new Set(state.metrics.filter(row => row.kind === kind).map(row => row.target))];
  // MAC-keyed samples (e.g. eero_device_upload_rate) carry no name of their own; look one up
  // from the already-fetched device list rather than showing the bare MAC.
  const deviceLabelFor = mac => {
    const normalized = String(mac).toLowerCase();
    const device = state.devices.find(item => String(item.mac || "").toLowerCase() === normalized);
    return device?.label || device?.eero_name || device?.hostname || device?.vendor || mac;
  };

  function healthView() {
    const status = state.status || {verdict:"unknown", open:false, targets:{}, wan:null};
    const targets = Object.values(status.targets || {});
    const pending = targets.some(item => !item.open && Number(item.failures) > 0);
    const mode = status.open ? "outage" : pending ? "degrading" : status.verdict === "unknown" ? "unknown" : "healthy";
    const tone = mode === "outage" ? "#f4584f" : mode === "degrading" || mode === "unknown" ? "#d8a657" : "#4af6c3";
    const glyph = mode === "outage" ? "✕" : mode === "degrading" ? "~" : mode === "unknown" ? "?" : "✓";
    return {status, targets, pending, mode, tone, glyph};
  }

  function shell(content) {
    const h = healthView();
    const topWord = h.mode === "degrading" ? "onset" : h.status.verdict || "unknown";
    const caption = h.mode === "degrading"
      ? "settled verdict · raw signal is already failing"
      : h.status.since ? `open · since ${new Date(h.status.since).toLocaleTimeString()}`
      : h.mode === "healthy" ? "all configured paths answering" : "waiting for enough evidence";
    return `<div class="shell">
      <aside class="rail">
        <div class="brand"><span class="brand-mark">△</span><b class="brand-name">netmon</b><span class="version">v${esc(config.version || "1.0")}</span></div>
        <nav class="nav" aria-label="Primary">${nav.map(([id,icon,label,path]) =>
          `<a class="nav-item ${state.route === id ? "active" : ""}" data-route="${id}" href="${path}"><span class="nav-icon">${icon}</span><span>${label}</span></a>`
        ).join("")}</nav>
        <div class="rail-foot"><div class="service"><span class="status-dot ${state.health ? "" : "down"}"></span><span>service ${state.health ? "up" : "unreachable"}</span></div>
          <div>${state.metrics.length.toLocaleString()} samples loaded</div><div>eero 7 · no local api</div></div>
      </aside>
      <main class="main">
        <header class="topbar" style="--tone:${h.tone}"><div class="verdict-pill"><span class="verdict-dot"></span><span class="verdict-label">${esc(topWord)}</span></div><span class="top-caption">${esc(caption)}</span></header>
        ${content}
        <footer class="footer"><span>netmon · ${esc(location.host)}</span><span>${config.api?.allow_loopback_unauthenticated ? "loopback exempt · no login by design" : "authentication required"}</span><span class="push">evidence console · labels editable</span></footer>
      </main>
    </div>`;
  }

  function panel(title, body, meta = "", classes = "") {
    return `<section class="panel ${classes}"><div class="panel-head"><h2 class="panel-title">${title}</h2>${meta ? `<span class="panel-meta">${meta}</span>` : ""}</div>${body}</section>`;
  }

  function nowScreen() {
    const h = healthView(), s = h.status;
    const online = state.devices.filter(device => device.online).length;
    const headline = h.mode === "healthy"
      ? "Gateway and external paths are replying. DNS and HTTP probes are being checked independently. Nothing has been sustained long enough to open an event."
      : h.mode === "degrading"
        ? "A raw probe is failing, but hysteresis has not confirmed an outage yet. The settled verdict remains healthy while netmon waits for sustained evidence."
        : h.mode === "outage"
          ? `${String(s.verdict).replaceAll("_"," ")} is open. The verdict below stays attached to the signals that produced it.`
          : "Collectors are starting. Netmon will not claim health until the first independent signals arrive.";
    const hero = `<section class="panel hero" style="--tone:${h.tone}"><div class="hero-inner">
      <div class="bloom"><span class="bloom-ring"></span><span class="bloom-ring"></span><span class="bloom-ring"></span><span class="bloom-core"></span><span class="bloom-glyph">${h.glyph}</span></div>
      <div class="hero-copy"><div class="eyebrow">/status · verdict</div><div class="hero-verdict">${esc(s.verdict || "unknown")}</div><div class="hero-headline">${esc(headline)}</div>
      <div class="chips"><span class="chip">since <strong>${s.since ? ago(s.since) : "startup"}</strong></span><span class="chip">wan ip <strong>${esc(s.wan?.external_ip || "unavailable")}</strong></span><span class="chip"><strong>${online}</strong> devices online</span></div></div>
    </div></section>`;
    const probes = liveProbes(h);
    const confidence = confidencePanel(h);
    const clocks = twoClocks(h);
    return `<div class="screen">${hero}<div class="grid-2">${probes}${confidence}</div>${clocks}</div>`;
  }

  function liveProbes(h) {
    const configured = config.latency?.targets || ["gateway","1.1.1.1","8.8.8.8"];
    const body = configured.map(target => {
      const key = Object.keys(h.status.targets || {}).find(item => item === `icmp:${target}`);
      const health = key ? h.status.targets[key] : {};
      const rows = series("latency_rtt_ms", target, 70);
      const loss = latest("latency_loss_percent", target)?.value;
      const value = latest("latency_rtt_ms", target)?.value;
      const fail = Math.min(Number(health.failures || 0), Number(config.outage?.failures_to_open || 3));
      const color = health.open ? "#f4584f" : fail ? "#d8a657" : colors[target] || "#4af6c3";
      return `<div class="probe" style="--probe:${color}"><div class="probe-row"><span class="status-dot" style="background:${color}"></span><span class="probe-name">${esc(target)}</span><span class="probe-proto">icmp</span><span class="probe-value">${value > 0 ? fmt(value) : "—"}</span><span class="unit">ms</span></div>
        ${interactiveChart({datasets:[{label:`${target} RTT`,color,rows}],unit:"ms",height:76,ariaLabel:`${target} latency history`})}
        <div class="hyst"><span>hysteresis</span>${[0,1,2].map(i => `<i class="pip ${i < fail ? "fail" : ""}"></i>`).join("")}<span>${fail} / ${config.outage?.failures_to_open || 3} failed cycles</span><span class="push">loss ${finite(loss) ? fmt(loss,0)+"%" : "—"}</span></div></div>`;
    }).join("");
    return panel("Live probes", body, "latest stored samples");
  }

  function confidencePanel(h) {
    const entries = h.targets.map(item => {
      const okay = item.last_success === true;
      const tone = item.open ? "#f4584f" : item.failures ? "#d8a657" : okay ? "#3fb950" : "#586870";
      const glyph = item.open ? "✕" : item.failures ? "!" : okay ? "✓" : "·";
      const detail = item.open ? `${item.failures} sustained failures · event-open evidence`
        : item.failures ? `${item.failures} failed cycle${item.failures === 1 ? "" : "s"} · not settled`
        : okay ? "latest observation answered" : "no observation yet";
      return `<div class="reason" style="--reason:${tone}"><span class="reason-mark">${glyph}</span><div><div class="reason-label">${esc(item.target)} · ${esc(item.protocol)}</div><div class="reason-detail">${detail}</div></div></div>`;
    }).join("") || `<div class="reason-detail">No target observations yet.</div>`;
    const positive = h.targets.filter(item => item.last_success === true).length;
    const filled = h.mode === "outage" ? Math.max(2, h.targets.filter(item => item.open).length) : Math.min(5, positive);
    const wan = h.status.wan;
    const wanState = wan?.reconnected_at ? "positive" : wan ? "available · not counted as health" : "absent";
    const wanNote = wan?.reconnected_at
      ? `The router reported a reconnect at ${new Date(wan.reconnected_at).toLocaleTimeString()}; this may incriminate the WAN.`
      : wan ? `Router reports ${wan.connection_status}. A positive self-report is deliberately not accepted as proof of health.`
      : "No current router observation exists, so WAN evidence is absent rather than assumed.";
    return panel("Confidence", `<div class="meter">${[0,1,2,3,4].map((_,i) => `<i style="${i < filled ? `background:${h.tone};opacity:${.55+i*.11}` : ""}"></i>`).join("")}</div>
      <div class="reason-detail">${h.mode === "healthy" ? "corroborated by independent local probes" : "confidence follows the evidence available"}</div>
      <div class="eyebrow" style="margin:14px 0 10px">Reasoning chain</div>${entries}
      <div class="evidence"><div class="chips" style="margin:0 0 5px"><span class="eyebrow">wan_evidence</span><span class="chip tone" style="--tone:${h.tone}">${esc(wanState)}</span></div><div class="reason-detail">${esc(wanNote)}</div></div>`,
      h.mode === "degrading" ? "onset · unconfirmed" : h.mode === "outage" ? "open" : "corroborated");
  }

  function twoClocks(h) {
    const raw = h.mode === "degrading" ? [[74,"#3fb950"],[9,"#d8a657"],[17,"#f4584f"]]
      : h.mode === "outage" ? [[16,"#3fb950"],[12,"#d8a657"],[42,"#f4584f"],[30,"#3fb950"]] : [[100,"#3fb950"]];
    const settled = h.mode === "degrading" ? [[100,"#3fb950"]]
      : h.mode === "outage" ? [[16,"#3fb950"],[40,"#243040"],[24,"#f4584f"],[20,"#3fb950"]] : [[100,"#3fb950"]];
    const lane = items => items.map(([width,color]) => `<i style="width:${width}%;background:${color}"></i>`).join("");
    return panel("Two clocks · raw signal vs settled verdict", `<p class="clock-copy">Hysteresis needs ${config.outage?.failures_to_open || 3} failed cycles before it opens an outage. That stops every blip becoming an alert, but means the settled verdict can trail a failing raw signal. Both clocks are shown.</p>
      <div class="clock"><div class="clock-lane"><span class="clock-label">raw signal</span><div class="segments">${lane(raw)}</div></div><div class="clock-lane"><span class="clock-label">settled</span><div class="segments">${lane(settled)}</div></div><div class="clock-lane"><span class="clock-label"></span><div class="axis"><span>−4m</span><span>−3m</span><span>−2m</span><span>−1m</span><span>now</span></div></div></div>
      <div class="legend"><span><i class="swatch" style="display:inline-block;background:#3fb950"></i> answering</span><span><i class="swatch" style="display:inline-block;background:#d8a657"></i> degraded · unconfirmed</span><span><i class="swatch" style="display:inline-block;background:#f4584f"></i> failing</span><span><i class="swatch" style="display:inline-block;background:#243040"></i> no verdict yet</span></div>`,
      `opens after ${config.outage?.failures_to_open || 3} failures`);
  }

  function timelineScreen() {
    const starts = state.events.filter(event => event.kind === "outage_start");
    const outage = starts[0];
    const end = outage && state.events.find(event => event.kind === "outage_end" && event.ts >= outage.ts);
    const details = outage?.details || {};
    const detected = details.detected_at ? new Date(details.detected_at).getTime()/1000 : outage?.ts;
    const endTs = end?.ts || (outage && state.status?.open ? Date.now()/1000 : null);
    const lag = outage && detected ? Math.max(0, detected - outage.ts) : null;
    const dur = outage && endTs ? Math.max(0, endTs - outage.ts) : null;
    const anatomy = outage ? `<p class="body-copy">Onset is not flattened into a single timestamp. The event was backdated to its first sustained failure; detection and later classification changes remain separate evidence.</p>
      <div class="grid-3"><div class="stat"><div class="stat-label">Detection lag</div><div class="stat-value" style="color:var(--amber)">${fmt(lag,0)}<span class="unit"> s</span></div><div class="stat-note">backdated to first failure</div></div>
      <div class="stat"><div class="stat-label">Duration</div><div class="stat-value">${duration(dur)}</div><div class="stat-note">${stamp(outage.ts)} → ${end ? stamp(end.ts) : "open"}</div></div>
      <div class="stat"><div class="stat-label">Classification</div><div class="stat-value" style="font-size:15px;color:var(--red)">${esc(details.classification || outage.message)}</div><div class="stat-note">latest settled verdict</div></div></div>
      ${classificationHistory(details.classification_history || [])}` : `<div class="inline-error">No outage has been recorded yet. Absence is the current history, not a loading failure.</div>`;
    const traces = details.traceroute || [];
    const trace = panel("Traceroute", traces.length ? traces.map((hop,i) => `<div class="trace"><span class="event-time">${i+1}</span><span class="status-dot ${hop.status === "replied" ? "" : "down"}"></span><span style="min-width:0;flex:1">${esc(hop.host || hop.address || "* * *")}<div class="stat-note">${esc(hop.status || "silent timeout")}</div></span><span class="adapter-rate">${finite(hop.rtt_ms) ? fmt(hop.rtt_ms)+" ms" : "—"}</span></div>`).join("") : `<div class="reason-detail">No traceroute is attached to the latest outage.</div>`, details.target ? esc(details.target) : "captured on outage open");
    const events = panel("Events", state.events.length ? state.events.map(eventRow).join("") : `<div class="reason-detail">No events yet.</div>`, "/events?limit=100");
    return `<div class="screen">${panel("Outage anatomy", anatomy, outage ? `${stamp(outage.ts)} · ${esc(details.classification || "outage")}` : "history empty")}<div class="grid-2">${trace}${events}</div></div>`;
  }

  function classificationHistory(history) {
    if (!history.length) return "";
    return `<div class="evidence"><div class="eyebrow" style="margin-bottom:9px">classification_history</div><div class="chips">${history.map((item,index) => `${index ? '<span style="color:var(--dim)">→</span>' : ""}<span class="chip tone" style="--tone:${item.classification === "unknown" ? "#d8a657" : "#f4584f"}">${esc(item.classification)} <strong>${esc(new Date(item.at).toLocaleTimeString())}</strong></span>`).join("")}</div></div>`;
  }

  function eventRow(event) {
    const tones = {outage_end:"#3fb950",wan_reconnect:"#d8a657",outage_start:"#f4584f",device_join:"#8a99a0",device_depart:"#586870",alert_triggered:"#f4584f",alert_resolved:"#3fb950"};
    return `<div class="event"><span class="event-time">${stamp(event.ts)}</span><span class="kind" style="--kind:${tones[event.kind] || "#8a99a0"}">${esc(event.kind)}</span><span class="event-message">${esc(event.message)}</span></div>`;
  }

  function devicesScreen() {
    const devices = state.devices.filter(device => !["00:00:00:00:00:00","ff:ff:ff:ff:ff:ff"].includes(String(device.mac).toLowerCase()));
    const artefacts = state.devices.filter(device => !devices.includes(device));
    const total = devices.length, vendors = devices.filter(d => d.vendor).length, hosts = devices.filter(d => d.hostname).length, labels = devices.filter(d => d.label).length;
    const coverage = panel("Identity coverage", `<div class="grid-3">${coverageMetric("vendor",vendors,total,"#3fb950","full IEEE registry bundled · as good as it gets")}${coverageMetric("hostname",hosts,total,"#d8a657","rDNS + NetBIOS · identity is allowed to be incomplete")}${coverageMetric("label",labels,total,"#4af6c3","saved locally · editable at any time")}</div>`);
    const candidates = devices.filter(device => !device.label).slice(0,3);
    const guided = panel("◈ Guided naming", candidates.length ? candidates.map(device => `<div class="proposal"><div class="proposal-top"><span class="proposal-icon">◇</span><div style="flex:1"><div class="proposal-name">${esc(device.hostname || device.vendor || "Unknown device")}</div><div class="proposal-meta">${esc(device.mac)} · ${esc(device.vendor || "unknown vendor")}</div></div><span class="chip tone" style="--tone:${device.hostname ? "#3fb950" : "#d8a657"}">${device.hostname ? "medium" : "low"} confidence</span></div><form class="label-form" data-label-form="${device.id}"><input class="label-input" name="label" maxlength="120" value="${esc(device.hostname || "")}" placeholder="Name this device" aria-label="Label for ${esc(device.mac)}"><button class="btn" type="submit">save label</button></form><p class="proposal-note">${device.hostname ? "The discovered hostname is prefilled as the strongest available clue." : "Type a name you recognize; netmon will not invent a physical identity."}</p></div>`).join("") : `<div class="reason-detail">Every discovered device already has a label.</div>`, `<span class="chip tone" style="--tone:#4af6c3">✓ labels save locally</span>`, "accent");
    const inventory = panel("Inventory", devices.length ? devices.map(deviceRow).join("") : `<div class="reason-detail">No devices discovered yet.</div>`, `${devices.filter(d=>d.online).length} online · ${total} known`);
    const artefactPanel = `<section class="panel dashed"><div class="panel-head"><h2 class="panel-title">⌀ Discovery artefacts</h2><span class="panel-meta">${artefacts.length} rows · filtered from counts above</span></div>${artefacts.length ? artefacts.map(d => `<span class="artefact">${esc(d.mac)} · ${esc(d.vendor || "no vendor")}</span>`).join("") : `<span class="reason-detail">No broadcast or unspecified rows are present.</span>`}<p class="reason-detail">Shown explicitly because a device count that quietly differs from the API would be worse than an odd row.</p></section>`;
    const writeError = state.errors.deviceWrite ? `<div class="inline-error">Could not save that label: ${esc(state.errors.deviceWrite)}</div>` : "";
    return `<div class="screen">${writeError}${coverage}${guided}${inventory}${artefactPanel}</div>`;
  }

  function coverageMetric(label,value,total,tone,note) {
    const percent = total ? value / total * 100 : 0;
    return `<div style="--tone:${tone}"><div class="coverage-name"><span>${label}</span><span class="coverage-frac">${value} / ${total}</span></div><div class="bar"><i style="width:${Math.max(value ? percent : 2,2)}%"></i></div><div class="coverage-note">${note}</div></div>`;
  }
  function deviceRow(device) {
    const ip = device.ips?.[0]?.ip || "—";
    const tag = device.label ? "named" : String(device.mac).startsWith("02:") ? "privacy MAC" : device.online ? "online" : "offline";
    const tone = device.label ? "#4af6c3" : tag === "privacy MAC" ? "#f4584f" : "#586870";
    const editing = state.editingDeviceId === device.id;
    const identity = editing
      ? `<div><form class="inventory-label-form" data-label-form="${device.id}"><input class="label-input" name="label" maxlength="120" value="${esc(device.label || "")}" placeholder="Name this device" aria-label="Rename ${esc(device.label || device.mac)}" data-inline-label="${device.id}"><button class="btn" type="submit">save</button><button class="btn quiet" type="button" data-label-cancel>cancel</button></form><div class="device-meta">${esc(device.mac)} · ${esc(device.hostname || "no hostname")} · leave blank to clear</div></div>`
      : `<div><div class="device-name">${esc(device.label || device.eero_name || device.hostname || device.vendor || "Unknown device")}</div><div class="device-meta">${esc(device.mac)} · ${esc(device.hostname || "no hostname")} · ${esc(tag)}</div></div>`;
    const action = editing
      ? ""
      : `<button class="chip tone label-button" style="--tone:${tone}" type="button" data-label-device="${device.id}" aria-label="${device.label ? `Rename ${esc(device.label)}` : `Label ${esc(device.mac)}`}">${device.label ? "rename" : "add label"}</button>`;
    return `<div class="device-row ${device.online ? "" : "offline"}"><span class="device-dot"></span>${identity}<div class="device-presence"><div class="presence"></div><div class="stat-note">${device.online ? `seen ${ago(device.last_seen)} ago` : `last seen ${ago(device.last_seen)} ago`}</div></div><span class="device-ip">${esc(ip)}</span>${action}</div>`;
  }

  function diagnosticsScreen() {
    const latencyTargets = metricTargets("latency_rtt_ms");
    const bands = panel("Latency history", latencyTargets.length ? latencyTargets.map(target => latencyBand(target)).join("") : `<div class="reason-detail">No latency samples yet.</div>`, "interactive stored samples");
    const lossRows = state.metrics.filter(row => row.kind === "latency_loss_percent").slice(-120);
    const lossTimes = lossRows.map(row => Number(row.ts)).filter(Number.isFinite);
    const lossSpan = lossTimes.length ? Math.max(...lossTimes) - Math.min(...lossTimes) : 0;
    const loss = panel("Packet loss · hit strip", `<div class="loss-strip">${Array.from({length:120},(_,i) => {
      const row = lossRows[i - (120 - lossRows.length)];
      const value = Number(row?.value || 0); const cls = value >= 100 ? "total" : value > 5 ? "bad" : value > 0 ? "warn" : "";
      return `<i class="loss-cell ${cls}" title="${row ? `${chartTime(row.ts,lossSpan)} · ${fmt(value,0)}% loss · ${esc(row.target)}` : "no sample"}"></i>`;
    }).join("")}</div><div class="axis"><span>${lossTimes.length ? chartTime(Math.min(...lossTimes),lossSpan) : "no samples"}</span><span>${lossTimes.length ? chartTime(Math.max(...lossTimes),lossSpan) : "no samples"}</span></div><p class="reason-detail">Loss is drawn as strikes. Hover a strike for its time, target, and amount; a line pinned to zero would make a single dropped packet easy to miss.</p>`);
    const dnsTargets = metricTargets("dns_resolution_ms");
    const dns = panel("DNS resolver × name", dnsTargets.length ? dnsTargets.map(target => {
      const rows = series("dns_resolution_ms",target,500), value = rows.at(-1)?.value, p95 = percentile(rows,.95);
      const [resolver,...name] = target.split(":");
      return `<div class="dns-row"><span>${esc(resolver)}</span><span class="probe-proto">→ ${esc(name.join(":"))}</span><div class="bar" style="width:110px;margin-left:auto"><i style="width:${Math.min(100,Number(value||0)/60*100)}%"></i></div><strong style="width:64px;text-align:right">${fmt(value)} ms</strong><span class="unit">p95 ${fmt(p95)}</span></div>`;
    }).join("") : `<div class="reason-detail">No DNS samples yet.</div>`);
    const httpTargets = metricTargets("http_ttfb_ms"), httpTarget = httpTargets[0];
    const httpRows = httpTarget ? series("http_ttfb_ms",httpTarget,180) : [], httpValue = httpRows.at(-1)?.value, code = latest("http_status_code",httpTarget)?.value;
    const http = panel("HTTP probe", httpTarget ? `<div style="display:flex;align-items:baseline;gap:8px"><span class="big-value">${fmt(httpValue,0)}</span><span class="unit">ms ttfb</span><span class="chip tone push" style="--tone:${code >= 200 && code < 400 ? "#3fb950" : "#f4584f"}">${fmt(code,0)}</span></div><div class="reason-detail">${esc(httpTarget)}</div>${interactiveChart({datasets:[{label:"TTFB",color:"#4af6c3",rows:httpRows}],unit:"ms",height:100,ariaLabel:`HTTP response time for ${httpTarget}`})}<div class="metric-stats" style="margin-top:9px"><span>min <strong>${fmt(percentile(httpRows,0))}</strong></span><span>p95 <strong>${fmt(percentile(httpRows,.95))}</strong></span></div>` : `<div class="reason-detail">No HTTP samples yet.</div>`);
    const times = state.metrics.map(row => Number(row.ts)).filter(Number.isFinite), run = times.length ? Math.max(...times)-Math.min(...times) : 0;
    const retention = `<section class="panel dashed"><div class="panel-head"><h2 class="panel-title">⧖ Retention</h2><span class="chip">${config.retention?.raw_days || 7}d raw · ${config.retention?.rollup_5m_days || 365}d rollups</span></div><p class="body-copy">The loaded metric history spans <strong style="color:var(--text)">${duration(run)}</strong>. Charts show only stored samples—no smoothing that invents shape and no axis that implies more history than exists.</p></section>`;
    return `<div class="screen">${bands}${loss}<div class="grid-2">${dns}${http}</div>${docsisLineQualityPanel()}${retention}</div>`;
  }

  function docsisLineQualityPanel() {
    const channelKinds = ["docsis_ds_snr", "docsis_ds_power", "docsis_ds_uncorrectables", "docsis_us_power"];
    const channels = [...new Set(channelKinds.flatMap(kind => hitronSeries(kind).map(row => row.target)))].sort();
    const latestChannels = kind => channels.map(target => latestHitron(kind, target)).filter(Boolean);
    const snr = latestChannels("docsis_ds_snr");
    const dsPower = latestChannels("docsis_ds_power");
    const uncorrectables = latestChannels("docsis_ds_uncorrectables");
    const usPower = latestChannels("docsis_us_power");
    const snrWorst = snr.length ? Math.min(...snr.map(row => Number(row.value))) : null;
    const powerWorst = dsPower.length ? dsPower.reduce((worst, row) => Math.abs(Number(row.value)) > Math.abs(Number(worst.value)) ? row : worst) : null;
    const uncorrectableMax = uncorrectables.length ? Math.max(...uncorrectables.map(row => Number(row.value))) : null;
    const uptime = latestHitron("modem_uptime_seconds", "modem");
    const link = latestHitron("modem_link_speed", "modem");
    const registered = latestHitron("modem_registered", "modem");
    const registeredLabel = registered ? (Number(registered.value) > 0 ? "registered" : "not registered") : "no data";
    const registeredTone = !registered ? "#586870" : Number(registered.value) > 0 ? "#3fb950" : "#f4584f";
    const snrTone = snrWorst === null ? "#586870" : snrWorst < 30 ? "#f4584f" : snrWorst > 35 ? "#3fb950" : "#d8a657";
    const powerTone = powerWorst === null ? "#586870" : Math.abs(Number(powerWorst.value)) > 15 ? "#f4584f" : "#3fb950";
    const uncorrectableTone = uncorrectableMax === null ? "#586870" : uncorrectableMax > 0 ? "#f4584f" : "#3fb950";
    if (!channels.length && !uptime && !link && !registered) {
      return panel("DOCSIS line quality", `<div class="reason-detail">No modem line-quality samples yet.</div><p class="reason-detail">The Hitron collector is disabled or has not completed a poll.</p>`, "no data");
    }
    const status = `<div class="grid-3"><div class="stat"><div class="stat-label">modem uptime</div><div class="stat-value">${uptime ? duration(Number(uptime.value)) : "-"}</div><div class="stat-note">Hitron reported</div></div><div class="stat"><div class="stat-label">link speed</div><div class="stat-value">${link ? fmt(link.value,0) : "-"}<span class="unit">${link ? " Mbps" : ""}</span></div><div class="stat-note">modem ethernet link</div></div><div class="stat"><div class="stat-label">registration</div><div class="stat-value" style="color:${registeredTone}">${registeredLabel}</div><div class="stat-note">DOCSIS init stages</div></div></div>`;
    const channelSummary = channels.length ? `<div class="grid-2"><div class="stat"><div class="stat-label">downstream SNR</div><div class="stat-value" style="color:${snrTone}">${snrWorst === null ? "-" : fmt(snrWorst)}<span class="unit"> dB worst</span></div><div class="stat-note">typical ${fmt(percentile(snr,.5))} dB - ${snr.length}/${channels.length} channels</div></div><div class="stat"><div class="stat-label">downstream power</div><div class="stat-value" style="color:${powerTone}">${powerWorst === null ? "-" : fmt(powerWorst.value)}<span class="unit"> dBmV worst</span></div><div class="stat-note">typical ${fmt(percentile(dsPower,.5))} dBmV - spec +/-15</div></div><div class="stat"><div class="stat-label">uncorrectables</div><div class="stat-value" style="color:${uncorrectableTone}">${uncorrectableMax === null ? "-" : fmt(uncorrectableMax,0)}<span class="unit"> max</span></div><div class="stat-note">${uncorrectables.filter(row => Number(row.value) > 0).length}/${uncorrectables.length} channels flagged</div></div><div class="stat"><div class="stat-label">upstream power</div><div class="stat-value">${fmt(percentile(usPower,.5))}<span class="unit"> dBmV typical</span></div><div class="stat-note">${usPower.length} channels reported</div></div></div>` : `<div class="reason-detail">No per-channel line-quality samples yet.</div>`;
    return panel("DOCSIS line quality", `${status}${channelSummary}<p class="reason-detail">Summarized across ${channels.length || "no"} modem channels. SNR below 30 dB, downstream power outside +/-15 dBmV, and any uncorrectable errors are flagged.</p>`, `${channels.length} channels - hitron`);
  }

  function latencyBand(target) {
    const rows = series("latency_rtt_ms",target,300), jitter = series("latency_jitter_ms",target,300), color = colors[target] || "#4af6c3";
    return `<div class="metric-block"><div class="metric-head"><i class="swatch" style="background:${color}"></i><span>${esc(target)}</span><span class="probe-proto">${target === "gateway" ? "LAN" : "external"}</span><div class="metric-stats"><span>p50 <strong>${fmt(percentile(rows,.5))}</strong></span><span>peak <strong>${fmt(percentile(rows,1))}</strong></span><span>jitter <strong>${fmt(percentile(jitter,.5))}</strong></span></div></div>${interactiveChart({datasets:[{label:"RTT",color,rows},{label:"Jitter",color:"#8a99a0",rows:jitter,width:1.2}],unit:"ms",height:104,ariaLabel:`Latency and jitter history for ${target}`})}</div>`;
  }

  function bandwidthScreen() {
    const modemRows = hitronSeries("wan_downstream_rate", "wan").slice(-240);
    const upstreamRows = state.metrics.filter(row => row.kind === "wan_upstream_estimated_rate" && row.target === "wan" && row.source === "eero_cloud" && finite(row.value)).slice(-240);
    const eeroAvailable = latest("eero_usage_available", "eero");
    const currentUpstream = eeroAvailable && Number(eeroAvailable.value) === 0 ? null : upstreamRows.at(-1)?.value;
    const uplinkCapacity = Number(config.investigation?.uplink_mbps || 0) * 125000;
    const wholeHouseDatasets = [
      ...(modemRows.length ? [{label:"Download · Hitron WAN counter",color:"#4af6c3",rows:modemRows}] : []),
      ...(upstreamRows.length ? [{label:"Upload · eero cloud estimate",color:"#d08c60",rows:upstreamRows}] : []),
    ];
    const wholeHouse = wholeHouseDatasets.length ? panel("Whole-house WAN edge", `<div class="throughput-stats"><div class="stat"><div class="stat-label">down · now</div><div class="stat-value" style="color:var(--mint)">${bytes(modemRows.at(-1)?.value)}</div><div class="stat-note">Hitron WAN counter</div></div><div class="stat"><div class="stat-label">peak down</div><div class="stat-value">${bytes(percentile(modemRows,1))}</div><div class="stat-note">loaded sample window</div></div><div class="stat"><div class="stat-label">up · now</div><div class="stat-value" style="color:var(--orange)">${bytes(currentUpstream)}</div><div class="stat-note">${eeroAvailable && Number(eeroAvailable.value) === 0 ? "eero cloud currently unavailable" : "eero cloud estimate"}</div></div><div class="stat"><div class="stat-label">peak up</div><div class="stat-value">${bytes(percentile(upstreamRows,1))}</div><div class="stat-note">loaded sample window</div></div></div>${interactiveChart({datasets:wholeHouseDatasets,valueFormatter:bytes,floor:0,height:150,reference:uplinkCapacity > 0 ? {value:uplinkCapacity,label:`Configured uplink capacity · ${fmt(config.investigation.uplink_mbps,0)} Mbps`,color:"#d8a657"} : null,ariaLabel:"Whole-house downstream counter and estimated upstream traffic history"})}<p class="reason-detail">Downstream is measured locally at the Hitron WAN edge. Upstream is an estimate summed from successfully reporting devices by the cloud-dependent eero API; it is not a Hitron or local WAN byte counter.</p>`, "down: hitron counter · up: eero cloud estimate") : panel("Whole-house WAN edge", `<div class="reason-detail">No whole-house throughput samples yet.</div><p class="reason-detail">The Hitron downstream counter and eero cloud upload estimate are disabled, unavailable, or have not completed a poll.</p>`, "no data");
    const uploaderRows = metricTargets("eero_device_upload_rate").map(target => ({
      target,
      row: latest("eero_device_upload_rate", target),
    })).filter(item => item.row && finite(item.row.value)).sort((left,right) => Number(right.row.value) - Number(left.row.value));
    const deviceName = deviceLabelFor;
    let uploaderBody = `<div class="reason-detail">eero cloud usage is disabled or has not completed a poll.</div>`;
    if (eeroAvailable && Number(eeroAvailable.value) === 0) {
      uploaderBody = `<div class="reason-detail">eero cloud usage is currently unavailable. Existing device rates are not shown as live.</div>`;
    } else if (eeroAvailable && !uploaderRows.length) {
      uploaderBody = `<div class="reason-detail">eero cloud responded, but no devices reported valid upload rates.</div>`;
    } else if (uploaderRows.length) {
      uploaderBody = uploaderRows.map((item,index) => `<div class="adapter"><span class="uploader-rank">${index+1}</span><span>${esc(deviceName(item.target))}<div class="stat-note">${esc(item.target)} · eero cloud estimate</div></span><span class="adapter-rate">${bytes(item.row.value)}</span></div>`).join("");
    }
    const uploaders = panel("Live uploaders · ranked", uploaderBody, eeroAvailable && Number(eeroAvailable.value) > 0 ? `${uploaderRows.length} devices · latest cloud poll` : "availability unknown");
    const recvTargets = metricTargets("host_nic_bytes_recv_rate"), sentTargets = metricTargets("host_nic_bytes_sent_rate");
    const allTargets = [...new Set([...recvTargets,...sentTargets])];
    const recvRows = state.metrics.filter(r=>r.kind==="host_nic_bytes_recv_rate"), sentRows = state.metrics.filter(r=>r.kind==="host_nic_bytes_sent_rate");
    const hostDownRows = sumByTimestamp(recvRows).slice(-240), hostUpRows = sumByTimestamp(sentRows).slice(-240);
    const nowDown = recvTargets.reduce((sum,target)=>sum+Number(latest("host_nic_bytes_recv_rate",target)?.value||0),0);
    const nowUp = sentTargets.reduce((sum,target)=>sum+Number(latest("host_nic_bytes_sent_rate",target)?.value||0),0);
    const host = panel("This PC · throughput", `<div class="throughput-stats"><div class="stat"><div class="stat-label">down · now</div><div class="stat-value" style="color:var(--mint)">${bytes(nowDown)}</div></div><div class="stat"><div class="stat-label">up · now</div><div class="stat-value" style="color:var(--orange)">${bytes(nowUp)}</div></div><div class="stat"><div class="stat-label">peak down</div><div class="stat-value">${bytes(percentile(hostDownRows,1))}</div></div><div class="stat"><div class="stat-label">adapters</div><div class="stat-value">${allTargets.length}</div></div></div>
      ${interactiveChart({datasets:[{label:"Download",color:"#4af6c3",rows:hostDownRows},{label:"Upload",color:"#d08c60",rows:hostUpRows}],valueFormatter:bytes,floor:0,height:150,ariaLabel:"This PC upload and download history"})}`, `${allTargets.length} adapters · host_nic source`);
    const adapters = panel("Adapters", allTargets.length ? allTargets.map(target => { const down=Number(latest("host_nic_bytes_recv_rate",target)?.value||0),up=Number(latest("host_nic_bytes_sent_rate",target)?.value||0); return `<div class="adapter"><span class="status-dot"></span><span>${esc(target)}<div class="stat-note">host adapter</div></span><span class="traffic-pair"><i>↓ ${bytes(down)}</i><i>↑ ${bytes(up)}</i></span></div>`; }).join("") : `<div class="reason-detail">No adapter rate samples yet.</div>`);
    const processes = metricTargets("host_process_bytes_recv_rate");
    const processPanel = panel("Processes", processes.length ? processes.map(target => `<div class="adapter"><span>${esc(target.split(":").slice(2).join(":"))}</span><span class="traffic-pair"><i>↓ ${bytes(latest("host_process_bytes_recv_rate",target)?.value||0)}</i><i>↑ ${bytes(latest("host_process_bytes_sent_rate",target)?.value||0)}</i></span></div>`).join("") : `<div class="reason-detail">Per-process network counters are unavailable on this platform or disabled in config.</div>`);
    const managedNames = config.managed_hosts?.hosts || [];
    const managedReported = metricTargets("managed_host_available");
    const managedHosts = [...new Set([...managedNames, ...managedReported])];
    const managed = panel("Managed Windows hosts · NIC traffic", managedHosts.length ? managedHosts.map(name => {
      const available = Number(latest("managed_host_available", name)?.value || 0) > 0;
      const downRows = series("managed_host_nic_bytes_recv_rate", name, 80);
      const upRows = series("managed_host_nic_bytes_sent_rate", name, 80);
      const down = Number(downRows.at(-1)?.value || 0), up = Number(upRows.at(-1)?.value || 0);
      return `<div class="metric-block"><div class="metric-head"><span class="status-dot ${available ? "" : "down"}"></span><span>${esc(name)}</span><span class="probe-proto">${available ? "available" : "unavailable"}</span><div class="metric-stats"><span>down <strong>${bytes(down)}</strong></span><span>up <strong>${bytes(up)}</strong></span></div></div>${interactiveChart({datasets:[{label:"Download",color:"#4af6c3",rows:downRows},{label:"Upload",color:"#d08c60",rows:upRows}],valueFormatter:bytes,floor:0,height:108,ariaLabel:`NIC traffic history for ${name}`})}</div>`;
    }).join("") : `<div class="reason-detail">Managed-host telemetry is disabled or has not reported yet.</div>`, `${managedHosts.length} configured/reported · SSH`);
    const managedNote = `<section class="panel dashed"><p class="body-copy">Managed-host rates are cumulative adapter counters read over SSH. They include that device's LAN traffic and are <strong style="color:var(--text)">not total WAN throughput</strong>.</p></section>`;
    const speed = `<section class="panel dashed"><div class="panel-head"><h2 class="panel-title">Speedtest · capacity</h2><span class="chip disabled">⊘ needs write API</span></div><p class="body-copy">Scheduled speedtest is <strong style="color:${config.speedtest?.enabled ? "var(--green)" : "var(--amber)"}">${config.speedtest?.enabled ? "enabled" : "disabled"}</strong>. Capacity is not utilisation, so it is never stacked on the host-traffic axis.</p><button class="btn" disabled>enable speedtest</button></section>`;
    return `<div class="screen">${wholeHouse}${uploaders}${host}<div class="grid-2">${adapters}${processPanel}</div>${managed}${managedNote}${speed}</div>`;
  }

  function alertsScreen() {
    const rules = config.alerts?.rules || [];
    const empty = !rules.length ? `<section class="panel empty"><div class="empty-icon">◬</div><h2>Nothing is watching yet</h2><p>The rule engine, independent notifier backends and <code>/alerts</code> work. The configured rule list is empty, so nothing will fire until a sustained rule is authored in TOML.</p><div class="chips" style="justify-content:center"><span class="chip tone" style="--tone:#3fb950">engine ✓</span><span class="chip tone" style="--tone:#3fb950">log ✓</span><span class="chip tone" style="--tone:#3fb950">windows toast ${config.alerts?.windows_toast ? "✓" : "off"}</span><span class="chip tone" style="--tone:#f4584f">rules 0</span></div></section>`
      : panel("Configured rules", rules.map(rule => `<div class="starting"><span class="kind" style="--kind:${rule.severity === "critical" ? "#f4584f" : "#d8a657"}">${esc(rule.severity)}</span><span>${esc(rule.name)}<div class="stat-note">${esc(rule.metric_kind)} ${esc(rule.operator)} ${esc(rule.threshold)} · ${rule.failures_to_open} cycles</div></span></div>`).join(""), `${rules.length} rules`);
    const composer = panel("❯ Rule composer", `<div class="composer">when <span class="token" style="color:var(--orange)">1.1.1.1 loss</span> is <span class="token">above</span><span class="token">20 %</span> for <span class="token">2 min</span> → notify <span class="token">windows toast</span><span class="caret"></span></div><div class="terminal"><div class="terminal-head">● ● ● · netmon.toml · compiles to</div><pre>[[alerts.rules]]
name = "wan-loss"
metric_kind = "latency_loss_percent"
target = "1.1.1.1"
operator = ">"
threshold = 20.0
failures_to_open = 12
successes_to_close = 2
severity = "warning"</pre></div><p class="reason-detail">The sustained duration is structurally required. It is what keeps one blip from becoming alert fatigue.</p>`, `<span class="chip disabled">⊘ needs write API</span>`, "accent");
    const loss = state.metrics.filter(r=>r.kind==="latency_loss_percent");
    const p95 = percentile(loss,.95);
    const starts = panel("Starting points", `<div class="starting"><span class="kind" style="--kind:#d8a657">warning</span><span>Sustained external loss<div class="stat-note">Current p95 is ${fmt(p95)}%; start above normal and require multiple cycles.</div></span><button class="btn push" disabled>copy TOML</button></div><div class="starting"><span class="kind" style="--kind:#f4584f">error</span><span>Gateway unreachable<div class="stat-note">Three consecutive failures distinguish a sustained LAN problem.</div></span><button class="btn push" disabled>copy TOML</button></div>`, "derived from stored samples");
    const history = state.alerts.length ? panel("Alert history", state.alerts.map(eventRow).join(""), `${state.alerts.length} records`) : "";
    return `<div class="screen">${empty}${composer}${starts}${history}</div>`;
  }

  function settingsScreen() {
    const intro = `<section class="panel dashed"><div class="panel-head"><h2 class="panel-title">⌗ Configuration mirror</h2><span class="chip">read-only · v${esc(config.version)}</span></div><p class="body-copy">Everything below is loaded from netmon's validated settings. Runtime settings have no write path, so this surface is a faithful mirror rather than an editor. Device labels are stored separately and remain editable. Secret values and webhook URLs are never exposed.</p></section>`;
    const sections = [
      ["Latency","[latency]",config.latency],["Outage","[outage]",config.outage],
      ["Discovery","[discovery]",config.discovery],["WAN · UPnP","[wan]",{...config.wan,byte_counters:"unimplemented stub"}],
      ["Retention","[retention]",config.retention],
      ["Alerts & extras","[alerts] · [speedtest] · [eero]",{
        "alerts.enabled":config.alerts?.enabled,"alerts.rules":`${config.alerts?.rules?.length || 0} rules`,
        "alerts.windows_toast":config.alerts?.windows_toast,"speedtest.enabled":config.speedtest?.enabled,
        "eero.enabled":config.eero?.enabled,"eero.token_configured":config.eero?.token_configured,
        "managed_hosts.enabled":config.managed_hosts?.enabled,
        "managed_hosts.interval_seconds":config.managed_hosts?.interval_seconds,
        "managed_hosts.hosts":config.managed_hosts?.hosts || [],
      }],
    ];
    return `<div class="screen">${intro}<div class="settings-grid">${sections.map(([title,key,values]) => settingCard(title,key,values)).join("")}</div></div>`;
  }

  const investigationCapacity = () => {
    const mbps = config.investigation?.uplink_mbps;
    return finite(mbps) && Number(mbps) > 0 ? Number(mbps) * 1000000 : null;
  };
  const investigationRows = (kind, startedAt) => state.metrics.filter(row => {
    const ts = Number(row.ts);
    return row.kind === kind && finite(ts) && finite(row.value) && (!finite(startedAt) || ts >= Number(startedAt));
  }).slice(-180);
  const investigationChart = (rows, color, label, reference = null) => {
    const capacity = reference && finite(reference) ? Number(reference) : null;
    const values = rows.map(row => Number(row.value)).filter(Number.isFinite);
    const max = Math.max(capacity || 0, ...values, 1);
    const isLatency = label.includes("ms");
    return interactiveChart({
      datasets:[{label,color,rows}],
      valueFormatter:isLatency ? value => `${fmt(value)} ms` : bytes,
      floor:0,
      ceiling:max,
      height:132,
      reference:capacity === null ? null : {value:capacity,color:"#d8a657"},
      ariaLabel:label,
    });
  };
  const investigationVerdict = report => report?.verdict ? String(report.verdict).replaceAll("_", " ") : "report unavailable";
  const investigationCoverage = coverage => {
    const fraction = finite(coverage?.device_data_fraction) ? Number(coverage.device_data_fraction) : null;
    const available = coverage?.eero_available === true ? "available" : coverage?.eero_available === false ? "unavailable" : "—";
    const hosts = Array.isArray(coverage?.managed_hosts_available) ? coverage.managed_hosts_available : [];
    return `<div class="coverage-block"><div class="coverage-name"><span>Device data fraction</span><span class="coverage-frac">${fraction === null ? "—" : fmt(fraction * 100, 0) + "%"}</span></div><div class="bar"><i style="width:${fraction === null ? 2 : Math.max(2, Math.min(100, fraction * 100))}%"></i></div><div class="coverage-note">eero cloud ${available} · managed hosts ${hosts.length ? hosts.map(esc).join(", ") : "none reported"} · ${finite(coverage?.degraded_buckets_with_device_data) ? fmt(coverage.degraded_buckets_with_device_data, 0) : "—"} / ${finite(coverage?.degraded_buckets) ? fmt(coverage.degraded_buckets, 0) : "—"} degraded buckets had device data.</div></div>`;
  };
  function investigationReportScreen(report) {
    const confidence = String(report?.confidence || "low");
    const tone = confidence === "high" ? "#3fb950" : confidence === "medium" ? "#d8a657" : "#f4584f";
    const reasons = Array.isArray(report?.reasons) ? report.reasons : [];
    const attributions = Array.isArray(report?.attributions) ? report.attributions : [];
    const caveats = Array.isArray(report?.caveats) ? report.caveats : [];
    const table = attributions.length ? `<div class="investigation-table-wrap"><table class="investigation-table"><thead><tr><th>Device</th><th>Mean</th><th>Peak</th><th>Share degraded</th><th>Correlation</th></tr></thead><tbody>${attributions.map(item => `<tr><td>${esc(item.label || item.name || item.device || "Unknown device")}</td><td>${bytes(item.mean_upload_bps)}</td><td>${bytes(item.peak_upload_bps)}</td><td>${finite(item.share_of_upload) ? fmt(Number(item.share_of_upload) * 100, 0) + "%" : "—"}</td><td>${fmt(item.correlation_with_latency, 2)}</td></tr>`).join("")}</tbody></table></div>` : `<div class="reason-detail">Attribution is unavailable for this verdict.</div>`;
    const reasonsHtml = reasons.length ? reasons.map(reason => `<div class="reason"><span class="reason-mark" style="--reason:${tone}">·</span><div class="reason-detail">${esc(reason)}</div></div>`).join("") : `<div class="reason-detail">No independently checkable reasons were returned.</div>`;
    const caveatsHtml = caveats.length ? caveats.map(caveat => `<div class="reason-detail">· ${esc(caveat)}</div>`).join("") : `<div class="reason-detail">No additional caveats were reported.</div>`;
    return `<div class="screen"><section class="panel hero investigation-verdict" style="--tone:${tone}"><div class="hero-inner"><div class="hero-copy"><div class="eyebrow">/investigate · completed verdict</div><div class="hero-verdict">${esc(investigationVerdict(report))}</div><div class="hero-headline">${confidence === "low" ? "Low confidence: " : ""}${esc(report?.headline || "The investigation did not return a headline.")}</div><div class="chips"><span class="chip tone" style="--tone:${tone}">${esc(confidence)} confidence</span><span class="chip">window <strong>${duration(Number(report?.total_seconds))}</strong></span></div></div></div></section>
      ${panel("Reasons", reasonsHtml)}${panel("Attribution", table, attributions.length ? `${attributions.length} candidates` : "not computed")}
      ${panel("Coverage + caveats", `${investigationCoverage(report?.coverage || {})}<div class="investigation-caveats"><div class="eyebrow">Caveats</div>${caveatsHtml}</div>`, "reported explicitly")}</div>`;
  }
  function investigationRunningScreen(active) {
    const started = Number(active?.started_at), ends = Number(active?.ends_at || active?.ended_at);
    const remaining = finite(ends) ? Math.max(0, ends - Date.now() / 1000) : null;
    const latency = investigationRows("latency_rtt_ms", started).filter(row => ["1.1.1.1", "8.8.8.8"].includes(row.target));
    const upload = investigationRows("eero_device_upload_rate", started);
    const totalUpload = [...new Set(upload.map(row => row.ts))].map(ts => ({ts, value: upload.filter(row => row.ts === ts).map(row => Number(row.value)).filter(Number.isFinite).reduce((sum, value) => sum + value, 0)}));
    const capacity = investigationCapacity();
    const devices = [...new Set(upload.map(row => row.target).filter(Boolean))].map(target => {
      const rows = upload.filter(row => row.target === target);
      return {target, value: Number(rows.at(-1)?.value)};
    }).sort((a,b) => (finite(b.value) ? b.value : -1) - (finite(a.value) ? a.value : -1));
    const deviceList = devices.length ? devices.map(item => `<div class="adapter"><span class="status-dot"></span><span>${esc(deviceLabelFor(item.target))}<div class="stat-note">${esc(item.target)} · current eero upload rate</div></span><span class="adapter-rate">${bytes(item.value)}</span></div>`).join("") : `<div class="reason-detail">No per-device upload samples yet. eero data is allowed to fail soft.</div>`;
    return `<div class="screen">${panel("Investigation running", `<div class="grid-3"><div class="stat"><div class="stat-label">time remaining</div><div class="stat-value" style="color:var(--mint)">${duration(remaining)}</div><div class="stat-note">watch window · ${duration(finite(ends - started) ? ends - started : null)}</div></div><div class="stat"><div class="stat-label">uplink reference</div><div class="stat-value">${capacity === null ? "—" : fmt(capacity / 1000000, 1)}<span class="unit"> Mbps</span></div><div class="stat-note">configured capacity</div></div><div class="stat"><div class="stat-label">sampling</div><div class="stat-value">burst</div><div class="stat-note">speedtest paused while active</div></div></div><p class="body-copy">Diagnostic collectors are polling faster, scheduled speedtest is paused, and this screen is watching the chosen window. Total upload is estimated from per-device eero rates, not measured directly at the WAN.</p><button class="btn" type="button" data-investigation-stop="${esc(active?.id)}">Stop now</button>`, `started ${stamp(started)}`)}
      ${panel("Internet RTT", latency.length ? investigationChart(latency, "#d08c60", "external RTT · ms") : `<div class="reason-detail">No internet RTT samples in this window yet.</div>`)}
      ${panel("Estimated total upload", investigationChart(totalUpload, "#4af6c3", `sum of eero device rates${capacity === null ? "" : " · amber = " + fmt(capacity / 1000000, 1) + " Mbps capacity"}`, capacity), "estimate · capacity reference")}
      ${panel("Per-device upload", deviceList, `${devices.length} ranked now`)}</div>`;
  }
  function investigationIdleScreen() {
    const durations = [300, 900, 1800, 3600];
    const error = state.errors.investigationWrite ? `<div class="inline-error">${esc(state.errors.investigationWrite)}</div>` : "";
    return `<div class="screen">${error}${panel("Start investigation", `<div class="eyebrow">time-boxed diagnostics</div><div class="chips investigation-duration">${durations.map(value => `<button class="chip ${state.investigationDuration === value ? "tone" : ""}" style="--tone:#4af6c3" type="button" data-investigation-duration="${value}">${value / 60} min</button>`).join("")}</div><p class="body-copy">Pressing start raises poll rates on the diagnostic collectors, pauses the scheduled speedtest, and watches the network for the chosen period. It does not shape traffic or intercept packets.</p><button class="btn" type="button" data-investigation-start>Start investigation</button>`, "operator-controlled")}${investigationHistory()}</div>`;
  }
  function investigationHistory() {
    const rows = Array.isArray(state.investigations) ? state.investigations : [];
    return panel("History", rows.length ? rows.map(item => `<button class="investigation-history-row" type="button" data-investigation-report="${esc(item.id)}"><span class="status-dot ${item.status === "cancelled" ? "down" : ""}"></span><span>${esc(investigationVerdict(item.report || item))}<div class="stat-note">${esc(stamp(item.started_at))} · ${esc(item.status || "unknown")}</div></span><span class="adapter-rate">${duration(item.ended_at && item.started_at ? Number(item.ended_at) - Number(item.started_at) : null)}</span></button>`).join("") : `<div class="reason-detail">No completed investigations yet.</div>`, `${rows.length} records`);
  }
  function investigateScreen() {
    if (state.investigationReport) return investigationReportScreen(state.investigationReport);
    return state.investigation ? investigationRunningScreen(state.investigation) : investigationIdleScreen();
  }
  function settingCard(title,key,values={}) {
    return panel(title, Object.entries(values).map(([name,value]) => {
      const display = Array.isArray(value) ? (value.length ? value.join(", ") : "[]") : value === null || value === "" ? "auto" : String(value);
      const cls = value === true ? "true" : value === false ? "false" : Array.isArray(value) && !value.length || display === "0 rules" || name === "byte_counters" ? "empty" : "";
      return `<div class="setting-row"><span>${esc(name)}</span><span class="setting-value ${cls}">${esc(display)}</span></div>`;
    }).join(""), `<code>${esc(key)}</code>`);
  }

  function render() {
    const screens = {now:nowScreen,timeline:timelineScreen,devices:devicesScreen,diagnostics:diagnosticsScreen,bandwidth:bandwidthScreen,alerts:alertsScreen,settings:settingsScreen,investigate:investigateScreen};
    chartRegistry.clear();
    chartSerial = 0;
    app.innerHTML = shell((screens[state.route] || nowScreen)());
  }

  async function fetchJson(url, key) {
    const response = await fetch(url, {headers:{Accept:"application/json"}});
    if (!response.ok) throw new Error(`${url} returned ${response.status}`);
    state.errors[key] = null;
    return response.json();
  }
  async function saveDeviceLabel(deviceId, label) {
    state.errors.deviceWrite = null;
    try {
      const response = await fetch(`/devices/${deviceId}`, {
        method:"PATCH",
        headers:{"Accept":"application/json","Content-Type":"application/json"},
        body:JSON.stringify({label}),
      });
      if (!response.ok) throw new Error(`write returned ${response.status}`);
      const updated = await response.json();
      const index = state.devices.findIndex(device => device.id === updated.id);
      if (index >= 0) state.devices[index] = updated;
      state.editingDeviceId = null;
    } catch (error) {
      state.errors.deviceWrite = error instanceof Error ? error.message : "unknown error";
    }
    render();
  }
  async function refreshInvestigations(renderRoute = false) {
    const previous = state.investigation;
    const results = await Promise.allSettled([fetchJson("/investigations/active", "investigationActive"), fetchJson("/investigations?limit=20", "investigations")]);
    if (results[0].status === "fulfilled") {
      state.investigation = results[0].value;
      if (previous?.id && !state.investigation) {
        try { state.investigationReport = await fetchJson(`/investigations/${encodeURIComponent(previous.id)}/report`, "investigationReport"); }
        catch { state.errors.investigationReport = "completed report is not available yet"; }
      }
    }
    if (results[1].status === "fulfilled") state.investigations = Array.isArray(results[1].value) ? results[1].value : results[1].value?.items || [];
    if (renderRoute && state.route === "investigate") render();
  }
  async function startInvestigation() {
    state.errors.investigationWrite = null;
    try {
      const response = await fetch("/investigations", {method:"POST", headers:{"Accept":"application/json", "Content-Type":"application/json"}, body:JSON.stringify({duration_seconds:state.investigationDuration})});
      if (!response.ok) throw new Error(`start returned ${response.status}`);
      state.investigation = await response.json(); state.investigationReport = null;
    } catch (error) { state.errors.investigationWrite = error instanceof Error ? error.message : "could not start investigation"; }
    render();
  }
  async function stopInvestigation(id) {
    if (!id) return;
    state.errors.investigationWrite = null;
    try {
      const response = await fetch(`/investigations/${encodeURIComponent(id)}/stop`, {method:"POST", headers:{Accept:"application/json"}});
      if (!response.ok) throw new Error(`stop returned ${response.status}`);
      const record = await response.json();
      state.investigation = null;
      state.investigationReport = record.report || await fetchJson(`/investigations/${encodeURIComponent(id)}/report`, "investigationReport");
      await refreshInvestigations(false);
    } catch (error) { state.errors.investigationWrite = error instanceof Error ? error.message : "could not stop investigation"; }
    render();
  }
  async function showInvestigationReport(id) {
    try { state.investigationReport = await fetchJson(`/investigations/${encodeURIComponent(id)}/report`, "investigationReport"); state.investigation = null; }
    catch (error) { state.errors.investigationWrite = error instanceof Error ? error.message : "could not load report"; }
    render();
  }
  async function initialLoad() {
    const results = await Promise.allSettled([
      fetchJson("/health","health"), fetchJson("/status","status"), fetchJson("/devices","devices"),
      fetchJson("/events?limit=100","events"), fetchJson("/metrics?limit=10000","metrics"),
      fetchJson("/alerts?limit=100","alerts"), fetchJson("/investigations/active","investigationActive"), fetchJson("/investigations?limit=20","investigations"),
    ]);
    const keys = ["health","status","devices","events","metrics","alerts","investigationActive","investigations"];
    results.forEach((result,index) => {
      const key = keys[index];
      if (result.status === "fulfilled") {
        if (key === "health") state.health = result.value.status === "ok";
        else if (key === "investigationActive") state.investigation = result.value;
        else if (key === "investigations") state.investigations = Array.isArray(result.value) ? result.value : result.value?.items || [];
        else state[key] = result.value;
      } else state.errors[key] = result.reason.message;
    });
    state.loaded = true; render();
  }
  async function refreshStatus() {
    try { state.status = await fetchJson("/status","status"); state.health = true; if (state.route === "now") render(); }
    catch { state.health = false; if (state.route === "now") render(); }
  }
  async function refreshSlow() {
    const results = await Promise.allSettled([fetchJson("/devices","devices"),fetchJson("/events?limit=100","events"),fetchJson("/alerts?limit=100","alerts")]);
    ["devices","events","alerts"].forEach((key,index)=>{ if(results[index].status==="fulfilled") state[key]=results[index].value; });
    await refreshInvestigations(false);
    const labelEditorOpen = state.route === "devices" && state.editingDeviceId !== null;
    if (["devices","timeline","alerts","investigate"].includes(state.route) && !labelEditorOpen) render();
  }

  function inspectChart(chart, requestedTs) {
    const spec = chartRegistry.get(chart.dataset.chartId);
    if (!spec?.timestamps.length) return;
    const selectedTs = spec.timestamps.reduce((nearest, ts) =>
      Math.abs(ts - requestedTs) < Math.abs(nearest - requestedTs) ? ts : nearest
    , spec.timestamps[0]);
    const ratio = (selectedTs - spec.minTs) / spec.timeRange;
    const crosshair = chart.querySelector(".chart-crosshair");
    const tooltip = chart.querySelector(".chart-tooltip");
    if (!crosshair || !tooltip) return;
    const x = 58 + ratio * (600 - 58 - 12);
    crosshair.setAttribute("x1", x.toFixed(1));
    crosshair.setAttribute("x2", x.toFixed(1));
    tooltip.style.left = `${58 / 600 * 100 + ratio * (600 - 58 - 12) / 600 * 100}%`;
    tooltip.style.transform = ratio < .18 ? "translateX(0)" : ratio > .82 ? "translateX(-100%)" : "translateX(-50%)";
    tooltip.innerHTML = `<strong>${esc(chartTime(selectedTs,spec.timeRange))}</strong>${spec.datasets.map(dataset => {
      const row = nearestRow(dataset.rows, selectedTs);
      return `<span><i style="background:${dataset.color}"></i>${esc(dataset.label)} <b>${esc(spec.valueFormatter(row?.value))}</b></span>`;
    }).join("")}`;
    chart.dataset.pointIndex = String(spec.timestamps.indexOf(selectedTs));
    chart.classList.add("inspecting");
  }

  document.addEventListener("pointermove", event => {
    const chart = event.target.closest(".interactive-chart");
    if (!chart) return;
    const spec = chartRegistry.get(chart.dataset.chartId);
    if (!spec) return;
    const rect = chart.getBoundingClientRect();
    const plotLeft = rect.width * 58 / 600, plotRight = rect.width * 12 / 600;
    const ratio = Math.max(0, Math.min(1, (event.clientX - rect.left - plotLeft) / Math.max(rect.width - plotLeft - plotRight, 1)));
    inspectChart(chart, spec.minTs + ratio * spec.timeRange);
  });
  document.addEventListener("pointerleave", event => {
    const chart = event.target.closest?.(".interactive-chart");
    if (chart && chart !== document.activeElement) chart.classList.remove("inspecting");
  }, true);
  document.addEventListener("focusin", event => {
    const chart = event.target.closest?.(".interactive-chart");
    const spec = chart && chartRegistry.get(chart.dataset.chartId);
    if (chart && spec) inspectChart(chart, spec.maxTs);
  });
  document.addEventListener("focusout", event => {
    const chart = event.target.closest?.(".interactive-chart");
    if (chart) chart.classList.remove("inspecting");
  });
  document.addEventListener("keydown", event => {
    const chart = event.target.closest?.(".interactive-chart");
    if (!chart || !["ArrowLeft","ArrowRight","Home","End"].includes(event.key)) return;
    const spec = chartRegistry.get(chart.dataset.chartId);
    if (!spec?.timestamps.length) return;
    event.preventDefault();
    const current = Number(chart.dataset.pointIndex || spec.timestamps.length - 1);
    const index = event.key === "Home" ? 0 : event.key === "End" ? spec.timestamps.length - 1
      : Math.max(0, Math.min(spec.timestamps.length - 1, current + (event.key === "ArrowLeft" ? -1 : 1)));
    inspectChart(chart, spec.timestamps[index]);
  });

  document.addEventListener("click", event => {
    const link = event.target.closest("[data-route]");
    if (link) {
      event.preventDefault(); state.route = link.dataset.route; history.pushState({}, "", link.getAttribute("href")); render();
      return;
    }
    const durationButton = event.target.closest("[data-investigation-duration]");
    if (durationButton) {
      state.investigationDuration = Number(durationButton.dataset.investigationDuration);
      render();
      return;
    }
    const start = event.target.closest("[data-investigation-start]");
    if (start) { startInvestigation(); return; }
    const stop = event.target.closest("[data-investigation-stop]");
    if (stop) { stopInvestigation(stop.dataset.investigationStop); return; }
    const report = event.target.closest("[data-investigation-report]");
    if (report) { showInvestigationReport(report.dataset.investigationReport); return; }
    const cancel = event.target.closest("[data-label-cancel]");
    if (cancel) {
      state.editingDeviceId = null;
      state.errors.deviceWrite = null;
      render();
      return;
    }
    const edit = event.target.closest("[data-label-device]");
    if (!edit) return;
    const device = state.devices.find(item => item.id === Number(edit.dataset.labelDevice));
    if (!device) return;
    state.editingDeviceId = device.id;
    state.errors.deviceWrite = null;
    render();
    requestAnimationFrame(() => {
      const input = document.querySelector(`[data-inline-label="${device.id}"]`);
      if (input) {
        input.focus();
        input.select();
      }
    });
  });
  document.addEventListener("submit", event => {
    const form = event.target.closest("[data-label-form]");
    if (!form) return;
    event.preventDefault();
    const input = form.querySelector("[name=label]");
    if (input) saveDeviceLabel(Number(form.dataset.labelForm), input.value);
  });
  addEventListener("popstate", () => { state.route = paths[location.pathname] || "now"; render(); });
  initialLoad();
  setInterval(refreshStatus, 2000);
  setInterval(refreshSlow, 30000);
})();
