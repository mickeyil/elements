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
import IconCCW from '../components/icons/IconCCW.vue';
import IconCircleTool from '../components/icons/IconCircleTool.vue';
import IconCW from '../components/icons/IconCW.vue';
import IconInactiveTool from '../components/icons/IconInactiveTool.vue';
import IconLineTool from '../components/icons/IconLineTool.vue';
import IconRecenter from '../components/icons/IconRecenter.vue';
import IconSave from '../components/icons/IconSave.vue';
import IconSelect from '../components/icons/IconSelect.vue';
import IconSingle from '../components/icons/IconSingle.vue';
import IconUndo from '../components/icons/IconUndo.vue';
import { useInjectedServerState, type SnapshotDevice } from '../composables/useServerState';
import {
  type CirclePrimitive,
  circleRadiusFromPoints,
  circleStartFromAngle,
  cloneDocument,
  clearInactiveIndices,
  createDocumentFromLayout,
  createEmptyDocument,
  documentWithoutPrimitive,
  documentCenter,
  documentsEqual,
  expandCircleCells,
  expandLineCells,
  GRID_SIZE,
  inactiveIndices,
  laterPrimitiveImpact,
  markIndicesInactive,
  placeCirclePrimitive,
  placeLinePrimitive,
  placeSinglePrimitive,
  primitiveIndexAtCell,
  previewPrimitiveReplacement,
  previewCirclePlacement,
  reactivateIndex,
  removePrimitive,
  replacePrimitive,
  previewLinePlacement,
  serializeDocument,
  type LinePrimitive,
  type Primitive,
  type SinglePrimitive,
  type EditorDocument,
  type Point,
  translatePrimitive,
  visibleLineEndpoints,
} from '../lib/editorModel';
import {
  centerViewport,
  DEFAULT_ZOOM,
  type EditorHandle,
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

type Tool = 'select' | 'single' | 'line' | 'circle';
type PlacementBubble = {
  text: string;
  x: number;
  y: number;
  visible: boolean;
};
type PendingRenumberConfirm = {
  nextDocument: EditorDocument;
  affectedPrimitiveCount: number;
  affectedLedCount: number;
};
type DragHandleKind =
  | 'single-position'
  | 'line-start'
  | 'line-end'
  | 'circle-center'
  | 'circle-start';
type DragState =
  | {
      kind: 'handle';
      primitiveIndex: number;
      handle: DragHandleKind;
    }
  | {
      kind: 'move';
      primitiveIndex: number;
      originCell: Point;
    };
type ArmedMoveState = {
  primitiveIndex: number;
  originCell: Point;
};

const SAVE_NOTICE_TIMEOUT_MS = 2600;
const PLACEMENT_BUBBLE_TIMEOUT_MS = 3000;
const PLACEMENT_BUBBLE_FADE_MS = 180;

const route = useRoute();
const router = useRouter();
const { snapshot } = useInjectedServerState();

const canvasRef = ref<HTMLCanvasElement | null>(null);
const editorSurfaceRef = ref<HTMLDivElement | null>(null);
const contextMenuRef = ref<HTMLDivElement | null>(null);
const inactivePanelRootRef = ref<HTMLDivElement | null>(null);
const loadingLayout = ref(false);
const saving = ref(false);
const error = ref('');
const statusNotice = ref('');
const inactivePanelError = ref('');
const inactivePanelInput = ref('');
const inactivePanelOpen = ref(false);
const hoverCell = ref<Point | null>(null);
const placementBubble = ref<PlacementBubble | null>(null);
const viewport = ref<EditorViewport>({
  zoom: DEFAULT_ZOOM,
  offsetX: 0,
  offsetY: 0,
});
const documentRef = shallowRef<EditorDocument>(createEmptyDocument(1));
const baselineDocument = shallowRef<EditorDocument>(createEmptyDocument(1));
const undoHistory = shallowRef<EditorDocument[]>([]);
const baseCsvHash = ref<string | null>(null);
const resolvedDeviceMeta = shallowRef<DeviceMeta | null>(null);
const activeTool = ref<Tool>('select');
const toolPopoverOpen = ref<null | 'line' | 'circle'>(null);
const contextMenu = ref<{ x: number; y: number } | null>(null);
const lineSpacing = ref(0);
const lineStart = ref<Point | null>(null);
const circleCenter = ref<Point | null>(null);
const circleDirection = ref<'cw' | 'ccw'>('cw');
const selectedPrimitiveIndex = ref<number | null>(null);
const dragState = shallowRef<DragState | null>(null);
const armedMoveState = shallowRef<ArmedMoveState | null>(null);
const lineEditSpacing = ref(0);
const circleEditDirection = ref<'cw' | 'ccw'>('cw');
const circleEditSpacing = ref(0);
const selectionError = ref('');
const pendingRenumberConfirm = shallowRef<PendingRenumberConfirm | null>(null);

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
    undoHistory.value.length > 0,
);
const canSave = computed(
  () => routeState.value.kind === 'ready' && !loadingLayout.value && !saving.value,
);
const isDirty = computed(() => !documentsEqual(documentRef.value, baselineDocument.value));
const currentSpacing = computed(() => Math.max(0, Math.floor(Number(lineSpacing.value) || 0)));
const inactiveLedNumbers = computed(() => inactiveIndices(documentRef.value));
const selectedPrimitive = computed<Primitive | null>(() => {
  if (selectedPrimitiveIndex.value == null) {
    return null;
  }
  return documentRef.value.primitives[selectedPrimitiveIndex.value] ?? null;
});
const selectedPrimitiveCells = computed(() => {
  const cells = new Set<string>();
  const primitive = selectedPrimitive.value;
  if (!primitive) {
    return cells;
  }
  for (const [key, cell] of documentRef.value.occupied.entries()) {
    if (cell.primitive === primitive) {
      cells.add(key);
    }
  }
  return cells;
});
const selectedSingle = computed<SinglePrimitive | null>(() =>
  selectedPrimitive.value?.type === 'single' ? selectedPrimitive.value : null,
);
const selectedLine = computed<LinePrimitive | null>(() =>
  selectedPrimitive.value?.type === 'line' ? selectedPrimitive.value : null,
);
const selectedCircle = computed<CirclePrimitive | null>(() =>
  selectedPrimitive.value?.type === 'circle' ? selectedPrimitive.value : null,
);
const dragBaseDocument = computed<EditorDocument | null>(() => {
  if (!dragState.value) {
    return null;
  }
  return documentWithoutPrimitive(documentRef.value, dragState.value.primitiveIndex);
});
const dragPreview = computed(() => {
  const state = dragState.value;
  const primitive = selectedPrimitive.value;
  const baseDocument = dragBaseDocument.value;
  const target = hoverCell.value;
  if (!state || !primitive || !baseDocument || !target) {
    return null;
  }
  if (selectedPrimitiveIndex.value !== state.primitiveIndex) {
    return null;
  }

  if (state.kind === 'move') {
    return previewPrimitiveReplacement(
      documentRef.value,
      state.primitiveIndex,
      translatePrimitive(primitive, {
        x: target.x - state.originCell.x,
        y: target.y - state.originCell.y,
      }),
    );
  }

  if (state.kind === 'handle' && state.handle === 'single-position' && primitive.type === 'single') {
    return previewPrimitiveReplacement(documentRef.value, state.primitiveIndex, {
      type: 'single',
      index: primitive.index,
      position: [target.x, target.y],
      ...(primitive.inactive ? { inactive: true } : {}),
    });
  }

  if (
    state.kind === 'handle' &&
    (state.handle === 'line-start' || state.handle === 'line-end') &&
    primitive.type === 'line'
  ) {
    const start =
      state.handle === 'line-start'
        ? { x: target.x, y: target.y }
        : { x: primitive.start[0], y: primitive.start[1] };
    const end =
      state.handle === 'line-end'
        ? { x: target.x, y: target.y }
        : { x: primitive.end[0], y: primitive.end[1] };
    const count = expandLineCells(start, end, primitive.spacing).length;
    const inactiveOffsets = trimInactiveOffsets(primitive.inactiveOffsets, count);
    return previewPrimitiveReplacement(documentRef.value, state.primitiveIndex, {
      type: 'line',
      startIndex: primitive.startIndex,
      count,
      spacing: primitive.spacing,
      start: [start.x, start.y],
      end: [end.x, end.y],
      ...(inactiveOffsets ? { inactiveOffsets } : {}),
    });
  }

  if (
    state.kind === 'handle' &&
    (state.handle === 'circle-center' || state.handle === 'circle-start') &&
    primitive.type === 'circle'
  ) {
    const previousCenter = { x: primitive.center[0], y: primitive.center[1] };
    const previousStart = { x: primitive.start[0], y: primitive.start[1] };
    const center =
      state.handle === 'circle-center'
        ? { x: target.x, y: target.y }
        : previousCenter;
    const start =
      state.handle === 'circle-center'
        ? circleStartFromAngle(
            center,
            circleRadiusFromPoints(previousCenter, previousStart),
            Math.atan2(previousStart.y - previousCenter.y, previousStart.x - previousCenter.x),
          )
        : target;
    const count = expandCircleCells(center, start, primitive.spacing, primitive.direction).length;
    const inactiveOffsets = trimInactiveOffsets(primitive.inactiveOffsets, count);
    return previewPrimitiveReplacement(documentRef.value, state.primitiveIndex, {
      type: 'circle',
      startIndex: primitive.startIndex,
      count,
      spacing: primitive.spacing,
      center: [center.x, center.y],
      start: [start.x, start.y],
      direction: primitive.direction,
      ...(inactiveOffsets ? { inactiveOffsets } : {}),
    });
  }

  return null;
});
const renderDocument = computed(() => dragBaseDocument.value ?? documentRef.value);
const activePreview = computed<EditorPreview | null>(() => dragPreview.value ?? placementPreview.value);
const renderSelectedCells = computed<ReadonlySet<string> | undefined>(() =>
  dragState.value ? undefined : selectedPrimitiveCells.value,
);
const handlePrimitive = computed<Primitive | null>(() => dragPreview.value?.primitive ?? selectedPrimitive.value);
const activeHandle = computed<DragHandleKind | null>(() =>
  dragState.value?.kind === 'handle' ? dragState.value.handle ?? null : null,
);
const dragHandles = computed<EditorHandle[]>(() =>
  buildHandlesForPrimitive(
    handlePrimitive.value,
    activeHandle.value,
  ),
);
const hoveredHandle = computed<DragHandleKind | null>(() =>
  hoverCell.value ? handleAtCell(hoverCell.value) : null,
);
const hoveredSelectedBodyCell = computed(() => {
  if (activeTool.value !== 'select' || !hoverCell.value) {
    return false;
  }
  return selectedPrimitiveCells.value.has(`${hoverCell.value.x},${hoverCell.value.y}`);
});
const dragRenumberImpact = computed(() => {
  const state = dragState.value;
  const preview = dragPreview.value;
  if (!state || !preview || preview.error) {
    return null;
  }
  try {
    const nextDocument = replacePrimitive(documentRef.value, state.primitiveIndex, preview.primitive);
    const impact = laterPrimitiveImpact(documentRef.value, nextDocument, state.primitiveIndex);
    return impact.renumbersLaterPrimitives ? impact : null;
  } catch {
    return null;
  }
});
const canvasCursor = computed(() => {
  if (dragState.value) {
    return 'grabbing';
  }
  if (activeTool.value === 'select' && hoveredHandle.value) {
    return 'grab';
  }
  if (hoveredSelectedBodyCell.value) {
    return 'grab';
  }
  if (activeTool.value !== 'select') {
    return 'crosshair';
  }
  return 'default';
});
const selectedPrimitiveLabel = computed(() => {
  if (!selectedPrimitive.value) {
    return '';
  }
  return selectedPrimitive.value.type === 'single'
    ? 'Single LED'
    : selectedPrimitive.value.type === 'line'
      ? 'Line'
      : 'Circle';
});
const toolbarNotice = computed(() => {
  if (error.value) {
    return error.value;
  }
  if (statusNotice.value) {
    return statusNotice.value;
  }
  return '';
});
const pendingRenumberMessage = computed(() => {
  const pending = pendingRenumberConfirm.value;
  if (!pending) {
    return '';
  }
  const primitiveLabel = pending.affectedPrimitiveCount === 1 ? 'primitive' : 'primitives';
  const ledLabel = pending.affectedLedCount === 1 ? 'LED' : 'LEDs';
  return `This change will renumber LEDs in ${pending.affectedPrimitiveCount} later ${primitiveLabel} (${pending.affectedLedCount} ${ledLabel}).`;
});
const dragRenumberMessage = computed(() => {
  const impact = dragRenumberImpact.value;
  if (!impact) {
    return '';
  }
  const primitiveLabel = impact.affectedPrimitiveCount === 1 ? 'primitive' : 'primitives';
  const ledLabel = impact.affectedLedCount === 1 ? 'LED' : 'LEDs';
  return `Release will renumber ${impact.affectedLedCount} ${ledLabel} in ${impact.affectedPrimitiveCount} later ${primitiveLabel}.`;
});
const toolbarNoticeClass = computed(() => ({
  'editor-toolbar-notice-error': Boolean(error.value),
  'editor-toolbar-notice-saved': !error.value && Boolean(statusNotice.value),
}));
const hoverBlocked = computed(() => {
  if (activeTool.value !== 'single' || !hoverCell.value) {
    return false;
  }
  return documentRef.value.occupied.has(`${hoverCell.value.x},${hoverCell.value.y}`);
});

const placementPreview = computed<EditorPreview | null>(() => {
  if (activeTool.value === 'line' && lineStart.value) {
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
  }

  if (activeTool.value === 'circle' && circleCenter.value) {
    if (!hoverCell.value) {
      return {
        cells: [],
        error: null,
        anchor: circleCenter.value,
      };
    }

    try {
      const preview = previewCirclePlacement(
        documentRef.value,
        circleCenter.value,
        hoverCell.value,
        currentSpacing.value,
        circleDirection.value,
      );
      return {
        cells: preview.cells,
        error: preview.error,
        anchor: circleCenter.value,
      };
    } catch (err) {
      return {
        cells: [],
        error: err instanceof Error ? err.message : 'Failed to preview circle.',
        anchor: circleCenter.value,
      };
    }
  }

  return null;
});

function clearLineDraft(): void {
  lineStart.value = null;
}

function clearCircleDraft(): void {
  circleCenter.value = null;
}

function clearSelection(): void {
  clearDrag();
  selectedPrimitiveIndex.value = null;
  closeContextMenu();
  pendingRenumberConfirm.value = null;
}

function syncSelectedLineDraft(): void {
  if (!selectedLine.value) {
    return;
  }
  lineEditSpacing.value = selectedLine.value.spacing;
}

function syncSelectedCircleDraft(): void {
  if (!selectedCircle.value) {
    return;
  }
  circleEditDirection.value = selectedCircle.value.direction;
  circleEditSpacing.value = selectedCircle.value.spacing;
}

function parsePanelInteger(value: number, message: string): number {
  const normalized = Math.floor(Number(value));
  if (!Number.isFinite(normalized)) {
    throw new Error(message);
  }
  return normalized;
}

function trimInactiveOffsets(inactiveOffsets: number[] | undefined, count: number): number[] | undefined {
  const trimmed = (inactiveOffsets ?? []).filter(
    (offset) => Number.isInteger(offset) && offset >= 0 && offset < count,
  );
  return trimmed.length ? trimmed : undefined;
}

function buildHandlesForPrimitive(
  primitive: Primitive | null,
  activeHandle: DragHandleKind | null,
): EditorHandle[] {
  if (!primitive) {
    return [];
  }
  if (primitive.type === 'single') {
    return [
      {
        x: primitive.position[0],
        y: primitive.position[1],
        active: activeHandle === 'single-position',
      },
    ];
  }
  if (primitive.type === 'line') {
    const visible = visibleLineEndpoints(
      { x: primitive.start[0], y: primitive.start[1] },
      { x: primitive.end[0], y: primitive.end[1] },
      primitive.spacing,
    );
    return [
      {
        x: visible.start.x,
        y: visible.start.y,
        active: activeHandle === 'line-start',
        style: 'endpoint',
      },
      {
        x: visible.end.x,
        y: visible.end.y,
        active: activeHandle === 'line-end',
        style: 'endpoint',
      },
    ];
  }
  return [
    {
      x: primitive.center[0],
      y: primitive.center[1],
      active: activeHandle === 'circle-center',
    },
    {
      x: primitive.start[0],
      y: primitive.start[1],
      active: activeHandle === 'circle-start',
    },
  ];
}

function handleAtCell(cell: Point): DragHandleKind | null {
  if (selectedSingle.value) {
    return selectedSingle.value.position[0] === cell.x && selectedSingle.value.position[1] === cell.y
      ? 'single-position'
      : null;
  }
  if (selectedLine.value) {
    const visible = visibleLineEndpoints(
      { x: selectedLine.value.start[0], y: selectedLine.value.start[1] },
      { x: selectedLine.value.end[0], y: selectedLine.value.end[1] },
      selectedLine.value.spacing,
    );
    if (visible.start.x === cell.x && visible.start.y === cell.y) {
      return 'line-start';
    }
    if (visible.end.x === cell.x && visible.end.y === cell.y) {
      return 'line-end';
    }
    return null;
  }
  if (selectedCircle.value) {
    if (selectedCircle.value.center[0] === cell.x && selectedCircle.value.center[1] === cell.y) {
      return 'circle-center';
    }
    if (selectedCircle.value.start[0] === cell.x && selectedCircle.value.start[1] === cell.y) {
      return 'circle-start';
    }
  }
  return null;
}

function clearDrag(): void {
  dragState.value = null;
  armedMoveState.value = null;
}

function selectPrimitiveAtCell(cell: Point): boolean {
  const primitiveIndex = primitiveIndexAtCell(documentRef.value, cell.x, cell.y);
  if (primitiveIndex == null) {
    return false;
  }
  selectedPrimitiveIndex.value = primitiveIndex;
  selectionError.value = '';
  return true;
}

function closeToolPopover(): void {
  toolPopoverOpen.value = null;
}

function closeContextMenu(): void {
  contextMenu.value = null;
  selectionError.value = '';
}

function clearPendingRenumberConfirm(): void {
  pendingRenumberConfirm.value = null;
}

function closeInactivePanel(): void {
  inactivePanelOpen.value = false;
  inactivePanelError.value = '';
}

function setContextMenuPosition(x: number, y: number): void {
  contextMenu.value = { x, y };
  void nextTick(() => {
    const surface = editorSurfaceRef.value;
    const menu = contextMenuRef.value;
    if (!surface || !menu || !contextMenu.value) {
      return;
    }
    const maxX = Math.max(12, surface.clientWidth - menu.offsetWidth - 12);
    const maxY = Math.max(12, surface.clientHeight - menu.offsetHeight - 12);
    contextMenu.value = {
      x: Math.max(12, Math.min(contextMenu.value.x, maxX)),
      y: Math.max(12, Math.min(contextMenu.value.y, maxY)),
    };
  });
}

function openContextMenu(event: MouseEvent): void {
  const surface = editorSurfaceRef.value;
  if (!surface) {
    return;
  }
  const rect = surface.getBoundingClientRect();
  setContextMenuPosition(event.clientX - rect.left + 10, event.clientY - rect.top + 10);
}

function parseInactiveInput(text: string): number[] {
  const trimmed = text.trim();
  if (!trimmed) {
    throw new Error('Enter one or more LED numbers.');
  }

  const values = new Set<number>();
  for (const rawPart of trimmed.split(',')) {
    const part = rawPart.trim();
    if (!part) {
      continue;
    }

    const rangeMatch = part.match(/^(\d+)\s*-\s*(\d+)$/);
    if (rangeMatch) {
      const start = Number(rangeMatch[1]);
      const end = Number(rangeMatch[2]);
      if (start > end) {
        throw new Error(`Invalid range "${part}".`);
      }
      for (let value = start; value <= end; value += 1) {
        values.add(value);
      }
      continue;
    }

    if (!/^\d+$/.test(part)) {
      throw new Error(`Invalid LED number "${part}".`);
    }
    values.add(Number(part));
  }

  if (!values.size) {
    throw new Error('Enter one or more LED numbers.');
  }

  return [...values].sort((left, right) => left - right);
}

function applyInactivePanel(): void {
  if (pendingRenumberConfirm.value) {
    return;
  }
  try {
    commitDocument(markIndicesInactive(documentRef.value, parseInactiveInput(inactivePanelInput.value)));
    inactivePanelInput.value = '';
    inactivePanelError.value = '';
  } catch (err) {
    inactivePanelError.value = err instanceof Error ? err.message : 'Failed to mark LEDs inactive.';
  }
}

function removeInactiveLed(index: number): void {
  if (pendingRenumberConfirm.value) {
    return;
  }
  try {
    commitDocument(reactivateIndex(documentRef.value, index));
    inactivePanelError.value = '';
  } catch (err) {
    inactivePanelError.value = err instanceof Error ? err.message : 'Failed to reactivate LED.';
  }
}

function resetInactiveLeds(): void {
  if (pendingRenumberConfirm.value) {
    return;
  }
  commitDocument(clearInactiveIndices(documentRef.value));
  inactivePanelError.value = '';
}

function toggleInactivePanel(): void {
  if (pendingRenumberConfirm.value) {
    return;
  }
  inactivePanelOpen.value = !inactivePanelOpen.value;
  if (inactivePanelOpen.value) {
    closeToolPopover();
    closeContextMenu();
    clearLineDraft();
    clearCircleDraft();
    dismissPlacementBubble();
  } else {
    inactivePanelError.value = '';
  }
}

function replaceDocumentWithoutHistory(nextDocument: EditorDocument): void {
  documentRef.value = nextDocument;
  clearStatusNotice();
}

function commitDocument(nextDocument: EditorDocument): void {
  if (documentsEqual(documentRef.value, nextDocument)) {
    return;
  }
  undoHistory.value = [...undoHistory.value, cloneDocument(documentRef.value)];
  replaceDocumentWithoutHistory(nextDocument);
}

function computePrimitiveReplacement(
  primitiveIndex: number,
  replacement: Primitive,
): { nextDocument: EditorDocument; impact: ReturnType<typeof laterPrimitiveImpact> } {
  const nextDocument = replacePrimitive(documentRef.value, primitiveIndex, replacement);
  return {
    nextDocument,
    impact: laterPrimitiveImpact(documentRef.value, nextDocument, primitiveIndex),
  };
}

function commitPrimitiveReplacement(primitiveIndex: number, replacement: Primitive): 'committed' | 'pending' {
  const { nextDocument, impact } = computePrimitiveReplacement(primitiveIndex, replacement);
  if (impact.renumbersLaterPrimitives) {
    pendingRenumberConfirm.value = {
      nextDocument,
      affectedPrimitiveCount: impact.affectedPrimitiveCount,
      affectedLedCount: impact.affectedLedCount,
    };
    selectionError.value = '';
    return 'pending';
  }
  clearPendingRenumberConfirm();
  commitDocument(nextDocument);
  selectionError.value = '';
  return 'committed';
}

function confirmPendingRenumber(): void {
  const pending = pendingRenumberConfirm.value;
  if (!pending) {
    return;
  }
  clearPendingRenumberConfirm();
  commitDocument(pending.nextDocument);
  selectionError.value = '';
  requestRender();
}

function cancelPendingRenumber(): void {
  clearPendingRenumberConfirm();
  requestRender();
}

function applySelectedLineEdit(): void {
  const primitive = selectedLine.value;
  if (!primitive || selectedPrimitiveIndex.value == null) {
    return;
  }

  try {
    const spacing = parsePanelInteger(lineEditSpacing.value, 'Spacing must be an integer.');
    if (spacing < 0) {
      selectionError.value = 'Spacing must be zero or greater.';
      return;
    }

    const start = { x: primitive.start[0], y: primitive.start[1] };
    const end = { x: primitive.end[0], y: primitive.end[1] };
    const count = expandLineCells(start, end, spacing).length;
    const inactiveOffsets = trimInactiveOffsets(primitive.inactiveOffsets, count);
    const outcome = commitPrimitiveReplacement(selectedPrimitiveIndex.value, {
      type: 'line',
      startIndex: primitive.startIndex,
      count,
      spacing,
      start: [start.x, start.y],
      end: [end.x, end.y],
      ...(inactiveOffsets ? { inactiveOffsets } : {}),
    });
    if (outcome === 'committed') {
      syncSelectedLineDraft();
    }
  } catch (err) {
    selectionError.value = err instanceof Error ? err.message : 'Failed to update line.';
  }
}

function applySelectedCircleEdit(): void {
  const primitive = selectedCircle.value;
  if (!primitive || selectedPrimitiveIndex.value == null) {
    return;
  }

  try {
    const spacing = parsePanelInteger(circleEditSpacing.value, 'Spacing must be an integer.');
    if (spacing < 0) {
      selectionError.value = 'Spacing must be zero or greater.';
      return;
    }

    const direction = circleEditDirection.value;
    const center = { x: primitive.center[0], y: primitive.center[1] };
    const start = { x: primitive.start[0], y: primitive.start[1] };
    const count = expandCircleCells(center, start, spacing, direction).length;
    const inactiveOffsets = trimInactiveOffsets(primitive.inactiveOffsets, count);

    const outcome = commitPrimitiveReplacement(selectedPrimitiveIndex.value, {
      type: 'circle',
      startIndex: primitive.startIndex,
      count,
      spacing,
      center: [center.x, center.y],
      start: [start.x, start.y],
      direction,
      ...(inactiveOffsets ? { inactiveOffsets } : {}),
    });
    if (outcome === 'committed') {
      syncSelectedCircleDraft();
    }
  } catch (err) {
    selectionError.value = err instanceof Error ? err.message : 'Failed to update circle.';
  }
}

function deleteSelectedPrimitive(): void {
  if (pendingRenumberConfirm.value) {
    return;
  }
  if (selectedPrimitiveIndex.value == null) {
    return;
  }
  try {
    commitDocument(removePrimitive(documentRef.value, selectedPrimitiveIndex.value));
    clearDrag();
    clearSelection();
    clearLineDraft();
    clearCircleDraft();
    dismissPlacementBubble();
  } catch (err) {
    selectionError.value = err instanceof Error ? err.message : 'Failed to delete primitive.';
  }
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
      renderDocument.value,
      viewport.value,
      hoverCell.value,
      activePreview.value,
      hoverBlocked.value,
      renderSelectedCells.value,
      dragHandles.value,
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
  clearDrag();
  clearLineDraft();
  clearCircleDraft();
  clearSelection();
  clearPendingRenumberConfirm();
  closeToolPopover();
  closeInactivePanel();
  activeTool.value = 'select';
  const token = ++loadToken;

  try {
    const payload = await getLayout(device.deviceUid);
    if (token !== loadToken) {
      return;
    }
    const nextDocument = createDocumentFromLayout(payload, device.length);
    undoHistory.value = [];
    replaceDocumentWithoutHistory(nextDocument);
    baselineDocument.value = cloneDocument(nextDocument);
    baseCsvHash.value = payload?.editor?.csv_hash ?? null;
    await resetViewport();
  } catch (err) {
    if (token !== loadToken) {
      return;
    }
    const nextDocument = createEmptyDocument(device.length);
    undoHistory.value = [];
    replaceDocumentWithoutHistory(nextDocument);
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

function promoteArmedMove(): void {
  const state = armedMoveState.value;
  const target = hoverCell.value;
  if (!state || !target) {
    return;
  }
  if (target.x === state.originCell.x && target.y === state.originCell.y) {
    return;
  }
  dragState.value = {
    kind: 'move',
    primitiveIndex: state.primitiveIndex,
    originCell: state.originCell,
  };
  armedMoveState.value = null;
  selectionError.value = '';
  requestRender();
}

function handleMouseDown(event: MouseEvent): void {
  if (pendingRenumberConfirm.value) {
    return;
  }
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
    clearDrag();
    return;
  }

  if (
    event.button === 0 &&
    !spacePressed &&
    routeState.value.kind === 'ready' &&
    !loadingLayout.value &&
    !saving.value &&
    !lineStart.value &&
    !circleCenter.value
  ) {
    const point = relativePoint(event);
    if (!point) {
      return;
    }
    const cell = screenToCell(viewport.value, point.x, point.y, GRID_SIZE);
    if (!cell) {
      return;
    }

    if (activeTool.value !== 'select') {
      return;
    }

    const primitiveIndex = primitiveIndexAtCell(documentRef.value, cell.x, cell.y);
    if (primitiveIndex == null) {
      return;
    }

    event.preventDefault();
    closeContextMenu();
    clearDrag();
    hoverCell.value = cell;
    if (selectedPrimitiveIndex.value !== primitiveIndex) {
      selectedPrimitiveIndex.value = primitiveIndex;
    }
    selectionError.value = '';
    const handle = handleAtCell(cell);
    if (handle) {
      dragState.value = {
        kind: 'handle',
        primitiveIndex,
        handle,
      };
    } else {
      armedMoveState.value = {
        primitiveIndex,
        originCell: cell,
      };
    }
    suppressClick = true;
    requestRender();
  }
}

function handleMouseMove(event: MouseEvent): void {
  if (pendingRenumberConfirm.value) {
    return;
  }
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
  if (armedMoveState.value) {
    promoteArmedMove();
  }
}

function handleMouseLeave(): void {
  if (pendingRenumberConfirm.value) {
    return;
  }
  if (panning || dragState.value || armedMoveState.value) {
    return;
  }
  dismissPlacementBubble();
  hoverCell.value = null;
  requestRender();
}

function handleMouseUp(event: MouseEvent): void {
  if (pendingRenumberConfirm.value) {
    panning = null;
    return;
  }
  if (dragState.value) {
    const preview = dragPreview.value;
    const primitiveIndex = dragState.value.primitiveIndex;
    clearDrag();
    if (!preview) {
      requestRender();
      panning = null;
      return;
    }
    if (preview.error) {
      selectionError.value = preview.error;
      showPlacementBubble(event, preview.error);
      requestRender();
      panning = null;
      return;
    }
    try {
      commitPrimitiveReplacement(primitiveIndex, preview.primitive);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to update primitive.';
      selectionError.value = message;
      showPlacementBubble(event, message);
    }
    requestRender();
  } else if (armedMoveState.value) {
    armedMoveState.value = null;
    requestRender();
  }
  panning = null;
}

function handleWindowMouseMove(event: MouseEvent): void {
  if (pendingRenumberConfirm.value) {
    return;
  }
  if (!panning && !dragState.value && !armedMoveState.value) {
    return;
  }
  handleMouseMove(event);
}

function handleWheel(event: WheelEvent): void {
  if (pendingRenumberConfirm.value) {
    return;
  }
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
    const nextDocument = placeLinePrimitive(documentRef.value, lineStart.value, cell, currentSpacing.value);
    commitDocument(nextDocument);
    clearLineDraft();
  } catch (err) {
    showPlacementBubble(
      event,
      err instanceof Error ? err.message : 'Failed to place line.',
    );
  }
}

function beginCircleAt(cell: Point): void {
  circleCenter.value = cell;
  clearStatusNotice();
}

function commitCircleAt(cell: Point, event: MouseEvent): void {
  if (!circleCenter.value) {
    beginCircleAt(cell);
    return;
  }

  try {
    const nextDocument = placeCirclePrimitive(
      documentRef.value,
      circleCenter.value,
      cell,
      currentSpacing.value,
      circleDirection.value,
    );
    commitDocument(nextDocument);
    clearCircleDraft();
  } catch (err) {
    showPlacementBubble(
      event,
      err instanceof Error ? err.message : 'Failed to place circle.',
    );
  }
}

function handleClick(event: MouseEvent): void {
  if (pendingRenumberConfirm.value) {
    return;
  }
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
    closeContextMenu();
    clearSelection();
    return;
  }
  closeContextMenu();
  if (activeTool.value === 'line' && lineStart.value) {
    commitLineAt(cell, event);
    return;
  }
  if (activeTool.value === 'circle' && circleCenter.value) {
    commitCircleAt(cell, event);
    return;
  }
  if (activeTool.value === 'select') {
    clearSelection();
    return;
  }
  if (documentRef.value.occupied.has(`${cell.x},${cell.y}`)) {
    selectPrimitiveAtCell(cell);
    return;
  }
  clearSelection();
  if (activeTool.value === 'line') {
    commitLineAt(cell, event);
    return;
  }
  if (activeTool.value === 'circle') {
    commitCircleAt(cell, event);
    return;
  }
  try {
    commitDocument(placeSinglePrimitive(documentRef.value, cell.x, cell.y));
  } catch (err) {
    showPlacementBubble(
      event,
      err instanceof Error ? err.message : 'Failed to place LED.',
    );
  }
}

function handleContextMenu(event: MouseEvent): void {
  if (pendingRenumberConfirm.value) {
    return;
  }
  if (routeState.value.kind !== 'ready' || loadingLayout.value || saving.value) {
    return;
  }

  dismissPlacementBubble();
  clearDrag();
  closeToolPopover();
  closeInactivePanel();

  const point = relativePoint(event);
  if (!point) {
    closeContextMenu();
    return;
  }
  const cell = screenToCell(viewport.value, point.x, point.y, GRID_SIZE);

  if (lineStart.value) {
    clearLineDraft();
    clearStatusNotice();
  }
  if (circleCenter.value) {
    clearCircleDraft();
    clearStatusNotice();
  }

  if (!cell || !documentRef.value.occupied.has(`${cell.x},${cell.y}`)) {
    closeContextMenu();
    clearSelection();
    requestRender();
    return;
  }

  selectPrimitiveAtCell(cell);
  openContextMenu(event);
  requestRender();
}

function undo(): void {
  if (pendingRenumberConfirm.value) {
    return;
  }
  const previous = undoHistory.value[undoHistory.value.length - 1];
  if (!previous) {
    return;
  }
  clearDrag();
  undoHistory.value = undoHistory.value.slice(0, -1);
  replaceDocumentWithoutHistory(cloneDocument(previous));
  clearLineDraft();
  clearCircleDraft();
  clearSelection();
  dismissPlacementBubble();
}

function backToDevices(): void {
  void router.push('/');
}

function selectTool(tool: Tool): void {
  if (pendingRenumberConfirm.value) {
    return;
  }
  closeContextMenu();
  closeInactivePanel();
  if ((tool === 'line' || tool === 'circle') && activeTool.value === tool) {
    toolPopoverOpen.value = toolPopoverOpen.value === tool ? null : tool;
    dismissPlacementBubble();
    return;
  }
  activeTool.value = tool;
  clearDrag();
  clearLineDraft();
  clearCircleDraft();
  clearStatusNotice();
  dismissPlacementBubble();
  if (tool !== 'select') {
    clearSelection();
  }
  toolPopoverOpen.value = tool === 'line' || tool === 'circle' ? tool : null;
}

async function save(): Promise<void> {
  if (pendingRenumberConfirm.value) {
    return;
  }
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
  if (pendingRenumberConfirm.value) {
    if (event.code === 'Escape') {
      event.preventDefault();
      cancelPendingRenumber();
    } else if (event.code === 'Enter') {
      event.preventDefault();
      confirmPendingRenumber();
    }
    return;
  }

  if (event.code === 'Space') {
    event.preventDefault();
    spacePressed = true;
    return;
  }

  if (event.code === 'Escape' && (dragState.value || armedMoveState.value)) {
    event.preventDefault();
    clearDrag();
    closeContextMenu();
    dismissPlacementBubble();
    requestRender();
    return;
  }

  if (event.code === 'Escape' && contextMenu.value) {
    event.preventDefault();
    closeContextMenu();
    return;
  }

  if (event.code === 'Escape' && inactivePanelOpen.value) {
    event.preventDefault();
    closeInactivePanel();
    return;
  }

  if (event.code === 'Escape' && toolPopoverOpen.value) {
    event.preventDefault();
    closeToolPopover();
    return;
  }

  if (event.code === 'Escape' && lineStart.value) {
    event.preventDefault();
    clearLineDraft();
    clearStatusNotice();
    dismissPlacementBubble();
    return;
  }

  if (event.code === 'Escape' && circleCenter.value) {
    event.preventDefault();
    clearCircleDraft();
    clearStatusNotice();
    dismissPlacementBubble();
    return;
  }

  if (event.code === 'Escape' && activeTool.value === 'select' && selectedPrimitive.value) {
    event.preventDefault();
    clearSelection();
    dismissPlacementBubble();
    return;
  }

  if (event.code === 'Escape' && activeTool.value !== 'select') {
    event.preventDefault();
    selectTool('select');
    requestRender();
    return;
  }

  const target = event.target;
  const inEditableField =
    target instanceof HTMLInputElement ||
    target instanceof HTMLTextAreaElement ||
    target instanceof HTMLSelectElement ||
    (target instanceof HTMLElement && target.isContentEditable);
  if (!inEditableField && (event.code === 'Delete' || event.code === 'Backspace') && selectedPrimitive.value) {
    event.preventDefault();
    deleteSelectedPrimitive();
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

function handleDocumentClick(event: MouseEvent): void {
  if (pendingRenumberConfirm.value) {
    return;
  }
  const target = event.target;
  if (!(target instanceof Element)) {
    return;
  }
  if (!target.closest('[data-inactive-panel-root]')) {
    closeInactivePanel();
  }
  if (!target.closest('[data-tool-popover-root]')) {
    closeToolPopover();
  }
  if (!target.closest('[data-context-menu-root]')) {
    closeContextMenu();
  }
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

watch(
  selectedPrimitive,
  (primitive) => {
    if (!primitive) {
      if (selectedPrimitiveIndex.value != null) {
        clearSelection();
      }
      return;
    }
    selectionError.value = '';
    if (primitive.type === 'line') {
      syncSelectedLineDraft();
      return;
    }
    if (primitive.type === 'circle') {
      syncSelectedCircleDraft();
    }
  },
  { immediate: true },
);

watch(
  [
    documentRef,
    viewport,
    hoverCell,
    activeTool,
    lineStart,
    circleCenter,
    lineSpacing,
    circleDirection,
    toolPopoverOpen,
    contextMenu,
    dragState,
    armedMoveState,
    selectedPrimitiveIndex,
  ],
  () => {
    requestRender();
  },
);

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
  window.addEventListener('mousemove', handleWindowMouseMove);
  window.addEventListener('mouseup', handleMouseUp);
  window.addEventListener('keydown', handleKeyDown);
  window.addEventListener('keyup', handleKeyUp);
  window.addEventListener('resize', requestRender);
  document.addEventListener('click', handleDocumentClick);
});

onBeforeUnmount(() => {
  clearStatusNoticeTimer();
  clearPlacementBubble();
  window.removeEventListener('mousemove', handleWindowMouseMove);
  window.removeEventListener('mouseup', handleMouseUp);
  window.removeEventListener('keydown', handleKeyDown);
  window.removeEventListener('keyup', handleKeyUp);
  window.removeEventListener('resize', requestRender);
  document.removeEventListener('click', handleDocumentClick);
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
          <button
            type="button"
            class="ghost-button editor-back-button editor-icon-button"
            :disabled="saving"
            title="Back"
            aria-label="Back"
            @click="backToDevices"
          >
            <IconBack />
          </button>
          <span
            class="editor-device-summary"
            :title="`${routeState.device.deviceUid} · ${routeState.device.strip} · ${routeState.device.length} px`"
          >
            {{ routeState.device.deviceUid }} · {{ routeState.device.strip }}
          </span>
        </div>

        <div class="editor-command-center">
          <div class="tool-buttons">
            <button
              type="button"
              class="tool-button editor-tool-button"
              :class="{ 'tool-button-active': activeTool === 'select' }"
              :disabled="loadingLayout || saving"
              title="Select tool"
              aria-label="Select tool"
              @click="selectTool('select')"
            >
              <IconSelect />
            </button>
            <button
              type="button"
              class="tool-button editor-tool-button"
              :class="{ 'tool-button-active': activeTool === 'single' }"
              :disabled="loadingLayout || saving"
              title="Single LED tool"
              aria-label="Single LED tool"
              @click="selectTool('single')"
            >
              <IconSingle />
            </button>
            <div class="editor-tool-popover-root" data-tool-popover-root>
              <div class="tool-buttons">
                <button
                  type="button"
                  class="tool-button editor-tool-button"
                  :class="{ 'tool-button-active': activeTool === 'line' }"
                  :disabled="loadingLayout || saving"
                  title="Line tool"
                  aria-label="Line tool"
                  @click.stop="selectTool('line')"
                >
                  <IconLineTool />
                </button>
                <button
                  type="button"
                  class="tool-button editor-tool-button"
                  :class="{ 'tool-button-active': activeTool === 'circle' }"
                  :disabled="loadingLayout || saving"
                  title="Circle tool"
                  aria-label="Circle tool"
                  @click.stop="selectTool('circle')"
                >
                  <IconCircleTool />
                </button>
              </div>
              <div
                v-if="toolPopoverOpen"
                class="panel editor-tool-popover"
                role="dialog"
                aria-modal="false"
                :aria-label="toolPopoverOpen === 'line' ? 'Line defaults' : 'Circle defaults'"
                @click.stop
              >
                <p class="editor-tool-popover-label">
                  {{ toolPopoverOpen === 'line' ? 'Line defaults' : 'Circle defaults' }}
                </p>
                <label class="editor-context-row">
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
                <div v-if="toolPopoverOpen === 'circle'" class="editor-context-row">
                  <span class="editor-inline-label">Direction</span>
                  <div class="tool-buttons">
                    <button
                      type="button"
                      class="tool-button editor-direction-button"
                      :class="{ 'tool-button-active': circleDirection === 'cw' }"
                      :disabled="loadingLayout || saving"
                      title="Clockwise"
                      aria-label="Clockwise"
                      @click="circleDirection = 'cw'"
                    >
                      <IconCW />
                    </button>
                    <button
                      type="button"
                      class="tool-button editor-direction-button"
                      :class="{ 'tool-button-active': circleDirection === 'ccw' }"
                      :disabled="loadingLayout || saving"
                      title="Counter-clockwise"
                      aria-label="Counter-clockwise"
                      @click="circleDirection = 'ccw'"
                    >
                      <IconCCW />
                    </button>
                  </div>
                </div>
              </div>
            </div>
            <div
              ref="inactivePanelRootRef"
              class="editor-inactive-panel-root"
              data-inactive-panel-root
            >
              <button
                type="button"
                class="tool-button editor-tool-button"
                :class="{ 'tool-button-active': inactivePanelOpen }"
                :disabled="loadingLayout || saving"
                title="Manage inactive LEDs"
                aria-label="Manage inactive LEDs"
                @click.stop="toggleInactivePanel"
              >
                <IconInactiveTool />
              </button>
              <div
                v-if="inactivePanelOpen"
                class="panel editor-inactive-panel"
                role="dialog"
                aria-modal="false"
                aria-label="Inactive LEDs"
                @click.stop
              >
                <p class="editor-inactive-panel-label">Inactive LEDs</p>
                <p class="editor-inactive-panel-copy">Enter 1-based LED numbers or ranges.</p>
                <div class="editor-inactive-panel-input-row">
                  <input
                    v-model="inactivePanelInput"
                    class="editor-inactive-panel-input mono"
                    type="text"
                    placeholder="15,16,40-42"
                    :disabled="saving"
                    @keydown.enter.prevent="applyInactivePanel"
                  />
                  <button
                    type="button"
                    class="ghost-button editor-inactive-apply"
                    :disabled="saving"
                    @click="applyInactivePanel"
                  >
                    Mark inactive
                  </button>
                </div>
                <p v-if="inactivePanelError" class="editor-inactive-panel-error">
                  {{ inactivePanelError }}
                </p>
                <div class="editor-inactive-list">
                  <div class="editor-inactive-list-head">
                    <span class="editor-inline-label">Current inactive</span>
                    <button
                      type="button"
                      class="ghost-button editor-inactive-clear"
                      :disabled="!inactiveLedNumbers.length || saving"
                      @click="resetInactiveLeds"
                    >
                      Clear inactive
                    </button>
                  </div>
                  <div v-if="inactiveLedNumbers.length" class="editor-inactive-chip-list">
                    <button
                      v-for="index in inactiveLedNumbers"
                      :key="index"
                      type="button"
                      class="editor-inactive-chip"
                      :disabled="saving"
                      :title="`Reactivate LED ${index}`"
                      @click="removeInactiveLed(index)"
                    >
                      {{ index }}
                    </button>
                  </div>
                  <p v-else class="editor-inactive-empty">No inactive LEDs.</p>
                </div>
              </div>
            </div>
          </div>
        </div>

        <div class="editor-command-right">
          <strong class="editor-placed-count">{{ placedCount }} / {{ deviceLength }}</strong>
          <button
            type="button"
            class="ghost-button editor-icon-button"
            :disabled="loadingLayout || saving"
            title="Recenter"
            aria-label="Recenter"
            @click="resetViewport"
          >
            <IconRecenter />
          </button>
          <button
            type="button"
            class="ghost-button editor-icon-button"
            :disabled="!canUndo"
            title="Undo"
            aria-label="Undo"
            @click="undo"
          >
            <IconUndo />
          </button>
          <div
            class="editor-toolbar-notice"
            :class="toolbarNoticeClass"
            :title="toolbarNotice || undefined"
            aria-live="polite"
          >
            <span>{{ toolbarNotice }}</span>
          </div>
          <button
            type="button"
            class="primary-button editor-save-button editor-icon-button"
            :class="{ 'editor-save-button-dirty': isDirty }"
            :disabled="!canSave"
            :title="saving ? 'Saving layout' : 'Save layout'"
            aria-label="Save layout"
            @click="save"
          >
            <IconSave />
            <span v-if="isDirty && !saving" class="editor-dirty-dot" aria-hidden="true" />
          </button>
        </div>
      </div>

      <div v-if="loadingLayout" class="editor-loading">Loading layout…</div>
      <div v-else ref="editorSurfaceRef" class="editor-surface">
        <canvas
          ref="canvasRef"
          class="editor-canvas"
          :style="{ cursor: canvasCursor }"
          @mousedown="handleMouseDown"
          @mousemove="handleMouseMove"
          @mouseleave="handleMouseLeave"
          @click="handleClick"
          @contextmenu.prevent="handleContextMenu"
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
        <div
          v-if="dragRenumberImpact"
          class="editor-drag-warning"
          role="status"
          aria-live="polite"
        >
          {{ dragRenumberMessage }}
        </div>
        <section
          v-if="contextMenu && selectedPrimitive"
          ref="contextMenuRef"
          class="panel editor-context-menu"
          :style="{ left: `${contextMenu.x}px`, top: `${contextMenu.y}px` }"
          data-context-menu-root
          role="dialog"
          aria-modal="false"
          aria-live="polite"
          @click.stop
        >
          <div class="editor-context-menu-head">
            <div>
              <p class="editor-context-menu-label">{{ selectedPrimitiveLabel }}</p>
              <p v-if="selectedLine || selectedCircle" class="editor-context-menu-copy">
                {{ selectedLine?.count ?? selectedCircle?.count }} LEDs
              </p>
            </div>
            <button
              type="button"
              class="ghost-button editor-context-menu-delete"
              :disabled="saving"
              @click="deleteSelectedPrimitive"
            >
              Delete
            </button>
          </div>

          <div v-if="selectedLine" class="editor-context-menu-fields">
            <label class="editor-context-row">
              <span class="editor-inline-label">Spacing</span>
              <input
                v-model.number="lineEditSpacing"
                class="spacing-input"
                type="number"
                min="0"
                step="1"
                :disabled="saving"
              />
            </label>
            <button
              type="button"
              class="ghost-button editor-context-menu-apply"
              :disabled="saving"
              @click="applySelectedLineEdit"
            >
              Apply
            </button>
          </div>

          <div v-else-if="selectedCircle" class="editor-context-menu-fields">
            <label class="editor-context-row">
              <span class="editor-inline-label">Spacing</span>
              <input
                v-model.number="circleEditSpacing"
                class="spacing-input"
                type="number"
                min="0"
                step="1"
                :disabled="saving"
              />
            </label>
            <div class="editor-context-row">
              <span class="editor-inline-label">Direction</span>
              <div class="tool-buttons">
                <button
                  type="button"
                  class="tool-button editor-direction-button"
                  :class="{ 'tool-button-active': circleEditDirection === 'cw' }"
                  :disabled="saving"
                  title="Clockwise"
                  aria-label="Clockwise"
                  @click="circleEditDirection = 'cw'"
                >
                  <IconCW />
                </button>
                <button
                  type="button"
                  class="tool-button editor-direction-button"
                  :class="{ 'tool-button-active': circleEditDirection === 'ccw' }"
                  :disabled="saving"
                  title="Counter-clockwise"
                  aria-label="Counter-clockwise"
                  @click="circleEditDirection = 'ccw'"
                >
                  <IconCCW />
                </button>
              </div>
            </div>
            <button
              type="button"
              class="ghost-button editor-context-menu-apply"
              :disabled="saving"
              @click="applySelectedCircleEdit"
            >
              Apply
            </button>
          </div>

          <p v-if="selectionError" class="editor-context-menu-error">{{ selectionError }}</p>
        </section>
      </div>
      <div
        v-if="pendingRenumberConfirm"
        class="editor-confirm-overlay"
        role="dialog"
        aria-modal="true"
        aria-labelledby="renumber-confirm-title"
        @click.self="cancelPendingRenumber"
      >
        <section class="panel editor-confirm-dialog" @click.stop>
          <p id="renumber-confirm-title" class="editor-confirm-title">Renumber later LEDs?</p>
          <p class="editor-confirm-copy">{{ pendingRenumberMessage }}</p>
          <div class="editor-confirm-actions">
            <button
              type="button"
              class="ghost-button"
              :disabled="saving"
              @click="cancelPendingRenumber"
            >
              Cancel
            </button>
            <button
              type="button"
              class="primary-button"
              :disabled="saving"
              @click="confirmPendingRenumber"
            >
              Apply
            </button>
          </div>
        </section>
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
  color: var(--muted);
  font-size: 0.72rem;
  font-weight: 400;
  letter-spacing: 0.02em;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.tool-buttons {
  display: flex;
  align-items: center;
  gap: 0.4rem;
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

.editor-tool-button,
.editor-icon-button {
  gap: 0;
  padding: 0.48rem 0.52rem;
  min-width: 2.25rem;
  min-height: 2.25rem;
}

.editor-inactive-panel-root {
  position: relative;
}

.editor-tool-popover-root {
  position: relative;
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

.editor-tool-popover,
.editor-context-menu {
  position: absolute;
  z-index: 3;
  width: min(18rem, calc(100vw - 3rem));
  padding: 0.75rem;
  display: grid;
  gap: 0.7rem;
  background: rgba(9, 11, 15, 0.96);
  box-shadow: 0 12px 28px rgba(0, 0, 0, 0.28);
}

.editor-tool-popover {
  top: calc(100% + 0.55rem);
  left: 0;
}

.editor-tool-popover-label,
.editor-context-menu-label {
  margin: 0;
  color: var(--text);
  font-size: 0.78rem;
  font-weight: 700;
  letter-spacing: 0.05em;
  text-transform: uppercase;
}

.editor-context-menu-copy {
  margin: 0.18rem 0 0;
  color: var(--muted);
  font-size: 0.74rem;
}

.editor-context-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.65rem;
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

.editor-drag-warning {
  position: absolute;
  top: 0.85rem;
  right: 0.85rem;
  z-index: 2;
  max-width: min(22rem, calc(100% - 1.7rem));
  padding: 0.48rem 0.7rem;
  border: 1px solid rgba(255, 209, 102, 0.45);
  border-radius: var(--radius-tight);
  background: rgba(18, 14, 8, 0.95);
  color: #ffe7b0;
  font-size: 0.74rem;
  line-height: 1.3;
  box-shadow: 0 10px 24px rgba(0, 0, 0, 0.24);
  pointer-events: none;
}

.editor-context-menu {
  max-width: min(18rem, calc(100% - 1.6rem));
}

.editor-context-menu-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 0.75rem;
}

.editor-context-menu-fields {
  display: grid;
  gap: 0.55rem;
}

.editor-context-menu-error {
  margin: 0;
  color: #ffdede;
  font-size: 0.74rem;
}

.editor-confirm-overlay {
  position: fixed;
  inset: 0;
  z-index: 20;
  display: grid;
  place-items: center;
  padding: 1.25rem;
  background: rgba(4, 6, 10, 0.42);
  backdrop-filter: blur(4px);
}

.editor-confirm-dialog {
  width: min(24rem, calc(100vw - 2rem));
  display: grid;
  gap: 0.8rem;
  padding: 0.9rem;
  background: rgba(9, 11, 15, 0.98);
  box-shadow: 0 18px 40px rgba(0, 0, 0, 0.34);
}

.editor-confirm-title {
  margin: 0;
  color: var(--text);
  font-size: 0.84rem;
  font-weight: 700;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.editor-confirm-copy {
  margin: 0;
  color: var(--muted);
  font-size: 0.78rem;
  line-height: 1.35;
}

.editor-confirm-actions {
  display: flex;
  justify-content: flex-end;
  gap: 0.6rem;
}

.editor-inactive-panel {
  position: absolute;
  top: calc(100% + 0.55rem);
  right: 0;
  z-index: 3;
  width: min(24rem, 72vw);
  padding: 0.75rem;
  display: grid;
  gap: 0.7rem;
}

.editor-inactive-panel-label {
  margin: 0;
  color: var(--text);
  font-size: 0.78rem;
  font-weight: 700;
  letter-spacing: 0.05em;
  text-transform: uppercase;
}

.editor-inactive-panel-copy {
  margin: 0;
  color: var(--muted);
  font-size: 0.76rem;
}

.editor-inactive-panel-input-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 0.55rem;
}

.editor-inactive-panel-input {
  width: 100%;
  min-width: 0;
  border: 1px solid var(--panel-edge);
  border-radius: var(--radius-tight);
  padding: 0.52rem 0.65rem;
  background: rgba(255, 255, 255, 0.04);
  color: var(--text);
}

.editor-inactive-panel-input::placeholder {
  color: rgba(255, 255, 255, 0.28);
}

.editor-inactive-panel-error {
  margin: 0;
  color: #ffdede;
  font-size: 0.74rem;
}

.editor-inactive-list {
  display: grid;
  gap: 0.55rem;
}

.editor-inactive-list-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
}

.editor-inactive-chip-list {
  display: flex;
  flex-wrap: wrap;
  gap: 0.4rem;
}

.editor-inactive-chip {
  border: 1px solid rgba(255, 255, 255, 0.12);
  border-radius: 999px;
  padding: 0.3rem 0.58rem;
  background: rgba(255, 255, 255, 0.04);
  color: var(--text);
  cursor: pointer;
  font-size: 0.74rem;
  font-weight: 700;
}

.editor-inactive-chip:hover:not(:disabled),
.editor-inactive-chip:focus-visible {
  border-color: rgba(108, 162, 255, 0.38);
  color: var(--accent);
  outline: none;
}

.editor-inactive-empty {
  margin: 0;
  color: var(--muted);
  font-size: 0.74rem;
}

.editor-inactive-apply,
.editor-inactive-clear {
  white-space: nowrap;
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

  .editor-inactive-panel {
    right: auto;
    left: 0;
    width: min(24rem, calc(100vw - 3rem));
  }

  .editor-inactive-panel-input-row {
    grid-template-columns: 1fr;
  }

  .editor-tool-popover,
  .editor-context-menu {
    left: 0.8rem;
    width: auto;
  }
}
</style>
