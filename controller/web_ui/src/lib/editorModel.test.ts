import { describe, expect, it } from 'vitest';

import {
  createEmptyDocument,
  createDocumentFromLayout,
  expandLineCells,
  placeLinePrimitive,
  previewLinePlacement,
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

describe('line placement', () => {
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
    expect(Array.from(nextDocument.occupied.values()).map((cell) => [cell.x, cell.y, cell.index])).toEqual([
      [0, 0, 1],
      [2, 0, 2],
      [4, 0, 3],
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

  it('serializes and reloads a line primitive intact', () => {
    const original = placeLinePrimitive(createEmptyDocument(10), { x: 0, y: 0 }, { x: 4, y: 0 }, 1);
    const serialized = serializeDocument(original);
    const loaded = createDocumentFromLayout({ rows: serialized.rows, editor: serialized.editor }, 10);

    expect(serializeDocument(loaded)).toEqual(serialized);
    expect(loaded.primitives).toEqual([
      {
        type: 'line',
        startIndex: 1,
        count: 3,
        spacing: 1,
        start: [253, 255],
        end: [257, 255],
      },
    ]);
  });
});
