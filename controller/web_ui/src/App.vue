<script setup lang="ts">
import { provide } from 'vue';
import { RouterLink, RouterView } from 'vue-router';

import IconDevices from './components/icons/IconDevices.vue';
import IconError from './components/icons/IconError.vue';
import IconSensors from './components/icons/IconSensors.vue';
import IconVisibility from './components/icons/IconVisibility.vue';
import { relayStateKey, useRelayState } from './composables/useRelayState';

const relayState = useRelayState();
provide(relayStateKey, relayState);

const { controllerConnected, relayConnected } = relayState;
</script>

<template>
  <div class="app-shell">
    <aside class="app-sidebar">
      <div class="app-branding">
        <strong class="app-title">ELEM</strong>
        <p class="app-eyebrow">Web Console</p>
      </div>

      <nav class="app-nav" aria-label="Primary">
        <RouterLink class="app-nav-link" to="/">
          <IconDevices />
          <span>Status</span>
        </RouterLink>
        <RouterLink class="app-nav-link" to="/viewer">
          <IconVisibility />
          <span>Viewer</span>
        </RouterLink>
      </nav>
    </aside>

    <main class="app-content">
      <RouterView />
    </main>

    <footer class="app-footer">
      <div class="app-footer-status" :class="relayConnected ? 'app-footer-status-online' : 'app-footer-status-offline'">
        <IconSensors v-if="relayConnected" />
        <IconError v-else />
        <span class="app-footer-label">Relay</span>
        <strong>{{ relayConnected ? 'CONNECTED' : 'DISCONNECTED' }}</strong>
      </div>
      <div
        class="app-footer-status"
        :class="controllerConnected ? 'app-footer-status-online' : 'app-footer-status-offline'"
      >
        <IconSensors v-if="controllerConnected" />
        <IconError v-else />
        <span class="app-footer-label">Controller</span>
        <strong>{{ controllerConnected ? 'CONNECTED' : 'DISCONNECTED' }}</strong>
      </div>
    </footer>
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
  font-size: 2rem;
  line-height: 1;
  letter-spacing: -0.04em;
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
  gap: 1.25rem;
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

.app-footer-status-online {
  color: var(--status-online);
}

.app-footer-status-offline {
  color: var(--status-dropped);
}

.app-footer-label {
  color: var(--muted);
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
