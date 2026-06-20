import { afterEach, describe, expect, it, vi } from 'vitest';

import { loadProgram, rescanPrograms, sessionCommand } from './playbackApi';

function mockFetch(response: { ok: boolean; body: unknown }) {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: response.ok,
    json: () => Promise.resolve(response.body),
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('playbackApi', () => {
  it('rescanPrograms POSTs the rescan route', async () => {
    const fetchMock = mockFetch({ ok: true, body: { ok: true, result: {} } });
    await rescanPrograms();
    expect(fetchMock).toHaveBeenCalledWith('/api/programs/rescan', { method: 'POST' });
  });

  it('loadProgram encodes the id into the load route', async () => {
    const fetchMock = mockFetch({ ok: true, body: { ok: true } });
    await loadProgram('ring16/blue');
    expect(fetchMock).toHaveBeenCalledWith('/api/programs/ring16%2Fblue/load', { method: 'POST' });
  });

  it('sessionCommand POSTs the verb route', async () => {
    const fetchMock = mockFetch({ ok: true, body: { ok: true } });
    await sessionCommand('play');
    expect(fetchMock).toHaveBeenCalledWith('/api/session/play', { method: 'POST' });
  });

  it('throws the server error message on a non-ok response', async () => {
    mockFetch({ ok: false, body: { error: "unknown program: 'x'" } });
    await expect(loadProgram('x')).rejects.toThrow("unknown program: 'x'");
  });

  it('throws a fallback message when the body has no error', async () => {
    mockFetch({ ok: false, body: {} });
    await expect(sessionCommand('stop')).rejects.toThrow('Failed to stop.');
  });
});
