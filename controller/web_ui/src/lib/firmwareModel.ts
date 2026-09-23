// Pure firmware-update presentation logic for the status page: version
// labels, the one running transfer's progress text, and when "Update
// firmware" may be offered. Local structural types keep it free of any
// composable dependency (mirrors playbackModel). Updates are manual
// only; nothing here compares versions or suggests an update.

export type FirmwarePhase = 'inviting' | 'sending' | 'done' | 'failed';

// The controller's snapshot of the current (or last) transfer.
export interface FirmwareUpdateState {
  uid?: string;
  phase?: FirmwarePhase | string;
  bytes_sent?: number;
  total_bytes?: number;
  error?: string | null;
  started_at?: number | null;
  finished_at?: number | null;
}

// The state's firmware block; null while the controller is offline.
export interface FirmwareState {
  available_version?: string | null;
  image_present?: boolean;
  update?: FirmwareUpdateState | null;
}

export interface UpdateTarget {
  isSim: boolean;
  ip?: string | null;
}

export interface UpdateAvailability {
  enabled: boolean;
  reason: string;   // why it is disabled; '' when enabled
}

// A device's reported version. Null means no DISCOVER heard yet; '' is
// firmware that predates reporting one. Both read as unknown.
export function versionLabel(version: string | null | undefined): string {
  return version ? version : 'unknown';
}

// The header note for the image an update would install, or null when
// there is nothing to say (controller offline).
export function availableImageLabel(firmware: FirmwareState | null | undefined): string | null {
  if (!firmware) {
    return null;
  }
  if (!firmware.image_present) {
    return 'No firmware image built';
  }
  return `Firmware image ${versionLabel(firmware.available_version)}`;
}

export function isUpdateRunning(update: FirmwareUpdateState | null | undefined): boolean {
  return update?.phase === 'inviting' || update?.phase === 'sending';
}

// The transfer to show on uid's card, if it is the one the controller
// is (or was last) running.
export function updateForDevice(
  firmware: FirmwareState | null | undefined,
  uid: string,
): FirmwareUpdateState | null {
  const update = firmware?.update;
  return update && update.uid === uid ? update : null;
}

function kib(bytes: number): string {
  return `${Math.round(bytes / 1024)} KB`;
}

export function updateProgressLabel(update: FirmwareUpdateState): string {
  if (update.phase === 'inviting') {
    return 'Update: contacting device';
  }
  if (update.phase === 'sending') {
    const sent = update.bytes_sent ?? 0;
    const total = update.total_bytes ?? 0;
    if (total <= 0) {
      return 'Update: sending';
    }
    const percent = Math.floor((100 * sent) / total);
    return `Update: sending ${percent}% (${kib(sent)} / ${kib(total)})`;
  }
  if (update.phase === 'done') {
    return 'Update sent; device rebooting';
  }
  if (update.phase === 'failed') {
    return `Update failed: ${update.error || 'unknown error'}`;
  }
  return '';
}

// Whether "Update firmware" can be offered for a device. The controller
// re-checks all of this; the UI mirrors it so dead controls are not offered.
export function updateAvailability(
  target: UpdateTarget,
  firmware: FirmwareState | null | undefined,
  controllerConnected: boolean,
): UpdateAvailability {
  if (!controllerConnected || !firmware) {
    return { enabled: false, reason: 'Controller offline' };
  }
  if (target.isSim) {
    return { enabled: false, reason: 'Sim devices take no firmware updates' };
  }
  if (!firmware.image_present) {
    return { enabled: false, reason: 'No firmware image built' };
  }
  if (!target.ip) {
    return { enabled: false, reason: 'Device address unknown (no DISCOVER heard)' };
  }
  if (isUpdateRunning(firmware.update)) {
    return { enabled: false, reason: 'A firmware update is already running' };
  }
  return { enabled: true, reason: '' };
}
