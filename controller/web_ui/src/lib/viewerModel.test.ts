import { describe, expect, it } from 'vitest';

import { deriveSimTargets, type DeviceInput, type StripInput } from './viewerModel';
import type { LayoutPayload } from './viewerRenderer';

describe('deriveSimTargets', () => {
  const ringLayout: LayoutPayload = {
    rows: [
      [1, 2, 3, 4],
      [5, 6, 7, 8],
    ],
  };

  function v3Snapshot(): {
    devices: DeviceInput[];
    strips: StripInput[];
    layouts: Record<string, LayoutPayload>;
  } {
    return {
      devices: [{ uid: 'sim-16', status: 'online', strip_id: 'ring16', length: 16 }],
      strips: [{ strip_id: 'ring16', length: 16 }],
      layouts: { 'sim-16': ringLayout },
    };
  }

  it('emits one target for a matching sim device with its layout attached', () => {
    const { devices, strips, layouts } = v3Snapshot();
    const targets = deriveSimTargets(devices, strips, layouts);

    expect(targets).toHaveLength(1);
    const [target] = targets;
    expect(target.deviceUid).toBe('sim-16');
    expect(target.stripName).toBe('ring16');
    expect(target.logicalIndex).toBe(0);
    expect(target.logicalLength).toBe(16);
    expect(target.physicalLength).toBe(16);
    expect(target.connected).toBe(true);
    expect(target.layout).toBe(ringLayout);
    expect(target.gridWidth).toBe(4);
    expect(target.gridHeight).toBe(2);
  });

  it('derives connected from status, not a connected flag', () => {
    const { strips, layouts } = v3Snapshot();
    const offline: DeviceInput[] = [
      { uid: 'sim-16', status: 'offline', strip_id: 'ring16', length: 16 },
    ];
    expect(deriveSimTargets(offline, strips, layouts)[0].connected).toBe(false);
  });

  it('skips non-sim devices and devices on other strips', () => {
    const strips: StripInput[] = [{ strip_id: 'ring16', length: 16 }];
    const devices: DeviceInput[] = [
      { uid: 'esp-1', status: 'online', strip_id: 'ring16', length: 16 },
      { uid: 'sim-99', status: 'online', strip_id: 'other', length: 8 },
    ];
    expect(deriveSimTargets(devices, strips, {})).toHaveLength(0);
  });
});
