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

// Starts a firmware update of one device (the controller flashes the built
// image over the air). Resolves once the transfer has started; its progress
// arrives in the server state's firmware.update.
export async function updateFirmware(deviceUid: string): Promise<Record<string, unknown>> {
  const response = await fetch(`/api/devices/${encodeURIComponent(deviceUid)}/update`, {
    method: 'POST',
  });

  let data: Record<string, unknown> | null = null;
  try {
    data = (await response.json()) as Record<string, unknown>;
  } catch {
    data = null;
  }

  if (!response.ok) {
    const message = data?.error;
    throw new Error(typeof message === 'string' ? message : 'Failed to start the firmware update.');
  }

  return data ?? {};
}
