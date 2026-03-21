import { describe, expect, it } from 'vitest';

import {
  clearInactiveIndices,
  circleRadiusFromPoints,
  circleStartFromAngle,
  createDocumentFromLayout,
  createEmptyDocument,
  expandCircleCells,
  expandLineCells,
  markIndicesInactive,
  placeCirclePrimitive,
  placeLinePrimitive,
  placeSinglePrimitive,
  primitiveIndexAtCell,
  previewCirclePlacement,
  previewLinePlacement,
  reactivateIndex,
  removePrimitive,
  replacePrimitive,
  serializeDocument,
} from './editorModel';

describe('expandLineCells', () => {
  it.each([
    [{ x: 0, y: 0 }, { x: 4, y: 0 }, 0, [{ x: 0, y: 0 }, { x: 1, y: 0 }, { x: 2, y: 0 }, { x: 3, y: 0 }, { x: 4, y: 0 }]],
    [{ x: 0, y: 0 }, { x: 4, y: 0 }, 1, [{ x: 0, y: 0 }, { x: 2, y: 0 }, { x: 4, y: 0 }]],
    [{ x: 0, y: 0 }, { x: 0, y: 4 }, 1, [{ x: 0, y: 0 }, { x: 0, y: 2 }, { x: 0, y: 4 }]],
    [{ x: 0, y: 0 }, { x: 4, y: 4 }, 1, [{ x: 0, y: 0 }, { x: 2, y: 2 }, { x: 4, y: 4 }]],
    [{ x: 4, y: 0 }, { x: 0, y: 4 }, 0, [{ x: 4, y: 0 }, { x: 3, y: 1 }, { x: 2, y: 2 }, { x: 1, y: 3 }, { x: 0, y: 4 }]],
  ])('expands %o -> %o with spacing %d', (start, end, spacing, expected) => {
    expect(expandLineCells(start, end, spacing)).toEqual(expected);
  });
});

describe('expandCircleCells', () => {
  it('shares radius rounding with panel editing helpers', () => {
    expect(circleRadiusFromPoints({ x: 10, y: 10 }, { x: 13, y: 11 })).toBe(3);
    expect(circleStartFromAngle({ x: 10, y: 10 }, 3, 0)).toEqual({ x: 13, y: 10 });
  });

  it('expands a clockwise circle deterministically', () => {
    expect(expandCircleCells({ x: 0, y: 0 }, { x: 2, y: 0 }, 0, 'cw')).toEqual([
      { x: 2, y: 0 },
      { x: 2, y: 1 },
      { x: 1, y: 2 },
      { x: 0, y: 2 },
      { x: -1, y: 2 },
      { x: -2, y: 1 },
      { x: -2, y: 0 },
      { x: -2, y: -1 },
      { x: -1, y: -2 },
      { x: 0, y: -2 },
      { x: 1, y: -2 },
      { x: 2, y: -1 },
    ]);
  });

  it('respects counterclockwise ordering and spacing', () => {
    expect(expandCircleCells({ x: 0, y: 0 }, { x: 2, y: 0 }, 1, 'ccw')).toEqual([
      { x: 2, y: 0 },
      { x: 1, y: -2 },
      { x: -1, y: -2 },
      { x: -2, y: 0 },
      { x: -1, y: 2 },
      { x: 1, y: 2 },
    ]);
  });

  it('rounds the radius from a non-axis start point', () => {
    expect(expandCircleCells({ x: 0, y: 0 }, { x: 3, y: 1 }, 0, 'cw')).toEqual([
      { x: 3, y: 1 },
      { x: 2, y: 2 },
      { x: 1, y: 3 },
      { x: 0, y: 3 },
      { x: -1, y: 3 },
      { x: -2, y: 2 },
      { x: -3, y: 1 },
      { x: -3, y: 0 },
      { x: -3, y: -1 },
      { x: -2, y: -2 },
      { x: -1, y: -3 },
      { x: 0, y: -3 },
      { x: 1, y: -3 },
      { x: 2, y: -2 },
      { x: 3, y: -1 },
      { x: 3, y: 0 },
    ]);
  });
});

describe('editorModel inactive LEDs', () => {
  it('creates a line primitive with spacing', () => {
    const document = createEmptyDocument(10);
    const nextDocument = placeLinePrimitive(document, { x: 0, y: 0 }, { x: 4, y: 0 }, 1);

    expect(nextDocument.primitives).toEqual([
      {
        type: 'line',
        startIndex: 1,
        count: 3,
        spacing: 1,
        start: [0, 0],
        end: [4, 0],
      },
    ]);
    expect(
      Array.from(nextDocument.occupied.values()).map((cell) => [
        cell.x,
        cell.y,
        cell.index,
        cell.inactive,
      ]),
    ).toEqual([
      [0, 0, 1, false],
      [2, 0, 2, false],
      [4, 0, 3, false],
    ]);
  });

  it('returns a single primitive for a degenerate line', () => {
    const document = createEmptyDocument(10);
    const preview = previewLinePlacement(document, { x: 3, y: 4 }, { x: 3, y: 4 }, 0);

    expect(preview.primitive).toEqual({
      type: 'single',
      index: 1,
      position: [3, 4],
    });
    expect(preview.cells).toHaveLength(1);
  });

  it('previews a circle with stable ordered indices', () => {
    const preview = previewCirclePlacement(
      createEmptyDocument(20),
      { x: 0, y: 0 },
      { x: 2, y: 0 },
      1,
      'cw',
    );

    expect(preview.primitive).toEqual({
      type: 'circle',
      startIndex: 1,
      count: 6,
      spacing: 1,
      center: [0, 0],
      start: [2, 0],
      direction: 'cw',
    });
    expect(preview.cells.map((cell) => [cell.x, cell.y, cell.index])).toEqual([
      [2, 0, 1],
      [1, 2, 2],
      [-1, 2, 3],
      [-2, 0, 4],
      [-1, -2, 5],
      [1, -2, 6],
    ]);
  });

  it('marks a placed single LED inactive without changing its index', () => {
    const placed = placeSinglePrimitive(createEmptyDocument(10), 1, 2);
    const inactive = markIndicesInactive(placed, [1]);

    expect(inactive.placedCount).toBe(0);
    expect(inactive.currentIndex).toBe(2);
    expect(Array.from(inactive.occupied.values())).toEqual([
      expect.objectContaining({
        index: 1,
        x: 1,
        y: 2,
        inactive: true,
      }),
    ]);
    expect(serializeDocument(inactive)).toEqual({
      rows: [[null]],
      editor: {
        version: 1,
        primitives: [
          {
            type: 'single',
            index: 1,
            position: [0, 0],
            inactive: true,
          },
        ],
      },
    });
  });

  it('rejects inactive indices that are not currently placed', () => {
    expect(() => markIndicesInactive(createEmptyDocument(10), [4])).toThrow('LED 4 is not placed.');
  });

  it('keeps stable numbering for inactive offsets inside line primitives', () => {
    const document = placeLinePrimitive(
      createEmptyDocument(10),
      { x: 0, y: 0 },
      { x: 4, y: 0 },
      0,
      [1, 3],
    );

    expect(document.placedCount).toBe(3);
    expect(document.currentIndex).toBe(6);
    expect(
      Array.from(document.occupied.values()).map((cell) => [
        cell.x,
        cell.y,
        cell.index,
        cell.inactive,
      ]),
    ).toEqual([
      [0, 0, 1, false],
      [1, 0, 2, true],
      [2, 0, 3, false],
      [3, 0, 4, true],
      [4, 0, 5, false],
    ]);

    expect(serializeDocument(document)).toEqual({
      rows: [[1, null, 3, null, 5]],
      editor: {
        version: 1,
        primitives: [
          {
            type: 'line',
            startIndex: 1,
            count: 5,
            spacing: 0,
            start: [0, 0],
            end: [4, 0],
            inactiveOffsets: [1, 3],
          },
        ],
      },
    });
  });

  it('allows fully inactive line primitives without renumbering later LEDs', () => {
    const document = placeLinePrimitive(
      createEmptyDocument(10),
      { x: 0, y: 0 },
      { x: 2, y: 0 },
      0,
      [0, 1, 2],
    );

    expect(document.placedCount).toBe(0);
    expect(document.currentIndex).toBe(4);
    expect(serializeDocument(document)).toEqual({
      rows: [[null, null, null]],
      editor: {
        version: 1,
        primitives: [
          {
            type: 'line',
            startIndex: 1,
            count: 3,
            spacing: 0,
            start: [0, 0],
            end: [2, 0],
            inactiveOffsets: [0, 1, 2],
          },
        ],
      },
    });
  });

  it('marks and reactivates indices across an existing line', () => {
    const line = placeLinePrimitive(createEmptyDocument(10), { x: 0, y: 0 }, { x: 2, y: 0 }, 0);
    const inactive = markIndicesInactive(line, [2]);
    const reactivated = reactivateIndex(inactive, 2);

    expect(inactive.primitives).toEqual([
      {
        type: 'line',
        startIndex: 1,
        count: 3,
        spacing: 0,
        start: [0, 0],
        end: [2, 0],
        inactiveOffsets: [1],
      },
    ]);
    expect(inactive.placedCount).toBe(2);
    expect(inactive.currentIndex).toBe(4);
    expect(reactivated.primitives).toEqual(line.primitives);
    expect(reactivated.placedCount).toBe(3);
  });

  it('places circles with inactive offsets without renumbering later LEDs', () => {
    const circle = placeCirclePrimitive(
      createEmptyDocument(20),
      { x: 0, y: 0 },
      { x: 2, y: 0 },
      1,
      'cw',
      [1, 4],
    );

    expect(circle.primitives).toEqual([
      {
        type: 'circle',
        startIndex: 1,
        count: 6,
        spacing: 1,
        center: [0, 0],
        start: [2, 0],
        direction: 'cw',
        inactiveOffsets: [1, 4],
      },
    ]);
    expect(circle.placedCount).toBe(4);
    expect(circle.currentIndex).toBe(7);
    expect(circle.occupied.get('1,2')).toEqual(
      expect.objectContaining({ index: 2, inactive: true }),
    );
    expect(circle.occupied.get('-1,-2')).toEqual(
      expect.objectContaining({ index: 5, inactive: true }),
    );
    expect(serializeDocument(circle)).toEqual({
      rows: [
        [null, null, null, 6, null],
        [null, null, null, null, null],
        [4, null, null, null, 1],
        [null, null, null, null, null],
        [null, 3, null, null, null],
      ],
      editor: {
        version: 1,
        primitives: [
          {
            type: 'circle',
            startIndex: 1,
            count: 6,
            spacing: 1,
            center: [2, 2],
            start: [4, 2],
            direction: 'cw',
            inactiveOffsets: [1, 4],
          },
        ],
      },
    });
  });

  it('clears inactive state across singles and lines', () => {
    const line = placeLinePrimitive(createEmptyDocument(10), { x: 0, y: 0 }, { x: 2, y: 0 }, 0);
    const mixed = markIndicesInactive(
      placeSinglePrimitive(line, 4, 0),
      [1, 4],
    );
    const cleared = clearInactiveIndices(mixed);

    expect(cleared.placedCount).toBe(4);
    expect(cleared.currentIndex).toBe(5);
    expect(cleared.primitives).toEqual([
      {
        type: 'line',
        startIndex: 1,
        count: 3,
        spacing: 0,
        start: [0, 0],
        end: [2, 0],
      },
      {
        type: 'single',
        index: 4,
        position: [4, 0],
      },
    ]);
  });

  it('round-trips inactive metadata through layout loading', () => {
    const payload = {
      rows: [[1, null, 3], [null, null, null]],
      editor: {
        version: 1 as const,
        primitives: [
          {
            type: 'line' as const,
            startIndex: 1,
            count: 3,
            spacing: 0,
            start: [0, 0] as [number, number],
            end: [2, 0] as [number, number],
            inactiveOffsets: [1],
          },
          {
            type: 'single' as const,
            index: 4,
            position: [1, 1] as [number, number],
            inactive: true,
          },
        ],
      },
    };

    const loaded = createDocumentFromLayout(payload, 10);

    expect(serializeDocument(loaded)).toEqual(payload);
    expect(loaded.placedCount).toBe(2);
    expect(loaded.currentIndex).toBe(5);
  });

  it('round-trips circle metadata through layout loading', () => {
    const payload = {
      rows: [
        [null, null, null, 6, null],
        [null, null, null, null, null],
        [4, null, null, null, 1],
        [null, null, null, null, null],
        [null, 3, null, null, null],
      ],
      editor: {
        version: 1 as const,
        primitives: [
          {
            type: 'circle' as const,
            startIndex: 1,
            count: 6,
            spacing: 1,
            center: [2, 2] as [number, number],
            start: [4, 2] as [number, number],
            direction: 'cw' as const,
            inactiveOffsets: [1, 4],
          },
        ],
      },
    };

    const loaded = createDocumentFromLayout(payload, 20);

    expect(serializeDocument(loaded)).toEqual(payload);
    expect(loaded.placedCount).toBe(4);
    expect(loaded.currentIndex).toBe(7);
  });

  it('finds the owning primitive by occupied cell', () => {
    const document = placeCirclePrimitive(
      placeLinePrimitive(createEmptyDocument(20), { x: 0, y: 0 }, { x: 2, y: 0 }, 0),
      { x: 6, y: 0 },
      { x: 8, y: 0 },
      1,
      'cw',
    );

    expect(primitiveIndexAtCell(document, 1, 0)).toBe(0);
    expect(primitiveIndexAtCell(document, 8, 0)).toBe(1);
    expect(primitiveIndexAtCell(document, 20, 20)).toBeNull();
  });

  it('replaces a single primitive and preserves later indices through reindexing', () => {
    const original = placeSinglePrimitive(
      placeLinePrimitive(createEmptyDocument(20), { x: 0, y: 0 }, { x: 2, y: 0 }, 0),
      5,
      0,
    );

    const replaced = replacePrimitive(original, 0, {
      type: 'single',
      index: 999,
      position: [2, 2],
    });

    expect(replaced.primitives).toEqual([
      {
        type: 'single',
        index: 1,
        position: [2, 2],
      },
      {
        type: 'single',
        index: 2,
        position: [5, 0],
      },
    ]);
    expect(replaced.currentIndex).toBe(3);
  });

  it('reindexes later primitives when replacing one primitive with a larger primitive', () => {
    const original = placeSinglePrimitive(
      placeLinePrimitive(createEmptyDocument(20), { x: 0, y: 0 }, { x: 2, y: 0 }, 0),
      5,
      0,
    );

    const replaced = replacePrimitive(original, 0, {
      type: 'line',
      startIndex: 999,
      count: 2,
      spacing: 0,
      start: [2, 2],
      end: [3, 2],
    });

    expect(replaced.primitives).toEqual([
      {
        type: 'line',
        startIndex: 1,
        count: 2,
        spacing: 0,
        start: [2, 2],
        end: [3, 2],
      },
      {
        type: 'single',
        index: 3,
        position: [5, 0],
      },
    ]);
    expect(replaced.currentIndex).toBe(4);
  });

  it('removes a primitive and reindexes later primitives', () => {
    const original = placeSinglePrimitive(
      placeCirclePrimitive(createEmptyDocument(20), { x: 6, y: 0 }, { x: 8, y: 0 }, 1, 'cw'),
      12,
      0,
    );

    const removed = removePrimitive(original, 0);

    expect(removed.primitives).toEqual([
      {
        type: 'single',
        index: 1,
        position: [12, 0],
      },
    ]);
    expect(removed.currentIndex).toBe(2);
  });
});
