import { ApiError, apiRequest } from './deviceApi';
import type { LayoutDocumentPayload } from './editorModel';

export interface SaveLayoutRequest extends LayoutDocumentPayload {
  base_csv_hash: string | null;
}

function layoutUrl(deviceUid: string): string {
  return `/api/layouts/${encodeURIComponent(deviceUid)}`;
}

export async function getLayout(deviceUid: string): Promise<LayoutDocumentPayload | null> {
  try {
    return await apiRequest<LayoutDocumentPayload>(layoutUrl(deviceUid), {
      fallbackError: (status) => `Failed to load layout (${status})`,
    });
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) {
      return null;
    }
    throw error;
  }
}

export function saveLayout(
  deviceUid: string,
  payload: SaveLayoutRequest,
): Promise<LayoutDocumentPayload> {
  return apiRequest<LayoutDocumentPayload>(layoutUrl(deviceUid), {
    method: 'POST',
    body: payload,
    fallbackError: (status) => `Failed to save layout (${status})`,
  });
}
