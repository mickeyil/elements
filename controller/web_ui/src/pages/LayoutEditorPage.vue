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
import IconCircleTool from '../components/icons/IconCircleTool.vue';
import IconInactiveTool from '../components/icons/IconInactiveTool.vue';
import IconLineTool from '../components/icons/IconLineTool.vue';
import IconSave from '../components/icons/IconSave.vue';
import IconSingle from '../components/icons/IconSingle.vue';
import IconUndo from '../components/icons/IconUndo.vue';
import { useInjectedRelayState, type SnapshotDevice } from '../composables/useRelayState';
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
  undoLastPrimitive,
  type LinePrimitive,
  type Primitive,
  type SinglePrimitive,
  type EditorDocument,
  type Point,
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

type Tool = 'single' | 'line' | 'circle';
type PlacementBubble = {
  text: string;
  x: number;
  y: number;
  visible: boolean;
};
type DragHandleKind =
  | 'single-position'
  | 'line-start'
  | 'line-end'
  | 'circle-center'
  | 'circle-start';
type DragState = {
  primitiveIndex: number;
  handle: DragHandleKind;
};

const SAVE_NOTICE_TIMEOUT_MS = 2600;
const PLACEMENT_BUBBLE_TIMEOUT_MS = 3000;
const PLACEMENT_BUBBLE_FADE_MS = 180;

const route = useRoute();
const router = useRouter();
const { snapshot } = useInjectedRelayState();

const canvasRef = ref<HTMLCanvasElement | null>(null);
const editorSurfaceRef = ref<HTMLDivElement | null>(null);
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
const baseCsvHash = ref<string | null>(null);
const resolvedDeviceMeta = shallowRef<DeviceMeta | null>(null);
const activeTool = ref<Tool>('single');
const lineSpacing = ref(0);
const lineStart = ref<Point | null>(null);
const circleCenter = ref<Point | null>(null);
const circleDirection = ref<'cw' | 'ccw'>('cw');
const selectedPrimitiveIndex = ref<number | null>(null);
const dragState = shallowRef<DragState | null>(null);
const singleEditX = ref(0);
const singleEditY = ref(0);
const lineEditStartX = ref(0);
const lineEditStartY = ref(0);
const lineEditEndX = ref(0);
const lineEditEndY = ref(0);
const lineEditSpacing = ref(0);
const circleEditCenterX = ref(0);
const circleEditCenterY = ref(0);
const circleEditRadius = ref(1);
const circleEditDirection = ref<'cw' | 'ccw'>('cw');
const circleEditSpacing = ref(0);
const selectionError = ref('');

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

  if (state.handle === 'single-position' && primitive.type === 'single') {
    return previewPrimitiveReplacement(documentRef.value, state.primitiveIndex, {
      type: 'single',
      index: primitive.index,
      position: [target.x, target.y],
      ...(primitive.inactive ? { inactive: true } : {}),
    });
  }

  if (
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
const dragHandles = computed<EditorHandle[]>(() =>
  buildHandlesForPrimitive(
    handlePrimitive.value,
    dragState.value?.handle ?? null,
  ),
);
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
  selectionError.value = '';
}

function syncSelectedSingleDraft(): void {
  if (!selectedSingle.value) {
    return;
  }
  singleEditX.value = selectedSingle.value.position[0];
  singleEditY.value = selectedSingle.value.position[1];
}

function syncSelectedLineDraft(): void {
  if (!selectedLine.value) {
    return;
  }
  lineEditStartX.value = selectedLine.value.start[0];
  lineEditStartY.value = selectedLine.value.start[1];
  lineEditEndX.value = selectedLine.value.end[0];
  lineEditEndY.value = selectedLine.value.end[1];
  lineEditSpacing.value = selectedLine.value.spacing;
}

function syncSelectedCircleDraft(): void {
  if (!selectedCircle.value) {
    return;
  }
  const center = {
    x: selectedCircle.value.center[0],
    y: selectedCircle.value.center[1],
  };
  const start = {
    x: selectedCircle.value.start[0],
    y: selectedCircle.value.start[1],
  };
  circleEditCenterX.value = center.x;
  circleEditCenterY.value = center.y;
  circleEditRadius.value = circleRadiusFromPoints(center, start);
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
    return [
      {
        x: primitive.start[0],
        y: primitive.start[1],
        active: activeHandle === 'line-start',
      },
      {
        x: primitive.end[0],
        y: primitive.end[1],
        active: activeHandle === 'line-end',
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
    if (selectedLine.value.start[0] === cell.x && selectedLine.value.start[1] === cell.y) {
      return 'line-start';
    }
    if (selectedLine.value.end[0] === cell.x && selectedLine.value.end[1] === cell.y) {
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

function closeInactivePanel(): void {
  inactivePanelOpen.value = false;
  inactivePanelError.value = '';
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
  try {
    setDocument(markIndicesInactive(documentRef.value, parseInactiveInput(inactivePanelInput.value)));
    inactivePanelInput.value = '';
    inactivePanelError.value = '';
  } catch (err) {
    inactivePanelError.value = err instanceof Error ? err.message : 'Failed to mark LEDs inactive.';
  }
}

function removeInactiveLed(index: number): void {
  try {
    setDocument(reactivateIndex(documentRef.value, index));
    inactivePanelError.value = '';
  } catch (err) {
    inactivePanelError.value = err instanceof Error ? err.message : 'Failed to reactivate LED.';
  }
}

function resetInactiveLeds(): void {
  setDocument(clearInactiveIndices(documentRef.value));
  inactivePanelError.value = '';
}

function toggleInactivePanel(): void {
  inactivePanelOpen.value = !inactivePanelOpen.value;
  if (inactivePanelOpen.value) {
    clearLineDraft();
    clearCircleDraft();
    dismissPlacementBubble();
  } else {
    inactivePanelError.value = '';
  }
}

function setDocument(nextDocument: EditorDocument): void {
  documentRef.value = nextDocument;
  clearStatusNotice();
}

function applySelectedSingleEdit(): void {
  const primitive = selectedSingle.value;
  if (!primitive || selectedPrimitiveIndex.value == null) {
    return;
  }

  try {
    const x = parsePanelInteger(singleEditX.value, 'Coordinates must be integers.');
    const y = parsePanelInteger(singleEditY.value, 'Coordinates must be integers.');
    setDocument(
      replacePrimitive(documentRef.value, selectedPrimitiveIndex.value, {
        type: 'single',
        index: primitive.index,
        position: [x, y],
        ...(primitive.inactive ? { inactive: true } : {}),
      }),
    );
    selectionError.value = '';
    syncSelectedSingleDraft();
  } catch (err) {
    selectionError.value = err instanceof Error ? err.message : 'Failed to update LED.';
  }
}

function applySelectedLineEdit(): void {
  const primitive = selectedLine.value;
  if (!primitive || selectedPrimitiveIndex.value == null) {
    return;
  }

  try {
    const start = {
      x: parsePanelInteger(lineEditStartX.value, 'Line coordinates must be integers.'),
      y: parsePanelInteger(lineEditStartY.value, 'Line coordinates must be integers.'),
    };
    const end = {
      x: parsePanelInteger(lineEditEndX.value, 'Line coordinates must be integers.'),
      y: parsePanelInteger(lineEditEndY.value, 'Line coordinates must be integers.'),
    };
    const spacing = parsePanelInteger(lineEditSpacing.value, 'Spacing must be an integer.');
    if (spacing < 0) {
      selectionError.value = 'Spacing must be zero or greater.';
      return;
    }

    const count = expandLineCells(start, end, spacing).length;
    const inactiveOffsets = trimInactiveOffsets(primitive.inactiveOffsets, count);
    setDocument(
      replacePrimitive(documentRef.value, selectedPrimitiveIndex.value, {
        type: 'line',
        startIndex: primitive.startIndex,
        count,
        spacing,
        start: [start.x, start.y],
        end: [end.x, end.y],
        ...(inactiveOffsets ? { inactiveOffsets } : {}),
      }),
    );
    selectionError.value = '';
    syncSelectedLineDraft();
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
    const center = {
      x: parsePanelInteger(circleEditCenterX.value, 'Circle center must use integer coordinates.'),
      y: parsePanelInteger(circleEditCenterY.value, 'Circle center must use integer coordinates.'),
    };
    const radius = parsePanelInteger(circleEditRadius.value, 'Radius must be an integer.');
    const spacing = parsePanelInteger(circleEditSpacing.value, 'Spacing must be an integer.');
    if (radius < 1) {
      selectionError.value = 'Radius must be at least 1.';
      return;
    }
    if (spacing < 0) {
      selectionError.value = 'Spacing must be zero or greater.';
      return;
    }

    const direction = circleEditDirection.value;
    const previousCenter = { x: primitive.center[0], y: primitive.center[1] };
    const previousStart = { x: primitive.start[0], y: primitive.start[1] };
    const angle = Math.atan2(previousStart.y - previousCenter.y, previousStart.x - previousCenter.x);
    const start = circleStartFromAngle(center, radius, angle);
    const count = expandCircleCells(center, start, spacing, direction).length;
    const inactiveOffsets = trimInactiveOffsets(primitive.inactiveOffsets, count);

    setDocument(
      replacePrimitive(documentRef.value, selectedPrimitiveIndex.value, {
        type: 'circle',
        startIndex: primitive.startIndex,
        count,
        spacing,
        center: [center.x, center.y],
        start: [start.x, start.y],
        direction,
        ...(inactiveOffsets ? { inactiveOffsets } : {}),
      }),
    );
    selectionError.value = '';
    syncSelectedCircleDraft();
  } catch (err) {
    selectionError.value = err instanceof Error ? err.message : 'Failed to update circle.';
  }
}

function deleteSelectedPrimitive(): void {
  if (selectedPrimitiveIndex.value == null) {
    return;
  }
  try {
    setDocument(removePrimitive(documentRef.value, selectedPrimitiveIndex.value));
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
  closeInactivePanel();
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
    !circleCenter.value &&
    selectedPrimitiveIndex.value != null
  ) {
    const point = relativePoint(event);
    if (!point) {
      return;
    }
    const cell = screenToCell(viewport.value, point.x, point.y, GRID_SIZE);
    if (!cell) {
      return;
    }
    const handle = handleAtCell(cell);
    if (handle) {
      event.preventDefault();
      hoverCell.value = cell;
      dragState.value = {
        primitiveIndex: selectedPrimitiveIndex.value,
        handle,
      };
      suppressClick = true;
      selectionError.value = '';
      requestRender();
    }
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
  if (panning || dragState.value) {
    return;
  }
  dismissPlacementBubble();
  hoverCell.value = null;
  requestRender();
}

function handleMouseUp(event: MouseEvent): void {
  if (dragState.value) {
    const preview = dragPreview.value;
    const primitiveIndex = dragState.value.primitiveIndex;
    clearDrag();
    if (!preview) {
      requestRender();
      return;
    }
    if (preview.error) {
      selectionError.value = preview.error;
      showPlacementBubble(event, preview.error);
      requestRender();
      return;
    }
    try {
      setDocument(replacePrimitive(documentRef.value, primitiveIndex, preview.primitive));
      selectionError.value = '';
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to update primitive.';
      selectionError.value = message;
      showPlacementBubble(event, message);
    }
    requestRender();
  }
  panning = null;
}

function handleWindowMouseMove(event: MouseEvent): void {
  if (!panning && !dragState.value) {
    return;
  }
  handleMouseMove(event);
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
    const nextDocument = placeLinePrimitive(documentRef.value, lineStart.value, cell, currentSpacing.value);
    setDocument(nextDocument);
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
    setDocument(nextDocument);
    clearCircleDraft();
  } catch (err) {
    showPlacementBubble(
      event,
      err instanceof Error ? err.message : 'Failed to place circle.',
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
    clearSelection();
    return;
  }
  if (activeTool.value === 'line' && lineStart.value) {
    commitLineAt(cell, event);
    return;
  }
  if (activeTool.value === 'circle' && circleCenter.value) {
    commitCircleAt(cell, event);
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
    setDocument(placeSinglePrimitive(documentRef.value, cell.x, cell.y));
  } catch (err) {
    showPlacementBubble(
      event,
      err instanceof Error ? err.message : 'Failed to place LED.',
    );
  }
}

function undo(): void {
  clearDrag();
  setDocument(undoLastPrimitive(documentRef.value));
  clearLineDraft();
  clearCircleDraft();
  clearSelection();
  dismissPlacementBubble();
}

function backToDevices(): void {
  void router.push('/');
}

function selectTool(tool: Tool): void {
  activeTool.value = tool;
  clearDrag();
  clearLineDraft();
  clearCircleDraft();
  clearSelection();
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

  if (event.code === 'Escape' && dragState.value) {
    event.preventDefault();
    clearDrag();
    dismissPlacementBubble();
    requestRender();
    return;
  }

  if (event.code === 'Escape' && inactivePanelOpen.value) {
    event.preventDefault();
    closeInactivePanel();
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

  if (event.code === 'Escape' && selectedPrimitive.value) {
    event.preventDefault();
    clearSelection();
    dismissPlacementBubble();
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
  const target = event.target;
  if (!(target instanceof Element) || target.closest('[data-inactive-panel-root]')) {
    return;
  }
  closeInactivePanel();
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
    if (primitive.type === 'single') {
      syncSelectedSingleDraft();
      return;
    }
    if (primitive.type === 'line') {
      syncSelectedLineDraft();
      return;
    }
    syncSelectedCircleDraft();
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
    dragState,
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
              :class="{ 'tool-button-active': activeTool === 'circle' }"
              :disabled="loadingLayout || saving"
              @click="selectTool('circle')"
              title="Circle tool"
            >
              <IconCircleTool />
              <span>Circle</span>
            </button>
          </div>
          <label v-if="activeTool === 'line' || activeTool === 'circle'" class="editor-inline-control">
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
          <div v-if="activeTool === 'circle'" class="editor-inline-control">
            <span class="editor-inline-label">Direction</span>
            <div class="tool-buttons">
              <button
                type="button"
                class="tool-button editor-direction-button"
                :class="{ 'tool-button-active': circleDirection === 'cw' }"
                :disabled="loadingLayout || saving"
                @click="circleDirection = 'cw'"
              >
                <span>CW</span>
              </button>
              <button
                type="button"
                class="tool-button editor-direction-button"
                :class="{ 'tool-button-active': circleDirection === 'ccw' }"
                :disabled="loadingLayout || saving"
                @click="circleDirection = 'ccw'"
              >
                <span>CCW</span>
              </button>
            </div>
          </div>
          <div
            ref="inactivePanelRootRef"
            class="editor-inactive-panel-root"
            data-inactive-panel-root
          >
            <button
              type="button"
              class="tool-button"
              :class="{ 'tool-button-active': inactivePanelOpen }"
              :disabled="loadingLayout || saving"
              title="Manage inactive LEDs"
              @click.stop="toggleInactivePanel"
            >
              <IconInactiveTool />
              <span>Inactive</span>
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

        <div class="editor-command-right">
          <strong class="editor-placed-count">{{ placedCount }} / {{ deviceLength }}</strong>
          <button type="button" class="ghost-button" :disabled="!canUndo" @click="undo">
            <IconUndo />
            <span>Undo</span>
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
        <section
          v-if="selectedPrimitive"
          class="panel editor-selection-panel"
          aria-live="polite"
        >
          <div class="editor-selection-head">
            <div>
              <p class="editor-selection-label">Selected</p>
              <h3 class="editor-selection-title">{{ selectedPrimitiveLabel }}</h3>
            </div>
            <button
              type="button"
              class="ghost-button editor-selection-delete"
              :disabled="saving"
              @click="deleteSelectedPrimitive"
            >
              Delete
            </button>
          </div>

          <div v-if="selectedSingle" class="editor-selection-fields">
            <label class="editor-selection-field">
              <span class="editor-inline-label">X</span>
              <input
                v-model.number="singleEditX"
                class="spacing-input"
                type="number"
                step="1"
                :disabled="saving"
              />
            </label>
            <label class="editor-selection-field">
              <span class="editor-inline-label">Y</span>
              <input
                v-model.number="singleEditY"
                class="spacing-input"
                type="number"
                step="1"
                :disabled="saving"
              />
            </label>
            <button
              type="button"
              class="ghost-button editor-selection-apply"
              :disabled="saving"
              @click="applySelectedSingleEdit"
            >
              Apply
            </button>
          </div>

          <div v-else-if="selectedLine" class="editor-selection-fields">
            <label class="editor-selection-field">
              <span class="editor-inline-label">Start X</span>
              <input
                v-model.number="lineEditStartX"
                class="spacing-input"
                type="number"
                step="1"
                :disabled="saving"
              />
            </label>
            <label class="editor-selection-field">
              <span class="editor-inline-label">Start Y</span>
              <input
                v-model.number="lineEditStartY"
                class="spacing-input"
                type="number"
                step="1"
                :disabled="saving"
              />
            </label>
            <label class="editor-selection-field">
              <span class="editor-inline-label">End X</span>
              <input
                v-model.number="lineEditEndX"
                class="spacing-input"
                type="number"
                step="1"
                :disabled="saving"
              />
            </label>
            <label class="editor-selection-field">
              <span class="editor-inline-label">End Y</span>
              <input
                v-model.number="lineEditEndY"
                class="spacing-input"
                type="number"
                step="1"
                :disabled="saving"
              />
            </label>
            <label class="editor-selection-field">
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
            <p class="editor-selection-detail">Current count: {{ selectedLine.count }} LEDs</p>
            <button
              type="button"
              class="ghost-button editor-selection-apply"
              :disabled="saving"
              @click="applySelectedLineEdit"
            >
              Apply
            </button>
          </div>

          <div v-else-if="selectedCircle" class="editor-selection-fields">
            <label class="editor-selection-field">
              <span class="editor-inline-label">Center X</span>
              <input
                v-model.number="circleEditCenterX"
                class="spacing-input"
                type="number"
                step="1"
                :disabled="saving"
              />
            </label>
            <label class="editor-selection-field">
              <span class="editor-inline-label">Center Y</span>
              <input
                v-model.number="circleEditCenterY"
                class="spacing-input"
                type="number"
                step="1"
                :disabled="saving"
              />
            </label>
            <label class="editor-selection-field">
              <span class="editor-inline-label">Radius</span>
              <input
                v-model.number="circleEditRadius"
                class="spacing-input"
                type="number"
                min="1"
                step="1"
                :disabled="saving"
              />
            </label>
            <label class="editor-selection-field">
              <span class="editor-inline-label">Direction</span>
              <select
                v-model="circleEditDirection"
                class="spacing-input"
                :disabled="saving"
              >
                <option value="cw">CW</option>
                <option value="ccw">CCW</option>
              </select>
            </label>
            <label class="editor-selection-field">
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
            <p class="editor-selection-detail">Current count: {{ selectedCircle.count }} LEDs</p>
            <button
              type="button"
              class="ghost-button editor-selection-apply"
              :disabled="saving"
              @click="applySelectedCircleEdit"
            >
              Apply
            </button>
          </div>

          <p v-if="selectionError" class="editor-selection-error">{{ selectionError }}</p>
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

.editor-inactive-panel-root {
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

.editor-selection-panel {
  position: absolute;
  top: 0.8rem;
  right: 0.8rem;
  z-index: 3;
  width: min(20rem, calc(100% - 1.6rem));
  padding: 0.75rem;
  display: grid;
  gap: 0.7rem;
  background: rgba(9, 11, 15, 0.96);
}

.editor-selection-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 0.75rem;
}

.editor-selection-label {
  margin: 0 0 0.2rem;
  color: var(--muted);
  font-size: 0.68rem;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.editor-selection-title {
  margin: 0;
  color: var(--text);
  font-size: 0.92rem;
}

.editor-selection-fields {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  align-items: end;
  gap: 0.55rem;
}

.editor-selection-field {
  display: grid;
  gap: 0.3rem;
}

.editor-selection-apply,
.editor-selection-detail {
  grid-column: 1 / -1;
}

.editor-selection-detail {
  margin: 0;
  color: var(--muted);
  font-size: 0.74rem;
}

.editor-selection-summary {
  display: grid;
  gap: 0.28rem;
  color: var(--muted);
  font-size: 0.76rem;
}

.editor-selection-summary p,
.editor-selection-error {
  margin: 0;
}

.editor-selection-error {
  color: #ffdede;
  font-size: 0.74rem;
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

  .editor-selection-panel {
    left: 0.8rem;
    right: 0.8rem;
    width: auto;
  }

  .editor-selection-fields {
    grid-template-columns: 1fr;
  }
}
</style>
