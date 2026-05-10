export const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";

export type RunStatus = "running" | "completed" | "failed" | "cancelled";

export type TaskContract = {
  goal: string;
  constraints: string;
  allowed_tools: string[];
  success_criteria: string[];
  expected_output: string;
};

export type EvalResult = {
  run_id: string;
  score: number;
  notes: string;
  passed: boolean;
  created_at: string;
};

export type AgentRun = {
  id: string;
  goal: string;
  status: RunStatus;
  created_at: string;
  updated_at?: string;
  final_answer?: string | null;
  error?: string | null;
  eval?: EvalResult;
  contract?: TaskContract;
};

export type MemoryRecord = {
  id: string;
  text: string;
  source: string;
  tags?: string[];
  importance?: number;
  created_at?: string;
};

export type RunEvent = {
  id?: string;
  sequence?: number;
  event_type: string;
  payload: Record<string, unknown>;
  created_at?: string;
};

export type ToolSpec = {
  name: string;
  description: string;
  input_schema: Record<string, unknown>;
  permission_category: string;
};

export type CreateRunOptions = {
  constraints?: string;
  allowed_tools?: string[];
  success_criteria?: string[];
  expected_output?: string;
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { "content-type": "application/json", ...(init?.headers ?? {}) },
    ...init,
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `Request failed: ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export function listRuns() {
  return request<AgentRun[]>("/runs");
}

export function createRun(goal: string, options: CreateRunOptions = {}) {
  return request<AgentRun>("/runs", {
    method: "POST",
    body: JSON.stringify({ goal, ...options }),
  });
}

export function retryRun(runId: string) {
  return request<AgentRun>(`/runs/${runId}/retry`, { method: "POST" });
}

export function cancelRun(runId: string) {
  return request<AgentRun>(`/runs/${runId}/cancel`, { method: "POST" });
}

export function getRun(runId: string) {
  return request<AgentRun>(`/runs/${runId}`);
}

export function getRunMemory(runId: string) {
  return request<MemoryRecord[]>(`/runs/${runId}/memory`);
}

export function getRunEvents(runId: string) {
  return request<RunEvent[]>(`/runs/${runId}/events.json`);
}

export function listTools() {
  return request<ToolSpec[]>("/tools");
}

export function listMemory() {
  return request<MemoryRecord[]>("/memory");
}

export function createMemory(text: string) {
  return request<MemoryRecord>("/memory", {
    method: "POST",
    body: JSON.stringify({ text, source: "manual", tags: [], importance: 0.5 }),
  });
}
