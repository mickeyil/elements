import { describe, expect, it } from 'vitest';

import {
  clearInactiveIndices,
  createDocumentFromLayout,
  createEmptyDocument,
  expandLineCells,
  markIndicesInactive,
  placeLinePrimitive,
  placeSinglePrimitive,
  previewLinePlacement,
  reactivateIndex,
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
});
