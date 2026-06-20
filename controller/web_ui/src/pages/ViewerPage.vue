<script setup lang="ts">
import { computed } from 'vue';

import { useInjectedServerState } from '../composables/useServerState';
import { CELL_PX, type SimTarget } from '../lib/viewerRenderer';

const {
  assignCanvas,
  emptyState,
  session,
  simTargets,
} = useInjectedServerState();

const playbackState = computed(() => session.value?.state ?? 'idle');
const sessionId = computed(() => session.value?.session_id ?? 'none');
const timeReadout = computed(() => `${Number(session.value?.current_t_rel ?? 0).toFixed(2)}s`);

function pillClass(isOnline: boolean): string {
  return isOnline ? 'pill-online' : 'pill-offline';
}

function hasLayout(target: SimTarget): boolean {
  return Boolean(target.layout && target.gridWidth > 0 && target.gridHeight > 0);
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

      <section class="summary">
        <div class="panel summary-card">
          <span class="label">Playback</span>
          <strong>{{ playbackState }}</strong>
        </div>
        <div class="panel summary-card">
          <span class="label">Session</span>
          <strong>{{ sessionId }}</strong>
        </div>
        <div class="panel summary-card">
          <span class="label">Time</span>
          <strong>{{ timeReadout }}</strong>
        </div>
      </section>

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

            <canvas
              v-if="hasLayout(target)"
              :ref="(el) => assignCanvas(target, el)"
              class="target-canvas target-canvas-2d"
              :width="target.gridWidth"
              :height="target.gridHeight"
              :style="{
                width: `${target.gridWidth * CELL_PX}px`,
                height: `${target.gridHeight * CELL_PX}px`,
              }"
            />

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

.summary {
  display: flex;
  gap: 0.75rem;
  flex-wrap: wrap;
  margin-bottom: 1.5rem;
}

.summary-card {
  min-width: 10rem;
  padding: 0.9rem 1rem;
}

.label {
  display: block;
  margin-bottom: 0.3rem;
  font-size: 0.78rem;
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 0.08em;
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

.target-canvas {
  border-radius: var(--radius-panel);
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid var(--panel-edge);
}

.target-canvas-2d {
  image-rendering: pixelated;
  align-self: start;
}

.target-no-layout {
  padding: 1rem;
  border-radius: var(--radius-panel);
  border: 1px dashed rgba(108, 162, 255, 0.35);
  color: var(--muted);
  background: rgba(255, 255, 255, 0.02);
}
</style>
