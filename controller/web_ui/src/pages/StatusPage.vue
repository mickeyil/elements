<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import { RouterLink } from 'vue-router';

import DeviceModal from '../components/DeviceModal.vue';
import RemoveDeviceModal from '../components/RemoveDeviceModal.vue';
import { useInjectedServerState, type SnapshotDevice } from '../composables/useServerState';

type DeviceStatus = 'online' | 'offline' | 'discovered';

interface StatusCardDevice extends SnapshotDevice {
  uid: string;
  cardStatus: DeviceStatus;
  isSim: boolean;
  isConfigured: boolean;
  hasLayout: boolean;
}

const { controllerConnected, snapshot } = useInjectedServerState();
const openMenuUid = ref<string | null>(null);
const showNewDeviceModal = ref(false);
const editingDevice = ref<StatusCardDevice | null>(null);
const removingDevice = ref<StatusCardDevice | null>(null);
const configuringUid = ref<string | null>(null);

const layouts = computed<Record<string, unknown>>(() => {
  const value = snapshot.value?.layouts;
  return value && typeof value === 'object' ? value : {};
});

const devices = computed<SnapshotDevice[]>(() => {
  const items = Array.isArray(snapshot.value?.devices) ? snapshot.value.devices : [];
  return [...items];
});

// Device edits are valid only before a program is loaded; the controller
// enforces this, and the UI mirrors it so dead controls are not offered.
const editable = computed<boolean>(() => {
  if (!controllerConnected.value || !snapshot.value) {
    return false;
  }
  const state = snapshot.value.session?.state;
  return !state || state === 'idle';
});

const editDisabledReason = computed<string>(() => {
  if (!controllerConnected.value) {
    return 'Controller offline';
  }
  if (!editable.value) {
    return 'Devices can only be edited before a program is loaded';
  }
  return '';
});

function isSimUid(uid: string): boolean {
  return uid.startsWith('sim-');
}

function hasLayout(uid: string): boolean {
  return Boolean(layouts.value[uid]);
}

const sortedDevices = computed<StatusCardDevice[]>(() => {
  const priority: Record<DeviceStatus, number> = {
    online: 0,
    offline: 1,
    discovered: 2,
  };

  return devices.value
    .filter((device): device is SnapshotDevice & { uid: string } => Boolean(device?.uid))
    .map((device) => ({
      ...device,
      uid: device.uid,
      cardStatus: (device.status as DeviceStatus) ?? 'offline',
      isSim: isSimUid(device.uid),
      isConfigured: Boolean(device.configured),
      hasLayout: hasLayout(device.uid),
    }))
    .sort((left, right) => {
      const statusDelta = priority[left.cardStatus] - priority[right.cardStatus];
      if (statusDelta !== 0) {
        return statusDelta;
      }
      return String(left.strip_id ?? left.uid).localeCompare(String(right.strip_id ?? right.uid));
    });
});

const counts = computed(() => {
  let online = 0;
  let offline = 0;
  let discovered = 0;
  for (const device of sortedDevices.value) {
    if (device.cardStatus === 'online') {
      online += 1;
    } else if (device.cardStatus === 'discovered') {
      discovered += 1;
    } else {
      offline += 1;
    }
  }
  return { online, offline, discovered };
});

function statusLabel(status: DeviceStatus): string {
  if (status === 'online') {
    return 'Online';
  }
  if (status === 'discovered') {
    return 'Discovered';
  }
  return 'Offline';
}

function statusClass(status: DeviceStatus): string {
  if (status === 'online') {
    return 'device-status-online';
  }
  if (status === 'discovered') {
    return 'device-status-discovered';
  }
  return 'device-status-offline';
}

function stripLabel(device: StatusCardDevice): string {
  if (!device.isConfigured) {
    return 'Unconfigured';
  }
  return device.strip_id ?? 'unknown_strip';
}

function layoutMenuLabel(device: StatusCardDevice): string {
  return device.hasLayout ? 'Edit layout' : 'Create layout';
}

function actionMenuLabel(device: StatusCardDevice): string {
  return `Actions for ${device.uid}`;
}

function toggleMenu(deviceUid: string): void {
  openMenuUid.value = openMenuUid.value === deviceUid ? null : deviceUid;
}

function closeMenu(): void {
  openMenuUid.value = null;
}

function openNewDeviceModal(): void {
  if (!editable.value) {
    return;
  }
  showNewDeviceModal.value = true;
}

function closeNewDeviceModal(): void {
  showNewDeviceModal.value = false;
}

function openConfigureModal(device: StatusCardDevice): void {
  if (!editable.value) {
    return;
  }
  configuringUid.value = device.uid;
  closeMenu();
}

function closeConfigureModal(): void {
  configuringUid.value = null;
}

function openEditDeviceModal(device: StatusCardDevice): void {
  if (!editable.value) {
    return;
  }
  editingDevice.value = device;
  closeMenu();
}

function closeEditDeviceModal(): void {
  editingDevice.value = null;
}

function openRemoveDeviceModal(device: StatusCardDevice): void {
  if (!editable.value) {
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
          <span v-if="counts.discovered" class="status-summary-discovered">{{ counts.discovered }} discovered</span>
          <span v-if="counts.online" class="status-summary-online">{{ counts.online }} online</span>
          <span v-if="counts.offline" class="status-summary-offline">{{ counts.offline }} offline</span>
        </div>
        <div v-else class="status-toolbar-spacer" />
        <button
          type="button"
          class="action-button status-new-device-button"
          :disabled="!editable"
          :title="editable ? 'Create a configured device' : editDisabledReason"
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
          :key="device.uid"
          class="device-card"
          :class="{ 'device-card-offline': device.cardStatus === 'offline' }"
        >
          <div class="device-card-head">
            <span
              class="device-type-badge"
              :class="device.isSim ? 'device-type-badge-sim' : 'device-type-badge-esp'"
            >
              {{ device.isSim ? 'SIM' : 'ESP' }}
            </span>
            <span class="device-status" :class="statusClass(device.cardStatus)" :title="statusLabel(device.cardStatus)">
              <span class="device-status-dot" />
              {{ statusLabel(device.cardStatus) }}
            </span>
          </div>

          <div class="device-card-body">
            <h2 class="device-strip" :title="stripLabel(device)">
              {{ stripLabel(device) }}
            </h2>
            <div class="device-bottom-row">
              <p class="device-uid mono">DEVICE: {{ device.uid }}</p>
              <div class="device-action-slot">
                <div class="device-menu" data-device-menu>
                  <button
                    type="button"
                    class="device-menu-trigger"
                    aria-label="Device actions"
                    :title="actionMenuLabel(device)"
                    @click.stop="toggleMenu(device.uid)"
                  >
                    ⋮
                  </button>
                  <div v-if="openMenuUid === device.uid" class="device-menu-list">
                    <template v-if="device.isConfigured">
                      <button
                        type="button"
                        class="device-menu-item"
                        :disabled="!editable"
                        :title="editable ? 'Edit device' : editDisabledReason"
                        @click="openEditDeviceModal(device)"
                      >
                        Edit device
                      </button>
                      <button
                        type="button"
                        class="device-menu-item device-menu-item-danger"
                        :disabled="!editable"
                        :title="editable ? 'Remove device' : editDisabledReason"
                        @click="openRemoveDeviceModal(device)"
                      >
                        Remove device
                      </button>
                      <RouterLink
                        v-if="device.isSim"
                        class="device-menu-item"
                        :to="`/layouts/${encodeURIComponent(device.uid)}`"
                        @click="closeMenu"
                      >
                        {{ layoutMenuLabel(device) }}
                      </RouterLink>
                    </template>
                    <button
                      v-else
                      type="button"
                      class="device-menu-item"
                      :disabled="!editable"
                      :title="editable ? 'Configure device' : editDisabledReason"
                      @click="openConfigureModal(device)"
                    >
                      Configure
                    </button>
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
      v-if="configuringUid"
      mode="create"
      :preset-uid="configuringUid"
      :controller-connected="controllerConnected"
      @close="closeConfigureModal"
      @saved="closeConfigureModal"
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
      v-if="removingDevice && removingDevice.uid"
      :device-uid="removingDevice.uid"
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

.status-summary-discovered {
  color: #8df0dd;
}

.status-summary-offline {
  color: var(--status-offline);
}

.device-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(228px, 1fr));
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
  align-items: center;
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

.device-status-discovered {
  color: #8df0dd;
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
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
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
