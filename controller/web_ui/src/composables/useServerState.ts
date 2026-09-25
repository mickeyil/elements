import {
  computed,
  inject,
  onBeforeUnmount,
  onMounted,
  ref,
  shallowReactive,
  shallowRef,
  type InjectionKey,
} from 'vue';

import {
  attachTargetCanvas,
  paintTargets,
  type LayoutPayload,
  type SimTarget,
} from '../lib/viewerRenderer';
import { deriveSimTargets } from '../lib/viewerModel';
import type { FirmwareState } from '../lib/firmwareModel';
import { applyDeviceLogs, type DeviceLogRecord, type DeviceLogWireRecord } from '../lib/logsModel';
import type { PanelView } from '../lib/panelModel';

// Every binary websocket message leads with its controller-protocol kind byte
// (controller_protocol.py).
const KIND_FRAME = 0x02;         // a program frame
const KIND_DEVICE_FRAME = 0x03;  // one panel-driven device's picture

export interface SnapshotDevice {
  // v3 device shape (the status panel renders these)
  uid?: string;
  configured?: boolean;
  status?: 'online' | 'offline' | 'discovered';
  strip_id?: string;
  length?: number;
  label?: string | null;
  phase?: string;
  target_intent?: string;
  clock_synced?: boolean | null;
  clock_skew_ms?: number | null;
  // From the device's latest DISCOVER; null until one is heard. version is
  // '' for firmware that predates reporting it.
  version?: string | null;
  ip?: string | null;
  // The controller's verdict that the built image would change what this
  // device runs; the status page offers "Update firmware" only when true.
  update_available?: boolean;
  // Who drives the device: the loaded show, or the operator panel until the
  // next load reclaims it; panel is what the panel has it doing (null while
  // the show owns it).
  owner?: 'show' | 'panel';
  panel?: PanelView | null;
}

interface SessionStrip {
  strip_id?: string;
  length?: number;
}

export interface SessionState {
  session_id?: number | string;
  epoch?: number;
  duration?: number;
  loop?: boolean;            // a looping session replays each duration, never ends
  state?: string;            // v3 playback state: idle/loaded/playing/paused/ended
  program_id?: string | null;
  // From the latest preview frame: loop cycle and ms into it.
  current_cycle?: number;
  current_t_ms?: number;
  safe_intervals?: unknown[];
  strips?: SessionStrip[];
}

export interface SnapshotProgram {
  program_id: string;
  // Null for entries the compiler rejected; populated otherwise.
  beat?: number | null;
  duration?: number | null;
  strips?: string[] | null;
  error?: string | null;
}

// A program of the panel library, which the operator panel runs on one strip.
export interface SnapshotLibraryProgram {
  program_id: string;
  error?: string | null;
}

export interface SnapshotEvent {
  devices?: SnapshotDevice[];
  layouts?: Record<string, LayoutPayload>;
  server_version?: string | null;
  session?: SessionState | null;
  programs?: SnapshotProgram[];
  library?: SnapshotLibraryProgram[];
  firmware?: FirmwareState | null;
}

export interface EmptyState {
  title: string;
  copy: string;
}

export function useServerState() {
  const serverConnected = ref(false);
  const controllerConnected = ref(false);
  const snapshot = shallowRef<SnapshotEvent | null>(null);
  const session = ref<SessionState | null>(null);
  const logicalStrips = ref<SessionStrip[]>([]);
  const simTargets = ref<SimTarget[]>([]);
  const latestSlices = shallowRef<Uint8Array[]>([]);
  // uid -> rgb of the latest picture of each panel-driven device.
  const deviceFrames = shallowReactive(new Map<string, Uint8Array>());
  // Kept across a dropped socket or controller: the rows before a drop are
  // the ones worth reading, and the next history message replaces them.
  const deviceLogs = shallowRef<DeviceLogRecord[]>([]);
  const frameDirty = ref(false);
  const rafPending = ref(false);

  const emptyState = computed<EmptyState | null>(() => {
    if (!session.value || !logicalStrips.value.length) {
      return {
        title: 'No active session',
        copy: 'Load and play an animation from the writer client to see frames here.',
      };
    }
    if (!simTargets.value.length) {
      return {
        title: 'No simulated targets in current session',
        copy: 'This session does not include any sim devices that can be rendered here.',
      };
    }
    return null;
  });

  let ws: WebSocket | null = null;
  let reconnectTimer: number | null = null;
  let disposed = false;

  function getSnapshotDevices(): SnapshotDevice[] {
    return Array.isArray(snapshot.value?.devices) ? snapshot.value.devices : [];
  }

  function getSnapshotLayouts(): Record<string, LayoutPayload> {
    const layouts = snapshot.value?.layouts;
    return layouts && typeof layouts === 'object' ? layouts : {};
  }

  function clearFrameState(): void {
    latestSlices.value = [];
    frameDirty.value = false;
  }

  function rebuildTargets(): void {
    logicalStrips.value = Array.isArray(session.value?.strips) ? session.value?.strips ?? [] : [];
    clearFrameState();

    if (!session.value || !logicalStrips.value.length) {
      simTargets.value = [];
      return;
    }

    simTargets.value = deriveSimTargets(
      getSnapshotDevices(),
      session.value?.strips ?? [],
      getSnapshotLayouts(),
    );
  }

  function applySnapshot(nextSnapshot: SnapshotEvent): void {
    snapshot.value = nextSnapshot;
    session.value = nextSnapshot?.session ?? null;
    rebuildTargets();
  }

  function requestPaint(): void {
    if (rafPending.value) {
      return;
    }
    rafPending.value = true;
    window.requestAnimationFrame(() => {
      rafPending.value = false;
      if (!frameDirty.value) {
        return;
      }
      paintTargets(simTargets.value, latestSlices.value);
      frameDirty.value = false;
    });
  }

  function ingestFrame(buffer: ArrayBuffer): void {
    if (!session.value) {
      return;
    }

    // After the kind byte: u32 frame_index, u32 cycle, u32 t_ms, then each
    // strip's rgb (controller_protocol.py).
    const view = new DataView(buffer);
    if (view.byteLength < 13) {
      return;
    }

    session.value.current_cycle = view.getUint32(5, true);
    session.value.current_t_ms = view.getUint32(9, true);

    if (!logicalStrips.value.length || !simTargets.value.length) {
      return;
    }

    let offset = 13;
    const nextSlices: Uint8Array[] = [];
    for (const strip of logicalStrips.value) {
      const stripLength = Number(strip?.length ?? 0);
      const bytesNeeded = stripLength * 3;
      if (offset + bytesNeeded > view.byteLength) {
        return;
      }
      nextSlices.push(new Uint8Array(buffer, offset, bytesNeeded).slice());
      offset += bytesNeeded;
    }

    latestSlices.value = nextSlices;
    frameDirty.value = true;
    requestPaint();
  }

  // After the kind byte: u8 uid_len, the uid, then the device's rgb.
  function ingestDeviceFrame(buffer: ArrayBuffer): void {
    const bytes = new Uint8Array(buffer);
    const rgbStart = 2 + bytes[1];
    const uid = new TextDecoder().decode(bytes.subarray(2, rgbStart));
    deviceFrames.set(uid, bytes.subarray(rgbStart));
  }

  function applyEvent(msg: Record<string, unknown>): void {
    if (msg.event === 'server_status') {
      controllerConnected.value = Boolean(msg.controller_connected);
      if (!controllerConnected.value) {
        deviceFrames.clear();
      }
      return;
    }

    if (msg.event === 'snapshot') {
      applySnapshot(msg as SnapshotEvent);
      return;
    }

    if (msg.event === 'device_logs') {
      const records = Array.isArray(msg.records) ? msg.records as DeviceLogWireRecord[] : [];
      deviceLogs.value = applyDeviceLogs(deviceLogs.value, records, Boolean(msg.history));
    }
  }

  async function handleWsMessage(event: MessageEvent<string | Blob | ArrayBuffer>): Promise<void> {
    if (typeof event.data === 'string') {
      applyEvent(JSON.parse(event.data) as Record<string, unknown>);
      return;
    }

    const buffer = event.data instanceof ArrayBuffer ? event.data : await event.data.arrayBuffer();
    const kind = new Uint8Array(buffer)[0];
    if (kind === KIND_FRAME) {
      ingestFrame(buffer);
    } else if (kind === KIND_DEVICE_FRAME) {
      ingestDeviceFrame(buffer);
    }
  }

  function connect(): void {
    serverConnected.value = false;

    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(`${proto}//${window.location.host}/ws`);
    ws.binaryType = 'arraybuffer';

    ws.addEventListener('open', () => {
      serverConnected.value = true;
    });

    ws.addEventListener('message', (event) => {
      void handleWsMessage(event);
    });

    ws.addEventListener('close', () => {
      serverConnected.value = false;
      controllerConnected.value = false;
      snapshot.value = null;
      session.value = null;
      deviceFrames.clear();
      rebuildTargets();
      if (!disposed) {
        reconnectTimer = window.setTimeout(connect, 1000);
      }
    });
  }

  function assignCanvas(target: SimTarget, element: unknown): void {
    const canvas = element instanceof HTMLCanvasElement ? element : null;
    attachTargetCanvas(target, canvas);
    if (canvas && latestSlices.value.length) {
      paintTargets([target], latestSlices.value);
    }
  }

  onMounted(() => {
    connect();
  });

  onBeforeUnmount(() => {
    disposed = true;
    if (reconnectTimer !== null) {
      window.clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
    ws?.close();
    ws = null;
  });

  return {
    assignCanvas,
    controllerConnected,
    deviceFrames,
    deviceLogs,
    emptyState,
    serverConnected,
    session,
    snapshot,
    simTargets,
  };
}

export type ServerState = ReturnType<typeof useServerState>;

export const serverStateKey: InjectionKey<ServerState> = Symbol('server-state');

export function useInjectedServerState(): ServerState {
  const serverState = inject(serverStateKey);
  if (!serverState) {
    throw new Error('Server state is not available.');
  }
  return serverState;
}
