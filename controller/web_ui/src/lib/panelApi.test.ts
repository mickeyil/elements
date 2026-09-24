import { afterEach, describe, expect, it, vi } from 'vitest';

import { fillStrip, runLibraryProgram, stopStrip } from './panelApi';

function mockFetch(response: { ok: boolean; body: unknown }) {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: response.ok,
    json: () => Promise.resolve(response.body),
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

const JSON_HEADERS = { 'Content-Type': 'application/json' };

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('panelApi', () => {
  it('fillStrip POSTs the color to the fill route', async () => {
    const fetchMock = mockFetch({ ok: true, body: { ok: true } });
    await fillStrip('ring/8', { h: 120, s: 0.5, v: 1 });
    expect(fetchMock).toHaveBeenCalledWith('/api/panel/ring%2F8/fill', {
      method: 'POST',
      headers: JSON_HEADERS,
      body: JSON.stringify({ h: 120, s: 0.5, v: 1 }),
    });
  });

  it('runLibraryProgram POSTs the program id to the run route', async () => {
    const fetchMock = mockFetch({ ok: true, body: { ok: true } });
    await runLibraryProgram('ring8', 'pacifica');
    expect(fetchMock).toHaveBeenCalledWith('/api/panel/ring8/run', {
      method: 'POST',
      headers: JSON_HEADERS,
      body: JSON.stringify({ program_id: 'pacifica' }),
    });
  });

  it('stopStrip POSTs the stop route', async () => {
    const fetchMock = mockFetch({ ok: true, body: { ok: true } });
    await stopStrip('ring8');
    expect(fetchMock).toHaveBeenCalledWith('/api/panel/ring8/stop', { method: 'POST' });
  });

  it('throws the server error message on a non-ok response', async () => {
    mockFetch({ ok: false, body: { error: "unknown strip_id: 'x'" } });
    await expect(stopStrip('x')).rejects.toThrow("unknown strip_id: 'x'");
  });
});
