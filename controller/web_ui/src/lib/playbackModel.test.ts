import { describe, expect, it } from 'vitest';

import { deriveTransport, type TransportDevice } from './playbackModel';

const readyDevice: TransportDevice = {
  configured: true,
  status: 'online',
  phase: 'loaded',
  target_intent: 'ready',
};

describe('deriveTransport', () => {
  it('idle: only load is valid', () => {
    const t = deriveTransport({ state: 'idle' }, []);
    expect(t).toMatchObject({
      canLoad: true, canPlay: false, canPause: false, canResume: false, canStop: false,
    });
  });

  it('loaded with a settled device: play and stop, no pause/resume', () => {
    const t = deriveTransport({ state: 'loaded' }, [readyDevice]);
    expect(t.canPlay).toBe(true);
    expect(t.canStop).toBe(true);
    expect(t.waitingForDevices).toBe(false);
    expect(t.canPause).toBe(false);
  });

  it('playing: pause and stop only', () => {
    const t = deriveTransport({ state: 'playing' }, [readyDevice]);
    expect(t).toMatchObject({
      canPlay: false, canPause: true, canResume: false, canStop: true, canLoad: false,
    });
  });

  it('paused: resume and stop only', () => {
    const t = deriveTransport({ state: 'paused' }, [readyDevice]);
    expect(t).toMatchObject({
      canPlay: false, canPause: false, canResume: true, canStop: true, canLoad: false,
    });
  });

  it('ended: play (restart) and load are valid', () => {
    const t = deriveTransport({ state: 'ended' }, [readyDevice]);
    expect(t.canPlay).toBe(true);
    expect(t.canLoad).toBe(true);
  });

  it('loaded but a routed device has not ACKed: play blocked, waiting flagged', () => {
    const loading: TransportDevice = {
      configured: true, status: 'online', phase: 'idle', target_intent: 'ready',
    };
    const t = deriveTransport({ state: 'loaded' }, [loading]);
    expect(t.waitingForDevices).toBe(true);
    expect(t.canPlay).toBe(false);
  });

  it('a non-participating (detached) online device does not block play', () => {
    const detached: TransportDevice = {
      configured: true, status: 'online', phase: 'idle', target_intent: 'detached',
    };
    const t = deriveTransport({ state: 'loaded' }, [readyDevice, detached]);
    expect(t.waitingForDevices).toBe(false);
    expect(t.canPlay).toBe(true);
  });

  it('an offline configured device does not block play', () => {
    const offline: TransportDevice = {
      configured: true, status: 'offline', phase: 'idle', target_intent: 'ready',
    };
    const t = deriveTransport({ state: 'loaded' }, [readyDevice, offline]);
    expect(t.waitingForDevices).toBe(false);
    expect(t.canPlay).toBe(true);
  });

  it('treats a missing session as idle', () => {
    expect(deriveTransport(null, []).state).toBe('idle');
  });
});
