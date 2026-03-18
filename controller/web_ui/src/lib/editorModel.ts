export const GRID_SIZE = 512;

export interface Point {
  x: number;
  y: number;
}

export interface SinglePrimitive {
  type: 'single';
  index: number;
  position: [number, number];
}

export interface LinePrimitive {
  type: 'line';
  startIndex: number;
  count: number;
  spacing: number;
  start: [number, number];
  end: [number, number];
}

export type Primitive = SinglePrimitive | LinePrimitive;

export interface EditorPayload {
  version: 1;
  csv_hash?: string;
  primitives: Primitive[];
}

export interface LayoutDocumentPayload {
  rows: Array<Array<number | null>>;
  editor: EditorPayload | null;
}

export interface OccupiedCell {
  index: number;
  x: number;
  y: number;
  primitive: Primitive;
}

export interface EditorDocument {
  gridSize: number;
  maxIndex: number;
  primitives: Primitive[];
  occupied: Map<string, OccupiedCell>;
  indices: Set<number>;
  placedCount: number;
  currentIndex: number;
}

export interface SerializedDocument {
  rows: Array<Array<number | null>>;
  editor: {
    version: 1;
    primitives: Primitive[];
  };
}

export interface LinePlacementPreview {
  cells: OccupiedCell[];
  error: string | null;
  primitive: Primitive;
}

function keyOf(x: number, y: number): string {
  return `${x},${y}`;
}

function clampIndex(index: number, maxIndex: number): number {
  return Math.max(1, Math.min(maxIndex, index));
}

function clonePoint(position: [number, number]): [number, number] {
  return [position[0], position[1]];
}

function clonePrimitive(primitive: Primitive): Primitive {
  if (primitive.type === 'single') {
    return {
      type: 'single',
      index: primitive.index,
      position: clonePoint(primitive.position),
    };
  }

  return {
    type: 'line',
    startIndex: primitive.startIndex,
    count: primitive.count,
    spacing: primitive.spacing,
    start: clonePoint(primitive.start),
    end: clonePoint(primitive.end),
  };
}

function primitiveHighestIndex(primitive: Primitive): number {
  return primitive.type === 'single'
    ? primitive.index
    : primitive.startIndex + primitive.count - 1;
}

function computeCurrentIndex(primitives: readonly Primitive[], maxIndex: number): number {
  if (!primitives.length) {
    return 1;
  }
  const highest = Math.max(...primitives.map((primitive) => primitiveHighestIndex(primitive)));
  return clampIndex(highest + 1, maxIndex);
}

function normalizeSpacing(spacing: number): number {
  if (!Number.isInteger(spacing) || spacing < 0) {
    throw new Error('Line spacing must be a non-negative integer.');
  }
  return spacing;
}

function ensureGridCell(position: [number, number], gridSize: number): void {
  const [x, y] = position;
  if (!Number.isInteger(x) || !Number.isInteger(y)) {
    throw new Error('Primitive coordinates must be integers.');
  }
  if (x < 0 || y < 0 || x >= gridSize || y >= gridSize) {
    throw new Error('Loaded primitive is outside the editor grid.');
  }
}

function expandLinePath(start: Point, end: Point): Point[] {
  let x0 = start.x;
  let y0 = start.y;
  const x1 = end.x;
  const y1 = end.y;
  const dx = Math.abs(x1 - x0);
  const dy = -Math.abs(y1 - y0);
  const sx = x0 < x1 ? 1 : -1;
  const sy = y0 < y1 ? 1 : -1;
  let err = dx + dy;

  const cells: Point[] = [];
  while (true) {
    cells.push({ x: x0, y: y0 });
    if (x0 === x1 && y0 === y1) {
      return cells;
    }

    const twiceError = err * 2;
    if (twiceError >= dy) {
      err += dy;
      x0 += sx;
    }
    if (twiceError <= dx) {
      err += dx;
      y0 += sy;
    }
  }
}

export function expandLineCells(start: Point, end: Point, spacing: number): Point[] {
  const normalizedSpacing = normalizeSpacing(spacing);
  const path = expandLinePath(start, end);
  const step = normalizedSpacing + 1;
  const cells: Point[] = [];

  for (let i = 0; i < path.length; i += step) {
    const point = path[i];
    cells.push({ x: point.x, y: point.y });
  }

  return cells;
}

function expandPrimitive(primitive: Primitive, gridSize: number): OccupiedCell[] {
  if (primitive.type === 'single') {
    ensureGridCell(primitive.position, gridSize);
    return [
      {
        index: primitive.index,
        x: primitive.position[0],
        y: primitive.position[1],
        primitive,
      },
    ];
  }

  normalizeSpacing(primitive.spacing);
  if (!Number.isInteger(primitive.count) || primitive.count < 1) {
    throw new Error('Line primitive count must be a positive integer.');
  }

  ensureGridCell(primitive.start, gridSize);
  ensureGridCell(primitive.end, gridSize);

  const points = expandLineCells(
    { x: primitive.start[0], y: primitive.start[1] },
    { x: primitive.end[0], y: primitive.end[1] },
    primitive.spacing,
  );

  if (points.length !== primitive.count) {
    throw new Error('Loaded line primitive count does not match the expanded cells.');
  }

  return points.map((point, index) => ({
    index: primitive.startIndex + index,
    x: point.x,
    y: point.y,
    primitive,
  }));
}

function buildDocument(maxIndex: number, primitives: readonly Primitive[]): EditorDocument {
  const occupied = new Map<string, OccupiedCell>();
  const indices = new Set<number>();
  const normalized: Primitive[] = [];

  for (const original of primitives) {
    const primitive = clonePrimitive(original);
    const cells = expandPrimitive(primitive, GRID_SIZE);
    for (const cell of cells) {
      if (cell.index < 1 || cell.index > maxIndex) {
        throw new Error(`Loaded primitive index ${cell.index} is outside 1-${maxIndex}.`);
      }
      if (indices.has(cell.index)) {
        throw new Error(`Duplicate primitive index ${cell.index}.`);
      }
      const key = keyOf(cell.x, cell.y);
      if (occupied.has(key)) {
        throw new Error(`Duplicate occupied cell at ${cell.x},${cell.y}.`);
      }
      indices.add(cell.index);
      occupied.set(key, cell);
    }
    normalized.push(primitive);
  }

  return {
    gridSize: GRID_SIZE,
    maxIndex,
    primitives: normalized,
    occupied,
    indices,
    placedCount: occupied.size,
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

function translatePrimitive(primitive: Primitive, offset: Point): Primitive {
  if (primitive.type === 'single') {
    return {
      type: 'single',
      index: primitive.index,
      position: [primitive.position[0] + offset.x, primitive.position[1] + offset.y],
    };
  }

  return {
    type: 'line',
    startIndex: primitive.startIndex,
    count: primitive.count,
    spacing: primitive.spacing,
    start: [primitive.start[0] + offset.x, primitive.start[1] + offset.y],
    end: [primitive.end[0] + offset.x, primitive.end[1] + offset.y],
  };
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

  return buildDocument(maxIndex, basePrimitives.map((primitive) => translatePrimitive(primitive, offset)));
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

function validatePlacementCells(
  document: EditorDocument,
  cells: readonly OccupiedCell[],
): string | null {
  if (!cells.length) {
    return 'Line placement produced no cells.';
  }

  for (const cell of cells) {
    if (cell.index < 1 || cell.index > document.maxIndex) {
      return `Placement exceeds the configured length ${document.maxIndex}.`;
    }
    if (document.indices.has(cell.index)) {
      return `Index ${cell.index} is already placed.`;
    }
    if (document.occupied.has(keyOf(cell.x, cell.y))) {
      return 'That placement overlaps an existing LED.';
    }
  }

  return null;
}

export function previewLinePlacement(
  document: EditorDocument,
  start: Point,
  end: Point,
  spacing: number,
): LinePlacementPreview {
  const cells = expandLineCells(start, end, spacing).map((point, index) => ({
    x: point.x,
    y: point.y,
    index: document.currentIndex + index,
    primitive: {
      type: 'single',
      index: document.currentIndex,
      position: [point.x, point.y],
    } as Primitive,
  }));

  let primitive: Primitive;
  if (cells.length === 1) {
    primitive = {
      type: 'single',
      index: document.currentIndex,
      position: [cells[0].x, cells[0].y],
    };
    cells[0].primitive = primitive;
  } else {
    primitive = {
      type: 'line',
      startIndex: document.currentIndex,
      count: cells.length,
      spacing: normalizeSpacing(spacing),
      start: [start.x, start.y],
      end: [end.x, end.y],
    };
    for (const cell of cells) {
      cell.primitive = primitive;
    }
  }

  return {
    cells,
    error: validatePlacementCells(document, cells),
    primitive,
  };
}

export function placeSinglePrimitive(
  document: EditorDocument,
  x: number,
  y: number,
): EditorDocument {
  const nextPrimitive: SinglePrimitive = {
    type: 'single',
    index: document.currentIndex,
    position: [x, y],
  };

  const validation = validatePlacementCells(document, expandPrimitive(nextPrimitive, document.gridSize));
  if (validation) {
    throw new Error(validation);
  }

  return buildDocument(document.maxIndex, [...document.primitives, nextPrimitive]);
}

export function placeLinePrimitive(
  document: EditorDocument,
  start: Point,
  end: Point,
  spacing: number,
): EditorDocument {
  const preview = previewLinePlacement(document, start, end, spacing);
  if (preview.error) {
    throw new Error(preview.error);
  }

  return buildDocument(document.maxIndex, [...document.primitives, preview.primitive]);
}

export function undoLastPrimitive(document: EditorDocument): EditorDocument {
  if (!document.primitives.length) {
    return document;
  }
  return buildDocument(document.maxIndex, document.primitives.slice(0, -1));
}

export function documentCenter(document: EditorDocument): Point {
  if (!document.occupied.size) {
    const center = GRID_SIZE / 2;
    return { x: center, y: center };
  }

  const cells = Array.from(document.occupied.values());
  const xs = cells.map((cell) => cell.x);
  const ys = cells.map((cell) => cell.y);
  return {
    x: (Math.min(...xs) + Math.max(...xs) + 1) / 2,
    y: (Math.min(...ys) + Math.max(...ys) + 1) / 2,
  };
}

export function cloneDocument(document: EditorDocument): EditorDocument {
  return setCurrentIndex(buildDocument(document.maxIndex, document.primitives), document.currentIndex);
}

function primitivesEqual(left: Primitive, right: Primitive): boolean {
  if (left.type !== right.type) {
    return false;
  }
  if (left.type === 'single' && right.type === 'single') {
    return (
      left.index === right.index &&
      left.position[0] === right.position[0] &&
      left.position[1] === right.position[1]
    );
  }
  if (left.type === 'line' && right.type === 'line') {
    return (
      left.startIndex === right.startIndex &&
      left.count === right.count &&
      left.spacing === right.spacing &&
      left.start[0] === right.start[0] &&
      left.start[1] === right.start[1] &&
      left.end[0] === right.end[0] &&
      left.end[1] === right.end[1]
    );
  }
  return false;
}

export function documentsEqual(left: EditorDocument, right: EditorDocument): boolean {
  if (left.maxIndex !== right.maxIndex || left.currentIndex !== right.currentIndex) {
    return false;
  }
  if (left.primitives.length !== right.primitives.length) {
    return false;
  }

  for (let i = 0; i < left.primitives.length; i += 1) {
    if (!primitivesEqual(left.primitives[i], right.primitives[i])) {
      return false;
    }
  }

  return true;
}

export function serializeDocument(document: EditorDocument): SerializedDocument {
  if (!document.primitives.length) {
    throw new Error('Place at least one LED before saving.');
  }

  const cells = Array.from(document.occupied.values());
  const xs = cells.map((cell) => cell.x);
  const ys = cells.map((cell) => cell.y);
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);
  const width = maxX - minX + 1;
  const height = maxY - minY + 1;

  const rows = Array.from({ length: height }, () => Array<number | null>(width).fill(null));
  for (const cell of cells) {
    rows[cell.y - minY][cell.x - minX] = cell.index;
  }

  const primitives = document.primitives.map((primitive) => {
    if (primitive.type === 'single') {
      return {
        type: 'single' as const,
        index: primitive.index,
        position: [primitive.position[0] - minX, primitive.position[1] - minY] as [number, number],
      };
    }

    return {
      type: 'line' as const,
      startIndex: primitive.startIndex,
      count: primitive.count,
      spacing: primitive.spacing,
      start: [primitive.start[0] - minX, primitive.start[1] - minY] as [number, number],
      end: [primitive.end[0] - minX, primitive.end[1] - minY] as [number, number],
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
