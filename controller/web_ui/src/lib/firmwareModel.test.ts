import { describe, expect, it } from 'vitest';

import {
  availableImageLabel,
  isUpdateRunning,
  updateAvailability,
  updateForDevice,
  updateProgressLabel,
  versionLabel,
  type FirmwareState,
} from './firmwareModel';

const ESP = 'esp-aabbccddeeff';

function firmware(overrides: Partial<FirmwareState> = {}): FirmwareState {
  return { available_version: '0.55', image_present: true, update: null, ...overrides };
}

describe('versionLabel', () => {
  it('shows the reported version', () => {
    expect(versionLabel('0.54+d')).toBe('0.54+d');
  });

  it('shows unknown before discovery and for legacy firmware', () => {
    expect(versionLabel(null)).toBe('unknown');
    expect(versionLabel(undefined)).toBe('unknown');
    expect(versionLabel('')).toBe('unknown');
  });
});

describe('availableImageLabel', () => {
  it('says nothing while the controller is offline', () => {
    expect(availableImageLabel(null)).toBeNull();
  });

  it('notes a missing image', () => {
    expect(availableImageLabel(firmware({ image_present: false }))).toBe('No firmware image built');
  });

  it('shows the image version, or unknown without a sidecar', () => {
    expect(availableImageLabel(firmware())).toBe('Firmware image 0.55');
    expect(availableImageLabel(firmware({ available_version: null }))).toBe('Firmware image unknown');
  });
});

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

describe('updateAvailability', () => {
  const esp = { isSim: false, ip: '10.0.0.9' };

  it('is enabled for an addressable ESP with an image and nothing running', () => {
    expect(updateAvailability(esp, firmware(), true)).toEqual({ enabled: true, reason: '' });
  });

  it('allows retrying after a finished or failed transfer', () => {
    expect(updateAvailability(esp, firmware({ update: { uid: ESP, phase: 'failed' } }), true).enabled)
      .toBe(true);
  });

  it('is disabled with a reason otherwise', () => {
    expect(updateAvailability(esp, firmware(), false).reason).toBe('Controller offline');
    expect(updateAvailability(esp, null, true).reason).toBe('Controller offline');
    expect(updateAvailability({ isSim: true, ip: '127.0.0.1' }, firmware(), true).enabled).toBe(false);
    expect(updateAvailability(esp, firmware({ image_present: false }), true).reason)
      .toBe('No firmware image built');
    expect(updateAvailability({ isSim: false, ip: null }, firmware(), true).enabled).toBe(false);
    expect(updateAvailability(esp, firmware({ update: { uid: ESP, phase: 'sending' } }), true).reason)
      .toBe('A firmware update is already running');
  });
});
