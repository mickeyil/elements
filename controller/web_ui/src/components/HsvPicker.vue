<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';

import { hsvToCss, hsvToRgb, type PanelHsv } from '../lib/panelModel';

// A hue/saturation disc (angle = hue, distance from the center = saturation)
// beside a value slider. Emits change with the whole color on every move.
const props = defineProps<{ modelValue: PanelHsv }>();
const emit = defineEmits<{ change: [value: PanelHsv] }>();

const SIZE = 220;
const RADIUS = SIZE / 2;

const disc = ref<HTMLCanvasElement | null>(null);
let dragging = false;

const swatch = computed(() => hsvToCss(props.modelValue));

// The marker sits at the color's hue angle, saturation out from the center.
const marker = computed(() => {
  const angle = (props.modelValue.h * Math.PI) / 180;
  const distance = props.modelValue.s * RADIUS;
  return {
    left: `${RADIUS + Math.cos(angle) * distance}px`,
    top: `${RADIUS + Math.sin(angle) * distance}px`,
  };
});

const readout = computed(() => {
  const { h, s, v } = props.modelValue;
  return `H ${Math.round(h)}° S ${Math.round(s * 100)}% V ${Math.round(v * 100)}%`;
});

// The disc is drawn once at full value; the swatch shows the chosen value.
function paintDisc(canvas: HTMLCanvasElement): void {
  const scale = window.devicePixelRatio || 1;
  const px = Math.round(SIZE * scale);
  canvas.width = px;
  canvas.height = px;
  const ctx = canvas.getContext('2d');
  if (!ctx) {
    return;
  }
  const image = ctx.createImageData(px, px);
  const center = px / 2;
  for (let y = 0; y < px; y += 1) {
    for (let x = 0; x < px; x += 1) {
      const dx = x + 0.5 - center;
      const dy = y + 0.5 - center;
      const distance = Math.hypot(dx, dy);
      if (distance > center) {
        continue;
      }
      const h = ((Math.atan2(dy, dx) * 180) / Math.PI + 360) % 360;
      const [r, g, b] = hsvToRgb({ h, s: distance / center, v: 1 });
      const i = (y * px + x) * 4;
      image.data[i] = r;
      image.data[i + 1] = g;
      image.data[i + 2] = b;
      image.data[i + 3] = 255;
    }
  }
  ctx.putImageData(image, 0, 0);
}

function pick(event: PointerEvent): void {
  const rect = (event.currentTarget as HTMLCanvasElement).getBoundingClientRect();
  const dx = event.clientX - rect.left - RADIUS;
  const dy = event.clientY - rect.top - RADIUS;
  const h = ((Math.atan2(dy, dx) * 180) / Math.PI + 360) % 360;
  const s = Math.min(1, Math.hypot(dx, dy) / RADIUS);
  emit('change', { h, s, v: props.modelValue.v });
}

function onPointerDown(event: PointerEvent): void {
  (event.currentTarget as HTMLCanvasElement).setPointerCapture(event.pointerId);
  dragging = true;
  pick(event);
}

function onPointerMove(event: PointerEvent): void {
  if (dragging) {
    pick(event);
  }
}

function onPointerUp(): void {
  dragging = false;
}

function onValue(event: Event): void {
  const v = Number((event.target as HTMLInputElement).value);
  emit('change', { ...props.modelValue, v });
}

onMounted(() => {
  if (disc.value) {
    paintDisc(disc.value);
  }
});
</script>

<template>
  <div class="hsv-picker">
    <div class="hsv-disc-wrap" :style="{ width: `${SIZE}px`, height: `${SIZE}px` }">
      <canvas
        ref="disc"
        class="hsv-disc"
        :style="{ width: `${SIZE}px`, height: `${SIZE}px` }"
        @pointerdown="onPointerDown"
        @pointermove="onPointerMove"
        @pointerup="onPointerUp"
        @pointercancel="onPointerUp"
      />
      <span class="hsv-marker" :style="marker" />
    </div>
    <input
      class="hsv-value"
      type="range"
      min="0"
      max="1"
      step="0.01"
      aria-label="Value"
      :value="modelValue.v"
      :style="{ height: `${SIZE}px` }"
      @input="onValue"
    />
    <div class="hsv-readout">
      <span class="hsv-swatch" :style="{ background: swatch }" />
      <span class="hsv-text mono">{{ readout }}</span>
    </div>
  </div>
</template>

<style scoped>
.hsv-picker {
  display: flex;
  align-items: flex-start;
  gap: 1rem;
  flex-wrap: wrap;
}

.hsv-disc-wrap {
  position: relative;
  flex: 0 0 auto;
}

.hsv-disc {
  display: block;
  border-radius: 50%;
  cursor: crosshair;
  touch-action: none;
}

.hsv-marker {
  position: absolute;
  width: 0.9rem;
  height: 0.9rem;
  border: 2px solid #fff;
  border-radius: 50%;
  box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.6);
  transform: translate(-50%, -50%);
  pointer-events: none;
}

.hsv-value {
  writing-mode: vertical-lr;
  direction: rtl;
  width: 1.4rem;
  margin: 0;
  accent-color: var(--accent);
}

.hsv-readout {
  display: grid;
  gap: 0.5rem;
  align-content: start;
}

.hsv-swatch {
  width: 4.5rem;
  height: 4.5rem;
  border-radius: var(--radius-panel);
  border: 1px solid var(--panel-edge-strong);
}

.hsv-text {
  color: var(--muted);
  font-size: 0.72rem;
  letter-spacing: 0.04em;
  white-space: nowrap;
}
</style>
