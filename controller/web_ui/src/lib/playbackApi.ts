import { apiRequest } from './deviceApi';

export type SessionVerb = 'play' | 'pause' | 'resume' | 'stop';

function postJson(url: string, fallbackError: string): Promise<Record<string, unknown>> {
  return apiRequest(url, { method: 'POST', fallbackError });
}

export function rescanPrograms(): Promise<Record<string, unknown>> {
  return postJson('/api/programs/rescan', 'Failed to rescan programs.');
}

export function loadProgram(programId: string): Promise<Record<string, unknown>> {
  return postJson(
    `/api/programs/${encodeURIComponent(programId)}/load`,
    'Failed to load program.',
  );
}

export function sessionCommand(verb: SessionVerb): Promise<Record<string, unknown>> {
  return postJson(`/api/session/${verb}`, `Failed to ${verb}.`);
}
