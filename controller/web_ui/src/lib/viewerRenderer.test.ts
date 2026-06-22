import { describe, expect, it } from 'vitest';

import { CELL_PX, ledIndexAt, type LayoutPayload, type SimTarget } from './viewerRenderer';

function targetWithLayout(layout: LayoutPayload | null): SimTarget {
  return {
    deviceUid: 'sim',
    stripName: 'ring',
    logicalIndex: 0,
    logicalLength: 4,
    physicalLength: 4,
    connected: true,
    layout,
    gridWidth: 2,
    gridHeight: 2,
    canvas: null,
    ctx: null,
  };
}

describe('ledIndexAt', () => {
  const target = targetWithLayout({
    rows: [
      [1, 2],
      [3, null],
    ],
  });

  it('maps a cell to its 0-based logical index', () => {
    expect(ledIndexAt(target, 0, 0)).toBe(0);
    expect(ledIndexAt(target, CELL_PX, 0)).toBe(1);
    expect(ledIndexAt(target, 0, CELL_PX)).toBe(2);
  });

  it('returns null over a non-LED cell', () => {
    expect(ledIndexAt(target, CELL_PX, CELL_PX)).toBeNull();
  });

  it('returns null out of bounds', () => {
    expect(ledIndexAt(target, -1, 0)).toBeNull();
    expect(ledIndexAt(target, 2 * CELL_PX, 0)).toBeNull();
    expect(ledIndexAt(target, 0, 2 * CELL_PX)).toBeNull();
  });

  it('returns null with no layout', () => {
    expect(ledIndexAt(targetWithLayout(null), 0, 0)).toBeNull();
  });
});
