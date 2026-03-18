export const GRID_SIZE = 512;

export interface SinglePrimitive {
  type: 'single';
  index: number;
  position: [number, number];
}

export interface EditorPayload {
  version: 1;
  csv_hash?: string;
  primitives: SinglePrimitive[];
}

export interface LayoutDocumentPayload {
  rows: Array<Array<number | null>>;
  editor: EditorPayload | null;
}

export interface EditorDocument {
  gridSize: number;
  maxIndex: number;
  primitives: SinglePrimitive[];
  occupied: Map<string, SinglePrimitive>;
  indices: Set<number>;
  placedCount: number;
  currentIndex: number;
}

export interface SerializedDocument {
  rows: Array<Array<number | null>>;
  editor: {
    version: 1;
    primitives: SinglePrimitive[];
  };
}

export interface Point {
  x: number;
  y: number;
}

function keyOf(x: number, y: number): string {
  return `${x},${y}`;
}

function clampIndex(index: number, maxIndex: number): number {
  return Math.max(1, Math.min(maxIndex, index));
}

function computeCurrentIndex(primitives: readonly SinglePrimitive[], maxIndex: number): number {
  if (!primitives.length) {
    return 1;
  }
  const highest = Math.max(...primitives.map((primitive) => primitive.index));
  return clampIndex(highest + 1, maxIndex);
}

function buildDocument(maxIndex: number, primitives: readonly SinglePrimitive[]): EditorDocument {
  const occupied = new Map<string, SinglePrimitive>();
  const indices = new Set<number>();
  const normalized: SinglePrimitive[] = [];

  for (const primitive of primitives) {
    const [x, y] = primitive.position;
    if (x < 0 || y < 0 || x >= GRID_SIZE || y >= GRID_SIZE) {
      throw new Error('Loaded primitive is outside the editor grid.');
    }
    if (primitive.index < 1 || primitive.index > maxIndex) {
      throw new Error(`Loaded primitive index ${primitive.index} is outside 1-${maxIndex}.`);
    }

    const key = keyOf(x, y);
    if (occupied.has(key)) {
      throw new Error(`Duplicate occupied cell at ${x},${y}.`);
    }
    if (indices.has(primitive.index)) {
      throw new Error(`Duplicate primitive index ${primitive.index}.`);
    }

    const normalizedPrimitive: SinglePrimitive = {
      type: 'single',
      index: primitive.index,
      position: [x, y],
    };
    occupied.set(key, normalizedPrimitive);
    indices.add(primitive.index);
    normalized.push(normalizedPrimitive);
  }

  return {
    gridSize: GRID_SIZE,
    maxIndex,
    primitives: normalized,
    occupied,
    indices,
    placedCount: normalized.length,
    currentIndex: computeCurrentIndex(normalized, maxIndex),
  };
}

function rowsToPrimitives(rows: Array<Array<number | null>>): SinglePrimitive[] {
  const primitives: SinglePrimitive[] = [];
  for (let y = 0; y < rows.length; y += 1) {
    const row = rows[y] ?? [];
    for (let x = 0; x < row.length; x += 1) {
      const cell = row[x];
      if (!Number.isInteger(cell)) {
        continue;
      }
      primitives.push({
        type: 'single',
        index: Number(cell),
        position: [x, y],
      });
    }
  }
  primitives.sort((left, right) => left.index - right.index);
  return primitives;
}

function centeredOrigin(rows: Array<Array<number | null>>): Point {
  const width = Math.max(0, ...rows.map((row) => row.length));
  const height = rows.length;
  return {
    x: Math.max(0, Math.floor((GRID_SIZE - width) / 2)),
    y: Math.max(0, Math.floor((GRID_SIZE - height) / 2)),
  };
}

function translatePrimitives(
  primitives: readonly SinglePrimitive[],
  offset: Point,
): SinglePrimitive[] {
  return primitives.map((primitive) => ({
    type: 'single',
    index: primitive.index,
    position: [primitive.position[0] + offset.x, primitive.position[1] + offset.y],
  }));
}

export function createEmptyDocument(maxIndex: number): EditorDocument {
  return buildDocument(maxIndex, []);
}

export function createDocumentFromLayout(
  payload: LayoutDocumentPayload | null,
  maxIndex: number,
): EditorDocument {
  if (!payload) {
    return createEmptyDocument(maxIndex);
  }

  const rows = Array.isArray(payload.rows) ? payload.rows : [];
  const offset = centeredOrigin(rows);
  const basePrimitives = payload.editor?.primitives?.length
    ? payload.editor.primitives
    : rowsToPrimitives(rows);

  return buildDocument(maxIndex, translatePrimitives(basePrimitives, offset));
}

export function setCurrentIndex(document: EditorDocument, nextIndex: number): EditorDocument {
  return {
    ...document,
    currentIndex: clampIndex(nextIndex, document.maxIndex),
  };
}

export function incrementCurrentIndex(document: EditorDocument, delta: number): EditorDocument {
  return setCurrentIndex(document, document.currentIndex + delta);
}

export function placeSinglePrimitive(
  document: EditorDocument,
  x: number,
  y: number,
): EditorDocument {
  if (x < 0 || y < 0 || x >= document.gridSize || y >= document.gridSize) {
    throw new Error('The target cell is outside the editor grid.');
  }

  const key = keyOf(x, y);
  if (document.occupied.has(key)) {
    throw new Error('That cell is already occupied.');
  }

  if (document.indices.has(document.currentIndex)) {
    throw new Error(`Index ${document.currentIndex} is already placed.`);
  }

  const nextPrimitive: SinglePrimitive = {
    type: 'single',
    index: document.currentIndex,
    position: [x, y],
  };

  return buildDocument(document.maxIndex, [...document.primitives, nextPrimitive]);
}

export function undoLastPrimitive(document: EditorDocument): EditorDocument {
  if (!document.primitives.length) {
    return document;
  }
  return buildDocument(document.maxIndex, document.primitives.slice(0, -1));
}

export function documentCenter(document: EditorDocument): Point {
  if (!document.primitives.length) {
    const center = GRID_SIZE / 2;
    return { x: center, y: center };
  }

  const xs = document.primitives.map((primitive) => primitive.position[0]);
  const ys = document.primitives.map((primitive) => primitive.position[1]);
  return {
    x: (Math.min(...xs) + Math.max(...xs) + 1) / 2,
    y: (Math.min(...ys) + Math.max(...ys) + 1) / 2,
  };
}

export function serializeDocument(document: EditorDocument): SerializedDocument {
  if (!document.primitives.length) {
    throw new Error('Place at least one LED before saving.');
  }

  const xs = document.primitives.map((primitive) => primitive.position[0]);
  const ys = document.primitives.map((primitive) => primitive.position[1]);
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);
  const width = maxX - minX + 1;
  const height = maxY - minY + 1;

  const rows = Array.from({ length: height }, () => Array<number | null>(width).fill(null));
  const primitives = document.primitives.map((primitive) => {
    const [x, y] = primitive.position;
    const relX = x - minX;
    const relY = y - minY;
    rows[relY][relX] = primitive.index;
    return {
      type: 'single' as const,
      index: primitive.index,
      position: [relX, relY] as [number, number],
    };
  });

  return {
    rows,
    editor: {
      version: 1,
      primitives,
    },
  };
}
