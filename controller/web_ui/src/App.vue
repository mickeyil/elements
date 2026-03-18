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
    <header class="app-topbar">
      <div class="app-topbar-inner">
        <div class="app-branding">
          <p class="app-eyebrow">Elements</p>
          <strong class="app-title">Web Console</strong>
        </div>

        <nav class="app-nav" aria-label="Primary">
          <RouterLink class="app-nav-link" to="/">Status</RouterLink>
          <RouterLink class="app-nav-link" to="/viewer">Viewer</RouterLink>
        </nav>

        <div class="statusbox">
          <span class="pill" :class="relayStatusClass">
            {{ relayConnected ? 'relay connected' : 'relay disconnected' }}
          </span>
          <span class="pill" :class="controllerStatusClass">
            {{ controllerConnected ? 'controller connected' : 'controller disconnected' }}
          </span>
        </div>
      </div>
    </header>

    <RouterView />
  </div>
</template>

<style scoped>
.app-shell {
  min-height: 100vh;
}

.app-topbar {
  position: sticky;
  top: 0;
  z-index: 10;
  backdrop-filter: blur(12px);
  background: rgba(12, 18, 22, 0.82);
  border-bottom: 1px solid var(--panel-edge);
}

.app-topbar-inner {
  width: min(1200px, calc(100vw - 2rem));
  margin: 0 auto;
  padding: 0.9rem 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
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
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.app-nav-link {
  color: var(--muted);
  text-decoration: none;
  border-radius: 999px;
  padding: 0.45rem 0.9rem;
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

.statusbox {
  display: flex;
  gap: 0.75rem;
  flex-wrap: wrap;
  justify-content: flex-end;
}

@media (max-width: 900px) {
  .app-topbar-inner {
    flex-wrap: wrap;
  }

  .statusbox {
    width: 100%;
    justify-content: flex-start;
  }
}
</style>
