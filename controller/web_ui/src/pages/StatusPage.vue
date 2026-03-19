<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import { RouterLink } from 'vue-router';

import DeviceModal from '../components/DeviceModal.vue';
import RemoveDeviceModal from '../components/RemoveDeviceModal.vue';
import { useInjectedRelayState, type SnapshotDevice } from '../composables/useRelayState';

type DeviceStatus = 'online' | 'dropped' | 'offline';

interface StatusCardDevice extends SnapshotDevice {
  status: DeviceStatus;
  hasLayout: boolean;
}

const { controllerConnected, snapshot } = useInjectedRelayState();
const openMenuUid = ref<string | null>(null);
const showNewDeviceModal = ref(false);
const editingDevice = ref<StatusCardDevice | null>(null);
const removingDevice = ref<StatusCardDevice | null>(null);

const layouts = computed<Record<string, unknown>>(() => {
  const value = snapshot.value?.layouts;
  return value && typeof value === 'object' ? value : {};
});

const devices = computed<SnapshotDevice[]>(() => {
  const items = Array.isArray(snapshot.value?.devices) ? snapshot.value.devices : [];
  return [...items];
});

function deriveStatus(device: SnapshotDevice): DeviceStatus {
  if (device.connected) {
    return 'online';
  }
  if (device.last_seen != null) {
    return 'dropped';
  }
  return 'offline';
}

function hasLayout(deviceUid: string | undefined): boolean {
  return Boolean(deviceUid && layouts.value[deviceUid]);
}

const sortedDevices = computed<StatusCardDevice[]>(() => {
  const priority: Record<DeviceStatus, number> = {
    dropped: 0,
    online: 1,
    offline: 2,
  };

  return devices.value
    .map((device) => ({
      ...device,
      status: deriveStatus(device),
      hasLayout: hasLayout(device.device_uid),
    }))
    .sort((left, right) => {
      const statusDelta = priority[left.status] - priority[right.status];
      if (statusDelta !== 0) {
        return statusDelta;
      }
      return String(left.strip ?? '').localeCompare(String(right.strip ?? ''));
    });
});

const counts = computed(() => {
  let online = 0;
  let dropped = 0;
  let offline = 0;
  for (const device of sortedDevices.value) {
    if (device.status === 'online') {
      online += 1;
    } else if (device.status === 'dropped') {
      dropped += 1;
    } else {
      offline += 1;
    }
  }
  return { online, dropped, offline };
});

function statusLabel(status: DeviceStatus): string {
  if (status === 'online') {
    return 'Online';
  }
  if (status === 'dropped') {
    return 'Dropped';
  }
  return 'Offline';
}

function statusClass(status: DeviceStatus): string {
  if (status === 'online') {
    return 'device-status-online';
  }
  if (status === 'dropped') {
    return 'device-status-dropped';
  }
  return 'device-status-offline';
}

function statusTitle(device: StatusCardDevice): string {
  if (device.status !== 'dropped' || device.last_seen == null) {
    return statusLabel(device.status);
  }
  return `Dropped · last seen ${new Date(device.last_seen * 1000).toLocaleString()}`;
}

function layoutMenuLabel(device: StatusCardDevice): string {
  return device.hasLayout ? 'Edit layout' : 'Create layout';
}

function actionMenuLabel(device: StatusCardDevice): string {
  return `Actions for ${device.device_uid ?? device.strip ?? 'device'}`;
}

function toggleMenu(deviceUid: string): void {
  openMenuUid.value = openMenuUid.value === deviceUid ? null : deviceUid;
}

function closeMenu(): void {
  openMenuUid.value = null;
}

function openNewDeviceModal(): void {
  if (!controllerConnected.value) {
    return;
  }
  showNewDeviceModal.value = true;
}

function closeNewDeviceModal(): void {
  showNewDeviceModal.value = false;
}

function openEditDeviceModal(device: StatusCardDevice): void {
  if (!device.device_uid || !controllerConnected.value) {
    return;
  }
  editingDevice.value = device;
  closeMenu();
}

function closeEditDeviceModal(): void {
  editingDevice.value = null;
}

function openRemoveDeviceModal(device: StatusCardDevice): void {
  if (!device.device_uid || !controllerConnected.value) {
    return;
  }
  removingDevice.value = device;
  closeMenu();
}

function closeRemoveDeviceModal(): void {
  removingDevice.value = null;
}

function handleDocumentPointer(event: MouseEvent): void {
  const target = event.target;
  if (!(target instanceof Element) || !target.closest('[data-device-menu]')) {
    closeMenu();
  }
}

function handleKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape') {
    closeMenu();
  }
}

onMounted(() => {
  document.addEventListener('click', handleDocumentPointer);
  document.addEventListener('keydown', handleKeydown);
});

onBeforeUnmount(() => {
  document.removeEventListener('click', handleDocumentPointer);
  document.removeEventListener('keydown', handleKeydown);
});
</script>

<template>
  <main class="status-page page-scroll">
    <div class="status-frame">
      <div class="status-toolbar">
        <div v-if="snapshot && devices.length" class="status-inline-summary" aria-label="Device status summary">
          <span v-if="counts.dropped" class="status-summary-dropped">{{ counts.dropped }} dropped</span>
          <span v-if="counts.online" class="status-summary-online">{{ counts.online }} online</span>
          <span v-if="counts.offline" class="status-summary-offline">{{ counts.offline }} offline</span>
        </div>
        <div v-else class="status-toolbar-spacer" />
        <button
          type="button"
          class="action-button status-new-device-button"
          :disabled="!controllerConnected"
          :title="controllerConnected ? 'Create a configured device' : 'Controller offline'"
          @click="openNewDeviceModal"
        >
          <span class="status-new-device-plus" aria-hidden="true">+</span>
          <span>New device</span>
        </button>
      </div>

      <div v-if="!snapshot" class="panel">
        <div class="empty-state">
          <h2>Loading devices</h2>
          <p>Device data is not available yet.</p>
        </div>
      </div>

      <div v-else-if="!sortedDevices.length" class="panel">
        <div class="empty-state">
          <h2>No devices</h2>
        </div>
      </div>

      <section v-else class="device-grid">
        <article
          v-for="device in sortedDevices"
          :key="device.device_uid"
          class="device-card"
          :class="{ 'device-card-offline': device.status === 'offline' }"
        >
          <div class="device-card-head">
            <span
              class="device-type-badge"
              :class="device.device_type === 'sim' ? 'device-type-badge-sim' : 'device-type-badge-esp'"
            >
              {{ device.device_type === 'sim' ? 'SIM' : 'ESP' }}
            </span>
            <span class="device-status" :class="statusClass(device.status)" :title="statusTitle(device)">
              <span class="device-status-dot" />
              {{ statusLabel(device.status) }}
            </span>
          </div>

          <div class="device-card-body">
            <h2 class="device-strip" :title="device.strip ?? device.device_uid ?? 'unknown device'">
              {{ device.strip ?? 'unknown_strip' }}
            </h2>
            <div class="device-bottom-row">
              <p class="device-uid mono">DEVICE: {{ device.device_uid ?? 'unknown-device' }}</p>
              <div class="device-action-slot">
                <div v-if="device.device_uid" class="device-menu" data-device-menu>
                  <button
                    type="button"
                    class="device-menu-trigger"
                    aria-label="Device actions"
                    :title="actionMenuLabel(device)"
                    @click.stop="toggleMenu(device.device_uid)"
                  >
                    ⋮
                  </button>
                  <div v-if="openMenuUid === device.device_uid" class="device-menu-list">
                    <button
                      type="button"
                      class="device-menu-item"
                      :disabled="!controllerConnected"
                      :title="controllerConnected ? 'Edit device' : 'Controller offline'"
                      @click="openEditDeviceModal(device)"
                    >
                      Edit device
                    </button>
                    <button
                      type="button"
                      class="device-menu-item device-menu-item-danger"
                      :disabled="!controllerConnected"
                      :title="controllerConnected ? 'Remove device' : 'Controller offline'"
                      @click="openRemoveDeviceModal(device)"
                    >
                      Remove device
                    </button>
                    <RouterLink
                      v-if="device.device_type === 'sim'"
                      class="device-menu-item"
                      :to="`/layouts/${encodeURIComponent(device.device_uid)}`"
                      @click="closeMenu"
                    >
                      {{ layoutMenuLabel(device) }}
                    </RouterLink>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </article>
      </section>
    </div>

    <DeviceModal
      v-if="showNewDeviceModal"
      mode="create"
      :controller-connected="controllerConnected"
      @close="closeNewDeviceModal"
      @saved="closeNewDeviceModal"
    />
    <DeviceModal
      v-if="editingDevice"
      mode="edit"
      :device="editingDevice"
      :controller-connected="controllerConnected"
      @close="closeEditDeviceModal"
      @saved="closeEditDeviceModal"
    />
    <RemoveDeviceModal
      v-if="removingDevice && removingDevice.device_uid"
      :device-uid="removingDevice.device_uid"
      :controller-connected="controllerConnected"
      @close="closeRemoveDeviceModal"
      @removed="closeRemoveDeviceModal"
    />
  </main>
</template>

<style scoped>
.status-page {
  min-height: 0;
  padding: 0.75rem 0.75rem 0;
}

.status-frame {
  display: grid;
  gap: 0.75rem;
  align-content: start;
}

.status-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  min-height: 3rem;
  padding: 0 1rem;
  background: #131313;
  border-bottom: 1px solid rgba(59, 73, 76, 0.15);
}

.status-inline-summary {
  display: inline-flex;
  align-items: center;
  gap: 1rem;
  flex-wrap: wrap;
  text-transform: uppercase;
  letter-spacing: 0.14em;
  font-size: 0.64rem;
  font-weight: 700;
}

.status-toolbar-spacer {
  flex: 1 1 auto;
}

.status-new-device-button {
  flex: 0 0 auto;
}

.status-new-device-plus {
  font-size: 1rem;
  line-height: 1;
}

.status-summary-online {
  color: var(--status-online);
}

.status-summary-dropped {
  color: var(--status-dropped);
}

.status-summary-offline {
  color: var(--status-offline);
}

.device-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(190px, 1fr));
  gap: 1px;
  align-content: start;
}

.device-card {
  display: grid;
  gap: 0.7rem;
  padding: 0.7rem 0.75rem;
  background: var(--surface-raised);
  min-width: 0;
  min-height: 8.25rem;
  transition: background 120ms ease;
}

.device-card:hover {
  background: var(--surface-strong);
}

.device-card-offline {
  background: var(--surface-recessed);
  opacity: 0.52;
}

.device-card-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 0.75rem;
}

.device-type-badge {
  display: inline-flex;
  align-items: center;
  padding: 0.12rem 0.32rem;
  font-size: 0.55rem;
  font-weight: 700;
  letter-spacing: 0.14em;
  text-transform: uppercase;
}

.device-type-badge-esp {
  background: rgba(96, 126, 187, 0.24);
  color: #bdd0ff;
}

.device-type-badge-sim {
  background: rgba(58, 168, 148, 0.24);
  color: #8df0dd;
}

.device-status {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  font-size: 0.54rem;
  font-weight: 700;
  letter-spacing: 0.14em;
  text-transform: uppercase;
}

.device-status-dot {
  width: 0.48rem;
  height: 0.48rem;
  border-radius: 999px;
  background: currentColor;
  flex: 0 0 auto;
}

.device-status-online {
  color: var(--status-online);
}

.device-status-dropped {
  color: var(--status-dropped);
}

.device-status-offline {
  color: var(--status-offline);
}

.device-card-body {
  display: grid;
  gap: 0.45rem;
  min-width: 0;
}

.device-strip {
  margin: 0;
  color: var(--text);
  font-size: 1.05rem;
  font-weight: 700;
  text-transform: uppercase;
  line-height: 1.05;
  letter-spacing: 0.02em;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.device-bottom-row {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 0.5rem;
}

.device-uid {
  margin: 0;
  color: var(--muted);
  font-size: 0.68rem;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  min-width: 0;
}

.device-action-slot {
  min-width: 1.5rem;
  display: flex;
  justify-content: flex-end;
}

.device-menu {
  position: relative;
  display: inline-flex;
}

.device-menu-trigger {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 1.35rem;
  height: 1.35rem;
  border: 0;
  background: transparent;
  color: var(--muted);
  cursor: pointer;
  font-size: 1rem;
  line-height: 1;
  padding: 0;
}

.device-menu-trigger:hover,
.device-menu-trigger:focus-visible {
  color: var(--text);
  outline: none;
}

.device-menu-list {
  position: absolute;
  right: 0;
  top: calc(100% + 0.25rem);
  min-width: 9rem;
  padding: 0.25rem;
  background: #111319;
  border: 1px solid rgba(59, 73, 76, 0.3);
  z-index: 10;
}

.device-menu-item {
  display: block;
  width: 100%;
  border: 0;
  background: transparent;
  text-align: left;
  padding: 0.5rem 0.6rem;
  color: var(--text);
  text-decoration: none;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  font-size: 0.64rem;
  font-weight: 700;
  white-space: nowrap;
  cursor: pointer;
}

.device-menu-item:hover {
  background: #1c1b1b;
}

.device-menu-item:disabled {
  color: var(--muted);
  cursor: not-allowed;
}

.device-menu-item:disabled:hover {
  background: transparent;
}

.device-menu-item-danger {
  color: #ffdede;
}

@media (max-width: 900px) {
  .status-page {
    padding: 0.5rem 0.5rem 0;
  }

  .status-toolbar {
    padding-inline: 0.75rem;
  }
}
</style>
