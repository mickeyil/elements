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

// The sim preview carries raw program-space RGB; the device applies its own
// gamma to the physical LEDs (see SimFrameOutput), so without correction the
// on-screen pixels read dimmer than the strip. A display gamma lifts midtones;
// 0 -> 0 and 255 -> 255 are fixed, so blacks and full brightness are unchanged.
// Raise DISPLAY_GAMMA toward the device's 2.8 for more lift, or to 1.0 for none.
const DISPLAY_GAMMA = 2.2;
const GAMMA_LUT = ((): Uint8Array => {
  const lut = new Uint8Array(256);
  for (let i = 0; i < 256; i += 1) {
    lut[i] = Math.round((i / 255) ** (1 / DISPLAY_GAMMA) * 255);
  }
  return lut;
})();

// Pitch-black canvas; each LED cell gets a faint grayish border so the pixel
// grid stays legible even when cells are dark.
const BACKGROUND_FILL = '#000';
const PIXEL_BORDER = 'rgba(255, 255, 255, 0.18)';

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

// Map a canvas-local mouse position to the logical LED index under it, or null
// when the cell is empty or out of bounds. The index is 0-based to match the
// strip/program pixel index (layout rows store 1-based cell numbers).
export function ledIndexAt(target: SimTarget, offsetX: number, offsetY: number): number | null {
  const rows = Array.isArray(target.layout?.rows) ? target.layout.rows : [];
  const x = Math.floor(offsetX / CELL_PX);
  const y = Math.floor(offsetY / CELL_PX);
  if (y < 0 || y >= rows.length) {
    return null;
  }
  const row = Array.isArray(rows[y]) ? rows[y] : [];
  const cell = row[x] ?? null;
  return Number.isInteger(cell) ? Number(cell) - 1 : null;
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
    const gridWidth = target.gridWidth;
    const ctx = target.ctx;
    const src = latestSlices[target.logicalIndex] ?? null;
    const srcPixels = src
      ? Math.min(target.logicalLength, target.physicalLength, Math.floor(src.length / 3))
      : 0;

    ctx.fillStyle = BACKGROUND_FILL;
    ctx.fillRect(0, 0, gridWidth * CELL_PX, gridHeight * CELL_PX);
    ctx.lineWidth = 1;

    for (let y = 0; y < gridHeight; y += 1) {
      const row = Array.isArray(rows[y]) ? rows[y] : [];
      for (let x = 0; x < gridWidth; x += 1) {
        const cell = row[x] ?? null;
        if (!Number.isInteger(cell)) {
          // Non-LED cells stay pitch black (no border).
          continue;
        }

        let r = 0;
        let g = 0;
        let b = 0;
        const pixelIndex = Number(cell) - 1;
        if (pixelIndex >= 0 && pixelIndex < srcPixels && src) {
          const si = pixelIndex * 3;
          r = GAMMA_LUT[src[si]];
          g = GAMMA_LUT[src[si + 1]];
          b = GAMMA_LUT[src[si + 2]];
        }

        const px = x * CELL_PX;
        const py = y * CELL_PX;
        ctx.fillStyle = `rgb(${r}, ${g}, ${b})`;
        ctx.fillRect(px, py, CELL_PX, CELL_PX);
        ctx.strokeStyle = PIXEL_BORDER;
        ctx.strokeRect(px + 0.5, py + 0.5, CELL_PX - 1, CELL_PX - 1);
      }
    }
  }
}
