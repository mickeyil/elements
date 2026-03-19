<script setup lang="ts">
import { computed, provide } from 'vue';
import { RouterLink, RouterView } from 'vue-router';

import IconDevices from './components/icons/IconDevices.vue';
import IconSimulation from './components/icons/IconSimulation.vue';
import { relayStateKey, useRelayState } from './composables/useRelayState';

const relayState = useRelayState();
provide(relayStateKey, relayState);

const { controllerConnected, relayConnected } = relayState;

const footerStatus = computed(() => {
  if (!relayConnected.value) {
    return { label: 'Offline', className: 'app-footer-status-offline' };
  }
  if (!controllerConnected.value) {
    return { label: 'Controller offline', className: 'app-footer-status-offline' };
  }
  return { label: 'Connected', className: 'app-footer-status-online' };
});
</script>

<template>
  <div class="app-shell">
    <aside class="app-sidebar">
      <div class="app-branding">
        <strong class="app-title">ELEMENTS</strong>
        <p class="app-eyebrow">Web Console</p>
      </div>

      <nav class="app-nav" aria-label="Primary">
        <RouterLink class="app-nav-link" to="/">
          <IconDevices />
          <span>Devices</span>
        </RouterLink>
        <RouterLink class="app-nav-link" to="/viewer">
          <IconSimulation />
          <span>Simulation</span>
        </RouterLink>
      </nav>
    </aside>

    <main class="app-content">
      <RouterView />
    </main>

    <footer class="app-footer">
      <div class="app-footer-status" :class="footerStatus.className">
        <span class="app-footer-dot" />
        <strong>{{ footerStatus.label }}</strong>
      </div>
    </footer>

    <div
      v-if="!relayConnected"
      class="app-offline-overlay"
      role="alert"
      aria-live="polite"
      aria-label="Offline. Reconnecting to backend."
    >
      <div class="app-offline-card">
        <span class="app-offline-card-dot" aria-hidden="true" />
        <h2 class="app-offline-title">Offline</h2>
        <p class="app-offline-copy">Reconnecting to backend…</p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.app-shell {
  height: 100%;
  display: grid;
  grid-template-columns: 196px minmax(0, 1fr);
  grid-template-rows: minmax(0, 1fr) auto;
}

.app-sidebar {
  grid-row: 1 / -1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 1rem;
  padding: 1.5rem 0 0.75rem;
  border-right: 1px solid var(--panel-edge);
  background: #0e0e0e;
}

.app-branding {
  display: grid;
  gap: 0.3rem;
  padding: 0 1.5rem 1rem;
}

.app-title {
  font-family: var(--font-body);
  font-size: 1.44rem;
  line-height: 1;
  letter-spacing: 0.08em;
  color: var(--accent);
}

.app-eyebrow {
  margin: 0;
  text-transform: uppercase;
  letter-spacing: 0.22em;
  font-size: 0.58rem;
  font-weight: 700;
  color: var(--muted);
}

.app-nav {
  display: grid;
  gap: 0;
}

.app-nav-link {
  display: inline-flex;
  align-items: center;
  gap: 0.8rem;
  color: var(--muted);
  text-decoration: none;
  padding: 1.05rem 1.5rem;
  border-left: 4px solid transparent;
  transition: color 120ms ease, background 120ms ease, border-color 120ms ease;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  font-size: 0.72rem;
  font-weight: 700;
}

.app-nav-link:hover {
  color: var(--text);
  background: #1c1b1b;
}

.app-nav-link.router-link-active {
  color: var(--accent);
  background: #2a2a2a;
  border-left-color: var(--accent);
}

.app-content {
  min-width: 0;
  min-height: 0;
  overflow: hidden;
}

.app-footer {
  min-width: 0;
  display: flex;
  align-items: center;
  padding: 0.45rem 1rem 0.5rem;
  border-top: 1px solid var(--panel-edge);
  background: #0f1010;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  font-size: 0.66rem;
  font-weight: 700;
}

.app-footer-status {
  display: inline-flex;
  align-items: center;
  gap: 0.45rem;
}

.app-footer-dot {
  width: 0.52rem;
  height: 0.52rem;
  border-radius: 999px;
  background: currentColor;
  flex: 0 0 auto;
}

.app-footer-status-online {
  color: var(--status-online);
}

.app-footer-status-offline {
  color: var(--status-dropped);
}

.app-offline-overlay {
  position: fixed;
  inset: 0;
  z-index: 1000;
  display: grid;
  place-items: center;
  padding: 1.5rem;
  background: rgba(8, 10, 14, 0.84);
  backdrop-filter: blur(2px);
}

.app-offline-card {
  width: min(26rem, 100%);
  display: grid;
  justify-items: center;
  gap: 0.5rem;
  padding: 1.6rem 1.75rem;
  border: 1px solid rgba(239, 68, 68, 0.18);
  border-radius: var(--radius-panel);
  background: rgba(13, 16, 21, 0.98);
  text-align: center;
}

.app-offline-card-dot {
  width: 0.8rem;
  height: 0.8rem;
  border-radius: 999px;
  background: var(--status-dropped);
  box-shadow: 0 0 0 6px rgba(239, 68, 68, 0.12);
}

.app-offline-title {
  margin: 0;
  font-size: 1.25rem;
  font-weight: 700;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: var(--text);
}

.app-offline-copy {
  margin: 0;
  color: var(--muted);
  font-size: 0.92rem;
}

@media (max-width: 900px) {
  .app-shell {
    grid-template-columns: 1fr;
    grid-template-rows: auto minmax(0, 1fr) auto;
  }

  .app-sidebar {
    grid-row: auto;
    border-right: 0;
    border-bottom: 1px solid var(--panel-edge);
    flex-direction: row;
    align-items: center;
    flex-wrap: wrap;
    padding: 1rem;
  }

  .app-nav {
    display: flex;
    flex-wrap: wrap;
    gap: 0.35rem;
  }

  .app-nav-link {
    border-left: 0;
    padding: 0.7rem 0.8rem;
  }

  .app-footer {
    flex-wrap: wrap;
  }
}
</style>
