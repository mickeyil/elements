// Pure, semantic transport logic. Local structural input types keep this free
// of any composable dependency (mirrors viewerModel). It answers "is this verb
// valid in this state?"; the component ANDs in runtime constraints (controller
// connected, a request in flight, a program selected).

export interface TransportSession {
  state?: string;
}

export interface TransportDevice {
  configured?: boolean;
  status?: string;
  phase?: string;
  target_intent?: string;
}

export interface Transport {
  state: string;
  canLoad: boolean;
  canPlay: boolean;
  canPause: boolean;
  canResume: boolean;
  canStop: boolean;
  waitingForDevices: boolean;
}

// A device the session routed into the loaded program (target_intent 'ready')
// but that has not yet ACKed the load (phase still not 'loaded'). Gating Play on
// this is what closes the load->play race; non-participating devices
// (target_intent 'detached') never block.
function isStillLoading(device: TransportDevice): boolean {
  return (
    Boolean(device.configured)
    && device.status === 'online'
    && device.target_intent === 'ready'
    && device.phase !== 'loaded'
  );
}

export function deriveTransport(
  session: TransportSession | null | undefined,
  devices: readonly TransportDevice[],
): Transport {
  const state = session?.state ?? 'idle';
  const waitingForDevices = state === 'loaded' && devices.some(isStillLoading);

  return {
    state,
    // Loading mid-play silently resets a run; require Stop first.
    canLoad: state === 'idle' || state === 'loaded' || state === 'ended',
    canPlay: (state === 'loaded' || state === 'ended') && !waitingForDevices,
    canPause: state === 'playing',
    canResume: state === 'paused',
    canStop: state !== 'idle',
    waitingForDevices,
  };
}
