<script setup lang="ts">
import { computed, ref, watch } from 'vue';

import { useInjectedServerState, type SnapshotProgram } from '../composables/useServerState';
import { deriveTransport } from '../lib/playbackModel';
import { loadProgram, rescanPrograms, sessionCommand, type SessionVerb } from '../lib/playbackApi';

const { snapshot, session, controllerConnected } = useInjectedServerState();

const programs = computed<SnapshotProgram[]>(() => snapshot.value?.programs ?? []);
const devices = computed(() => snapshot.value?.devices ?? []);
const transport = computed(() => deriveTransport(session.value, devices.value));

const selectedProgramId = ref('');

// Default the picker to the loaded program, else the first listed; re-resolve if
// the current selection disappears (e.g. after a rescan removes a file).
watch(
  [programs, () => session.value?.program_id],
  () => {
    const known = programs.value.some((p) => p.program_id === selectedProgramId.value);
    if (!known) {
      selectedProgramId.value =
        session.value?.program_id ?? programs.value[0]?.program_id ?? '';
    }
  },
  { immediate: true },
);

const selectedProgram = computed<SnapshotProgram | null>(
  () => programs.value.find((p) => p.program_id === selectedProgramId.value) ?? null,
);
const selectedErrored = computed(() => Boolean(selectedProgram.value?.error));

const pending = ref(false);
const error = ref('');

async function run(action: () => Promise<unknown>): Promise<void> {
  pending.value = true;
  error.value = '';
  try {
    await action();
  } catch (err) {
    error.value = err instanceof Error ? err.message : 'Command failed.';
  } finally {
    pending.value = false;
  }
}

const base = computed(() => controllerConnected.value && !pending.value);
const canLoad = computed(
  () => base.value && transport.value.canLoad && Boolean(selectedProgramId.value) && !selectedErrored.value,
);
const canPlay = computed(() => base.value && transport.value.canPlay);
const canPause = computed(() => base.value && transport.value.canPause);
const canResume = computed(() => base.value && transport.value.canResume);
const canStop = computed(() => base.value && transport.value.canStop);

function onLoad(): void {
  void run(() => loadProgram(selectedProgramId.value));
}

function onRescan(): void {
  void run(() => rescanPrograms());
}

function onTransport(verb: SessionVerb): void {
  void run(() => sessionCommand(verb));
}

const stateLabel = computed(() => transport.value.state);
const timeReadout = computed(() => {
  const t = Number(session.value?.current_t_rel ?? 0);
  const duration = Number(session.value?.duration ?? 0);
  if (!duration) {
    return '';
  }
  return `${t.toFixed(1)}s / ${duration.toFixed(1)}s`;
});

function programLabel(program: SnapshotProgram): string {
  return program.error ? `${program.program_id} (error)` : program.program_id;
}
</script>

<template>
  <section class="panel control-panel">
    <div class="control-row">
      <select
        v-model="selectedProgramId"
        class="program-select"
        :disabled="!controllerConnected || pending || !programs.length"
        aria-label="Program"
      >
        <option v-if="!programs.length" value="">No programs</option>
        <option
          v-for="program in programs"
          :key="program.program_id"
          :value="program.program_id"
          :disabled="Boolean(program.error)"
          :title="program.error ?? ''"
        >
          {{ programLabel(program) }}
        </option>
      </select>
      <button type="button" class="ghost-button" :disabled="!canLoad" @click="onLoad">Load</button>
      <button type="button" class="ghost-button" :disabled="!base" @click="onRescan">Rescan</button>
    </div>

    <div class="control-row">
      <button type="button" class="ghost-button" :disabled="!canPlay" @click="onTransport('play')">Play</button>
      <button type="button" class="ghost-button" :disabled="!canPause" @click="onTransport('pause')">Pause</button>
      <button type="button" class="ghost-button" :disabled="!canResume" @click="onTransport('resume')">Resume</button>
      <button type="button" class="ghost-button" :disabled="!canStop" @click="onTransport('stop')">Stop</button>
    </div>

    <div class="control-status">
      <span class="state-pill">{{ stateLabel }}</span>
      <span v-if="timeReadout" class="state-time">{{ timeReadout }}</span>
      <span v-if="transport.waitingForDevices" class="state-hint">Waiting for devices to load…</span>
      <span v-if="!controllerConnected" class="state-hint">Controller offline</span>
    </div>

    <p v-if="error" class="control-error">{{ error }}</p>
  </section>
</template>

<style scoped>
.control-panel {
  padding: 1rem;
  display: grid;
  gap: 0.75rem;
  margin-bottom: 1.5rem;
}

.control-row {
  display: flex;
  gap: 0.5rem;
  flex-wrap: wrap;
  align-items: center;
}

.program-select {
  min-width: 14rem;
  padding: 0.45rem 0.6rem;
  border-radius: var(--radius-panel);
  border: 1px solid var(--panel-edge);
  background: rgba(255, 255, 255, 0.03);
  color: inherit;
  font-family: var(--font-mono);
}

.program-select:disabled {
  opacity: 0.55;
}

.control-status {
  display: flex;
  gap: 0.75rem;
  align-items: center;
  flex-wrap: wrap;
}

.state-pill {
  font-family: var(--font-mono);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  font-size: 0.82rem;
  padding: 0.2rem 0.6rem;
  border-radius: 999px;
  border: 1px solid var(--panel-edge);
  background: rgba(255, 255, 255, 0.04);
}

.state-time {
  color: var(--muted);
  font-size: 0.85rem;
}

.state-hint {
  color: var(--muted);
  font-size: 0.82rem;
}

.control-error {
  margin: 0;
  color: #ff8a8a;
  font-size: 0.85rem;
}
</style>
