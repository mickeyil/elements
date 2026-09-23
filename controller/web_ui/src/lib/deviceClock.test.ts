import { describe, expect, it } from 'vitest';

import { clockLabel } from './deviceClock';

describe('clockLabel', () => {
  it('shows a dash before the device has answered', () => {
    expect(clockLabel(null, null)).toBe('—');
    expect(clockLabel(undefined, undefined)).toBe('—');
  });

  it('shows syncing while the device holds no clock lease', () => {
    expect(clockLabel(false, 0)).toBe('syncing');
  });

  it('shows signed skew in milliseconds to one decimal', () => {
    expect(clockLabel(true, 0.42)).toBe('+0.4 ms');
    expect(clockLabel(true, -1.26)).toBe('-1.3 ms');
  });

  it('shows zero without a sign, including values that round to zero', () => {
    expect(clockLabel(true, 0)).toBe('0.0 ms');
    expect(clockLabel(true, -0.04)).toBe('0.0 ms');
  });
});
