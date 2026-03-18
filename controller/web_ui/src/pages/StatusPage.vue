<script setup lang="ts">
import { computed } from 'vue';
import { RouterLink } from 'vue-router';

import { useInjectedRelayState, type SnapshotDevice } from '../composables/useRelayState';

const { snapshot } = useInjectedRelayState();

const devices = computed<SnapshotDevice[]>(() => {
  const items = Array.isArray(snapshot.value?.devices) ? snapshot.value.devices : [];
  return [...items].sort((left, right) =>
    String(left?.device_uid ?? '').localeCompare(String(right?.device_uid ?? '')),
  );
});

const layouts = computed<Record<string, unknown>>(() => {
  const value = snapshot.value?.layouts;
  return value && typeof value === 'object' ? value : {};
});

const deviceCount = computed(() => devices.value.length);
const onlineCount = computed(() => devices.value.filter((device) => device.connected).length);
const simCount = computed(() =>
  devices.value.filter((device) => device.device_type === 'sim').length,
);

function pillClass(isOnline: boolean): string {
  return isOnline ? 'pill-online' : 'pill-offline';
}

function hasLayout(deviceUid: string | undefined): boolean {
  return Boolean(deviceUid && layouts.value[deviceUid]);
}
</script>

<template>
  <main class="page-shell">
    <header class="page-header">
      <div>
        <p class="page-eyebrow">Overview</p>
        <h1 class="page-title">Status</h1>
        <p class="page-copy">
          Configured devices and layout availability. Layout editing lives here even when no
          session is active.
        </p>
      </div>
    </header>

    <section class="status-summary">
      <div class="panel status-card">
        <span class="status-label">Configured devices</span>
        <strong>{{ deviceCount }}</strong>
      </div>
      <div class="panel status-card">
        <span class="status-label">Connected now</span>
        <strong>{{ onlineCount }}</strong>
      </div>
      <div class="panel status-card">
        <span class="status-label">Sim devices</span>
        <strong>{{ simCount }}</strong>
      </div>
    </section>

    <section class="panel devices-panel">
      <div class="devices-head">
        <div>
          <h2>Configured devices</h2>
          <p>Choose a sim device to create or edit its saved layout.</p>
        </div>
      </div>

      <div v-if="!snapshot" class="empty-state">
        <h2>Waiting for snapshot</h2>
        <p>The relay has not delivered device metadata yet.</p>
      </div>

      <div v-else-if="!devices.length" class="empty-state">
        <h2>No configured devices</h2>
        <p>The current snapshot does not include any configured devices.</p>
      </div>

      <div v-else class="device-list">
        <article v-for="device in devices" :key="device.device_uid" class="device-card">
          <div class="device-main">
            <div class="device-title-row">
              <strong class="device-uid">{{ device.device_uid }}</strong>
              <span class="pill" :class="pillClass(Boolean(device.connected))">
                {{ device.connected ? 'connected' : 'disconnected' }}
              </span>
            </div>

            <dl class="device-meta">
              <div>
                <dt>Type</dt>
                <dd>{{ device.device_type ?? 'unknown' }}</dd>
              </div>
              <div>
                <dt>Strip</dt>
                <dd>{{ device.strip_id ?? 'n/a' }}</dd>
              </div>
              <div>
                <dt>Length</dt>
                <dd>{{ device.length ?? 0 }} px</dd>
              </div>
              <div>
                <dt>Layout</dt>
                <dd>
                  <span
                    class="pill"
                    :class="hasLayout(device.device_uid) ? 'pill-online' : 'pill-offline'"
                  >
                    {{
                      device.device_type === 'sim'
                        ? hasLayout(device.device_uid)
                          ? 'saved'
                          : 'missing'
                        : 'n/a'
                    }}
                  </span>
                </dd>
              </div>
            </dl>
          </div>

          <div class="device-actions">
            <RouterLink
              v-if="device.device_type === 'sim' && device.device_uid"
              class="action-button"
              :to="`/layouts/${encodeURIComponent(device.device_uid)}`"
            >
              {{ hasLayout(device.device_uid) ? 'Edit layout' : 'Create layout' }}
            </RouterLink>
            <span v-else class="device-note">Browser editor is available for sim devices only.</span>
          </div>
        </article>
      </div>
    </section>
  </main>
</template>

<style scoped>
.status-summary {
  display: flex;
  gap: 0.75rem;
  flex-wrap: wrap;
  margin-bottom: 1.5rem;
}

.status-card {
  min-width: 11rem;
  padding: 0.9rem 1rem;
}

.status-label {
  display: block;
  margin-bottom: 0.3rem;
  font-size: 0.78rem;
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 0.08em;
}

.devices-panel {
  padding: 1rem;
}

.devices-head {
  margin-bottom: 1rem;
}

.devices-head h2 {
  margin: 0 0 0.35rem;
  font-size: 1.1rem;
}

.devices-head p {
  margin: 0;
  color: var(--muted);
}

.device-list {
  display: grid;
  gap: 1rem;
}

.device-card {
  display: flex;
  justify-content: space-between;
  gap: 1rem;
  padding: 1rem;
  border-radius: 16px;
  border: 1px solid var(--panel-edge);
  background: rgba(255, 255, 255, 0.03);
}

.device-main {
  min-width: 0;
  display: grid;
  gap: 0.85rem;
}

.device-title-row {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  flex-wrap: wrap;
}

.device-uid {
  font-family: 'IBM Plex Mono', 'SFMono-Regular', monospace;
  font-size: 0.95rem;
}

.device-meta {
  margin: 0;
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(8rem, 1fr));
  gap: 0.75rem;
}

.device-meta div {
  display: grid;
  gap: 0.2rem;
}

.device-meta dt {
  font-size: 0.72rem;
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 0.08em;
}

.device-meta dd {
  margin: 0;
}

.device-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  min-width: 12rem;
}

.device-note {
  color: var(--muted);
  font-size: 0.84rem;
  text-align: right;
}

@media (max-width: 760px) {
  .device-card {
    flex-direction: column;
  }

  .device-actions {
    justify-content: flex-start;
    min-width: 0;
  }

  .device-note {
    text-align: left;
  }
}
</style>
