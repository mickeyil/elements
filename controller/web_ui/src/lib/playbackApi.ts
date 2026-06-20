export type SessionVerb = 'play' | 'pause' | 'resume' | 'stop';

async function postJson(url: string, fallbackError: string): Promise<Record<string, unknown>> {
  const response = await fetch(url, { method: 'POST' });

  let data: Record<string, unknown> | null = null;
  try {
    data = (await response.json()) as Record<string, unknown>;
  } catch {
    data = null;
  }

  if (!response.ok) {
    const message = data?.error;
    throw new Error(typeof message === 'string' ? message : fallbackError);
  }

  return data ?? {};
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
