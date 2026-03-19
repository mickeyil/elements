<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';

import { removeDevice } from '../lib/deviceApi';

const props = defineProps<{
  controllerConnected: boolean;
  deviceUid: string;
}>();

const emit = defineEmits<{
  (e: 'close'): void;
  (e: 'removed'): void;
}>();

const removing = ref(false);
const error = ref('');

const canRemove = computed(() => props.controllerConnected && !removing.value);

function close(): void {
  if (removing.value) {
    return;
  }
  emit('close');
}

async function confirmRemove(): Promise<void> {
  if (!props.controllerConnected) {
    error.value = 'Controller offline.';
    return;
  }

  try {
    removing.value = true;
    error.value = '';
    await removeDevice(props.deviceUid);
    emit('removed');
  } catch (err) {
    error.value = err instanceof Error ? err.message : 'Failed to remove device.';
  } finally {
    removing.value = false;
  }
}

function handleBackdropClick(event: MouseEvent): void {
  if (event.target === event.currentTarget) {
    close();
  }
}

function handleKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape') {
    event.preventDefault();
    close();
  }
}

onMounted(() => {
  document.addEventListener('keydown', handleKeydown);
});

onBeforeUnmount(() => {
  document.removeEventListener('keydown', handleKeydown);
});
</script>

<template>
  <div class="modal-backdrop" @click="handleBackdropClick">
    <section class="panel modal-card" role="dialog" aria-modal="true" aria-labelledby="remove-device-title">
      <header class="modal-head">
        <div>
          <p class="modal-eyebrow">Devices</p>
          <h2 id="remove-device-title" class="modal-title">Remove device</h2>
        </div>
        <button type="button" class="ghost-button modal-close" :disabled="removing" @click="close">Close</button>
      </header>

      <div class="modal-copy">
        <p class="modal-copy-line">Remove device <span class="mono modal-device-uid">{{ deviceUid }}</span>?</p>
        <p class="modal-copy-line modal-copy-muted">This removes the configured device from the controller.</p>
      </div>

      <p v-if="error" class="modal-error">{{ error }}</p>
      <p v-else-if="!controllerConnected" class="modal-note">Controller offline.</p>

      <footer class="modal-actions">
        <button type="button" class="ghost-button" :disabled="removing" @click="close">Cancel</button>
        <button
          type="button"
          class="danger-button"
          :disabled="!canRemove"
          :title="controllerConnected ? 'Remove device' : 'Controller offline'"
          @click="confirmRemove"
        >
          {{ removing ? 'Removing…' : 'Remove device' }}
        </button>
      </footer>
    </section>
  </div>
</template>

<style scoped>
.modal-backdrop {
  position: fixed;
  inset: 0;
  z-index: 900;
  display: grid;
  place-items: center;
  padding: 1.5rem;
  background: rgba(8, 10, 14, 0.72);
}

.modal-card {
  width: min(28rem, 100%);
  display: grid;
  gap: 1rem;
  padding: 1.2rem;
}

.modal-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 1rem;
}

.modal-eyebrow {
  margin: 0 0 0.25rem;
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 0.16em;
  font-size: 0.62rem;
  font-weight: 700;
}

.modal-title {
  margin: 0;
  font-size: 1.2rem;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.modal-close {
  flex: 0 0 auto;
}

.modal-copy {
  display: grid;
  gap: 0.4rem;
}

.modal-copy-line {
  margin: 0;
}

.modal-copy-muted {
  color: var(--muted);
}

.modal-device-uid {
  font-size: 0.95em;
}

.modal-error,
.modal-note {
  margin: 0;
  padding: 0.75rem 0.9rem;
  border-radius: var(--radius-panel);
}

.modal-error {
  border: 1px solid rgba(239, 68, 68, 0.35);
  background: var(--status-dropped-soft);
  color: #ffdede;
}

.modal-note {
  border: 1px solid rgba(255, 255, 255, 0.06);
  background: rgba(255, 255, 255, 0.025);
  color: var(--muted);
}

.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 0.75rem;
}

.danger-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 0.45rem;
  border-radius: var(--radius-panel);
  padding: 0.58rem 0.95rem;
  text-decoration: none;
  cursor: pointer;
  transition: border-color 120ms ease, background 120ms ease, color 120ms ease;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  font-size: 0.77rem;
  font-weight: 700;
  border: 1px solid rgba(239, 68, 68, 0.35);
  background: rgba(153, 27, 27, 0.22);
  color: #ffdede;
}

.danger-button:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}
</style>
