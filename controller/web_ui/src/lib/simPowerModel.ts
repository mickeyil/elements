// A sim process the web runs, from server_status.sims (controller/elemctl/sims.py).
export interface SimProcess {
  running: boolean;
  pid: number | null;
  // Why it last stopped on its own ("killed by SIGKILL"); null after a
  // plain power off.
  last_exit: string | null;
}

interface SimCard {
  isSim: boolean;
  isConfigured: boolean;
  status?: 'online' | 'offline' | 'discovered';
}

export interface SimPowerOffer {
  action: 'on' | 'off' | null;
  enabled: boolean;
  reason?: string;
}

// The Power on / Power off item of a sim card's menu.
export function simPowerOffer(
  device: SimCard,
  sim: SimProcess | undefined,
  serverConnected: boolean,
): SimPowerOffer {
  if (!device.isSim || !device.isConfigured || !serverConnected) {
    return { action: null, enabled: false };
  }
  if (sim?.running) {
    return { action: 'off', enabled: true };
  }
  // Online without a process of ours: a sim started from a terminal, which
  // the web can neither stop nor start a second copy of.
  if (device.status === 'online') {
    return { action: 'on', enabled: false, reason: 'Running outside the app' };
  }
  return { action: 'on', enabled: true };
}

// The sim's line on its card, or null when there is nothing to say.
export function simPowerNote(device: SimCard, sim: SimProcess | undefined): string | null {
  if (sim?.running) {
    return device.status === 'online' ? null : 'Booting…';
  }
  return sim?.last_exit ? `Off: ${sim.last_exit}` : null;
}
