import { describe, expect, it } from 'vitest';

import { simPowerNote, simPowerOffer, type SimProcess } from './simPowerModel';

const running: SimProcess = { running: true, pid: 42, last_exit: null };
const killed: SimProcess = { running: false, pid: null, last_exit: 'killed by SIGKILL' };

function sim(status: 'online' | 'offline' | 'discovered' = 'offline') {
  return { isSim: true, isConfigured: true, status };
}

describe('simPowerOffer', () => {
  it('offers nothing for an ESP, an unconfigured sim, or a disconnected web', () => {
    expect(simPowerOffer({ ...sim(), isSim: false }, undefined, true).action).toBeNull();
    expect(simPowerOffer({ ...sim(), isConfigured: false }, undefined, true).action).toBeNull();
    expect(simPowerOffer(sim(), undefined, false).action).toBeNull();
  });

  it('offers Power off for a running sim of ours', () => {
    expect(simPowerOffer(sim('online'), running, true)).toEqual({ action: 'off', enabled: true });
    expect(simPowerOffer(sim('offline'), running, true)).toEqual({ action: 'off', enabled: true });
  });

  it('offers Power on for a stopped or never started sim', () => {
    expect(simPowerOffer(sim(), undefined, true)).toEqual({ action: 'on', enabled: true });
    expect(simPowerOffer(sim(), killed, true)).toEqual({ action: 'on', enabled: true });
  });

  it('disables Power on for a sim running outside the app', () => {
    expect(simPowerOffer(sim('online'), undefined, true))
      .toEqual({ action: 'on', enabled: false, reason: 'Running outside the app' });
  });
});

describe('simPowerNote', () => {
  it('says Booting until the running sim is online', () => {
    expect(simPowerNote(sim('offline'), running)).toBe('Booting…');
    expect(simPowerNote(sim('online'), running)).toBeNull();
  });

  it('gives the reason of an unexpected exit', () => {
    expect(simPowerNote(sim(), killed)).toBe('Off: killed by SIGKILL');
  });

  it('says nothing after a plain power off or before a first start', () => {
    expect(simPowerNote(sim(), { running: false, pid: null, last_exit: null })).toBeNull();
    expect(simPowerNote(sim(), undefined)).toBeNull();
  });
});
