export interface LayoutPayload {
  rows: Array<Array<number | null>>;
}

export interface SimTarget {
  deviceUid: string;
  stripName: string;
  logicalIndex: number;
  logicalLength: number;
  physicalLength: number;
  connected: boolean;
  layout: LayoutPayload | null;
  gridWidth: number;
  gridHeight: number;
  canvas: HTMLCanvasElement | null;
  ctx: CanvasRenderingContext2D | null;
}

export const CELL_PX = 14;
const EMPTY_CELL: [number, number, number, number] = [19, 28, 34, 255];

export function measureLayout(layout: LayoutPayload | null): {
  gridWidth: number;
  gridHeight: number;
} {
  const rows = Array.isArray(layout?.rows) ? layout.rows : [];
  const gridHeight = rows.length;
  const gridWidth = Math.max(0, ...rows.map((row) => (Array.isArray(row) ? row.length : 0)));
  return { gridWidth, gridHeight };
}

export function attachTargetCanvas(target: SimTarget, canvas: HTMLCanvasElement | null): void {
  target.canvas = canvas;
  target.ctx = canvas ? canvas.getContext('2d') : null;
}

export function paintTargets(
  targets: readonly SimTarget[],
  latestSlices: readonly Uint8Array[],
): void {
  for (const target of targets) {
    if (!target.layout || !target.canvas || !target.ctx) {
      continue;
    }

    const rows = Array.isArray(target.layout.rows) ? target.layout.rows : [];
    const gridHeight = rows.length;
    const gridWidth = target.canvas.width;
    const image = target.ctx.createImageData(gridWidth, gridHeight);
    const src = latestSlices[target.logicalIndex] ?? null;
    const srcPixels = src
      ? Math.min(target.logicalLength, target.physicalLength, Math.floor(src.length / 3))
      : 0;

    for (let y = 0; y < gridHeight; y += 1) {
      const row = Array.isArray(rows[y]) ? rows[y] : [];
      for (let x = 0; x < gridWidth; x += 1) {
        const di = (y * gridWidth + x) * 4;
        const cell = row[x] ?? null;

        if (Number.isInteger(cell)) {
          const pixelIndex = Number(cell) - 1;
          if (pixelIndex >= 0 && pixelIndex < srcPixels && src) {
            const si = pixelIndex * 3;
            image.data[di] = src[si];
            image.data[di + 1] = src[si + 1];
            image.data[di + 2] = src[si + 2];
          } else {
            image.data[di] = 0;
            image.data[di + 1] = 0;
            image.data[di + 2] = 0;
          }
          image.data[di + 3] = 255;
          continue;
        }

        image.data[di] = EMPTY_CELL[0];
        image.data[di + 1] = EMPTY_CELL[1];
        image.data[di + 2] = EMPTY_CELL[2];
        image.data[di + 3] = EMPTY_CELL[3];
      }
    }

    target.ctx.putImageData(image, 0, 0);
  }
}
