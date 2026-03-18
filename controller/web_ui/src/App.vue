<script setup lang="ts">
import { computed } from 'vue';

import { useRelayState } from './composables/useRelayState';
import { CELL_PX, type SimTarget } from './lib/viewerRenderer';

const {
  assignCanvas,
  controllerConnected,
  emptyState,
  relayConnected,
  session,
  simTargets,
} = useRelayState();

const playbackState = computed(() => session.value?.playback_state ?? 'idle');
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
  <main class="shell">
    <header class="topbar">
      <div>
        <p class="eyebrow">Elements</p>
        <h1>Realtime Viewer</h1>
      </div>
      <div class="statusbox">
        <span class="pill" :class="pillClass(relayConnected)"> 
          {{ relayConnected ? 'relay connected' : 'relay disconnected' }}
        </span>
        <span class="pill" :class="pillClass(controllerConnected)">
          {{ controllerConnected ? 'controller connected' : 'controller disconnected' }}
        </span>
      </div>
    </header>

    <section class="summary">
      <div class="summary-card">
        <span class="label">Playback</span>
        <strong>{{ playbackState }}</strong>
      </div>
      <div class="summary-card">
        <span class="label">Session</span>
        <strong>{{ sessionId }}</strong>
      </div>
      <div class="summary-card">
        <span class="label">Time</span>
        <strong>{{ timeReadout }}</strong>
      </div>
    </section>

    <section class="viewer">
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
  </main>
</template>

<style scoped>
.shell {
  width: min(1100px, calc(100vw - 2rem));
  margin: 0 auto;
  padding: 1.5rem 0 2.5rem;
}

.topbar {
  display: flex;
  justify-content: space-between;
  gap: 1rem;
  align-items: flex-start;
  margin-bottom: 1.5rem;
}

.eyebrow {
  margin: 0 0 0.35rem;
  text-transform: uppercase;
  letter-spacing: 0.18em;
  font-size: 0.72rem;
  color: var(--accent);
}

h1 {
  margin: 0;
  font-family: 'IBM Plex Mono', 'SFMono-Regular', monospace;
  font-size: clamp(1.75rem, 4vw, 2.6rem);
}

.statusbox,
.summary {
  display: flex;
  gap: 0.75rem;
  flex-wrap: wrap;
}

.summary {
  margin-bottom: 1.5rem;
}

.summary-card,
.viewer,
.target-panel {
  background: var(--panel);
  border: 1px solid var(--panel-edge);
  border-radius: 18px;
  backdrop-filter: blur(10px);
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

.pill {
  display: inline-flex;
  align-items: center;
  padding: 0.45rem 0.8rem;
  border-radius: 999px;
  font-size: 0.82rem;
  background: var(--surface);
  border: 1px solid var(--panel-edge);
}

.pill-online {
  color: #f7f3eb;
  background: var(--accent-soft);
  border-color: rgba(229, 156, 76, 0.35);
}

.pill-offline {
  color: #f7e5e5;
  background: rgba(182, 83, 83, 0.16);
  border-color: rgba(182, 83, 83, 0.35);
}

.viewer {
  padding: 1rem;
}

.empty-state {
  padding: 2rem 1rem;
  text-align: center;
  color: var(--muted);
}

.empty-state h2 {
  margin-top: 0;
  margin-bottom: 0.5rem;
  color: var(--text);
  font-size: 1.15rem;
}

.empty-state p {
  margin: 0;
}

.strip-list {
  display: grid;
  gap: 1rem;
}

.target-panel {
  padding: 0.9rem;
  display: grid;
  gap: 0.8rem;
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
  font-family: 'IBM Plex Mono', 'SFMono-Regular', monospace;
  font-size: 0.95rem;
}

.target-strip,
.target-length {
  color: var(--muted);
  font-size: 0.8rem;
}

.target-pill {
  flex: 0 0 auto;
}

.target-canvas {
  display: block;
  background:
    linear-gradient(90deg, rgba(255, 255, 255, 0.03), rgba(255, 255, 255, 0)),
    #091015;
  border-radius: 12px;
  image-rendering: pixelated;
}

.target-canvas-2d {
  width: auto;
  min-height: 0;
  margin-top: 0.1rem;
  border: 1px solid rgba(255, 255, 255, 0.08);
  box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.03);
}

.target-no-layout {
  padding: 1rem;
  border-radius: 12px;
  border: 1px dashed rgba(255, 255, 255, 0.14);
  background: rgba(9, 16, 21, 0.58);
  color: var(--muted);
  font-size: 0.85rem;
}

.target-panel-offline .target-canvas {
  opacity: 0.55;
}

.target-panel-offline .target-no-layout {
  opacity: 0.7;
}

@media (max-width: 640px) {
  .topbar {
    flex-direction: column;
  }

  .summary-card {
    flex: 1 1 100%;
  }
}
</style>
