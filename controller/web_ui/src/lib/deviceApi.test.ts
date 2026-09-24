import { afterEach, describe, expect, it, vi } from 'vitest';

import { ApiError, apiRequest, createSimTwin, stripHasSimTwin, updateDevice } from './deviceApi';
import { getLayout } from './layoutApi';

function mockFetch(response: { ok: boolean; status?: number; body: unknown }) {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: response.ok,
    status: response.status ?? (response.ok ? 200 : 400),
    json: () => (response.body === undefined
      ? Promise.reject(new SyntaxError('no body'))
      : Promise.resolve(response.body)),
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('apiRequest', () => {
  it('sends a JSON body with its content type', async () => {
    const fetchMock = mockFetch({ ok: true, body: { ok: true } });
    await updateDevice('sim-1', { device_uid: 'sim-2', strip_id: 'ring8', length: 8 });
    expect(fetchMock).toHaveBeenCalledWith('/api/devices/sim-1', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ device_uid: 'sim-2', strip_id: 'ring8', length: 8 }),
    });
  });

  it('throws an ApiError carrying the server message and status', async () => {
    mockFetch({ ok: false, status: 409, body: { error: 'strip ring8 is already simulated by sim-a' } });
    const error = await apiRequest('/x', { fallbackError: 'nope' }).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(409);
    expect((error as ApiError).message).toBe('strip ring8 is already simulated by sim-a');
  });

  it('falls back when the failed body is not JSON', async () => {
    mockFetch({ ok: false, status: 502, body: undefined });
    await expect(apiRequest('/x', { fallbackError: (s) => `failed (${s})` }))
      .rejects.toThrow('failed (502)');
  });

  it('resolves {} for an ok response without a body', async () => {
    mockFetch({ ok: true, body: undefined });
    await expect(apiRequest('/x', { fallbackError: 'nope' })).resolves.toEqual({});
  });
});

describe('createSimTwin', () => {
  it('POSTs the sim route of the encoded device uid', async () => {
    const fetchMock = mockFetch({ ok: true, body: { ok: true, result: {} } });
    await createSimTwin('esp-aabbccddeeff');
    expect(fetchMock).toHaveBeenCalledWith('/api/devices/esp-aabbccddeeff/sim', { method: 'POST' });
  });
});

describe('getLayout', () => {
  it('maps a 404 to null', async () => {
    mockFetch({ ok: false, status: 404, body: { error: 'layout not found' } });
    await expect(getLayout('sim-1')).resolves.toBeNull();
  });
});

describe('stripHasSimTwin', () => {
  const esp = { uid: 'esp-aabbccddeeff', configured: true, strip_id: 'ring8' };

  it('is false when only the ESP serves the strip', () => {
    expect(stripHasSimTwin([esp], 'ring8')).toBe(false);
  });

  it('is true once a configured sim serves the strip', () => {
    expect(stripHasSimTwin([esp, { uid: 'sim-ring8', configured: true, strip_id: 'ring8' }], 'ring8'))
      .toBe(true);
  });

  it('ignores sims on other strips and unconfigured sims', () => {
    expect(stripHasSimTwin([
      esp,
      { uid: 'sim-side', configured: true, strip_id: 'side' },
      { uid: 'sim-ring8', configured: false },
    ], 'ring8')).toBe(false);
  });

  it('is false without a strip id', () => {
    expect(stripHasSimTwin([{ uid: 'sim-x', configured: true }], undefined)).toBe(false);
  });
});
