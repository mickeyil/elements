<script setup lang="ts">
import { onMounted, ref, watch } from 'vue';

import { useInjectedServerState } from '../composables/useServerState';
import { DEVICE_LOG_CAP, formatLogTime, isAtBottom, levelClass } from '../lib/logsModel';

const { deviceLogs } = useInjectedServerState();

const list = ref<HTMLElement | null>(null);
// Pinned to the newest row until the operator scrolls up to read.
const following = ref(true);

function scrollToEnd(): void {
  if (list.value) {
    list.value.scrollTop = list.value.scrollHeight;
  }
}

function onScroll(): void {
  const el = list.value;
  if (el) {
    following.value = isAtBottom(el.scrollTop, el.scrollHeight, el.clientHeight);
  }
}

function jumpToLatest(): void {
  following.value = true;
  scrollToEnd();
}

// Post flush: the new rows are in the DOM, so scrollHeight includes them.
watch(deviceLogs, () => {
  if (following.value) {
    scrollToEnd();
  }
}, { flush: 'post' });

onMounted(scrollToEnd);
</script>

<template>
  <main class="logs-page">
    <header class="page-header">
      <div>
        <p class="page-eyebrow">Logs</p>
        <h1 class="page-title">Device Logs</h1>
      </div>
    </header>

    <div class="panel logs-frame">
      <div ref="list" class="logs-list mono" @scroll="onScroll">
        <div v-if="!deviceLogs.length" class="empty-state">
          <p>No device log records yet.</p>
        </div>
        <div
          v-for="(record, index) in deviceLogs"
          :key="index"
          class="logs-row"
          :class="`logs-row-${levelClass(record.level)}`"
        >
          <span>{{ formatLogTime(record.time) }}</span>
          <span class="logs-uid">{{ record.uid }}</span>
          <span>{{ record.level }}</span>
          <span class="logs-text">{{ record.text }}</span>
        </div>
      </div>
      <button v-if="!following" type="button" class="action-button logs-jump" @click="jumpToLatest">
        Jump to latest
      </button>
    </div>

    <p class="logs-footer">
      Last {{ DEVICE_LOG_CAP }} records since the controller started; controller receipt time.
    </p>
  </main>
</template>

<style scoped>
.logs-page {
  height: 100%;
  display: flex;
  flex-direction: column;
  padding: 1.5rem;
}

.logs-frame {
  position: relative;
  flex: 1 1 auto;
  min-height: 0;
  display: flex;
}

.logs-list {
  flex: 1 1 auto;
  overflow-y: auto;
  padding: 0.5rem 0;
  font-size: 0.78rem;
  line-height: 1.45;
}

.logs-row {
  display: grid;
  grid-template-columns: auto auto 1ch minmax(0, 1fr);
  gap: 1rem;
  padding: 0 0.9rem;
  color: var(--text);
}

.logs-row-warn {
  color: var(--log-warn);
}

.logs-row-error {
  color: var(--log-error);
}

.logs-uid {
  min-width: 10ch;
}

.logs-text {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.logs-jump {
  position: absolute;
  right: 1rem;
  bottom: 1rem;
  background: var(--surface-strong);
}

.logs-footer {
  margin: 0.6rem 0 0;
  color: var(--muted);
  font-size: 0.72rem;
}

@media (max-width: 900px) {
  .logs-page {
    padding: 1rem;
  }
}
</style>
