import { measureLayout, type LayoutPayload, type SimTarget } from './viewerRenderer';

// Local structural inputs so this stays a pure helper with no dependency on the
// server-state composable (importing from there would form a cycle).
export interface DeviceInput {
  uid?: string;
  status?: string;
  strip_id?: string;
  length?: number;
}

export interface StripInput {
  strip_id?: string;
  length?: number;
}

export function deriveSimTargets(
  devices: readonly DeviceInput[],
  strips: readonly StripInput[],
  layouts: Record<string, LayoutPayload>,
): SimTarget[] {
  const targets: SimTarget[] = [];

  for (let logicalIndex = 0; logicalIndex < strips.length; logicalIndex += 1) {
    const strip = strips[logicalIndex];
    const stripId = strip?.strip_id;
    if (!stripId) {
      continue;
    }

    for (const device of devices) {
      const uid = device?.uid;
      if (!uid || device.strip_id !== stripId || !uid.startsWith('sim-')) {
        continue;
      }

      const layout = layouts[uid] ?? null;
      const { gridWidth, gridHeight } = measureLayout(layout);
      targets.push({
        deviceUid: uid,
        stripName: stripId,
        logicalIndex,
        logicalLength: Number(strip?.length ?? 0),
        physicalLength: Number(device.length ?? 0),
        connected: device.status === 'online',
        layout,
        gridWidth,
        gridHeight,
        canvas: null,
        ctx: null,
      });
    }
  }

  return targets;
}
