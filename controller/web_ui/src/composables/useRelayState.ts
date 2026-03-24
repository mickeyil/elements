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
  measureLayout,
  paintTargets,
  type LayoutPayload,
  type SimTarget,
} from '../lib/viewerRenderer';

export interface SnapshotDevice {
  device_id?: number;
  device_uid?: string;
  device_type?: string;
  strip?: string;
  length?: number;
  connected?: boolean;
  last_seen?: number | null;
  clock_state?: string;
  clock_offset_ms?: number | null;
  clock_rtt_ms?: number | null;
  clock_last_sync_age_s?: number | null;
}

interface SessionStripTarget {
  device_uid?: string;
  device_type?: string;
  length?: number;
}

interface SessionStrip {
  name?: string;
  length?: number;
  targets?: SessionStripTarget[];
}

export interface SessionState {
  session_id?: number | string;
  epoch?: number;
  duration?: number;
  playback_state?: string;
  current_t_rel?: number;
  safe_intervals?: unknown[];
  strips?: SessionStrip[];
}

export interface SnapshotEvent {
  devices?: SnapshotDevice[];
  layouts?: Record<string, LayoutPayload>;
  relay_version?: string | null;
  session?: SessionState | null;
}

export interface EmptyState {
  title: string;
  copy: string;
}

export function useRelayState() {
  const relayConnected = ref(false);
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

  function getDeviceConnected(deviceUid: string): boolean {
    const device = getSnapshotDevices().find((item) => item?.device_uid === deviceUid);
    return Boolean(device?.connected);
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

    const layouts = getSnapshotLayouts();
    const nextTargets: SimTarget[] = [];

    for (let logicalIndex = 0; logicalIndex < logicalStrips.value.length; logicalIndex += 1) {
      const strip = logicalStrips.value[logicalIndex];
      const targets = Array.isArray(strip?.targets) ? strip.targets : [];
      for (const target of targets) {
        if (target?.device_type !== 'sim' || !target?.device_uid) {
          continue;
        }

        const layout = layouts[target.device_uid] ?? null;
        const { gridWidth, gridHeight } = measureLayout(layout);
        nextTargets.push({
          deviceUid: target.device_uid,
          stripName: strip?.name ?? 'unknown',
          logicalIndex,
          logicalLength: Number(strip?.length ?? 0),
          physicalLength: Number(target?.length ?? 0),
          connected: getDeviceConnected(target.device_uid),
          layout,
          gridWidth,
          gridHeight,
          canvas: null,
          ctx: null,
        });
      }
    }

    simTargets.value = nextTargets;
  }

  function applySnapshot(nextSnapshot: SnapshotEvent): void {
    snapshot.value = nextSnapshot;
    session.value = nextSnapshot?.session ?? null;
    rebuildTargets();
  }

  function handleDeviceStatus(msg: {
    device_uid?: string;
    connected?: boolean;
    last_seen?: number | null;
    clock_state?: string;
    clock_offset_ms?: number | null;
    clock_rtt_ms?: number | null;
    clock_last_sync_age_s?: number | null;
  }): void {
    const deviceUid = msg.device_uid;
    if (!deviceUid) {
      return;
    }

    const connected = Boolean(msg.connected);
    const hasLastSeen = Object.prototype.hasOwnProperty.call(msg, 'last_seen');
    const hasConnected = Object.prototype.hasOwnProperty.call(msg, 'connected');
    const hasClockState = Object.prototype.hasOwnProperty.call(msg, 'clock_state');
    const hasClockOffset = Object.prototype.hasOwnProperty.call(msg, 'clock_offset_ms');
    const hasClockRtt = Object.prototype.hasOwnProperty.call(msg, 'clock_rtt_ms');
    const hasClockAge = Object.prototype.hasOwnProperty.call(msg, 'clock_last_sync_age_s');
    const currentSnapshot = snapshot.value;
    const devices = Array.isArray(currentSnapshot?.devices) ? currentSnapshot.devices : [];
    if (currentSnapshot && devices.length) {
      let updated = false;
      const nextDevices = devices.map((device) => {
        if (device?.device_uid !== deviceUid) {
          return device;
        }

        updated = true;
        return {
          ...device,
          ...(hasConnected ? { connected } : {}),
          ...(hasLastSeen ? { last_seen: msg.last_seen ?? null } : {}),
          ...(hasClockState ? { clock_state: msg.clock_state } : {}),
          ...(hasClockOffset ? { clock_offset_ms: msg.clock_offset_ms ?? null } : {}),
          ...(hasClockRtt ? { clock_rtt_ms: msg.clock_rtt_ms ?? null } : {}),
          ...(hasClockAge ? { clock_last_sync_age_s: msg.clock_last_sync_age_s ?? null } : {}),
        };
      });

      if (updated) {
        snapshot.value = {
          ...currentSnapshot,
          devices: nextDevices,
        };
      }
    }

    for (const target of simTargets.value) {
      if (target.deviceUid === deviceUid && hasConnected) {
        target.connected = connected;
        break;
      }
    }
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
    if (msg.event === 'relay_status') {
      controllerConnected.value = Boolean(msg.controller_connected);
      return;
    }

    if (msg.event === 'snapshot') {
      applySnapshot(msg as SnapshotEvent);
      return;
    }

    if (msg.event === 'session_start') {
      session.value = {
        session_id: msg.session_id as number | string | undefined,
        epoch: msg.epoch as number | undefined,
        duration: msg.duration as number | undefined,
        playback_state: 'loaded',
        current_t_rel: 0,
        safe_intervals: (msg.safe_intervals as unknown[]) ?? [],
        strips: (msg.strips as SessionStrip[]) ?? [],
      };
      rebuildTargets();
      return;
    }

    if (msg.event === 'device_status') {
      handleDeviceStatus(msg as {
        device_uid?: string;
        connected?: boolean;
        last_seen?: number | null;
      });
      return;
    }

    if (!session.value) {
      return;
    }

    if (msg.event === 'state') {
      session.value.playback_state = msg.state as string | undefined;
      session.value.epoch = msg.epoch as number | undefined;
      return;
    }

    if (msg.event === 'loop') {
      session.value.epoch = msg.epoch as number | undefined;
      session.value.current_t_rel = 0;
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
    relayConnected.value = false;

    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(`${proto}//${window.location.host}/ws`);
    ws.binaryType = 'arraybuffer';

    ws.addEventListener('open', () => {
      relayConnected.value = true;
    });

    ws.addEventListener('message', (event) => {
      void handleWsMessage(event);
    });

    ws.addEventListener('close', () => {
      relayConnected.value = false;
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
    relayConnected,
    session,
    snapshot,
    simTargets,
  };
}

export type RelayState = ReturnType<typeof useRelayState>;

export const relayStateKey: InjectionKey<RelayState> = Symbol('relay-state');

export function useInjectedRelayState(): RelayState {
  const relayState = inject(relayStateKey);
  if (!relayState) {
    throw new Error('Relay state is not available.');
  }
  return relayState;
}
