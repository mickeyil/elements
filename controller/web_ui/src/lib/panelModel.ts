// Pure helpers of the operator panel page: strips to pick from, what the
// panel has a strip doing, and pacing of the color requests a drag produces.

export interface PanelHsv {
  h: number;   // degrees
  s: number;   // 0..1
  v: number;   // 0..1
}

export type PanelMode = 'manual' | 'program' | 'stopped';

// A panel-owned device's `panel` field in the state snapshot; hsv is
// [h, s, v] of the held color while the mode is manual.
export interface PanelView {
  mode: PanelMode;
  program_id: string | null;
  hsv: [number, number, number] | null;
}

// Local structural input, as in viewerModel, so this stays free of the
// server-state composable.
export interface PanelDeviceInput {
  uid?: string;
  configured?: boolean;
  status?: string;
  strip_id?: string;
  length?: number;
  owner?: string;
  panel?: PanelView | null;
}

export interface StripPanelState {
  mode: PanelMode;
  programId: string | null;
  hsv: PanelHsv | null;
}

export interface StripDevice {
  uid: string;
  online: boolean;
  isSim: boolean;
}

export interface PanelStrip {
  stripId: string;
  length: number;
  devices: StripDevice[];
  // What the panel has the strip doing; null while the show owns it.
  panel: StripPanelState | null;
}

// The panel state of a strip from its devices (the panel takes a whole strip,
// so any panel-owned member speaks for it); null when the show owns it.
export function stripPanelState(devices: readonly PanelDeviceInput[]): StripPanelState | null {
  const view = devices.find((device) => device.owner === 'panel' && device.panel)?.panel;
  if (!view) {
    return null;
  }
  const hsv = view.hsv ? { h: view.hsv[0], s: view.hsv[1], v: view.hsv[2] } : null;
  return { mode: view.mode, programId: view.program_id, hsv };
}

// The configured strips, ordered by strip id, each with its devices.
export function groupStrips(devices: readonly PanelDeviceInput[]): PanelStrip[] {
  const members = new Map<string, PanelDeviceInput[]>();
  for (const device of devices) {
    if (!device.configured || !device.uid || !device.strip_id) {
      continue;
    }
    const group = members.get(device.strip_id) ?? [];
    group.push(device);
    members.set(device.strip_id, group);
  }

  return [...members.entries()]
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([stripId, group]) => ({
      stripId,
      length: Number(group[0].length ?? 0),
      devices: group.map((device) => ({
        uid: String(device.uid),
        online: device.status === 'online',
        isSim: String(device.uid).startsWith('sim-'),
      })),
      panel: stripPanelState(group),
    }));
}

export function hsvToRgb({ h, s, v }: PanelHsv): [number, number, number] {
  const hh = (((h % 360) + 360) % 360) / 60;
  const i = Math.floor(hh);
  const f = hh - i;
  const p = v * (1 - s);
  const q = v * (1 - s * f);
  const t = v * (1 - s * (1 - f));
  const [r, g, b] = [[v, t, p], [q, v, p], [p, v, t], [p, q, v], [t, p, v], [v, p, q]][i];
  return [Math.round(r * 255), Math.round(g * 255), Math.round(b * 255)];
}

export function hsvToCss(hsv: PanelHsv): string {
  const [r, g, b] = hsvToRgb(hsv);
  return `rgb(${r}, ${g}, ${b})`;
}

// Sends values through an async function with at most one request in flight.
// A value pushed meanwhile waits; when the request completes only the latest
// waiting value is sent, the ones before it skipped. Sends start at least
// minIntervalMs apart. A failed send is the send function's to report; the
// sender goes on with the next value. cancel() drops the waiting value, and
// idle() resolves once no request is in flight, so a caller can let the
// in-flight one land before sending something that supersedes it.
export class LatestValueSender<T> {
  private next: { value: T } | null = null;
  private busy = false;
  private drained: Promise<void> = Promise.resolve();
  private lastStart = -Infinity;

  constructor(
    private readonly send: (value: T) => Promise<unknown>,
    private readonly minIntervalMs = 50,
  ) {}

  push(value: T): void {
    this.next = { value };
    if (!this.busy) {
      this.drained = this.drain();
    }
  }

  // Drops the waiting value; the request in flight is left to finish.
  cancel(): void {
    this.next = null;
  }

  idle(): Promise<void> {
    return this.drained;
  }

  private async drain(): Promise<void> {
    this.busy = true;
    while (this.next) {
      const wait = this.lastStart + this.minIntervalMs - Date.now();
      if (wait > 0) {
        await new Promise((resolve) => setTimeout(resolve, wait));
        continue;   // look again: a cancel may have dropped the value meanwhile
      }
      const { value } = this.next;
      this.next = null;
      this.lastStart = Date.now();
      try {
        await this.send(value);
      } catch {
        // Reported by the send function.
      }
    }
    this.busy = false;
  }
}
