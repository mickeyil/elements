import { apiRequest } from './deviceApi';
import type { PanelHsv } from './panelModel';

function panelUrl(stripId: string, action: 'fill' | 'run' | 'stop' | 'default'): string {
  return `/api/panel/${encodeURIComponent(stripId)}/${action}`;
}

// Holds one color on the whole strip, taking it from the show.
export function fillStrip(stripId: string, { h, s, v }: PanelHsv): Promise<Record<string, unknown>> {
  return apiRequest(panelUrl(stripId, 'fill'), {
    method: 'POST',
    body: { h, s, v },
    fallbackError: 'Failed to set the color.',
  });
}

// Loops a panel-library program on the strip, taking it from the show.
export function runLibraryProgram(stripId: string, programId: string): Promise<Record<string, unknown>> {
  return apiRequest(panelUrl(stripId, 'run'), {
    method: 'POST',
    body: { program_id: programId },
    fallbackError: 'Failed to run the program.',
  });
}

// Stores the program on every connected device of the strip as the animation
// it plays on its own: after a reboot, or when the controller lets go.
export function setDefaultProgram(stripId: string, programId: string): Promise<Record<string, unknown>> {
  return apiRequest(panelUrl(stripId, 'default'), {
    method: 'POST',
    body: { program_id: programId },
    fallbackError: 'Failed to set the default.',
  });
}

// Blanks the strip; it stays with the panel until the next program load.
export function stopStrip(stripId: string): Promise<Record<string, unknown>> {
  return apiRequest(panelUrl(stripId, 'stop'), {
    method: 'POST',
    fallbackError: 'Failed to stop the strip.',
  });
}
