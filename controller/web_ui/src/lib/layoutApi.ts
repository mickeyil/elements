import type { LayoutDocumentPayload } from './editorModel';

export interface SaveLayoutRequest extends LayoutDocumentPayload {
  base_csv_hash: string | null;
}

async function parseJsonResponse(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    return null;
  }
}

export async function getLayout(deviceUid: string): Promise<LayoutDocumentPayload | null> {
  const response = await fetch(`/api/layouts/${encodeURIComponent(deviceUid)}`);
  if (response.status === 404) {
    return null;
  }

  const payload = await parseJsonResponse(response);
  if (!response.ok) {
    const message =
      payload && typeof payload === 'object' && 'error' in payload
        ? String((payload as { error: unknown }).error)
        : `Failed to load layout (${response.status})`;
    throw new Error(message);
  }
  return payload as LayoutDocumentPayload;
}

export async function saveLayout(
  deviceUid: string,
  payload: SaveLayoutRequest,
): Promise<LayoutDocumentPayload> {
  const response = await fetch(`/api/layouts/${encodeURIComponent(deviceUid)}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(payload),
  });

  const responsePayload = await parseJsonResponse(response);
  if (!response.ok) {
    const message =
      responsePayload && typeof responsePayload === 'object' && 'error' in responsePayload
        ? String((responsePayload as { error: unknown }).error)
        : `Failed to save layout (${response.status})`;
    throw new Error(message);
  }

  return responsePayload as LayoutDocumentPayload;
}
