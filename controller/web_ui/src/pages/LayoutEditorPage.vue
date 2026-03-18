<script setup lang="ts">
import {
  computed,
  nextTick,
  onBeforeUnmount,
  onMounted,
  ref,
  shallowRef,
  watch,
} from 'vue';
import { onBeforeRouteLeave, onBeforeRouteUpdate, useRoute, useRouter } from 'vue-router';

import { useInjectedRelayState, type SnapshotDevice } from '../composables/useRelayState';
import {
  cloneDocument,
  createDocumentFromLayout,
  createEmptyDocument,
  documentCenter,
  documentsEqual,
  GRID_SIZE,
  incrementCurrentIndex,
  placeLinePrimitive,
  placeSinglePrimitive,
  previewLinePlacement,
  serializeDocument,
  undoLastPrimitive,
  type EditorDocument,
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
  type EditorPreview,
  type EditorViewport,
} from '../lib/editorRenderer';
import { getLayout, saveLayout } from '../lib/layoutApi';

interface DeviceMeta {
  deviceUid: string;
  deviceType: string;
  length: number;
  stripId: string;
}

type Tool = 'single' | 'line';

const route = useRoute();
const router = useRouter();
const { snapshot } = useInjectedRelayState();

const canvasRef = ref<HTMLCanvasElement | null>(null);
const loadingLayout = ref(false);
const saving = ref(false);
const error = ref('');
const statusNotice = ref('');
const hoverCell = ref<Point | null>(null);
const viewport = ref<EditorViewport>({
  zoom: DEFAULT_ZOOM,
  offsetX: 0,
  offsetY: 0,
});
const documentRef = shallowRef<EditorDocument>(createEmptyDocument(1));
const baselineDocument = shallowRef<EditorDocument>(createEmptyDocument(1));
const baseCsvHash = ref<string | null>(null);
const resolvedDeviceMeta = shallowRef<DeviceMeta | null>(null);
const activeTool = ref<Tool>('single');
const lineSpacing = ref(0);
const lineStart = ref<Point | null>(null);

let spacePressed = false;
let renderPending = false;
let loadToken = 0;
let suppressClick = false;
let panning:
  | {
      startClientX: number;
      startClientY: number;
      startOffsetX: number;
      startOffsetY: number;
    }
  | null = null;

const routeDeviceUid = computed(() =>
  typeof route.params.deviceUid === 'string' ? route.params.deviceUid : '',
);

const snapshotDevices = computed<SnapshotDevice[]>(() => {
  const devices = snapshot.value?.devices;
  return Array.isArray(devices) ? devices : [];
});

const matchedSnapshotDevice = computed<SnapshotDevice | null>(() => {
  if (!routeDeviceUid.value) {
    return null;
  }
  return (
    snapshotDevices.value.find((device) => device?.device_uid === routeDeviceUid.value) ?? null
  );
});

const currentDeviceMeta = computed<DeviceMeta | null>(() => {
  const matched = matchedSnapshotDevice.value;
  if (matched?.device_uid) {
    return {
      deviceUid: matched.device_uid,
      deviceType: String(matched.device_type ?? ''),
      length: Number(matched.length ?? 0),
      stripId: String(matched.strip_id ?? 'n/a'),
    };
  }
  if (resolvedDeviceMeta.value?.deviceUid === routeDeviceUid.value) {
    return resolvedDeviceMeta.value;
  }
  return null;
});

const routeState = computed<
  | { kind: 'waiting'; message: string }
  | { kind: 'invalid'; message: string }
  | { kind: 'ready'; device: DeviceMeta }
>(() => {
  if (!routeDeviceUid.value) {
    return { kind: 'invalid', message: 'Invalid layout route.' };
  }

  const device = currentDeviceMeta.value;
  if (!device) {
    if (!snapshot.value) {
      return { kind: 'waiting', message: 'Waiting for device metadata from the relay.' };
    }
    return { kind: 'invalid', message: `Unknown device: ${routeDeviceUid.value}` };
  }

  if (device.deviceType !== 'sim') {
    return {
      kind: 'invalid',
      message: `${device.deviceUid} is a ${device.deviceType || 'non-sim'} device and cannot be edited in the browser.`,
    };
  }

  if (device.length < 1) {
    return {
      kind: 'invalid',
      message: `${device.deviceUid} has an invalid configured length.`,
    };
  }

  return { kind: 'ready', device };
});

const readyDeviceKey = computed(() => {
  if (routeState.value.kind !== 'ready') {
    return null;
  }
  return `${routeState.value.device.deviceUid}:${routeState.value.device.length}`;
});

const deviceLength = computed(() =>
  routeState.value.kind === 'ready' ? routeState.value.device.length : baselineDocument.value.maxIndex,
);
const currentIndex = computed(() => documentRef.value.currentIndex);
const placedCount = computed(() => documentRef.value.placedCount);
const canUndo = computed(
  () =>
    routeState.value.kind === 'ready' &&
    !loadingLayout.value &&
    !saving.value &&
    documentRef.value.primitives.length > 0,
);
const canSave = computed(
  () => routeState.value.kind === 'ready' && !loadingLayout.value && !saving.value,
);
const isDirty = computed(() => !documentsEqual(documentRef.value, baselineDocument.value));
const currentSpacing = computed(() => Math.max(0, Math.floor(Number(lineSpacing.value) || 0)));
const toolDescription = computed(() =>
  activeTool.value === 'single' ? 'single LED tool' : `line tool · spacing ${currentSpacing.value}`,
);

const linePreview = computed<EditorPreview | null>(() => {
  if (activeTool.value !== 'line' || !lineStart.value) {
    return null;
  }

  if (!hoverCell.value) {
    return {
      cells: [],
      error: null,
      anchor: lineStart.value,
    };
  }

  try {
    const preview = previewLinePlacement(
      documentRef.value,
      lineStart.value,
      hoverCell.value,
      currentSpacing.value,
    );
    return {
      cells: preview.cells,
      error: preview.error,
      anchor: lineStart.value,
    };
  } catch (err) {
    return {
      cells: [],
      error: err instanceof Error ? err.message : 'Failed to preview line.',
      anchor: lineStart.value,
    };
  }
});

const previewCount = computed(() => linePreview.value?.cells.length ?? 0);
const previewError = computed(() => linePreview.value?.error ?? '');

function clearLineDraft(): void {
  lineStart.value = null;
}

function setDocument(nextDocument: EditorDocument): void {
  documentRef.value = nextDocument;
  statusNotice.value = '';
}

function requestRender(): void {
  if (renderPending) {
    return;
  }
  renderPending = true;
  window.requestAnimationFrame(() => {
    renderPending = false;
    const canvas = canvasRef.value;
    if (!canvas) {
      return;
    }
    renderEditor(canvas, documentRef.value, viewport.value, hoverCell.value, linePreview.value);
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

async function loadInitialDocument(device: DeviceMeta): Promise<void> {
  loadingLayout.value = true;
  error.value = '';
  statusNotice.value = '';
  clearLineDraft();
  activeTool.value = 'single';
  const token = ++loadToken;

  try {
    const payload = await getLayout(device.deviceUid);
    if (token !== loadToken) {
      return;
    }
    const nextDocument = createDocumentFromLayout(payload, device.length);
    documentRef.value = nextDocument;
    baselineDocument.value = cloneDocument(nextDocument);
    baseCsvHash.value = payload?.editor?.csv_hash ?? null;
    await resetViewport();
  } catch (err) {
    if (token !== loadToken) {
      return;
    }
    const nextDocument = createEmptyDocument(device.length);
    documentRef.value = nextDocument;
    baselineDocument.value = cloneDocument(nextDocument);
    baseCsvHash.value = null;
    error.value = err instanceof Error ? err.message : 'Failed to load layout.';
    await resetViewport();
  } finally {
    if (token === loadToken) {
      loadingLayout.value = false;
      requestRender();
    }
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

function beginLineAt(cell: Point): void {
  if (documentRef.value.occupied.has(`${cell.x},${cell.y}`)) {
    error.value = 'That cell is already occupied.';
    return;
  }
  lineStart.value = cell;
  statusNotice.value = '';
  error.value = '';
}

function commitLineAt(cell: Point): void {
  if (!lineStart.value) {
    beginLineAt(cell);
    return;
  }

  try {
    setDocument(placeLinePrimitive(documentRef.value, lineStart.value, cell, currentSpacing.value));
    clearLineDraft();
    error.value = '';
  } catch (err) {
    error.value = err instanceof Error ? err.message : 'Failed to place line.';
  }
}

function handleClick(event: MouseEvent): void {
  if (routeState.value.kind !== 'ready' || loadingLayout.value || saving.value) {
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
  if (activeTool.value === 'line') {
    commitLineAt(cell);
    return;
  }
  try {
    setDocument(placeSinglePrimitive(documentRef.value, cell.x, cell.y));
    error.value = '';
  } catch (err) {
    error.value = err instanceof Error ? err.message : 'Failed to place LED.';
  }
}

function shiftCurrentIndex(delta: number): void {
  setDocument(incrementCurrentIndex(documentRef.value, delta));
}

function undo(): void {
  setDocument(undoLastPrimitive(documentRef.value));
  error.value = '';
  clearLineDraft();
}

function resetDocument(): void {
  setDocument(cloneDocument(baselineDocument.value));
  clearLineDraft();
  error.value = '';
  void resetViewport();
}

function backToDevices(): void {
  void router.push('/');
}

function selectTool(tool: Tool): void {
  activeTool.value = tool;
  clearLineDraft();
  error.value = '';
  statusNotice.value = '';
}

async function save(): Promise<void> {
  if (routeState.value.kind !== 'ready') {
    return;
  }
  try {
    saving.value = true;
    error.value = '';
    const serialized = serializeDocument(documentRef.value);
    const response = await saveLayout(routeState.value.device.deviceUid, {
      rows: serialized.rows,
      editor: serialized.editor,
      base_csv_hash: baseCsvHash.value,
    });
    baselineDocument.value = cloneDocument(documentRef.value);
    baseCsvHash.value = response.editor?.csv_hash ?? null;
    statusNotice.value = 'Saved.';
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
    return;
  }

  if (event.code === 'Escape' && lineStart.value) {
    event.preventDefault();
    clearLineDraft();
    statusNotice.value = '';
    error.value = '';
  }
}

function handleKeyUp(event: KeyboardEvent): void {
  if (event.code === 'Space') {
    spacePressed = false;
  }
}

function beforeUnloadHandler(event: BeforeUnloadEvent): void {
  event.preventDefault();
  event.returnValue = '';
}

function confirmNavigationAway(): boolean {
  if (!isDirty.value) {
    return true;
  }
  return window.confirm('Discard unsaved layout changes?');
}

watch(
  currentDeviceMeta,
  (device) => {
    if (device && device.deviceUid === routeDeviceUid.value) {
      resolvedDeviceMeta.value = device;
    }
  },
  { immediate: true },
);

watch(
  readyDeviceKey,
  (key) => {
    if (!key || routeState.value.kind !== 'ready') {
      return;
    }
    void loadInitialDocument(routeState.value.device);
  },
  { immediate: true },
);

watch([documentRef, viewport, hoverCell, activeTool, lineStart, lineSpacing], () => {
  requestRender();
});

watch(
  () => canvasRef.value,
  () => {
    void resetViewport();
  },
);

watch(
  isDirty,
  (dirty) => {
    if (dirty) {
      window.addEventListener('beforeunload', beforeUnloadHandler);
    } else {
      window.removeEventListener('beforeunload', beforeUnloadHandler);
    }
  },
  { immediate: true },
);

onBeforeRouteLeave(() => {
  if (!confirmNavigationAway()) {
    return false;
  }
  return true;
});

onBeforeRouteUpdate(() => {
  if (!confirmNavigationAway()) {
    return false;
  }
  return true;
});

onMounted(() => {
  window.addEventListener('mouseup', handleMouseUp);
  window.addEventListener('keydown', handleKeyDown);
  window.addEventListener('keyup', handleKeyUp);
  window.addEventListener('resize', requestRender);
});

onBeforeUnmount(() => {
  window.removeEventListener('mouseup', handleMouseUp);
  window.removeEventListener('keydown', handleKeyDown);
  window.removeEventListener('keyup', handleKeyUp);
  window.removeEventListener('resize', requestRender);
  window.removeEventListener('beforeunload', beforeUnloadHandler);
});
</script>

<template>
  <main class="page-shell editor-page">
    <header class="page-header editor-page-head">
      <div>
        <p class="page-eyebrow">Layout Editor</p>
        <h1 class="page-title">
          {{ routeState.kind === 'ready' ? routeState.device.deviceUid : routeDeviceUid || 'Layout' }}
        </h1>
        <p v-if="routeState.kind === 'ready'" class="page-copy">
          strip {{ routeState.device.stripId }} · {{ routeState.device.length }} px · {{ toolDescription }}
        </p>
        <p v-else class="page-copy">
          {{ routeState.message }}
        </p>
      </div>

      <div class="editor-page-actions">
        <button type="button" class="ghost-button" @click="backToDevices">Back to devices</button>
      </div>
    </header>

    <section v-if="routeState.kind !== 'ready'" class="panel editor-state-panel">
      <div class="empty-state">
        <h2>{{ routeState.kind === 'waiting' ? 'Waiting for device metadata' : 'Editor unavailable' }}</h2>
        <p>{{ routeState.message }}</p>
      </div>
    </section>

    <section v-else class="panel editor-page-panel">
      <div class="editor-toolbar">
        <div class="toolbar-group">
          <span class="toolbar-label">Tool</span>
          <div class="tool-buttons">
            <button
              type="button"
              class="tool-button"
              :class="{ 'tool-button-active': activeTool === 'single' }"
              :disabled="loadingLayout || saving"
              @click="selectTool('single')"
            >
              Single
            </button>
            <button
              type="button"
              class="tool-button"
              :class="{ 'tool-button-active': activeTool === 'line' }"
              :disabled="loadingLayout || saving"
              @click="selectTool('line')"
            >
              Line
            </button>
          </div>
        </div>
        <div v-if="activeTool === 'line'" class="toolbar-group">
          <span class="toolbar-label">Spacing</span>
          <input v-model.number="lineSpacing" class="spacing-input" min="0" step="1" type="number" />
        </div>
        <div class="toolbar-group">
          <span class="toolbar-label">Index</span>
          <div class="index-controls">
            <button type="button" @click="shiftCurrentIndex(-1)" :disabled="loadingLayout || saving">
              &lt;
            </button>
            <strong>{{ currentIndex }}</strong>
            <span>/ {{ deviceLength }}</span>
            <button type="button" @click="shiftCurrentIndex(1)" :disabled="loadingLayout || saving">
              &gt;
            </button>
          </div>
        </div>
        <div class="toolbar-group">
          <span class="toolbar-label">Placed</span>
          <strong>{{ placedCount }} / {{ deviceLength }}</strong>
        </div>
        <div v-if="activeTool === 'line'" class="toolbar-group">
          <span class="toolbar-label">Preview</span>
          <strong v-if="lineStart">
            {{ previewCount ? `${previewCount} LEDs` : 'Pick an end point' }}
          </strong>
          <strong v-else>Pick a start point</strong>
        </div>
        <div class="toolbar-group toolbar-group-actions">
          <button type="button" class="ghost-button" :disabled="!canUndo" @click="undo">Undo</button>
          <span class="zoom-range">zoom {{ MIN_ZOOM }}-{{ MAX_ZOOM }}</span>
        </div>
      </div>

      <p v-if="error" class="editor-error">{{ error }}</p>
      <p v-else-if="activeTool === 'line' && previewError" class="editor-error">{{ previewError }}</p>
      <p v-else-if="statusNotice" class="editor-saved">{{ statusNotice }}</p>

      <div v-if="loadingLayout" class="editor-loading">Loading layout…</div>
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
          <template v-if="activeTool === 'single'">
            Click to place the current index. Use the wheel to zoom and hold space while dragging to pan.
          </template>
          <template v-else>
            Click once to set the line start, move to preview, and click again to confirm. Press Escape to cancel the line draft.
          </template>
        </div>
        <div class="editor-actions">
          <button type="button" class="ghost-button" :disabled="loadingLayout || saving" @click="resetDocument">
            Reset
          </button>
          <button type="button" class="ghost-button" :disabled="saving" @click="backToDevices">
            Back
          </button>
          <button type="button" class="primary-button" :disabled="!canSave" @click="save">
            {{ saving ? 'Saving…' : 'Save' }}
          </button>
        </div>
      </footer>
    </section>
  </main>
</template>

<style scoped>
.editor-page {
  min-height: calc(100vh - 4.5rem);
}

.editor-page-head {
  margin-bottom: 1rem;
}

.editor-page-actions {
  display: flex;
  gap: 0.75rem;
  align-items: center;
}

.editor-state-panel,
.editor-page-panel {
  padding: 1rem;
}

.editor-page-panel {
  min-height: calc(100vh - 14rem);
  display: grid;
  grid-template-rows: auto auto 1fr auto;
  gap: 1rem;
}

.editor-toolbar,
.editor-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
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

.tool-buttons,
.index-controls {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.tool-button,
.index-controls button,
.spacing-input {
  border: 1px solid var(--panel-edge);
  border-radius: 999px;
  padding: 0.55rem 0.9rem;
  background: rgba(255, 255, 255, 0.04);
  color: var(--text);
}

.tool-button {
  cursor: pointer;
}

.tool-button-active {
  border-color: rgba(229, 156, 76, 0.35);
  background: rgba(229, 156, 76, 0.16);
}

.spacing-input {
  width: 6rem;
}

.index-controls button:disabled,
.tool-button:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.zoom-range {
  color: var(--muted);
  font-size: 0.8rem;
}

.editor-error,
.editor-saved {
  margin: 0;
  padding: 0.75rem 0.9rem;
  border-radius: 14px;
}

.editor-error {
  border: 1px solid rgba(182, 83, 83, 0.35);
  background: rgba(182, 83, 83, 0.16);
  color: #f7e5e5;
}

.editor-saved {
  border: 1px solid rgba(229, 156, 76, 0.35);
  background: rgba(229, 156, 76, 0.14);
  color: #f7f3eb;
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
  .editor-page-panel {
    min-height: calc(100vh - 11rem);
  }

  .editor-toolbar,
  .editor-footer {
    align-items: stretch;
    flex-direction: column;
  }

  .toolbar-group-actions,
  .editor-actions {
    margin-left: 0;
    width: 100%;
    justify-content: flex-start;
  }
}
</style>
