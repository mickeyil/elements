// Label for a device's clock sync state, from the controller's
// clock_synced / clock_skew_ms fields (null until the device answers a
// status query after attaching). Skew is how far the device clock had
// wandered at its last sync round; positive means it was running ahead.
export function clockLabel(synced: boolean | null | undefined,
                           skewMs: number | null | undefined): string {
  if (synced === false) {
    return 'syncing';
  }
  if (synced !== true || typeof skewMs !== 'number' || !Number.isFinite(skewMs)) {
    return '—';
  }
  const text = Math.abs(skewMs).toFixed(1);
  if (text === '0.0') {
    return '0.0 ms';
  }
  return `${skewMs > 0 ? '+' : '-'}${text} ms`;
}

// The ACK status a device sends for a synced program while it holds no clock
// lease (wire.ACK_UNSYNCED).
const ACK_UNSYNCED = 6;

// Label for a device's latest refused command, from the controller's
// last_refusal ([command, ack status]; null once a command is accepted).
export function refusalLabel(lastRefusal: [string, number] | null | undefined): string {
  if (!lastRefusal) {
    return '';
  }
  const [command, status] = lastRefusal;
  if (status === ACK_UNSYNCED && (command === 'start' || command === 'resume')) {
    return 'waiting for clock sync';
  }
  return `refused ${command} (status ${status})`;
}
