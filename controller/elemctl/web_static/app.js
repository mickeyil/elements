const state = {
  relayConnected: false,
  controllerConnected: false,
  snapshot: null,
  session: null,
  strips: [],
  canvases: [],
};

const relayPill = document.getElementById("relay-pill");
const controllerPill = document.getElementById("controller-pill");
const playbackState = document.getElementById("playback-state");
const sessionId = document.getElementById("session-id");
const timeReadout = document.getElementById("time-readout");
const emptyState = document.getElementById("empty-state");
const stripList = document.getElementById("strip-list");

let ws = null;

function setPill(node, onlineText, offlineText, isOnline) {
  node.textContent = isOnline ? onlineText : offlineText;
  node.classList.toggle("pill-online", isOnline);
  node.classList.toggle("pill-offline", !isOnline);
}

function renderSummary() {
  const session = state.session;
  playbackState.textContent = session?.playback_state ?? "idle";
  sessionId.textContent = session?.session_id ?? "none";
  const t = Number(session?.current_t_rel ?? 0);
  timeReadout.textContent = `${t.toFixed(2)}s`;
  setPill(relayPill, "relay connected", "relay disconnected", state.relayConnected);
  setPill(
    controllerPill,
    "controller connected",
    "controller disconnected",
    state.controllerConnected,
  );
}

function rebuildStrips() {
  stripList.textContent = "";
  state.canvases = [];
  state.strips = Array.isArray(state.session?.strips) ? state.session.strips : [];

  if (!state.strips.length) {
    emptyState.hidden = false;
    return;
  }

  emptyState.hidden = true;
  for (const strip of state.strips) {
    const row = document.createElement("section");
    row.className = "strip-row";

    const meta = document.createElement("div");
    meta.className = "strip-meta";

    const name = document.createElement("strong");
    name.className = "strip-name";
    name.textContent = strip.name;

    const length = document.createElement("span");
    length.className = "strip-length";
    const targets = Array.isArray(strip.targets)
      ? strip.targets
          .map((target) => target?.device_uid)
          .filter((deviceUid) => typeof deviceUid === "string" && deviceUid.length)
      : [];
    length.textContent = targets.length
      ? `${strip.length} px • ${targets.join(", ")}`
      : `${strip.length} px`;

    meta.appendChild(name);
    meta.appendChild(length);

    const canvas = document.createElement("canvas");
    canvas.className = "strip-canvas";
    canvas.width = strip.length;
    canvas.height = 1;

    row.appendChild(meta);
    row.appendChild(canvas);
    stripList.appendChild(row);
    state.canvases.push(canvas);
  }
}

function applySnapshot(snapshot) {
  state.snapshot = snapshot;
  state.session = snapshot?.session ?? null;
  rebuildStrips();
  renderSummary();
}

function applyEvent(msg) {
  if (msg.event === "relay_status") {
    state.controllerConnected = Boolean(msg.controller_connected);
    renderSummary();
    return;
  }

  if (msg.event === "snapshot") {
    applySnapshot(msg);
    return;
  }

  if (msg.event === "session_start") {
    state.session = {
      session_id: msg.session_id,
      epoch: msg.epoch,
      duration: msg.duration,
      playback_state: "loaded",
      current_t_rel: 0,
      safe_intervals: msg.safe_intervals ?? [],
      strips: msg.strips ?? [],
    };
    rebuildStrips();
    renderSummary();
    return;
  }

  if (!state.session) {
    return;
  }

  if (msg.event === "state") {
    state.session.playback_state = msg.state;
    state.session.epoch = msg.epoch;
    renderSummary();
    return;
  }

  if (msg.event === "loop") {
    state.session.epoch = msg.epoch;
    state.session.current_t_rel = 0;
    renderSummary();
  }
}

function paintFrame(buffer) {
  if (!state.session || !state.canvases.length) {
    return;
  }

  const view = new DataView(buffer);
  if (view.byteLength < 8) {
    return;
  }

  const tRel = view.getFloat32(4, true);
  let offset = 8;

  for (let i = 0; i < state.strips.length; i += 1) {
    const strip = state.strips[i];
    const canvas = state.canvases[i];
    if (!canvas) {
      return;
    }
    const bytesNeeded = strip.length * 3;
    if (offset + bytesNeeded > view.byteLength) {
      return;
    }

    const ctx = canvas.getContext("2d");
    const image = ctx.createImageData(strip.length, 1);
    const src = new Uint8Array(buffer, offset, bytesNeeded);
    for (let px = 0; px < strip.length; px += 1) {
      const si = px * 3;
      const di = px * 4;
      image.data[di] = src[si];
      image.data[di + 1] = src[si + 1];
      image.data[di + 2] = src[si + 2];
      image.data[di + 3] = 255;
    }
    ctx.putImageData(image, 0, 0);
    offset += bytesNeeded;
  }

  state.session.current_t_rel = tRel;
  renderSummary();
}

function connect() {
  state.relayConnected = false;
  renderSummary();

  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  ws = new WebSocket(`${proto}//${window.location.host}/ws`);
  ws.binaryType = "arraybuffer";

  ws.addEventListener("open", () => {
    state.relayConnected = true;
    renderSummary();
  });

  ws.addEventListener("message", async (event) => {
    if (typeof event.data === "string") {
      applyEvent(JSON.parse(event.data));
      return;
    }

    const buffer = event.data instanceof ArrayBuffer
      ? event.data
      : await event.data.arrayBuffer();
    paintFrame(buffer);
  });

  ws.addEventListener("close", () => {
    state.relayConnected = false;
    state.controllerConnected = false;
    state.session = null;
    rebuildStrips();
    renderSummary();
    window.setTimeout(connect, 1000);
  });
}

renderSummary();
connect();
