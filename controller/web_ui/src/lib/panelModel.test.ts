import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  LatestValueSender,
  groupStrips,
  hsvToCss,
  stripPanelState,
  type PanelDeviceInput,
} from './panelModel';

describe('groupStrips', () => {
  it('groups configured devices by strip, ordered by strip id', () => {
    const devices: PanelDeviceInput[] = [
      { uid: 'esp-1', configured: true, status: 'online', strip_id: 'ring8', length: 8 },
      { uid: 'sim-porch', configured: true, status: 'offline', strip_id: 'porch', length: 30 },
      { uid: 'sim-ring8', configured: true, status: 'online', strip_id: 'ring8', length: 8 },
      { uid: 'esp-2', configured: false, status: 'discovered' },
    ];

    expect(groupStrips(devices)).toEqual([
      {
        stripId: 'porch',
        length: 30,
        devices: [{ uid: 'sim-porch', online: false, isSim: true }],
        panel: null,
      },
      {
        stripId: 'ring8',
        length: 8,
        devices: [
          { uid: 'esp-1', online: true, isSim: false },
          { uid: 'sim-ring8', online: true, isSim: true },
        ],
        panel: null,
      },
    ]);
  });

  it('carries the panel state of a panel-owned strip', () => {
    const devices: PanelDeviceInput[] = [{
      uid: 'sim-ring8', configured: true, status: 'online', strip_id: 'ring8', length: 8,
      owner: 'panel', panel: { mode: 'program', program_id: 'pacifica', hsv: null },
    }];

    expect(groupStrips(devices)[0].panel).toEqual(
      { mode: 'program', programId: 'pacifica', hsv: null });
  });
});

describe('stripPanelState', () => {
  it('is null while the show owns the strip', () => {
    expect(stripPanelState([{ uid: 'esp-1', owner: 'show', panel: null }])).toBeNull();
    expect(stripPanelState([])).toBeNull();
  });

  it('reads the held color from any panel-owned member', () => {
    const devices: PanelDeviceInput[] = [
      { uid: 'esp-1', owner: 'show', panel: null },
      { uid: 'sim-1', owner: 'panel', panel: { mode: 'manual', program_id: null, hsv: [120, 0.5, 1] } },
    ];

    expect(stripPanelState(devices)).toEqual(
      { mode: 'manual', programId: null, hsv: { h: 120, s: 0.5, v: 1 } });
  });
});

describe('hsvToCss', () => {
  it('converts hsv to an rgb color', () => {
    expect(hsvToCss({ h: 0, s: 1, v: 1 })).toBe('rgb(255, 0, 0)');
    expect(hsvToCss({ h: 120, s: 1, v: 0.5 })).toBe('rgb(0, 128, 0)');
    expect(hsvToCss({ h: 240, s: 0, v: 1 })).toBe('rgb(255, 255, 255)');
    expect(hsvToCss({ h: 360, s: 1, v: 1 })).toBe('rgb(255, 0, 0)');
  });
});

describe('LatestValueSender', () => {
  let sent: number[];
  let finish: Array<() => void>;
  let fail: Array<(error: Error) => void>;

  function send(value: number): Promise<void> {
    sent.push(value);
    return new Promise<void>((resolve, reject) => {
      finish.push(resolve);
      fail.push(reject);
    });
  }

  beforeEach(() => {
    vi.useFakeTimers();
    sent = [];
    finish = [];
    fail = [];
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('sends at once when idle', () => {
    new LatestValueSender(send, 50).push(1);
    expect(sent).toEqual([1]);
  });

  it('keeps one request in flight and sends only the latest value after it', async () => {
    const sender = new LatestValueSender(send, 50);
    sender.push(1);
    sender.push(2);
    sender.push(3);
    await vi.advanceTimersByTimeAsync(200);
    expect(sent).toEqual([1]);

    finish[0]();
    await vi.advanceTimersByTimeAsync(0);
    expect(sent).toEqual([1, 3]);

    finish[1]();
    await vi.advanceTimersByTimeAsync(200);
    expect(sent).toEqual([1, 3]);
  });

  it('spaces sends at least the minimum interval apart', async () => {
    const sender = new LatestValueSender(send, 50);
    sender.push(1);
    finish[0]();
    await vi.advanceTimersByTimeAsync(10);

    sender.push(2);
    expect(sent).toEqual([1]);
    await vi.advanceTimersByTimeAsync(39);
    expect(sent).toEqual([1]);
    await vi.advanceTimersByTimeAsync(1);
    expect(sent).toEqual([1, 2]);
  });

  it('goes on after a failed send', async () => {
    const sender = new LatestValueSender(send, 50);
    sender.push(1);
    sender.push(2);
    fail[0](new Error('controller unavailable'));
    await vi.advanceTimersByTimeAsync(50);
    expect(sent).toEqual([1, 2]);
  });

  it('drops the waiting value on cancel, so it is never sent', async () => {
    const sender = new LatestValueSender(send, 50);
    sender.push(1);
    sender.push(2);                              // waits on the request in flight
    sender.cancel();
    finish[0]();
    await vi.advanceTimersByTimeAsync(10);

    sender.push(3);                              // waits out the spacing
    sender.cancel();
    await vi.advanceTimersByTimeAsync(200);
    expect(sent).toEqual([1]);
  });

  it('resolves idle only after the in-flight send completes', async () => {
    const sender = new LatestValueSender(send, 50);
    await sender.idle();                         // nothing sent yet

    sender.push(1);
    let idle = false;
    void sender.idle().then(() => { idle = true; });
    await vi.advanceTimersByTimeAsync(200);
    expect(idle).toBe(false);

    finish[0]();
    await vi.advanceTimersByTimeAsync(0);
    expect(idle).toBe(true);
  });

  it('sends exactly the in-flight value for push, push, cancel, idle', async () => {
    const sender = new LatestValueSender(send, 50);
    sender.push(1);
    sender.push(2);
    sender.cancel();
    const idle = sender.idle();
    finish[0]();
    await idle;
    await vi.advanceTimersByTimeAsync(200);
    expect(sent).toEqual([1]);
  });
});
