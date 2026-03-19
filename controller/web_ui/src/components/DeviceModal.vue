<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue';

import { createDevice, updateDevice } from '../lib/deviceApi';

type DeviceType = 'sim' | 'esp32';

interface EditableDevice {
  device_uid?: string;
  device_type?: string;
  strip?: string;
  length?: number;
}

const props = defineProps<{
  mode: 'create' | 'edit';
  controllerConnected: boolean;
  device?: EditableDevice | null;
}>();

const emit = defineEmits<{
  (e: 'close'): void;
  (e: 'saved'): void;
}>();

const deviceUidRef = ref<HTMLInputElement | null>(null);
const deviceType = ref<DeviceType>((props.mode === 'edit' && props.device?.device_type === 'esp32') ? 'esp32' : 'sim');
const deviceUid = ref(props.mode === 'edit' ? props.device?.device_uid ?? '' : '');
const stripId = ref(props.mode === 'edit' ? props.device?.strip ?? '' : '');
const length = ref(props.mode === 'edit' && props.device?.length != null ? String(props.device.length) : '');
const saving = ref(false);
const error = ref('');

const isEditMode = computed(() => props.mode === 'edit');
const canSubmit = computed(() => props.controllerConnected && !saving.value);
const title = computed(() => (isEditMode.value ? 'Edit device' : 'New device'));
const submitLabel = computed(() => {
  if (saving.value) {
    return isEditMode.value ? 'Saving…' : 'Creating…';
  }
  return isEditMode.value ? 'Save changes' : 'Create device';
});
const submitTitle = computed(() => {
  if (!props.controllerConnected) {
    return 'Controller offline';
  }
  return isEditMode.value ? 'Save device changes' : 'Create device';
});

function close(): void {
  if (saving.value) {
    return;
  }
  emit('close');
}

function validate():
  | { device_type: DeviceType; device_uid: string; strip_id: string; length: number }
  | { device_uid: string; strip_id: string; length: number }
  | null {
  const trimmedUid = deviceUid.value.trim();
  const trimmedStrip = stripId.value.trim();
  const parsedLength = Number.parseInt(length.value, 10);

  if (!trimmedUid) {
    error.value = 'Device UID is required.';
    return null;
  }
  if (!trimmedStrip) {
    error.value = 'Strip ID is required.';
    return null;
  }
  if (!Number.isInteger(parsedLength) || parsedLength < 1) {
    error.value = 'Length must be a positive integer.';
    return null;
  }

  if (isEditMode.value) {
    return {
      device_uid: trimmedUid,
      strip_id: trimmedStrip,
      length: parsedLength,
    };
  }

  return {
    device_type: deviceType.value,
    device_uid: trimmedUid,
    strip_id: trimmedStrip,
    length: parsedLength,
  };
}

async function submit(): Promise<void> {
  if (!props.controllerConnected) {
    error.value = 'Controller offline.';
    return;
  }

  const payload = validate();
  if (!payload) {
    return;
  }

  try {
    saving.value = true;
    error.value = '';
    if (isEditMode.value) {
      const targetDeviceUid = props.device?.device_uid;
      if (!targetDeviceUid) {
        throw new Error('Missing device uid.');
      }
      await updateDevice(targetDeviceUid, payload);
    } else {
      await createDevice(payload);
    }
    emit('saved');
  } catch (err) {
    error.value = err instanceof Error
      ? err.message
      : isEditMode.value
        ? 'Failed to update device.'
        : 'Failed to create device.';
  } finally {
    saving.value = false;
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
  void nextTick(() => deviceUidRef.value?.focus());
});

onBeforeUnmount(() => {
  document.removeEventListener('keydown', handleKeydown);
});
</script>

<template>
  <div class="modal-backdrop" @click="handleBackdropClick">
    <section class="panel modal-card" role="dialog" aria-modal="true" aria-labelledby="device-modal-title">
      <header class="modal-head">
        <div>
          <p class="modal-eyebrow">Devices</p>
          <h2 id="device-modal-title" class="modal-title">{{ title }}</h2>
        </div>
        <button type="button" class="ghost-button modal-close" :disabled="saving" @click="close">Close</button>
      </header>

      <div class="modal-fields">
        <label class="modal-field">
          <span class="modal-label">Type</span>
          <select v-model="deviceType" :disabled="saving || isEditMode">
            <option value="sim">Simulation</option>
            <option value="esp32">ESP32</option>
          </select>
        </label>

        <label class="modal-field">
          <span class="modal-label">Device UID</span>
          <input
            ref="deviceUidRef"
            v-model="deviceUid"
            type="text"
            autocomplete="off"
            placeholder="unique device identifier"
            :disabled="saving"
          />
        </label>

        <label class="modal-field">
          <span class="modal-label">Strip ID</span>
          <input
            v-model="stripId"
            type="text"
            autocomplete="off"
            placeholder="strip name used in programs"
            :disabled="saving"
          />
        </label>

        <label class="modal-field">
          <span class="modal-label">Length</span>
          <input
            v-model="length"
            type="number"
            min="1"
            step="1"
            placeholder="number of LEDs"
            :disabled="saving"
          />
        </label>
      </div>

      <p v-if="error" class="modal-error">{{ error }}</p>
      <p v-else-if="!controllerConnected" class="modal-note">Controller offline.</p>

      <footer class="modal-actions">
        <button type="button" class="ghost-button" :disabled="saving" @click="close">Cancel</button>
        <button
          type="button"
          class="primary-button"
          :disabled="!canSubmit"
          :title="submitTitle"
          @click="submit"
        >
          {{ submitLabel }}
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
  width: min(34rem, 100%);
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

.modal-fields {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0.85rem;
}

.modal-field {
  display: grid;
  gap: 0.35rem;
}

.modal-label {
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 0.08em;
  font-size: 0.68rem;
  font-weight: 700;
}

.modal-field input,
.modal-field select {
  width: 100%;
  border: 1px solid var(--panel-edge);
  border-radius: var(--radius-tight);
  padding: 0.7rem 0.8rem;
  background: rgba(255, 255, 255, 0.04);
  color: var(--text);
}

.modal-field input:disabled,
.modal-field select:disabled {
  border-color: rgba(59, 73, 76, 0.12);
  background: rgba(0, 0, 0, 0.28);
  color: rgba(225, 226, 234, 0.68);
}

.modal-field input::placeholder {
  color: rgba(132, 147, 150, 0.82);
  font-size: 0.8em;
  font-style: italic;
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

@media (max-width: 720px) {
  .modal-fields {
    grid-template-columns: 1fr;
  }
}
</style>
