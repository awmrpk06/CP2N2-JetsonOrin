const dataStreams = [
  "pattern_gate_session",
  "pattern_gate_trial",
  "pattern_gate_gate",
  "pattern_gate_features",
  "pattern_gate_decision",
  "pattern_gate_control_status",
  "cl_spikes",
  "cl_stims"
];

const LIFECYCLE = [
  "discovered",
  "reserved",
  "preparing",
  "running",
  "validating",
  "cooldown",
  "ready"
];

function text(root, name, value) {
  const element = root.querySelector(`[data-bind="${name}"]`);
  if (element) element.textContent = value ?? "—";
}

function shortHash(value) {
  return value ? `${value.slice(0, 9)}…${value.slice(-5)}` : "—";
}

function shortId(value) {
  return value ? `${value.slice(0, 8)}…${value.slice(-4)}` : "—";
}

function validateReplayBundle(bundle) {
  if (bundle?.demo_bundle_version !== "1.1") {
    throw new Error("Unsupported BioPattern Gate demo bundle.");
  }
  if (bundle?.evidence?.level !== "E3" || bundle?.evidence?.biological_claim !== false) {
    throw new Error("Replay evidence boundary is missing or unsafe.");
  }
  if (!Array.isArray(bundle.trials) || bundle.trials.length === 0) {
    throw new Error("Replay contains no trials.");
  }
  const control = bundle?.control_plane;
  if (
    control?.provenance !== "recorded_audited_mcp_e3_run"
    || control?.audit_chain_verified !== true
    || control?.lease_released !== true
    || control?.raw_substrate_output_exposed_to_agent !== false
  ) {
    throw new Error("Verified control-plane evidence is missing or unsafe.");
  }
  if (
    !Array.isArray(control.lifecycle_evidence)
    || !Array.isArray(control.mcp_steps)
    || control.audit_event_count !== control.audit_request_count * 2
  ) {
    throw new Error("Control-plane evidence is incomplete.");
  }
  return bundle;
}

class PatternGateDashboard {
  constructor(root) {
    this.root = root;
    this.bundle = null;
    this.index = 0;
    this.timer = null;
    this.speed = 1;
    this.liveTrial = {};
    this.bindControls();
    this.renderLifecycle(0);
  }

  bindControls() {
    this.root.querySelector('[data-action="previous"]')?.addEventListener("click", () => {
      this.pause();
      this.show(this.index - 1);
    });
    this.root.querySelector('[data-action="next"]')?.addEventListener("click", () => {
      this.pause();
      this.show(this.index + 1);
    });
    this.root.querySelector('[data-action="play"]')?.addEventListener("click", () => {
      this.timer ? this.pause() : this.play();
    });
    this.root.querySelector('[data-action="speed"]')?.addEventListener("change", (event) => {
      this.speed = Number(event.target.value) || 1;
      if (this.timer) {
        this.pause();
        this.play();
      }
    });
  }

  load(bundle) {
    this.bundle = validateReplayBundle(bundle);
    this.index = 0;
    text(this.root, "evidence", bundle.evidence.level);
    text(this.root, "mode", bundle.mode.toUpperCase());
    text(this.root, "run-status", bundle.run.status.toUpperCase());
    text(this.root, "cp-proof", "MCP AUDITED");
    text(this.root, "trial-total", bundle.trials.length);
    text(this.root, "resource-id", bundle.control_plane.resource_id);
    text(this.root, "preset-id", bundle.control_plane.preset_id);
    text(this.root, "config-hash", shortHash(bundle.hashes.config_sha256));
    text(this.root, "decoder-hash", shortHash(bundle.hashes.decoder_sha256));
    text(this.root, "lease-state", bundle.control_plane.lease_released ? "released" : "active");
    text(this.root, "audit-state", "CHAIN VERIFIED");
    text(this.root, "audit-chain", "verified");
    text(this.root, "audit-event-count", bundle.control_plane.audit_event_count);
    text(this.root, "audit-request-count", bundle.control_plane.audit_request_count);
    text(this.root, "audit-head", shortHash(bundle.control_plane.audit_head_sha256));
    text(this.root, "result-artifact", shortHash(bundle.control_plane.result_artifact_sha256));
    text(this.root, "application-source", shortHash(bundle.hashes.application_source_sha256));
    text(
      this.root,
      "raw-output-state",
      bundle.control_plane.raw_substrate_output_exposed_to_agent ? "exposed" : "blocked"
    );
    text(this.root, "correlation-id", `correlation ${shortId(bundle.run.orchestration_correlation_id)}`);
    text(this.root, "demo-status", "Audited MCP E3 replay loaded · chain verified.");
    this.renderLifecycleEvidence(bundle.control_plane.lifecycle_evidence);
    this.renderMcpFlow(bundle.control_plane.mcp_steps);
    this.show(0);
  }

  show(nextIndex) {
    if (!this.bundle) return;
    const maximum = this.bundle.trials.length - 1;
    this.index = Math.max(0, Math.min(maximum, nextIndex));
    const trial = this.bundle.trials[this.index];
    const completed = this.bundle.trials.slice(0, this.index + 1);
    const scored = completed.filter((item) => item.correct !== null);
    const correct = scored.filter((item) => item.correct).length;

    text(this.root, "trial-number", this.index + 1);
    text(this.root, "expected-label", trial.expected_label ?? "SHAM");
    text(this.root, "predicted-label", trial.predicted_label);
    text(this.root, "route", trial.route.toUpperCase());
    text(this.root, "correctness", trial.correct === null ? "CONTROL" : trial.correct ? "CORRECT" : "INCORRECT");
    text(this.root, "probability-a", `P(A) ${trial.probability_a.toFixed(3)}`);
    text(this.root, "commit", shortHash(trial.decision_commit_sha256));
    text(this.root, "score", scored.length ? `${correct} / ${scored.length}` : "—");
    text(this.root, "token-label", trial.expected_label ?? "S");

    const probability = this.root.querySelector("[data-probability-fill]");
    if (probability) probability.style.width = `${trial.probability_a * 100}%`;

    const progress = this.root.querySelector("[data-progress-fill]");
    if (progress) progress.style.width = `${((this.index + 1) / this.bundle.trials.length) * 100}%`;

    this.renderSpikes(trial.event_timestamps_ms);
    this.renderFeatures(trial.feature_values);
    this.animateGate(trial);
  }

  renderSpikes(events = []) {
    const raster = this.root.querySelector("[data-spike-raster]");
    if (!raster) return;
    raster.replaceChildren();
    events.forEach((timestamp, index) => {
      const spike = document.createElement("i");
      spike.className = "spike";
      spike.style.left = `${Math.max(0, Math.min(99.5, timestamp))}%`;
      spike.style.animationDelay = `${index * 35}ms`;
      raster.append(spike);
    });
  }

  renderFeatures(features = {}) {
    const container = this.root.querySelector("[data-feature-bars]");
    if (!container) return;
    container.replaceChildren();
    const bins = Object.entries(features)
      .filter(([name]) => name.includes("bin-"))
      .slice(0, 5);
    const maximum = Math.max(1, ...bins.map(([, value]) => Number(value)));
    bins.forEach(([name, value], index) => {
      const row = document.createElement("div");
      row.className = "feature-row";
      row.innerHTML = `
        <span>bin ${String(index + 1).padStart(2, "0")}</span>
        <i class="feature-track"><i class="feature-fill" style="width:${(Number(value) / maximum) * 100}%"></i></i>
        <strong>${Number(value).toFixed(0)}</strong>
      `;
      container.append(row);
    });
  }

  animateGate(trial) {
    const token = this.root.querySelector("[data-gate-token]");
    if (!token) return;
    token.className = "gate-token";
    this.root.querySelectorAll(".route").forEach((route) => route.classList.remove("is-active"));
    void token.offsetWidth;
    token.classList.add(`route-${trial.route}`);
    if (trial.correct === true) token.classList.add("is-correct");
    if (trial.correct === false) token.classList.add("is-incorrect");
    this.root.querySelector(`.route--${trial.route}`)?.classList.add("is-active");
  }

  renderLifecycle(currentIndex) {
    const container = this.root.querySelector("[data-lifecycle]");
    if (!container) return;
    container.replaceChildren();
    LIFECYCLE.forEach((state, index) => {
      const item = document.createElement("li");
      item.textContent = state;
      if (index < currentIndex || currentIndex === LIFECYCLE.length - 1) item.classList.add("is-complete");
      if (index === currentIndex && currentIndex !== LIFECYCLE.length - 1) item.classList.add("is-current");
      container.append(item);
    });
  }

  renderLifecycleEvidence(records) {
    const container = this.root.querySelector("[data-lifecycle]");
    if (!container) return;
    container.replaceChildren();
    records.forEach((record) => {
      const item = document.createElement("li");
      item.textContent = record.state;
      item.classList.add("is-complete");
      item.title = `${record.source} · ${record.occurred_at}`;
      item.dataset.proofSha256 = record.proof_sha256;
      container.append(item);
    });
  }

  renderMcpFlow(steps) {
    const container = this.root.querySelector("[data-mcp-flow]");
    if (!container) return;
    container.replaceChildren();
    steps.forEach((step) => {
      const item = document.createElement("li");
      const status = document.createElement("strong");
      const tool = document.createElement("span");
      const proof = document.createElement("code");
      status.textContent = step.ok ? "✓ success" : "failed";
      tool.textContent = step.tool ?? "automatic release";
      proof.textContent = step.audit_event_sha256
        ? shortHash(step.audit_event_sha256)
        : "lease absent";
      item.title = step.request_id
        ? `${step.stage} · request ${step.request_id}`
        : step.stage;
      item.append(status, tool, proof);
      container.append(item);
    });
  }

  play() {
    if (!this.bundle || this.timer) return;
    const button = this.root.querySelector('[data-action="play"]');
    if (button) button.textContent = "Pause";
    if (this.index >= this.bundle.trials.length - 1) this.show(0);
    this.timer = window.setInterval(() => {
      if (this.index >= this.bundle.trials.length - 1) {
        this.pause();
        return;
      }
      this.show(this.index + 1);
    }, 1500 / this.speed);
  }

  pause() {
    if (this.timer) window.clearInterval(this.timer);
    this.timer = null;
    const button = this.root.querySelector('[data-action="play"]');
    if (button) button.textContent = "Play replay";
  }

  update(stream, data) {
    text(this.root, "mode", "LIVE");
    text(this.root, "demo-status", `${stream.replace("pattern_gate_", "")} update received.`);
    if (stream === "pattern_gate_session") {
      text(this.root, "run-status", String(data.status ?? "running").toUpperCase());
      text(this.root, "evidence", data.evidence_level ?? "UNKNOWN");
    } else if (stream === "pattern_gate_control_status") {
      text(this.root, "lease-state", data.lease_state ?? "unknown");
      const index = LIFECYCLE.indexOf(data.lifecycle_state);
      if (index >= 0) this.renderLifecycle(index);
    } else if (stream === "cl_spikes" || stream === "cl_stims") {
      text(
        this.root,
        "demo-status",
        `${stream === "cl_spikes" ? "Neural spike" : "Stimulation"} event received.`
      );
    } else {
      this.liveTrial = { ...this.liveTrial, ...data };
      if (stream === "pattern_gate_decision" && this.liveTrial.route) {
        this.bundle = {
          trials: [this.liveTrial],
          evidence: { level: "E3", biological_claim: false },
          mode: "live",
          run: { status: "running" },
          control_plane: { resource_id: "provider stream", preset_id: "pattern_gate_v1" },
          hashes: {}
        };
        this.show(0);
      }
    }
  }
}

async function mountReplayDemo(root, bundleUrl) {
  const dashboard = new PatternGateDashboard(root);
  try {
    const response = await fetch(bundleUrl, { cache: "no-store" });
    if (!response.ok) throw new Error(`Replay request failed (${response.status}).`);
    dashboard.load(await response.json());
  } catch (error) {
    text(root, "run-status", "FAILED");
    text(root, "demo-status", error.message);
    throw error;
  }
  return dashboard;
}

function createVisualiser(uniqueId, div) {
  const root = div.querySelector("[data-pattern-gate-root]") ?? div;
  const dashboard = new PatternGateDashboard(root);
  text(root, "demo-status", `CL visualiser ${uniqueId} ready for application streams.`);
  function reset() {
    dashboard.pause();
    dashboard.liveTrial = {};
    text(root, "mode", "LIVE");
    text(root, "run-status", "READY");
    text(root, "demo-status", "Waiting for CL application streams.");
  }
  function process(dataStreamName, timestamp, data) {
    void timestamp;
    dashboard.update(dataStreamName, data);
  }
  function draw(browserTimestampMs, dataStreamTimestamp) {
    void browserTimestampMs;
    void dataStreamTimestamp;
  }
  function attributesReset(dataStreamName, initialAttributes) {
    void dataStreamName;
    void initialAttributes;
  }
  function attributesUpdated(dataStreamName, updatedAttributes) {
    void dataStreamName;
    void updatedAttributes;
  }
  return {
    bufferMs: 100,
    reset,
    process,
    draw,
    attributesReset,
    attributesUpdated,
    // Kept for the local replay harness that predates the CL 1.0 interface.
    update(stream, data) {
      dashboard.update(stream, data);
    }
  };
}

export {
  PatternGateDashboard,
  createVisualiser,
  dataStreams,
  mountReplayDemo,
  validateReplayBundle
};
