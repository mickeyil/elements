import type { EditorDocument, OccupiedCell, Point } from './editorModel';

export const DEFAULT_ZOOM = 18;
export const MIN_ZOOM = 4;
export const MAX_ZOOM = 40;

export interface EditorViewport {
  zoom: number;
  offsetX: number;
  offsetY: number;
}

export interface EditorPreview {
  cells: OccupiedCell[];
  error: string | null;
  anchor: Point | null;
}

export interface EditorHandle {
  x: number;
  y: number;
  active?: boolean;
}

const SURFACE = '#081015';
const GRID_LINE = 'rgba(255, 255, 255, 0.08)';
const HOVER = 'rgba(108, 162, 255, 0.18)';
const HOVER_BLOCKED = 'rgba(182, 83, 83, 0.24)';
const PLACED = '#6ca2ff';
const INACTIVE = 'rgba(116, 123, 132, 0.38)';
const PREVIEW = 'rgba(108, 162, 255, 0.42)';
const PREVIEW_BLOCKED = 'rgba(182, 83, 83, 0.36)';
const PREVIEW_COLLISION = 'rgba(161, 34, 34, 0.62)';
const ANCHOR = 'rgba(108, 162, 255, 0.28)';
const LABEL = '#091015';
const COLLISION_MARK = '#ffe0e0';
const HANDLE_FILL = 'rgba(8, 16, 21, 0.95)';
const HANDLE_STROKE = '#a6c4ff';
const HANDLE_ACTIVE_STROKE = '#ffd166';

export function clampZoom(nextZoom: number): number {
  return Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, nextZoom));
}

export function resizeCanvasToDisplaySize(canvas: HTMLCanvasElement): void {
  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  const width = Math.max(1, Math.round(rect.width * dpr));
  const height = Math.max(1, Math.round(rect.height * dpr));
  if (canvas.width !== width || canvas.height !== height) {
    canvas.width = width;
    canvas.height = height;
  }
}

export function centerViewport(
  canvas: HTMLCanvasElement,
  focus: Point,
  zoom = DEFAULT_ZOOM,
): EditorViewport {
  const rect = canvas.getBoundingClientRect();
  const nextZoom = clampZoom(zoom);
  return {
    zoom: nextZoom,
    offsetX: rect.width / 2 - focus.x * nextZoom,
    offsetY: rect.height / 2 - focus.y * nextZoom,
  };
}

export function zoomViewportAt(
  viewport: EditorViewport,
  cursorX: number,
  cursorY: number,
  nextZoom: number,
): EditorViewport {
  const clamped = clampZoom(nextZoom);
  const worldX = (cursorX - viewport.offsetX) / viewport.zoom;
  const worldY = (cursorY - viewport.offsetY) / viewport.zoom;
  return {
    zoom: clamped,
    offsetX: cursorX - worldX * clamped,
    offsetY: cursorY - worldY * clamped,
  };
}

export function screenToCell(
  viewport: EditorViewport,
  x: number,
  y: number,
  gridSize: number,
): Point | null {
  const cellX = Math.floor((x - viewport.offsetX) / viewport.zoom);
  const cellY = Math.floor((y - viewport.offsetY) / viewport.zoom);
  if (cellX < 0 || cellY < 0 || cellX >= gridSize || cellY >= gridSize) {
    return null;
  }
  return { x: cellX, y: cellY };
}

function drawCell(
  ctx: CanvasRenderingContext2D,
  viewport: EditorViewport,
  cell: { x: number; y: number; index?: number },
  color: string,
  options?: {
    showIndex?: boolean;
    marker?: string;
    markerZoomThreshold?: number;
    selected?: boolean;
  },
): void {
  const inset = Math.max(1.5, viewport.zoom * 0.14);
  const sx = viewport.offsetX + cell.x * viewport.zoom;
  const sy = viewport.offsetY + cell.y * viewport.zoom;

  ctx.fillStyle = color;
  ctx.fillRect(
    sx + inset,
    sy + inset,
    Math.max(1, viewport.zoom - inset * 2),
    Math.max(1, viewport.zoom - inset * 2),
  );

  if (options?.selected) {
    ctx.strokeStyle = '#ffd166';
    ctx.lineWidth = Math.max(1.25, viewport.zoom * 0.09);
    ctx.strokeRect(
      sx + inset * 0.5,
      sy + inset * 0.5,
      Math.max(1, viewport.zoom - inset),
      Math.max(1, viewport.zoom - inset),
    );
  }

  if (options?.marker && viewport.zoom >= (options.markerZoomThreshold ?? 10)) {
    ctx.fillStyle = COLLISION_MARK;
    ctx.font = `${Math.max(10, viewport.zoom * 0.52)}px "Elements Mono", monospace`;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(options.marker, sx + viewport.zoom / 2, sy + viewport.zoom / 2);
    return;
  }

  if (options?.showIndex !== false && cell.index != null && viewport.zoom >= 16) {
    ctx.fillStyle = LABEL;
    ctx.font = `${Math.max(10, viewport.zoom * 0.42)}px "Elements Mono", monospace`;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(String(cell.index), sx + viewport.zoom / 2, sy + viewport.zoom / 2);
  }
}

function drawHandle(
  ctx: CanvasRenderingContext2D,
  viewport: EditorViewport,
  handle: EditorHandle,
): void {
  const size = Math.max(6, viewport.zoom * 0.42);
  const sx = viewport.offsetX + handle.x * viewport.zoom + viewport.zoom / 2;
  const sy = viewport.offsetY + handle.y * viewport.zoom + viewport.zoom / 2;
  const left = sx - size / 2;
  const top = sy - size / 2;

  ctx.fillStyle = HANDLE_FILL;
  ctx.fillRect(left, top, size, size);
  ctx.strokeStyle = handle.active ? HANDLE_ACTIVE_STROKE : HANDLE_STROKE;
  ctx.lineWidth = Math.max(1.25, viewport.zoom * 0.08);
  ctx.strokeRect(left, top, size, size);
}

export function renderEditor(
  canvas: HTMLCanvasElement,
  document: EditorDocument,
  viewport: EditorViewport,
  hoverCell: Point | null,
  preview: EditorPreview | null,
  hoverBlocked = false,
  selectedCells?: ReadonlySet<string>,
  handles?: readonly EditorHandle[],
): void {
  resizeCanvasToDisplaySize(canvas);
  const ctx = canvas.getContext('2d');
  if (!ctx) {
    return;
  }

  const dpr = window.devicePixelRatio || 1;
  const width = canvas.width / dpr;
  const height = canvas.height / dpr;

  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = SURFACE;
  ctx.fillRect(0, 0, width, height);

  const startX = Math.max(0, Math.floor((0 - viewport.offsetX) / viewport.zoom) - 1);
  const endX = Math.min(document.gridSize, Math.ceil((width - viewport.offsetX) / viewport.zoom) + 1);
  const startY = Math.max(0, Math.floor((0 - viewport.offsetY) / viewport.zoom) - 1);
  const endY = Math.min(document.gridSize, Math.ceil((height - viewport.offsetY) / viewport.zoom) + 1);

  ctx.strokeStyle = GRID_LINE;
  ctx.lineWidth = 1;
  ctx.beginPath();
  for (let x = startX; x <= endX; x += 1) {
    const sx = viewport.offsetX + x * viewport.zoom;
    ctx.moveTo(sx, 0);
    ctx.lineTo(sx, height);
  }
  for (let y = startY; y <= endY; y += 1) {
    const sy = viewport.offsetY + y * viewport.zoom;
    ctx.moveTo(0, sy);
    ctx.lineTo(width, sy);
  }
  ctx.stroke();

  if (hoverCell) {
    const sx = viewport.offsetX + hoverCell.x * viewport.zoom;
    const sy = viewport.offsetY + hoverCell.y * viewport.zoom;
    ctx.fillStyle = hoverBlocked ? HOVER_BLOCKED : HOVER;
    ctx.fillRect(sx, sy, viewport.zoom, viewport.zoom);
  }

  if (preview?.anchor) {
    const sx = viewport.offsetX + preview.anchor.x * viewport.zoom;
    const sy = viewport.offsetY + preview.anchor.y * viewport.zoom;
    ctx.fillStyle = ANCHOR;
    ctx.fillRect(sx, sy, viewport.zoom, viewport.zoom);
  }

  for (const cell of document.occupied.values()) {
    if (cell.x < startX - 1 || cell.x > endX || cell.y < startY - 1 || cell.y > endY) {
      continue;
    }
    const selected = Boolean(selectedCells?.has(`${cell.x},${cell.y}`));
    if (cell.inactive) {
      drawCell(ctx, viewport, cell, INACTIVE, { showIndex: false, selected });
    } else {
      drawCell(ctx, viewport, cell, PLACED, { selected });
    }
  }

  if (preview) {
    const color = preview.error ? PREVIEW_BLOCKED : PREVIEW;
    for (const cell of preview.cells) {
      if (cell.x < startX - 1 || cell.x > endX || cell.y < startY - 1 || cell.y > endY) {
        continue;
      }
      const isCollision = document.occupied.has(`${cell.x},${cell.y}`);
      drawCell(
        ctx,
        viewport,
        cell,
        isCollision ? PREVIEW_COLLISION : color,
        isCollision ? { showIndex: false, marker: 'X', markerZoomThreshold: 10 } : undefined,
      );
    }
  }

  if (handles?.length) {
    for (const handle of handles) {
      if (handle.x < startX - 1 || handle.x > endX || handle.y < startY - 1 || handle.y > endY) {
        continue;
      }
      drawHandle(ctx, viewport, handle);
    }
  }
}
