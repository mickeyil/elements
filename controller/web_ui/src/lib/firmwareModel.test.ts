import { describe, expect, it } from 'vitest';

import {
  isUpdateOffered,
  isUpdateRunning,
  updateAvailability,
  updateOfferLabel,
  updateForDevice,
  updateProgressLabel,
  type FirmwareState,
} from './firmwareModel';

const ESP = 'esp-aabbccddeeff';

function firmware(overrides: Partial<FirmwareState> = {}): FirmwareState {
  return { available_version: '0.55', image_present: true, update: null, ...overrides };
}

describe('isUpdateRunning / updateForDevice', () => {
  it('treats inviting and sending as running', () => {
    expect(isUpdateRunning({ phase: 'inviting' })).toBe(true);
    expect(isUpdateRunning({ phase: 'sending' })).toBe(true);
    expect(isUpdateRunning({ phase: 'done' })).toBe(false);
    expect(isUpdateRunning({ phase: 'failed' })).toBe(false);
    expect(isUpdateRunning(null)).toBe(false);
  });

  it('matches the transfer to its device only', () => {
    const state = firmware({ update: { uid: ESP, phase: 'sending' } });
    expect(updateForDevice(state, ESP)?.phase).toBe('sending');
    expect(updateForDevice(state, 'esp-000000000001')).toBeNull();
    expect(updateForDevice(null, ESP)).toBeNull();
  });
});

describe('updateProgressLabel', () => {
  it('describes each phase', () => {
    expect(updateProgressLabel({ phase: 'inviting' })).toBe('Update: contacting device');
    expect(updateProgressLabel({ phase: 'done' })).toBe('Update sent; device rebooting');
  });

  it('shows sending progress as a percentage and KB', () => {
    expect(updateProgressLabel({ phase: 'sending', bytes_sent: 471040, total_bytes: 942080 }))
      .toBe('Update: sending 50% (460 KB / 920 KB)');
    expect(updateProgressLabel({ phase: 'sending', bytes_sent: 0, total_bytes: 0 }))
      .toBe('Update: sending');
  });

  it('carries the failure text', () => {
    expect(updateProgressLabel({ phase: 'failed', error: 'no answer from 10.0.0.9:3232' }))
      .toBe('Update failed: no answer from 10.0.0.9:3232');
    expect(updateProgressLabel({ phase: 'failed', error: null })).toBe('Update failed: unknown error');
  });
});

describe('isUpdateOffered', () => {
  it('follows the controller verdict only', () => {
    expect(isUpdateOffered({ update_available: true })).toBe(true);
    expect(isUpdateOffered({ update_available: false })).toBe(false);
    // An older controller (or a disconnected snapshot) that says nothing
    // offers nothing.
    expect(isUpdateOffered({})).toBe(false);
  });
});

describe('updateAvailability', () => {
  const due = { update_available: true };

  it('is enabled when the controller offers one and nothing is running', () => {
    expect(updateAvailability(due, firmware(), true)).toEqual({ enabled: true, reason: '' });
  });

  it('allows retrying after a finished or failed transfer', () => {
    expect(updateAvailability(due, firmware({ update: { uid: ESP, phase: 'failed' } }), true).enabled)
      .toBe(true);
    expect(updateAvailability(due, firmware({ update: { uid: ESP, phase: 'done' } }), true).enabled)
      .toBe(true);
  });

  it('is disabled while any transfer runs', () => {
    for (const phase of ['inviting', 'sending']) {
      expect(updateAvailability(due, firmware({ update: { uid: ESP, phase } }), true))
        .toEqual({ enabled: false, reason: 'A firmware update is already running' });
    }
    // Aimed at another device still blocks: the controller runs one at a time.
    expect(updateAvailability(due, firmware({ update: { uid: 'esp-000000000001', phase: 'sending' } }), true)
      .enabled).toBe(false);
  });

  it('is disabled while the controller is offline', () => {
    expect(updateAvailability(due, firmware(), false))
      .toEqual({ enabled: false, reason: 'Controller offline' });
    expect(updateAvailability(due, null, false).reason).toBe('Controller offline');
  });

  it('is disabled without the controller verdict', () => {
    expect(updateAvailability({ update_available: false }, firmware(), true).enabled).toBe(false);
    expect(updateAvailability({}, firmware(), true).enabled).toBe(false);
  });

  it('does not second-guess the verdict with the image block', () => {
    // Versions and image presence are the controller's to weigh.
    expect(updateAvailability(due, firmware({ available_version: null }), true).enabled).toBe(true);
    expect(updateAvailability(due, null, true).enabled).toBe(true);
  });
});

describe('updateOfferLabel', () => {
  it('shows the device version and the image version', () => {
    expect(updateOfferLabel({ version: '0.3' }, firmware({ available_version: '0.4+d' })))
      .toBe('0.3 -> 0.4+d');
  });

  it('reads unknown for a missing or empty side', () => {
    expect(updateOfferLabel({ version: '' }, firmware({ available_version: '0.4' })))
      .toBe('unknown -> 0.4');
    expect(updateOfferLabel({ version: null }, null)).toBe('unknown -> unknown');
  });
});
