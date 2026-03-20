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

import IconBack from '../components/icons/IconBack.vue';
import IconInactiveTool from '../components/icons/IconInactiveTool.vue';
import IconLineTool from '../components/icons/IconLineTool.vue';
import IconSave from '../components/icons/IconSave.vue';
import IconSingle from '../components/icons/IconSingle.vue';
import IconUndo from '../components/icons/IconUndo.vue';
import { useInjectedRelayState, type SnapshotDevice } from '../composables/useRelayState';
import {
  cloneDocument,
  createDocumentFromLayout,
  createEmptyDocument,
  documentCenter,
  documentsEqual,
  GRID_SIZE,
  placeInactivePrimitive,
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
  strip: string;
}

type Tool = 'single' | 'line' | 'inactive';
type PlacementBubble = {
  text: string;
  x: number;
  y: number;
  visible: boolean;
};

const SAVE_NOTICE_TIMEOUT_MS = 2600;
const PLACEMENT_BUBBLE_TIMEOUT_MS = 3000;
const PLACEMENT_BUBBLE_FADE_MS = 180;

const route = useRoute();
const router = useRouter();
const { snapshot } = useInjectedRelayState();

const canvasRef = ref<HTMLCanvasElement | null>(null);
const editorSurfaceRef = ref<HTMLDivElement | null>(null);
const loadingLayout = ref(false);
const saving = ref(false);
const error = ref('');
const statusNotice = ref('');
const hoverCell = ref<Point | null>(null);
const placementBubble = ref<PlacementBubble | null>(null);
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
let statusNoticeTimer: number | null = null;
let placementBubbleHideTimer: number | null = null;
let placementBubbleClearTimer: number | null = null;
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
      strip: String(matched.strip ?? 'n/a'),
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
const hoverBlocked = computed(() => {
  if (activeTool.value === 'line' || !hoverCell.value) {
    return false;
  }
  return documentRef.value.occupied.has(`${hoverCell.value.x},${hoverCell.value.y}`);
});

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

function clearLineDraft(): void {
  lineStart.value = null;
}

function setDocument(nextDocument: EditorDocument): void {
  documentRef.value = nextDocument;
  clearStatusNotice();
}

function clearStatusNoticeTimer(): void {
  if (statusNoticeTimer !== null) {
    window.clearTimeout(statusNoticeTimer);
    statusNoticeTimer = null;
  }
}

function clearStatusNotice(): void {
  clearStatusNoticeTimer();
  statusNotice.value = '';
}

function showStatusNotice(message: string): void {
  statusNotice.value = message;
  clearStatusNoticeTimer();
  statusNoticeTimer = window.setTimeout(() => {
    statusNotice.value = '';
    statusNoticeTimer = null;
  }, SAVE_NOTICE_TIMEOUT_MS);
}

function clearPlacementBubbleTimers(): void {
  if (placementBubbleHideTimer !== null) {
    window.clearTimeout(placementBubbleHideTimer);
    placementBubbleHideTimer = null;
  }
  if (placementBubbleClearTimer !== null) {
    window.clearTimeout(placementBubbleClearTimer);
    placementBubbleClearTimer = null;
  }
}

function clearPlacementBubble(): void {
  clearPlacementBubbleTimers();
  placementBubble.value = null;
}

function dismissPlacementBubble(): void {
  if (!placementBubble.value) {
    return;
  }
  clearPlacementBubbleTimers();
  if (!placementBubble.value.visible) {
    placementBubble.value = null;
    return;
  }
  placementBubble.value = {
    ...placementBubble.value,
    visible: false,
  };
  placementBubbleClearTimer = window.setTimeout(() => {
    placementBubble.value = null;
    placementBubbleClearTimer = null;
  }, PLACEMENT_BUBBLE_FADE_MS);
}

function showPlacementBubble(event: MouseEvent, message: string): void {
  const surface = editorSurfaceRef.value ?? canvasRef.value;
  if (!surface) {
    return;
  }
  const rect = surface.getBoundingClientRect();
  const maxLeft = Math.max(12, rect.width - 228);
  const maxTop = Math.max(12, rect.height - 52);
  const left = Math.max(12, Math.min(event.clientX - rect.left + 14, maxLeft));
  const top = Math.max(12, Math.min(event.clientY - rect.top - 8, maxTop));

  clearPlacementBubbleTimers();
  placementBubble.value = {
    text: message,
    x: left,
    y: top,
    visible: false,
  };
  window.requestAnimationFrame(() => {
    if (!placementBubble.value || placementBubble.value.text !== message) {
      return;
    }
    placementBubble.value = {
      ...placementBubble.value,
      visible: true,
    };
  });
  placementBubbleHideTimer = window.setTimeout(() => {
    dismissPlacementBubble();
  }, PLACEMENT_BUBBLE_TIMEOUT_MS);
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
    renderEditor(
      canvas,
      documentRef.value,
      viewport.value,
      hoverCell.value,
      linePreview.value,
      hoverBlocked.value,
    );
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
  dismissPlacementBubble();
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
  dismissPlacementBubble();
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
  dismissPlacementBubble();
  hoverCell.value = null;
  requestRender();
}

function handleMouseUp(): void {
  panning = null;
}

function handleWheel(event: WheelEvent): void {
  event.preventDefault();
  dismissPlacementBubble();
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

function beginLineAt(cell: Point, event: MouseEvent): void {
  if (documentRef.value.occupied.has(`${cell.x},${cell.y}`)) {
    showPlacementBubble(event, 'That cell is already occupied.');
    return;
  }
  lineStart.value = cell;
  clearStatusNotice();
}

function commitLineAt(cell: Point, event: MouseEvent): void {
  if (!lineStart.value) {
    beginLineAt(cell, event);
    return;
  }

  try {
    setDocument(placeLinePrimitive(documentRef.value, lineStart.value, cell, currentSpacing.value));
    clearLineDraft();
  } catch (err) {
    showPlacementBubble(
      event,
      err instanceof Error ? err.message : 'Failed to place line.',
    );
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
    commitLineAt(cell, event);
    return;
  }
  try {
    setDocument(
      activeTool.value === 'inactive'
        ? placeInactivePrimitive(documentRef.value, cell.x, cell.y)
        : placeSinglePrimitive(documentRef.value, cell.x, cell.y),
    );
  } catch (err) {
    showPlacementBubble(
      event,
      err instanceof Error
        ? err.message
        : activeTool.value === 'inactive'
          ? 'Failed to place inactive cell.'
          : 'Failed to place LED.',
    );
  }
}

function undo(): void {
  setDocument(undoLastPrimitive(documentRef.value));
  clearLineDraft();
  dismissPlacementBubble();
}

function backToDevices(): void {
  void router.push('/');
}

function selectTool(tool: Tool): void {
  activeTool.value = tool;
  clearLineDraft();
  clearStatusNotice();
  dismissPlacementBubble();
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
    error.value = '';
    showStatusNotice('Saved');
  } catch (err) {
    error.value = err instanceof Error ? err.message : 'Failed to save layout.';
    clearStatusNotice();
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
    clearStatusNotice();
    dismissPlacementBubble();
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
  clearStatusNoticeTimer();
  clearPlacementBubble();
  window.removeEventListener('mouseup', handleMouseUp);
  window.removeEventListener('keydown', handleKeyDown);
  window.removeEventListener('keyup', handleKeyUp);
  window.removeEventListener('resize', requestRender);
  window.removeEventListener('beforeunload', beforeUnloadHandler);
});
</script>

<template>
  <main class="editor-page">
    <section v-if="routeState.kind !== 'ready'" class="panel editor-state-panel">
      <div class="editor-state-actions">
        <button type="button" class="ghost-button" @click="backToDevices">
          <IconBack />
          <span>Back</span>
        </button>
      </div>
      <div class="empty-state">
        <h2>{{ routeState.kind === 'waiting' ? 'Waiting for device metadata' : 'Editor unavailable' }}</h2>
        <p>{{ routeState.message }}</p>
      </div>
    </section>

    <section v-else class="panel editor-page-panel">
      <div class="editor-command-bar">
        <div class="editor-command-left">
          <button type="button" class="ghost-button editor-back-button" :disabled="saving" @click="backToDevices">
            <IconBack />
            <span>Back</span>
          </button>
          <strong class="editor-device-summary">
            {{ routeState.device.deviceUid }} · {{ routeState.device.strip }} · {{ routeState.device.length }} px
          </strong>
        </div>

        <div class="editor-command-center">
          <div class="tool-buttons">
            <button
              type="button"
              class="tool-button"
              :class="{ 'tool-button-active': activeTool === 'single' }"
              :disabled="loadingLayout || saving"
              @click="selectTool('single')"
              title="Single LED tool"
            >
              <IconSingle />
              <span>Single</span>
            </button>
            <button
              type="button"
              class="tool-button"
              :class="{ 'tool-button-active': activeTool === 'line' }"
              :disabled="loadingLayout || saving"
              @click="selectTool('line')"
              title="Line tool"
            >
              <IconLineTool />
              <span>Line</span>
            </button>
            <button
              type="button"
              class="tool-button"
              :class="{ 'tool-button-active': activeTool === 'inactive' }"
              :disabled="loadingLayout || saving"
              @click="selectTool('inactive')"
              title="Inactive cell tool"
            >
              <IconInactiveTool />
              <span>Inactive</span>
            </button>
          </div>
          <label v-if="activeTool === 'line'" class="editor-inline-control">
            <span class="editor-inline-label">Spacing</span>
            <input
              v-model.number="lineSpacing"
              class="spacing-input"
              min="0"
              step="1"
              type="number"
              :disabled="loadingLayout || saving"
            />
          </label>
        </div>

        <div class="editor-command-right">
          <strong class="editor-placed-count">{{ placedCount }} / {{ deviceLength }}</strong>
          <button type="button" class="ghost-button" :disabled="!canUndo" @click="undo">
            <IconUndo />
            <span>Undo</span>
          </button>
          <div
            class="editor-toolbar-notice"
            :class="{
              'editor-toolbar-notice-error': error,
              'editor-toolbar-notice-saved': !error && statusNotice,
            }"
            :title="error || statusNotice || undefined"
            aria-live="polite"
          >
            <span>{{ error || statusNotice }}</span>
          </div>
          <button
            type="button"
            class="primary-button editor-save-button"
            :class="{ 'editor-save-button-dirty': isDirty }"
            :disabled="!canSave"
            @click="save"
          >
            <IconSave />
            <span>{{ saving ? 'Saving…' : 'Save' }}</span>
            <span v-if="isDirty && !saving" class="editor-dirty-dot" aria-hidden="true" />
          </button>
        </div>
      </div>

      <div v-if="loadingLayout" class="editor-loading">Loading layout…</div>
      <div v-else ref="editorSurfaceRef" class="editor-surface">
        <canvas
          ref="canvasRef"
          class="editor-canvas"
          @mousedown="handleMouseDown"
          @mousemove="handleMouseMove"
          @mouseleave="handleMouseLeave"
          @click="handleClick"
          @wheel="handleWheel"
        />
        <div
          v-if="placementBubble"
          class="editor-placement-bubble"
          :class="{ 'editor-placement-bubble-visible': placementBubble.visible }"
          :style="{ left: `${placementBubble.x}px`, top: `${placementBubble.y}px` }"
          role="status"
          aria-live="polite"
        >
          {{ placementBubble.text }}
        </div>
      </div>
    </section>
  </main>
</template>

<style scoped>
.editor-page {
  height: 100%;
  min-height: 0;
  overflow: hidden;
  display: grid;
  padding: 0.9rem 0.9rem 1.25rem;
}

.editor-state-panel,
.editor-page-panel {
  min-height: 0;
  padding: 0.8rem 0.8rem 1rem;
  height: 100%;
}

.editor-state-actions {
  display: flex;
  justify-content: flex-start;
  margin-bottom: 1rem;
}

.editor-page-panel {
  display: grid;
  grid-template-rows: auto 1fr;
  gap: 0.75rem;
  min-height: 0;
  overflow: hidden;
}

.editor-command-bar {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  align-items: center;
  gap: 0.8rem;
  padding: 0.65rem 0.8rem;
  border-radius: var(--radius-panel);
  border: 1px solid var(--panel-edge);
  background: rgba(255, 255, 255, 0.025);
}

.editor-command-left,
.editor-command-right {
  display: flex;
  align-items: center;
  gap: 0.55rem;
  flex-wrap: wrap;
}

.editor-command-left {
  min-width: 0;
}

.editor-command-right {
  justify-content: flex-end;
}

.editor-command-center {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0.75rem;
  flex-wrap: wrap;
  min-width: 0;
}

.editor-device-summary {
  min-width: 0;
  margin: 0;
  color: var(--text);
  font-size: 0.82rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.tool-buttons {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  flex-wrap: wrap;
}

.tool-button,
.spacing-input {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 0.42rem;
  border: 1px solid var(--panel-edge);
  border-radius: var(--radius-tight);
  padding: 0.42rem 0.68rem;
  background: rgba(255, 255, 255, 0.04);
  color: var(--text);
}

.tool-button {
  cursor: pointer;
}

.editor-inline-control {
  display: inline-flex;
  align-items: center;
  gap: 0.45rem;
  min-width: 0;
}

.editor-inline-label {
  color: var(--muted);
  font-size: 0.68rem;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.tool-button-active {
  border-color: rgba(108, 162, 255, 0.38);
  background: rgba(108, 162, 255, 0.16);
}

.spacing-input {
  width: 4.6rem;
}

.tool-button:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.editor-loading,
.editor-surface {
  min-height: 0;
  border-radius: var(--radius-panel);
  border: 1px solid var(--panel-edge);
  background: #07090d;
}

.editor-loading {
  display: grid;
  place-items: center;
  color: var(--muted);
}

.editor-surface {
  overflow: hidden;
  position: relative;
  box-shadow:
    inset 0 -1px 0 rgba(255, 255, 255, 0.035),
    0 10px 22px rgba(0, 0, 0, 0.12);
}

.editor-canvas {
  width: 100%;
  height: 100%;
  display: block;
  cursor: crosshair;
}

.editor-back-button {
  flex: 0 0 auto;
}

.editor-placed-count {
  font-size: 0.8rem;
  font-weight: 700;
  letter-spacing: 0.04em;
  white-space: nowrap;
}

.editor-save-button {
  position: relative;
}

.editor-toolbar-notice {
  width: 13rem;
  min-width: 0;
  color: var(--muted);
  font-size: 0.78rem;
  line-height: 1.2;
  text-align: right;
}

.editor-toolbar-notice span {
  display: block;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  opacity: 0;
  transition: opacity 160ms ease;
}

.editor-toolbar-notice-error span,
.editor-toolbar-notice-saved span {
  opacity: 1;
}

.editor-toolbar-notice-error {
  color: #ffdede;
}

.editor-toolbar-notice-saved {
  color: var(--status-online);
}

.editor-save-button-dirty {
  border-color: rgba(108, 162, 255, 0.52);
}

.editor-dirty-dot {
  width: 0.44rem;
  height: 0.44rem;
  border-radius: 999px;
  background: #ffd166;
  flex: 0 0 auto;
}

.editor-placement-bubble {
  position: absolute;
  z-index: 2;
  max-width: 13rem;
  padding: 0.4rem 0.55rem;
  border: 1px solid rgba(239, 68, 68, 0.42);
  border-radius: var(--radius-tight);
  background: rgba(10, 12, 18, 0.96);
  color: #ffdede;
  font-size: 0.74rem;
  line-height: 1.25;
  box-shadow: 0 10px 28px rgba(0, 0, 0, 0.32);
  opacity: 0;
  transform: translateY(-6px);
  pointer-events: none;
  transition:
    opacity 120ms ease,
    transform 120ms ease;
}

.editor-placement-bubble-visible {
  opacity: 1;
  transform: translateY(-12px);
}

@media (max-width: 900px) {
  .editor-page {
    padding: 0.75rem 0.75rem 1rem;
  }

  .editor-command-bar {
    grid-template-columns: 1fr;
    justify-items: stretch;
  }

  .editor-command-left,
  .editor-command-right {
    flex-wrap: wrap;
  }

  .editor-command-center {
    width: 100%;
    justify-content: flex-start;
  }

  .editor-toolbar-notice {
    width: 100%;
    text-align: left;
    order: 3;
  }
}
</style>
