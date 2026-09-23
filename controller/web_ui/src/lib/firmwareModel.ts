// Pure firmware-update presentation logic for the status page: the one
// running transfer's progress text, and when "Update firmware" is offered.
// Local structural types keep it free of any composable dependency (mirrors
// playbackModel). Updates are manual only, and the controller alone decides
// which devices are due one (per-device update_available); the UI compares
// nothing and shows versions only in the offer's tooltip.

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

// The state's firmware block; null while the controller is offline. The
// versions stay on the wire for API clients; this page does not show them.
export interface FirmwareState {
  available_version?: string | null;
  image_present?: boolean;
  update?: FirmwareUpdateState | null;
}

export interface UpdateTarget {
  // The controller's per-device verdict: an image is built and flashing it
  // would change what the device runs. Absent or false means no offer.
  update_available?: boolean;
  // What the device last reported; null before any DISCOVER, '' for
  // firmware that predates reporting one.
  version?: string | null;
}

export interface UpdateAvailability {
  enabled: boolean;
  reason: string;   // why it is disabled; '' when enabled
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

// Whether "Update firmware" appears in the device's menu at all. A device
// that is current (or a sim, or unreachable) shows no item, not a dead one.
export function isUpdateOffered(target: UpdateTarget): boolean {
  return target.update_available === true;
}

// The tooltip of an offered "Update firmware": what the device runs and what
// the image would replace it with, e.g. "0.3 -> 0.4+d". Either side reads
// "unknown" when the string is missing or empty.
export function updateOfferLabel(
  target: UpdateTarget,
  firmware: FirmwareState | null | undefined,
): string {
  const current = target.version || 'unknown';
  const offered = firmware?.available_version || 'unknown';
  return `${current} -> ${offered}`;
}

// Whether an offered "Update firmware" can be clicked now, with the reason
// for its tooltip when not. The controller re-checks all of this.
export function updateAvailability(
  target: UpdateTarget,
  firmware: FirmwareState | null | undefined,
  controllerConnected: boolean,
): UpdateAvailability {
  if (!controllerConnected) {
    return { enabled: false, reason: 'Controller offline' };
  }
  if (!isUpdateOffered(target)) {
    return { enabled: false, reason: 'No firmware update available' };
  }
  if (isUpdateRunning(firmware?.update)) {
    return { enabled: false, reason: 'A firmware update is already running' };
  }
  return { enabled: true, reason: '' };
}
