export interface CreateDevicePayload {
  device_uid: string;
  strip_id: string;
  length: number;
}

export interface UpdateDevicePayload {
  device_uid: string;
  strip_id: string;
  length: number;
}

// An API call that failed: the server's error message, or the fallback when
// the body carries none, with the HTTP status for callers that branch on it.
export class ApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

export interface ApiRequestOptions {
  method?: string;
  // Sent as JSON when present.
  body?: unknown;
  // The error message when a failed response carries no `error` string.
  fallbackError: string | ((status: number) => string);
}

// The one fetch -> parse -> throw path of the web API clients. Resolves with
// the parsed JSON body ({} when there is none); throws ApiError on a non-ok
// response.
export async function apiRequest<T = Record<string, unknown>>(
  url: string,
  { method = 'GET', body, fallbackError }: ApiRequestOptions,
): Promise<T> {
  const init: RequestInit = { method };
  if (body !== undefined) {
    init.headers = { 'Content-Type': 'application/json' };
    init.body = JSON.stringify(body);
  }
  const response = await fetch(url, init);

  let data: unknown = null;
  try {
    data = await response.json();
  } catch {
    data = null;
  }

  if (!response.ok) {
    const message = (data as { error?: unknown } | null)?.error;
    const fallback = typeof fallbackError === 'function'
      ? fallbackError(response.status)
      : fallbackError;
    throw new ApiError(typeof message === 'string' ? message : fallback, response.status);
  }

  return (data ?? {}) as T;
}

function deviceUrl(deviceUid: string, action = ''): string {
  return `/api/devices/${encodeURIComponent(deviceUid)}${action}`;
}

export function createDevice(payload: CreateDevicePayload): Promise<Record<string, unknown>> {
  return apiRequest('/api/devices', {
    method: 'POST',
    body: payload,
    fallbackError: 'Failed to create device.',
  });
}

export function updateDevice(
  targetDeviceUid: string,
  payload: UpdateDevicePayload,
): Promise<Record<string, unknown>> {
  return apiRequest(deviceUrl(targetDeviceUid), {
    method: 'PATCH',
    body: payload,
    fallbackError: 'Failed to update device.',
  });
}

export function removeDevice(deviceUid: string): Promise<Record<string, unknown>> {
  return apiRequest(deviceUrl(deviceUid), {
    method: 'DELETE',
    fallbackError: 'Failed to remove device.',
  });
}

// Starts a firmware update of one device (the controller flashes the built
// image over the air). Resolves once the transfer has started; its progress
// arrives in the server state's firmware.update.
export function updateFirmware(deviceUid: string): Promise<Record<string, unknown>> {
  return apiRequest(deviceUrl(deviceUid, '/update'), {
    method: 'POST',
    fallbackError: 'Failed to start the firmware update.',
  });
}

// Adds a sim device that mirrors this device's strip (same strip id and
// length). The server picks the sim's uid; the new card arrives with the
// next state snapshot.
export function createSimTwin(deviceUid: string): Promise<Record<string, unknown>> {
  return apiRequest(deviceUrl(deviceUid, '/sim'), {
    method: 'POST',
    fallbackError: 'Failed to create the simulated device.',
  });
}

interface StripMember {
  uid?: string;
  configured?: boolean;
  strip_id?: string;
}

// True when a configured sim device already serves the strip; the server
// allows one sim twin per strip, so "Simulate" is not offered then.
export function stripHasSimTwin(devices: readonly StripMember[], stripId: string | undefined): boolean {
  if (!stripId) {
    return false;
  }
  return devices.some((device) =>
    Boolean(device.configured)
    && typeof device.uid === 'string'
    && device.uid.startsWith('sim-')
    && device.strip_id === stripId);
}
