import { describe, expect, it } from 'vitest';

import {
  DEVICE_LOG_CAP,
  applyDeviceLogs,
  formatLogTime,
  isAtBottom,
  levelClass,
  type DeviceLogRecord,
} from './logsModel';

function record(seq: number): DeviceLogRecord {
  return { uid: 'sim-a', level: 'I', text: `r${seq}`, time: 1, uptime_ms: seq, seq, boot_token: 1 };
}

describe('applyDeviceLogs', () => {
  it('replaces the rows on a history message', () => {
    expect(applyDeviceLogs([record(1)], [record(5), record(6)], true)).toEqual([record(5), record(6)]);
    expect(applyDeviceLogs([record(1)], [], true)).toEqual([]);
  });

  it('appends a live message', () => {
    expect(applyDeviceLogs([record(1)], [record(2)], false)).toEqual([record(1), record(2)]);
  });

  it('trims the oldest rows to the cap', () => {
    expect(applyDeviceLogs([record(1), record(2)], [record(3), record(4)], false, 3))
      .toEqual([record(2), record(3), record(4)]);
    expect(applyDeviceLogs([], [record(1), record(2), record(3)], true, 2))
      .toEqual([record(2), record(3)]);
  });

  it('caps at the controller history by default', () => {
    const many = Array.from({ length: DEVICE_LOG_CAP + 5 }, (_, i) => record(i));
    const out = applyDeviceLogs([], many, false);
    expect(out).toHaveLength(DEVICE_LOG_CAP);
    expect(out[0].seq).toBe(5);
  });
});

describe('formatLogTime', () => {
  it('formats local time with milliseconds', () => {
    const unixSeconds = new Date(2026, 0, 2, 3, 4, 5, 67).getTime() / 1000;
    expect(formatLogTime(unixSeconds)).toBe('03:04:05.067');
  });
});

describe('levelClass', () => {
  it('maps warn and error, everything else is info', () => {
    expect(levelClass('W')).toBe('warn');
    expect(levelClass('E')).toBe('error');
    expect(levelClass('I')).toBe('info');
    expect(levelClass('?')).toBe('info');
  });
});

describe('isAtBottom', () => {
  it('is true at the end or within rounding of it', () => {
    expect(isAtBottom(600, 1000, 400)).toBe(true);
    expect(isAtBottom(598.5, 1000, 400)).toBe(true);
    expect(isAtBottom(500, 1000, 400)).toBe(false);
  });
});
