<script setup lang="ts">
import { computed, ref, watch, watchPostEffect } from 'vue';

import HsvPicker from '../components/HsvPicker.vue';
import { useInjectedServerState, type SnapshotLibraryProgram } from '../composables/useServerState';
import { fillStrip, runLibraryProgram, stopStrip } from '../lib/panelApi';
import {
  LatestValueSender,
  groupStrips,
  type PanelHsv,
  type PanelStrip,
  type StripPanelState,
} from '../lib/panelModel';
import {
  CELL_PX,
  attachTargetCanvas,
  measureLayout,
  paintTargets,
  type LayoutPayload,
  type SimTarget,
} from '../lib/viewerRenderer';

const STORAGE_KEY = 'elements.panel.strip';

const { controllerConnected, deviceFrames, snapshot } = useInjectedServerState();

const strips = computed<PanelStrip[]>(() => groupStrips(snapshot.value?.devices ?? []));
const library = computed<SnapshotLibraryProgram[]>(() => snapshot.value?.library ?? []);
const layouts = computed<Record<string, LayoutPayload>>(() => snapshot.value?.layouts ?? {});

function readStoredStrip(): string {
  try {
    return window.localStorage.getItem(STORAGE_KEY) ?? '';
  } catch {
    return '';
  }
}

// The operator's pick; the page falls back to the first strip while it is
// not (or no longer) configured.
const preferredStripId = ref(readStoredStrip());

const selectedStrip = computed<PanelStrip | null>(
  () => strips.value.find((strip) => strip.stripId === preferredStripId.value)
    ?? strips.value[0] ?? null,
);
const panelState = computed<StripPanelState | null>(() => selectedStrip.value?.panel ?? null);

function selectStrip(stripId: string): void {
  preferredStripId.value = stripId;
  try {
    window.localStorage.setItem(STORAGE_KEY, stripId);
  } catch {
    // Not remembered; the pick still holds for this visit.
  }
}

function onlineCount(strip: PanelStrip): number {
  return strip.devices.filter((device) => device.online).length;
}

const pending = ref(false);
const error = ref('');

async function send(action: () => Promise<unknown>): Promise<void> {
  try {
    await action();
    error.value = '';
  } catch (err) {
    error.value = err instanceof Error ? err.message : 'Command failed.';
  }
}

async function run(action: () => Promise<unknown>): Promise<void> {
  pending.value = true;
  await send(action);
  pending.value = false;
}

const canCommand = computed(() => controllerConnected.value && Boolean(selectedStrip.value));

// --- color -----------------------------------------------------------------

const color = ref<PanelHsv>({ h: 0, s: 1, v: 1 });

// Start the picker from the strip's held color when a strip is selected;
// later state echoes do not move it under the operator's pointer.
watch(
  () => selectedStrip.value?.stripId,
  () => {
    const hsv = panelState.value?.hsv;
    if (hsv) {
      color.value = hsv;
    }
  },
  { immediate: true },
);

const fillSender = new LatestValueSender<{ stripId: string; hsv: PanelHsv }>(
  ({ stripId, hsv }) => send(() => fillStrip(stripId, hsv)),
);

function onColor(hsv: PanelHsv): void {
  color.value = hsv;
  const strip = selectedStrip.value;
  if (strip && controllerConnected.value) {
    fillSender.push({ stripId: strip.stripId, hsv });
  }
}

// --- animations --------------------------------------------------------------

function isRunning(program: SnapshotLibraryProgram): boolean {
  return panelState.value?.mode === 'program' && panelState.value.programId === program.program_id;
}

function onRun(program: SnapshotLibraryProgram): void {
  const strip = selectedStrip.value;
  if (strip) {
    void run(() => runLibraryProgram(strip.stripId, program.program_id));
  }
}

function onStop(): void {
  const strip = selectedStrip.value;
  if (strip) {
    void run(() => stopStrip(strip.stripId));
  }
}

const stateLabel = computed(() => {
  const state = panelState.value;
  if (!state) {
    return 'Show';
  }
  if (state.mode === 'program') {
    return `Program ${state.programId}`;
  }
  return state.mode === 'manual' ? 'Color' : 'Stopped';
});

// --- preview -----------------------------------------------------------------

const hasSim = computed(() => Boolean(selectedStrip.value?.devices.some((device) => device.isSim)));

// One target per sim device on the strip that has a layout; each paints the
// device's own frame as a one-strip slice list.
const previewTargets = computed<SimTarget[]>(() => {
  const strip = selectedStrip.value;
  if (!strip) {
    return [];
  }
  return strip.devices
    .filter((device) => device.isSim && layouts.value[device.uid])
    .map((device) => {
      const layout = layouts.value[device.uid];
      return {
        deviceUid: device.uid,
        stripName: strip.stripId,
        logicalIndex: 0,
        logicalLength: strip.length,
        physicalLength: strip.length,
        connected: device.online,
        layout,
        ...measureLayout(layout),
        canvas: null,
        ctx: null,
      };
    });
});

// Device frames are the panel's pictures; while the show drives the strip
// the cached one is stale, so nothing is painted.
function slicesFor(target: SimTarget): Uint8Array[] {
  const frame = panelState.value ? deviceFrames.get(target.deviceUid) : undefined;
  return frame ? [frame] : [];
}

function assignPreviewCanvas(target: SimTarget, element: unknown): void {
  attachTargetCanvas(target, element instanceof HTMLCanvasElement ? element : null);
}

// After render, so every target has its canvas; reruns on each new frame.
watchPostEffect(() => {
  for (const target of previewTargets.value) {
    paintTargets([target], slicesFor(target));
  }
});
</script>

<template>
  <main class="panel-page page-scroll">
    <div class="page-shell">
      <header class="page-header">
        <div>
          <p class="page-eyebrow">Panel</p>
          <h1 class="page-title">Operator Panel</h1>
          <p class="page-copy">
            Take one strip out of the show: hold a color or loop a library program.
            The next program load hands it back to the show.
          </p>
        </div>
      </header>

      <div v-if="!strips.length" class="panel">
        <div class="empty-state">
          <h2>No strips</h2>
          <p>Configure a device on the Devices page to drive its strip here.</p>
        </div>
      </div>

      <div v-else class="panel-layout">
        <nav class="panel strip-picker" aria-label="Strips">
          <p class="pane-label">Strips</p>
          <button
            v-for="strip in strips"
            :key="strip.stripId"
            type="button"
            class="strip-row"
            :class="{ 'strip-row-active': strip.stripId === selectedStrip?.stripId }"
            @click="selectStrip(strip.stripId)"
          >
            <span class="strip-row-id mono">{{ strip.stripId }}</span>
            <span class="strip-row-count mono">{{ onlineCount(strip) }}/{{ strip.devices.length }}</span>
            <span v-if="strip.panel" class="strip-row-badge">{{ strip.panel.mode }}</span>
          </button>
        </nav>

        <div v-if="selectedStrip" class="panel-main">
          <div class="strip-head">
            <strong class="strip-title mono">{{ selectedStrip.stripId }}</strong>
            <span class="strip-meta">{{ selectedStrip.length }} px</span>
            <span class="state-pill">{{ stateLabel }}</span>
            <span v-if="!controllerConnected" class="state-hint">Controller offline</span>
          </div>

          <p v-if="error" class="panel-error">{{ error }}</p>

          <div class="pane-row">
            <section class="panel pane" :class="{ 'pane-disabled': !canCommand }">
              <p class="pane-label">Color</p>
              <HsvPicker :model-value="color" @change="onColor" />
            </section>

            <section class="panel pane">
              <div class="pane-head">
                <p class="pane-label">Animations</p>
                <button
                  type="button"
                  class="ghost-button"
                  :disabled="!canCommand || pending"
                  @click="onStop"
                >
                  Stop
                </button>
              </div>
              <p v-if="!library.length" class="pane-note">No programs in the panel library.</p>
              <ul v-else class="program-list">
                <li
                  v-for="program in library"
                  :key="program.program_id"
                  class="program-row"
                  :class="{ 'program-row-running': isRunning(program) }"
                >
                  <span class="program-id mono">{{ program.program_id }}</span>
                  <span v-if="isRunning(program)" class="program-running">Running</span>
                  <span v-else-if="program.error" class="program-error">Error</span>
                  <button
                    type="button"
                    class="action-button"
                    :disabled="!canCommand || pending || Boolean(program.error)"
                    :title="program.error ?? ''"
                    @click="onRun(program)"
                  >
                    Run
                  </button>
                </li>
              </ul>
            </section>
          </div>

          <section class="panel pane">
            <p class="pane-label">Preview</p>
            <p v-if="!hasSim" class="pane-note">
              No sim device serves this strip. Add one with Simulate on the Devices page to preview it here.
            </p>
            <p v-else-if="!previewTargets.length" class="pane-note">
              The sim devices on this strip have no layout yet.
            </p>
            <template v-else>
              <p v-if="!panelState" class="pane-note">
                The show drives this strip; its picture is on the Simulation page.
              </p>
              <div class="preview-list">
                <figure v-for="target in previewTargets" :key="target.deviceUid" class="preview">
                  <canvas
                    :ref="(el) => assignPreviewCanvas(target, el)"
                    class="preview-canvas"
                    :width="target.gridWidth * CELL_PX"
                    :height="target.gridHeight * CELL_PX"
                  />
                  <figcaption class="preview-caption mono">{{ target.deviceUid }}</figcaption>
                </figure>
              </div>
            </template>
          </section>
        </div>
      </div>
    </div>
  </main>
</template>

<style scoped>
.panel-page {
  min-height: 0;
}

.panel-layout {
  display: grid;
  grid-template-columns: 220px minmax(0, 1fr);
  gap: 1rem;
  align-items: start;
}

.pane-label {
  margin: 0;
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 0.14em;
  font-size: 0.64rem;
  font-weight: 700;
}

.strip-picker {
  display: grid;
  padding: 0.75rem 0;
}

.strip-picker .pane-label {
  padding: 0 1rem 0.5rem;
}

.strip-row {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.65rem 1rem;
  border: 0;
  border-left: 4px solid transparent;
  background: transparent;
  color: var(--muted);
  text-align: left;
  cursor: pointer;
  transition: color 120ms ease, background 120ms ease, border-color 120ms ease;
}

.strip-row:hover {
  color: var(--text);
  background: #1c1b1b;
}

.strip-row-active {
  color: var(--accent);
  background: #2a2a2a;
  border-left-color: var(--accent);
}

.strip-row-id {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 0.82rem;
}

.strip-row-count {
  font-size: 0.7rem;
  color: var(--muted);
}

.strip-row-badge {
  padding: 0.12rem 0.32rem;
  font-size: 0.55rem;
  font-weight: 700;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  background: var(--accent-soft);
  color: var(--accent);
}

.panel-main {
  display: grid;
  gap: 1rem;
  min-width: 0;
}

.strip-head {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  flex-wrap: wrap;
}

.strip-title {
  font-size: 1.05rem;
}

.strip-meta,
.state-hint {
  color: var(--muted);
  font-size: 0.82rem;
}

.state-pill {
  font-family: var(--font-mono);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  font-size: 0.82rem;
  padding: 0.2rem 0.6rem;
  border-radius: 999px;
  border: 1px solid var(--panel-edge);
  background: rgba(255, 255, 255, 0.04);
}

.panel-error {
  margin: 0;
  color: #ff8a8a;
  font-size: 0.85rem;
}

.pane-row {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(340px, 1fr));
  gap: 1rem;
  align-items: start;
}

.pane {
  display: grid;
  gap: 0.75rem;
  padding: 1rem;
  min-width: 0;
}

.pane-disabled {
  opacity: 0.45;
  pointer-events: none;
}

.pane-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
}

.pane-note {
  margin: 0;
  color: var(--muted);
  font-size: 0.85rem;
}

.program-list {
  display: grid;
  gap: 1px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.program-row {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 0.45rem 0.6rem;
  background: var(--surface-raised);
  border-left: 3px solid transparent;
}

.program-row-running {
  border-left-color: var(--status-online);
}

.program-id {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 0.85rem;
}

.program-running,
.program-error {
  font-size: 0.55rem;
  font-weight: 700;
  letter-spacing: 0.14em;
  text-transform: uppercase;
}

.program-running {
  color: var(--status-online);
}

.program-error {
  color: #ff8a8a;
}

.preview-list {
  display: flex;
  flex-wrap: wrap;
  gap: 1rem;
}

.preview {
  display: grid;
  gap: 0.4rem;
  margin: 0;
}

.preview-canvas {
  display: block;
  border-radius: var(--radius-panel);
  background: #000;
  border: 1px solid var(--panel-edge);
}

.preview-caption {
  color: var(--muted);
  font-size: 0.72rem;
}

@media (max-width: 900px) {
  .panel-layout {
    grid-template-columns: 1fr;
  }
}
</style>
