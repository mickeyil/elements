<script setup lang="ts">
import { ref } from 'vue';
import { useInjectedServerState } from '../composables/useServerState';
import { CELL_PX, ledIndexAt, type SimTarget } from '../lib/viewerRenderer';
import ControlPanel from '../components/ControlPanel.vue';

const {
  assignCanvas,
  emptyState,
  simTargets,
} = useInjectedServerState();

const hovered = ref<{ uid: string; index: number; x: number; y: number } | null>(null);

function pillClass(isOnline: boolean): string {
  return isOnline ? 'pill-online' : 'pill-offline';
}

function hasLayout(target: SimTarget): boolean {
  return Boolean(target.layout && target.gridWidth > 0 && target.gridHeight > 0);
}

function onCanvasMove(event: MouseEvent, target: SimTarget): void {
  const index = ledIndexAt(target, event.offsetX, event.offsetY);
  if (index === null) {
    clearHover(target);
    return;
  }
  hovered.value = { uid: target.deviceUid, index, x: event.offsetX, y: event.offsetY };
}

function clearHover(target: SimTarget): void {
  if (hovered.value?.uid === target.deviceUid) {
    hovered.value = null;
  }
}
</script>

<template>
  <main class="viewer-page page-scroll">
    <div class="page-shell">
      <header class="page-header">
        <div>
          <p class="page-eyebrow">Simulation</p>
          <h1 class="page-title">Realtime Simulation</h1>
          <p class="page-copy">
            Session-driven simulation output for configured sim targets.
          </p>
        </div>
      </header>

      <ControlPanel />

      <section class="panel viewer">
        <div v-if="emptyState" class="empty-state">
          <h2>{{ emptyState.title }}</h2>
          <p>{{ emptyState.copy }}</p>
        </div>

        <div v-else class="strip-list">
          <section
            v-for="target in simTargets"
            :key="target.deviceUid"
            class="target-panel"
            :class="{ 'target-panel-offline': !target.connected }"
          >
            <div class="target-head">
              <div class="target-info">
                <strong class="target-name">{{ target.deviceUid }}</strong>
                <span class="target-strip">strip {{ target.stripName }}</span>
                <span class="target-length">
                  logical {{ target.logicalLength }} / physical {{ target.physicalLength }} px
                </span>
              </div>
              <span class="pill target-pill" :class="pillClass(target.connected)">
                {{ target.connected ? 'connected' : 'disconnected' }}
              </span>
            </div>

            <div v-if="hasLayout(target)" class="canvas-wrap">
              <canvas
                :ref="(el) => assignCanvas(target, el)"
                class="target-canvas target-canvas-2d"
                :width="target.gridWidth * CELL_PX"
                :height="target.gridHeight * CELL_PX"
                @mousemove="(event) => onCanvasMove(event, target)"
                @mouseleave="() => clearHover(target)"
              />
              <div
                v-if="hovered && hovered.uid === target.deviceUid"
                class="led-tooltip"
                :style="{ left: `${hovered.x}px`, top: `${hovered.y}px` }"
              >
                LED {{ hovered.index }}
              </div>
            </div>

            <div v-else class="target-no-layout">No layout file for this sim target.</div>
          </section>
        </div>
      </section>
    </div>
  </main>
</template>

<style scoped>
.viewer-page {
  min-height: 0;
}

.viewer {
  padding: 1rem;
}

.strip-list {
  display: grid;
  gap: 1rem;
}

.target-panel {
  padding: 0.9rem;
  display: grid;
  gap: 0.8rem;
  border-radius: var(--radius-panel);
  border: 1px solid var(--panel-edge);
  background: rgba(255, 255, 255, 0.025);
}

.target-panel-offline {
  opacity: 0.78;
}

.target-head {
  display: flex;
  justify-content: space-between;
  gap: 1rem;
  align-items: flex-start;
}

.target-info {
  display: grid;
  gap: 0.2rem;
}

.target-name {
  font-family: var(--font-mono);
  font-size: 0.95rem;
}

.target-strip,
.target-length {
  color: var(--muted);
  font-size: 0.82rem;
}

.canvas-wrap {
  position: relative;
  align-self: start;
}

.target-canvas {
  display: block;
  border-radius: var(--radius-panel);
  background: #000;
  border: 1px solid var(--panel-edge);
}

.led-tooltip {
  position: absolute;
  transform: translate(0.75rem, -1.6rem);
  padding: 0.1rem 0.4rem;
  border-radius: 0.35rem;
  background: rgba(0, 0, 0, 0.85);
  border: 1px solid var(--panel-edge);
  color: var(--text, #fff);
  font-family: var(--font-mono);
  font-size: 0.72rem;
  white-space: nowrap;
  pointer-events: none;
}

.target-no-layout {
  padding: 1rem;
  border-radius: var(--radius-panel);
  border: 1px dashed rgba(108, 162, 255, 0.35);
  color: var(--muted);
  background: rgba(255, 255, 255, 0.02);
}
</style>
