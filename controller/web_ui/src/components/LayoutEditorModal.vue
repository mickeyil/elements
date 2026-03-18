<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue';

import {
  createDocumentFromLayout,
  createEmptyDocument,
  documentCenter,
  GRID_SIZE,
  incrementCurrentIndex,
  placeSinglePrimitive,
  serializeDocument,
  setCurrentIndex,
  undoLastPrimitive,
  type EditorDocument,
  type LayoutDocumentPayload,
  type Point,
} from '../lib/editorModel';
import {
  centerViewport,
  DEFAULT_ZOOM,
  MAX_ZOOM,
  MIN_ZOOM,
  renderEditor,
  screenToCell,
  zoomViewportAt,
  type EditorViewport,
} from '../lib/editorRenderer';
import { getLayout, saveLayout } from '../lib/layoutApi';

const props = defineProps<{
  deviceLength: number;
  deviceUid: string;
}>();

const emit = defineEmits<{
  close: [];
}>();

const canvasRef = ref<HTMLCanvasElement | null>(null);
const loading = ref(true);
const saving = ref(false);
const error = ref('');
const hoverCell = ref<Point | null>(null);
const documentRef = shallowRef<EditorDocument>(createEmptyDocument(props.deviceLength));
const viewport = ref<EditorViewport>({
  zoom: DEFAULT_ZOOM,
  offsetX: 0,
  offsetY: 0,
});
const baselinePayload = shallowRef<LayoutDocumentPayload | null>(null);
const baseCsvHash = ref<string | null>(null);

let spacePressed = false;
let panning:
  | {
      startClientX: number;
      startClientY: number;
      startOffsetX: number;
      startOffsetY: number;
    }
  | null = null;
let suppressClick = false;
let renderPending = false;

const currentIndex = computed(() => documentRef.value.currentIndex);
const placedCount = computed(() => documentRef.value.placedCount);
const canUndo = computed(() => documentRef.value.primitives.length > 0 && !loading.value && !saving.value);
const canSave = computed(() => !loading.value && !saving.value);

function requestRender(): void {
  if (renderPending) {
    return;
  }
  renderPending = true;
  window.requestAnimationFrame(() => {
    renderPending = false;
    const canvas = canvasRef.value;
    if (!canvas || loading.value) {
      return;
    }
    renderEditor(canvas, documentRef.value, viewport.value, hoverCell.value);
  });
}

async function resetViewport(): Promise<void> {
  await nextTick();
  const canvas = canvasRef.value;
  if (!canvas) {
    return;
  }
  viewport.value = centerViewport(canvas, documentCenter(documentRef.value), DEFAULT_ZOOM);
  requestRender();
}

async function loadInitialDocument(): Promise<void> {
  loading.value = true;
  error.value = '';
  try {
    const payload = await getLayout(props.deviceUid);
    baselinePayload.value = payload;
    baseCsvHash.value = payload?.editor?.csv_hash ?? null;
    documentRef.value = createDocumentFromLayout(payload, props.deviceLength);
    await resetViewport();
  } catch (err) {
    error.value = err instanceof Error ? err.message : 'Failed to load layout.';
    baselinePayload.value = null;
    baseCsvHash.value = null;
    documentRef.value = createEmptyDocument(props.deviceLength);
    await resetViewport();
  } finally {
    loading.value = false;
    requestRender();
  }
}

function relativePoint(event: MouseEvent | WheelEvent): Point | null {
  const canvas = canvasRef.value;
  if (!canvas) {
    return null;
  }
  const rect = canvas.getBoundingClientRect();
  return {
    x: event.clientX - rect.left,
    y: event.clientY - rect.top,
  };
}

function updateHover(event: MouseEvent): void {
  const point = relativePoint(event);
  if (!point || panning) {
    hoverCell.value = null;
    requestRender();
    return;
  }
  hoverCell.value = screenToCell(viewport.value, point.x, point.y, GRID_SIZE);
  requestRender();
}

function handleMouseDown(event: MouseEvent): void {
  if (event.button === 1 || (event.button === 0 && spacePressed)) {
    event.preventDefault();
    panning = {
      startClientX: event.clientX,
      startClientY: event.clientY,
      startOffsetX: viewport.value.offsetX,
      startOffsetY: viewport.value.offsetY,
    };
    suppressClick = true;
  }
}

function handleMouseMove(event: MouseEvent): void {
  if (panning) {
    viewport.value = {
      ...viewport.value,
      offsetX: panning.startOffsetX + (event.clientX - panning.startClientX),
      offsetY: panning.startOffsetY + (event.clientY - panning.startClientY),
    };
    requestRender();
    return;
  }
  updateHover(event);
}

function handleMouseLeave(): void {
  hoverCell.value = null;
  requestRender();
}

function handleMouseUp(): void {
  panning = null;
}

function handleWheel(event: WheelEvent): void {
  event.preventDefault();
  const point = relativePoint(event);
  if (!point) {
    return;
  }
  const factor = event.deltaY < 0 ? 1.1 : 0.9;
  viewport.value = zoomViewportAt(
    viewport.value,
    point.x,
    point.y,
    viewport.value.zoom * factor,
  );
  requestRender();
}

function handleClick(event: MouseEvent): void {
  if (loading.value || saving.value) {
    return;
  }
  if (suppressClick) {
    suppressClick = false;
    return;
  }
  const point = relativePoint(event);
  if (!point) {
    return;
  }
  const cell = screenToCell(viewport.value, point.x, point.y, GRID_SIZE);
  if (!cell) {
    return;
  }
  try {
    documentRef.value = placeSinglePrimitive(documentRef.value, cell.x, cell.y);
    error.value = '';
  } catch (err) {
    error.value = err instanceof Error ? err.message : 'Failed to place LED.';
  }
}

function shiftCurrentIndex(delta: number): void {
  documentRef.value = incrementCurrentIndex(documentRef.value, delta);
}

function undo(): void {
  documentRef.value = undoLastPrimitive(documentRef.value);
  error.value = '';
}

function resetDocument(): void {
  documentRef.value = createDocumentFromLayout(baselinePayload.value, props.deviceLength);
  error.value = '';
  void resetViewport();
}

function close(): void {
  if (!saving.value) {
    emit('close');
  }
}

async function save(): Promise<void> {
  try {
    saving.value = true;
    error.value = '';
    const serialized = serializeDocument(documentRef.value);
    const response = await saveLayout(props.deviceUid, {
      rows: serialized.rows,
      editor: serialized.editor,
      base_csv_hash: baseCsvHash.value,
    });
    baselinePayload.value = response;
    baseCsvHash.value = response.editor?.csv_hash ?? null;
    emit('close');
  } catch (err) {
    error.value = err instanceof Error ? err.message : 'Failed to save layout.';
  } finally {
    saving.value = false;
  }
}

function handleKeyDown(event: KeyboardEvent): void {
  if (event.code === 'Space') {
    event.preventDefault();
    spacePressed = true;
  }
}

function handleKeyUp(event: KeyboardEvent): void {
  if (event.code === 'Space') {
    spacePressed = false;
  }
}

watch([documentRef, viewport, hoverCell, loading], () => {
  requestRender();
});

watch(
  () => canvasRef.value,
  () => {
    void resetViewport();
  },
);

onMounted(() => {
  window.addEventListener('mouseup', handleMouseUp);
  window.addEventListener('keydown', handleKeyDown);
  window.addEventListener('keyup', handleKeyUp);
  window.addEventListener('resize', requestRender);
  void loadInitialDocument();
});

onBeforeUnmount(() => {
  window.removeEventListener('mouseup', handleMouseUp);
  window.removeEventListener('keydown', handleKeyDown);
  window.removeEventListener('keyup', handleKeyUp);
  window.removeEventListener('resize', requestRender);
});
</script>

<template>
  <div class="editor-backdrop" @click.self="close">
    <section class="editor-modal" aria-modal="true" role="dialog">
      <header class="editor-head">
        <div>
          <p class="editor-eyebrow">Layout Editor</p>
          <h2>{{ deviceUid }}</h2>
        </div>
        <button class="ghost-button" type="button" @click="close">Close</button>
      </header>

      <div class="editor-toolbar">
        <div class="toolbar-group">
          <span class="toolbar-label">Tool</span>
          <strong>Single LED</strong>
        </div>
        <div class="toolbar-group">
          <span class="toolbar-label">Index</span>
          <div class="index-controls">
            <button type="button" @click="shiftCurrentIndex(-1)" :disabled="loading || saving">&lt;</button>
            <strong>{{ currentIndex }}</strong>
            <span>/ {{ deviceLength }}</span>
            <button type="button" @click="shiftCurrentIndex(1)" :disabled="loading || saving">&gt;</button>
          </div>
        </div>
        <div class="toolbar-group">
          <span class="toolbar-label">Placed</span>
          <strong>{{ placedCount }} / {{ deviceLength }}</strong>
        </div>
        <div class="toolbar-group toolbar-group-actions">
          <button type="button" class="ghost-button" :disabled="!canUndo" @click="undo">Undo</button>
          <span class="zoom-range">zoom {{ MIN_ZOOM }}-{{ MAX_ZOOM }}</span>
        </div>
      </div>

      <p v-if="error" class="editor-error">{{ error }}</p>

      <div v-if="loading" class="editor-loading">Loading layout…</div>
      <div v-else class="editor-surface">
        <canvas
          ref="canvasRef"
          class="editor-canvas"
          @mousedown="handleMouseDown"
          @mousemove="handleMouseMove"
          @mouseleave="handleMouseLeave"
          @click="handleClick"
          @wheel="handleWheel"
        />
      </div>

      <footer class="editor-footer">
        <div class="editor-hint">
          Click to place the current index. Use the wheel to zoom and hold space while dragging to pan.
        </div>
        <div class="editor-actions">
          <button type="button" class="ghost-button" :disabled="loading || saving" @click="resetDocument">
            Reset
          </button>
          <button type="button" class="ghost-button" :disabled="saving" @click="close">Cancel</button>
          <button type="button" class="save-button" :disabled="!canSave" @click="save">
            {{ saving ? 'Saving…' : 'Save' }}
          </button>
        </div>
      </footer>
    </section>
  </div>
</template>

<style scoped>
.editor-backdrop {
  position: fixed;
  inset: 0;
  z-index: 20;
  display: grid;
  place-items: center;
  padding: 2rem;
  background: rgba(5, 9, 12, 0.82);
  backdrop-filter: blur(6px);
}

.editor-modal {
  width: min(96vw, 1200px);
  height: min(92vh, 900px);
  display: grid;
  grid-template-rows: auto auto auto 1fr auto;
  gap: 1rem;
  padding: 1.25rem;
  border-radius: 22px;
  border: 1px solid var(--panel-edge);
  background: rgba(13, 19, 24, 0.96);
  box-shadow: 0 28px 80px rgba(0, 0, 0, 0.45);
}

.editor-head,
.editor-toolbar,
.editor-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
}

.editor-eyebrow {
  margin: 0 0 0.35rem;
  text-transform: uppercase;
  letter-spacing: 0.18em;
  font-size: 0.72rem;
  color: var(--accent);
}

.editor-head h2 {
  margin: 0;
  font-family: 'IBM Plex Mono', 'SFMono-Regular', monospace;
}

.editor-toolbar {
  flex-wrap: wrap;
  padding: 0.85rem 1rem;
  border-radius: 16px;
  border: 1px solid var(--panel-edge);
  background: rgba(255, 255, 255, 0.03);
}

.toolbar-group {
  display: grid;
  gap: 0.25rem;
}

.toolbar-group-actions {
  margin-left: auto;
  align-items: end;
}

.toolbar-label {
  font-size: 0.74rem;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--muted);
}

.index-controls {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.index-controls button,
.ghost-button,
.save-button {
  border: 1px solid var(--panel-edge);
  border-radius: 999px;
  padding: 0.55rem 0.9rem;
  background: rgba(255, 255, 255, 0.04);
  color: var(--text);
  cursor: pointer;
}

.index-controls button:disabled,
.ghost-button:disabled,
.save-button:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.save-button {
  background: var(--accent-soft);
  border-color: rgba(229, 156, 76, 0.35);
}

.zoom-range {
  color: var(--muted);
  font-size: 0.8rem;
}

.editor-error {
  margin: 0;
  padding: 0.75rem 0.9rem;
  border-radius: 14px;
  border: 1px solid rgba(182, 83, 83, 0.35);
  background: rgba(182, 83, 83, 0.16);
  color: #f7e5e5;
}

.editor-loading,
.editor-surface {
  min-height: 0;
  border-radius: 18px;
  border: 1px solid var(--panel-edge);
  background: #05090c;
}

.editor-loading {
  display: grid;
  place-items: center;
  color: var(--muted);
}

.editor-surface {
  overflow: hidden;
}

.editor-canvas {
  width: 100%;
  height: 100%;
  display: block;
  cursor: crosshair;
}

.editor-footer {
  align-items: end;
}

.editor-hint {
  color: var(--muted);
  font-size: 0.85rem;
  max-width: 40rem;
}

.editor-actions {
  display: flex;
  gap: 0.75rem;
  justify-content: flex-end;
}

@media (max-width: 900px) {
  .editor-backdrop {
    padding: 1rem;
  }

  .editor-modal {
    width: 100%;
    height: 100%;
  }

  .editor-footer,
  .editor-head,
  .editor-toolbar {
    align-items: flex-start;
    flex-direction: column;
  }

  .toolbar-group-actions {
    margin-left: 0;
    align-items: flex-start;
  }
}
</style>
