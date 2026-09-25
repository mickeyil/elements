import { describe, expect, it } from 'vitest';

import {
  DEVICE_LOG_CAP,
  applyDeviceLogs,
  formatLogTime,
  isAtBottom,
  levelClass,
  type DeviceLogRecord,
  type DeviceLogWireRecord,
} from './logsModel';

function record(seq: number): DeviceLogWireRecord {
  return { uid: 'sim-a', level: 'I', text: `r${seq}`, time: 1, uptime_ms: seq, seq, boot_token: 1 };
}

function seqs(records: DeviceLogRecord[]): number[] {
  return records.map((r) => r.seq);
}

describe('applyDeviceLogs', () => {
  it('replaces the rows on a history message', () => {
    const current = applyDeviceLogs([], [record(1)], false);
    expect(seqs(applyDeviceLogs(current, [record(5), record(6)], true))).toEqual([5, 6]);
    expect(applyDeviceLogs(current, [], true)).toEqual([]);
  });

  it('appends a live message', () => {
    const current = applyDeviceLogs([], [record(1)], false);
    const next = applyDeviceLogs(current, [record(2)], false);
    expect(seqs(next)).toEqual([1, 2]);
    expect(next[0]).toBe(current[0]);             // kept rows keep their id
    expect(next[1]).toMatchObject(record(2));
  });

  it('trims the oldest rows to the cap', () => {
    const current = applyDeviceLogs([], [record(1), record(2)], false);
    expect(seqs(applyDeviceLogs(current, [record(3), record(4)], false, 3))).toEqual([2, 3, 4]);
    expect(seqs(applyDeviceLogs([], [record(1), record(2), record(3)], true, 2))).toEqual([2, 3]);
  });

  it('gives every ingested record a fresh, strictly increasing id', () => {
    const first = applyDeviceLogs([], [record(1), record(2)], false);
    const live = applyDeviceLogs(first, [record(3)], false);
    const history = applyDeviceLogs(live, [record(1), record(2), record(3)], true);
    const ids = live.concat(history).map((r) => r.id);
    for (let i = 1; i < ids.length; i += 1) {
      expect(ids[i]).toBeGreaterThan(ids[i - 1]);
    }
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
