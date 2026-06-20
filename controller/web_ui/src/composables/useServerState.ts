import {
  computed,
  inject,
  onBeforeUnmount,
  onMounted,
  ref,
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
}

interface SessionStrip {
  strip_id?: string;
  length?: number;
}

export interface SessionState {
  session_id?: number | string;
  epoch?: number;
  duration?: number;
  state?: string;            // v3 playback state: idle/loaded/playing/paused/ended
  current_t_rel?: number;
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

export interface SnapshotEvent {
  devices?: SnapshotDevice[];
  layouts?: Record<string, LayoutPayload>;
  server_version?: string | null;
  session?: SessionState | null;
  programs?: SnapshotProgram[];
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

    const view = new DataView(buffer);
    if (view.byteLength < 8) {
      return;
    }

    session.value.current_t_rel = view.getFloat32(4, true);

    if (!logicalStrips.value.length || !simTargets.value.length) {
      return;
    }

    let offset = 8;
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

  function applyEvent(msg: Record<string, unknown>): void {
    if (msg.event === 'server_status') {
      controllerConnected.value = Boolean(msg.controller_connected);
      return;
    }

    if (msg.event === 'snapshot') {
      applySnapshot(msg as SnapshotEvent);
    }
  }

  async function handleWsMessage(event: MessageEvent<string | Blob | ArrayBuffer>): Promise<void> {
    if (typeof event.data === 'string') {
      applyEvent(JSON.parse(event.data) as Record<string, unknown>);
      return;
    }

    const buffer = event.data instanceof ArrayBuffer ? event.data : await event.data.arrayBuffer();
    ingestFrame(buffer);
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
