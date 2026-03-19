<script setup lang="ts">
import { computed, provide } from 'vue';
import { RouterLink, RouterView } from 'vue-router';

import { relayStateKey, useRelayState } from './composables/useRelayState';

const relayState = useRelayState();
provide(relayStateKey, relayState);

const { controllerConnected, relayConnected } = relayState;

const relayStatusClass = computed(() =>
  relayConnected.value ? 'pill-online' : 'pill-offline',
);
const controllerStatusClass = computed(() =>
  controllerConnected.value ? 'pill-online' : 'pill-offline',
);
</script>

<template>
  <div class="app-shell">
    <aside class="app-sidebar">
      <div class="app-branding">
        <p class="app-eyebrow">Elements</p>
        <strong class="app-title">Web Console</strong>
      </div>

      <nav class="app-nav" aria-label="Primary">
        <RouterLink class="app-nav-link" to="/">Status</RouterLink>
        <RouterLink class="app-nav-link" to="/viewer">Viewer</RouterLink>
      </nav>

      <div class="app-statusbox">
        <span class="pill app-status-pill" :class="relayStatusClass">
          {{ relayConnected ? 'relay connected' : 'relay disconnected' }}
        </span>
        <span class="pill app-status-pill" :class="controllerStatusClass">
          {{ controllerConnected ? 'controller connected' : 'controller disconnected' }}
        </span>
      </div>
    </aside>

    <main class="app-content">
      <RouterView />
    </main>
  </div>
</template>

<style scoped>
.app-shell {
  height: 100%;
  display: grid;
  grid-template-columns: 196px minmax(0, 1fr);
}

.app-sidebar {
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 1.25rem;
  padding: 1.2rem 1rem;
  border-right: 1px solid var(--panel-edge);
  background:
    radial-gradient(circle at top, rgba(229, 156, 76, 0.08), transparent 18rem),
    rgba(12, 18, 22, 0.92);
  backdrop-filter: blur(12px);
}

.app-branding {
  display: grid;
  gap: 0.2rem;
}

.app-eyebrow {
  margin: 0;
  text-transform: uppercase;
  letter-spacing: 0.18em;
  font-size: 0.7rem;
  color: var(--accent);
}

.app-title {
  font-family: 'IBM Plex Mono', 'SFMono-Regular', monospace;
  font-size: 1rem;
}

.app-nav {
  display: grid;
  gap: 0.35rem;
}

.app-nav-link {
  color: var(--muted);
  text-decoration: none;
  border-radius: 14px;
  padding: 0.7rem 0.85rem;
  border: 1px solid transparent;
  transition: color 120ms ease, border-color 120ms ease, background 120ms ease;
}

.app-nav-link:hover {
  color: var(--text);
  border-color: var(--panel-edge);
}

.app-nav-link.router-link-active {
  color: var(--text);
  background: rgba(255, 255, 255, 0.05);
  border-color: var(--panel-edge);
}

.app-statusbox {
  margin-top: auto;
  display: grid;
  gap: 0.65rem;
}

.app-status-pill {
  justify-content: center;
  text-align: center;
}

.app-content {
  min-width: 0;
  min-height: 0;
  overflow: hidden;
}

@media (max-width: 900px) {
  .app-shell {
    grid-template-columns: 1fr;
    grid-template-rows: auto minmax(0, 1fr);
  }

  .app-sidebar {
    border-right: 0;
    border-bottom: 1px solid var(--panel-edge);
    flex-direction: row;
    align-items: center;
    flex-wrap: wrap;
  }

  .app-nav {
    display: flex;
    flex-wrap: wrap;
  }

  .app-statusbox {
    margin-top: 0;
    margin-left: auto;
    display: flex;
    flex-wrap: wrap;
    justify-content: flex-end;
  }

  .app-status-pill {
    justify-content: flex-start;
  }
}
</style>
