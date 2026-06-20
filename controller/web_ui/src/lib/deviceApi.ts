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

export async function createDevice(payload: CreateDevicePayload): Promise<Record<string, unknown>> {
  const response = await fetch('/api/devices', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(payload),
  });

  let data: Record<string, unknown> | null = null;
  try {
    data = (await response.json()) as Record<string, unknown>;
  } catch {
    data = null;
  }

  if (!response.ok) {
    const message = data?.error;
    throw new Error(typeof message === 'string' ? message : 'Failed to create device.');
  }

  return data ?? {};
}

export async function updateDevice(
  targetDeviceUid: string,
  payload: UpdateDevicePayload,
): Promise<Record<string, unknown>> {
  const response = await fetch(`/api/devices/${encodeURIComponent(targetDeviceUid)}`, {
    method: 'PATCH',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(payload),
  });

  let data: Record<string, unknown> | null = null;
  try {
    data = (await response.json()) as Record<string, unknown>;
  } catch {
    data = null;
  }

  if (!response.ok) {
    const message = data?.error;
    throw new Error(typeof message === 'string' ? message : 'Failed to update device.');
  }

  return data ?? {};
}

export async function removeDevice(deviceUid: string): Promise<Record<string, unknown>> {
  const response = await fetch(`/api/devices/${encodeURIComponent(deviceUid)}`, {
    method: 'DELETE',
  });

  let data: Record<string, unknown> | null = null;
  try {
    data = (await response.json()) as Record<string, unknown>;
  } catch {
    data = null;
  }

  if (!response.ok) {
    const message = data?.error;
    throw new Error(typeof message === 'string' ? message : 'Failed to remove device.');
  }

  return data ?? {};
}
