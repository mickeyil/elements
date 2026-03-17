const state = {
  relayConnected: false,
  controllerConnected: false,
  snapshot: null,
  session: null,
  logicalStrips: [],
  simTargets: [],
  latestSlices: [],
  frameDirty: false,
  rafPending: false,
};

const relayPill = document.getElementById("relay-pill");
const controllerPill = document.getElementById("controller-pill");
const playbackState = document.getElementById("playback-state");
const sessionId = document.getElementById("session-id");
const timeReadout = document.getElementById("time-readout");
const emptyState = document.getElementById("empty-state");
const emptyTitle = document.getElementById("empty-title");
const emptyCopy = document.getElementById("empty-copy");
const stripList = document.getElementById("strip-list");

let ws = null;

function setPill(node, onlineText, offlineText, isOnline) {
  node.textContent = isOnline ? onlineText : offlineText;
  node.classList.toggle("pill-online", isOnline);
  node.classList.toggle("pill-offline", !isOnline);
}

function setEmptyState(title, copy) {
  emptyTitle.textContent = title;
  emptyCopy.textContent = copy;
  emptyState.hidden = false;
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

function getSnapshotDevices() {
  return Array.isArray(state.snapshot?.devices) ? state.snapshot.devices : [];
}

function getDeviceConnected(deviceUid) {
  const device = getSnapshotDevices().find((item) => item?.device_uid === deviceUid);
  return Boolean(device?.connected);
}

function clearFrameState() {
  state.latestSlices = [];
  state.frameDirty = false;
}

function updateTargetConnection(target, connected) {
  target.connected = connected;
  setPill(target.statusNode, "connected", "disconnected", connected);
  target.panel.classList.toggle("target-panel-offline", !connected);
}

function rebuildTargets() {
  stripList.textContent = "";
  state.simTargets = [];
  state.logicalStrips = Array.isArray(state.session?.strips) ? state.session.strips : [];
  clearFrameState();

  if (!state.session || !state.logicalStrips.length) {
    setEmptyState(
      "No active session",
      "Load and play an animation from the writer client to see frames here.",
    );
    return;
  }

  for (let logicalIndex = 0; logicalIndex < state.logicalStrips.length; logicalIndex += 1) {
    const strip = state.logicalStrips[logicalIndex];
    const targets = Array.isArray(strip?.targets) ? strip.targets : [];
    for (const target of targets) {
      if (target?.device_type !== "sim") {
        continue;
      }

      const panel = document.createElement("section");
      panel.className = "target-panel";

      const head = document.createElement("div");
      head.className = "target-head";

      const info = document.createElement("div");
      info.className = "target-info";

      const name = document.createElement("strong");
      name.className = "target-name";
      name.textContent = target.device_uid;

      const stripName = document.createElement("span");
      stripName.className = "target-strip";
      stripName.textContent = `strip ${strip.name}`;

      const lengths = document.createElement("span");
      lengths.className = "target-length";
      lengths.textContent = `logical ${strip.length} / physical ${target.length} px`;

      info.appendChild(name);
      info.appendChild(stripName);
      info.appendChild(lengths);

      const statusNode = document.createElement("span");
      statusNode.className = "pill target-pill pill-offline";

      head.appendChild(info);
      head.appendChild(statusNode);

      const canvas = document.createElement("canvas");
      canvas.className = "target-canvas";
      canvas.width = target.length;
      canvas.height = 1;

      panel.appendChild(head);
      panel.appendChild(canvas);
      stripList.appendChild(panel);

      const simTarget = {
        deviceUid: target.device_uid,
        logicalIndex,
        logicalLength: strip.length,
        physicalLength: target.length,
        connected: false,
        panel,
        statusNode,
        canvas,
        ctx: canvas.getContext("2d"),
      };
      updateTargetConnection(simTarget, getDeviceConnected(target.device_uid));
      state.simTargets.push(simTarget);
    }
  }

  if (!state.simTargets.length) {
    setEmptyState(
      "No simulated targets in current session",
      "This session does not include any sim devices that can be rendered here.",
    );
    return;
  }

  emptyState.hidden = true;
}

function applySnapshot(snapshot) {
  state.snapshot = snapshot;
  state.session = snapshot?.session ?? null;
  rebuildTargets();
  renderSummary();
}

function handleDeviceStatus(msg) {
  const devices = getSnapshotDevices();
  const deviceUid = msg.device_uid;
  const connected = Boolean(msg.connected);

  for (const device of devices) {
    if (device?.device_uid === deviceUid) {
      device.connected = connected;
      break;
    }
  }

  for (const target of state.simTargets) {
    if (target.deviceUid === deviceUid) {
      updateTargetConnection(target, connected);
      break;
    }
  }
}

function paintTargets() {
  for (const target of state.simTargets) {
    const image = target.ctx.createImageData(target.physicalLength, 1);
    const src = state.latestSlices[target.logicalIndex] ?? null;
    const srcPixels = src
      ? Math.min(target.logicalLength, target.physicalLength, Math.floor(src.length / 3))
      : 0;

    for (let px = 0; px < target.physicalLength; px += 1) {
      const di = px * 4;
      if (px < srcPixels) {
        const si = px * 3;
        image.data[di] = src[si];
        image.data[di + 1] = src[si + 1];
        image.data[di + 2] = src[si + 2];
      }
      image.data[di + 3] = 255;
    }

    target.ctx.putImageData(image, 0, 0);
  }
}

function requestPaint() {
  if (state.rafPending) {
    return;
  }
  state.rafPending = true;
  window.requestAnimationFrame(() => {
    state.rafPending = false;
    if (!state.frameDirty) {
      return;
    }
    paintTargets();
    state.frameDirty = false;
  });
}

function ingestFrame(buffer) {
  if (!state.session) {
    return;
  }

  const view = new DataView(buffer);
  if (view.byteLength < 8) {
    return;
  }

  state.session.current_t_rel = view.getFloat32(4, true);
  renderSummary();

  if (!state.logicalStrips.length || !state.simTargets.length) {
    return;
  }

  let offset = 8;
  const nextSlices = [];

  for (const strip of state.logicalStrips) {
    const bytesNeeded = strip.length * 3;
    if (offset + bytesNeeded > view.byteLength) {
      return;
    }
    nextSlices.push(new Uint8Array(buffer, offset, bytesNeeded).slice());
    offset += bytesNeeded;
  }

  state.latestSlices = nextSlices;
  state.frameDirty = true;
  requestPaint();
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
    rebuildTargets();
    renderSummary();
    return;
  }

  if (msg.event === "device_status") {
    handleDeviceStatus(msg);
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
    ingestFrame(buffer);
  });

  ws.addEventListener("close", () => {
    state.relayConnected = false;
    state.controllerConnected = false;
    state.snapshot = null;
    state.session = null;
    rebuildTargets();
    renderSummary();
    window.setTimeout(connect, 1000);
  });
}

renderSummary();
rebuildTargets();
connect();
