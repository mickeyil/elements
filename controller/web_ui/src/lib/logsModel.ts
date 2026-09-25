// A device log record as the controller publishes it (service.py): one per
// line a device logged, plus a 'W' note wherever records were lost.
export interface DeviceLogWireRecord {
  uid: string;
  level: string;       // 'I' | 'W' | 'E'
  text: string;
  time: number;        // controller receipt time, unix seconds
  uptime_ms: number;   // device clock when it logged the line
  seq: number;
  boot_token: number;
}

// A record as the page holds it. The id is the browser's own, unique per
// ingested record, so a row keeps its DOM node while older rows are trimmed.
export interface DeviceLogRecord extends DeviceLogWireRecord {
  id: number;
}

let nextId = 1;

// The controller and the relay keep this many; the page keeps the same.
export const DEVICE_LOG_CAP = 1000;

// A history message replaces the rows (a fresh connection, or a restarted
// controller); a live one appends. Every incoming record gets a fresh id,
// history included: nothing ties a history row to one already shown.
export function applyDeviceLogs(
  current: DeviceLogRecord[],
  records: DeviceLogWireRecord[],
  history: boolean,
  cap: number = DEVICE_LOG_CAP,
): DeviceLogRecord[] {
  const incoming = records.map((record) => ({ ...record, id: nextId++ }));
  const next = history ? incoming : current.concat(incoming);
  return next.length > cap ? next.slice(next.length - cap) : next;
}

function pad(value: number, width = 2): string {
  return String(value).padStart(width, '0');
}

// Local wall-clock HH:MM:SS.mmm.
export function formatLogTime(unixSeconds: number): string {
  const date = new Date(Math.round(unixSeconds * 1000));
  return `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
    + `.${pad(date.getMilliseconds(), 3)}`;
}

export function levelClass(level: string): 'info' | 'warn' | 'error' {
  if (level === 'W') {
    return 'warn';
  }
  if (level === 'E') {
    return 'error';
  }
  return 'info';
}

// Within a couple of pixels of the end counts as the bottom: fractional
// scroll offsets on scaled displays never land exactly on it.
export function isAtBottom(scrollTop: number, scrollHeight: number, clientHeight: number): boolean {
  return scrollHeight - scrollTop - clientHeight <= 2;
}
