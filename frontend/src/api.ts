export const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8003";

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
  rubric?: Array<{
    name: string;
    label: string;
    score: number;
    weight: number;
    passed: boolean;
    notes: string;
  }>;
  evaluator_version?: string;
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

export type SkillRecord = {
  id: string;
  name: string;
  description: string;
  instructions: string;
  trigger_terms: string[];
  tool_names: string[];
  enabled: boolean;
  created_at: string;
  updated_at: string;
};

export type KnowledgeExport = {
  kind: "mnemosyne_core_knowledge_export";
  schema_version: number;
  exported_at: string;
  counts: {
    memories: number;
    skills: number;
  };
  memories: MemoryRecord[];
  skills: SkillRecord[];
};

export type KnowledgeImportSummary = {
  mode: "merge" | "replace";
  memories: {
    created: number;
    updated: number;
    skipped: number;
  };
  skills: {
    created: number;
    updated: number;
    skipped: number;
  };
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

export type ToolExecutionResult = {
  tool_name: string;
  arguments: Record<string, unknown>;
  status: "completed" | "failed";
  duration_ms: number;
  result?: Record<string, unknown>;
  error?: string;
  permission_category: string;
  requires_confirmation: boolean;
};

export type TerminalJob = {
  id: string;
  shell: string;
  command: string;
  working_directory: string;
  shell_mode?: string | null;
  status: "starting" | "running" | "cancelling" | "completed" | "failed" | "cancelled";
  pid?: number | null;
  exit_code?: number | null;
  error?: string | null;
  created_at: string;
  started_at?: string | null;
  completed_at?: string | null;
  updated_at: string;
};

export type TerminalJobLog = {
  id: string;
  job_id: string;
  sequence: number;
  stream: "stdout" | "stderr" | "system";
  text: string;
  created_at: string;
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

export function listSkills() {
  return request<SkillRecord[]>("/skills");
}

export function createSkill(payload: {
  name: string;
  description: string;
  instructions: string;
  trigger_terms: string[];
  tool_names: string[];
  enabled: boolean;
}) {
  return request<SkillRecord>("/skills", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function updateSkill(
  skillId: string,
  payload: {
    name: string;
    description: string;
    instructions: string;
    trigger_terms: string[];
    tool_names: string[];
    enabled: boolean;
  },
) {
  return request<SkillRecord>(`/skills/${skillId}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export function deleteSkill(skillId: string) {
  return fetch(`${API_BASE}/skills/${skillId}`, { method: "DELETE" }).then((response) => {
    if (!response.ok) {
      return response.text().then((text) => {
        throw new Error(text || `Request failed: ${response.status}`);
      });
    }
  });
}

export function exportKnowledge() {
  return request<KnowledgeExport>("/knowledge/export");
}

export function importKnowledge(payload: Record<string, unknown>) {
  return request<KnowledgeImportSummary>("/knowledge/import", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function executeTool(
  toolName: string,
  argumentsPayload: Record<string, unknown>,
  confirmRisk: boolean,
) {
  return request<ToolExecutionResult>(`/tools/${toolName}/execute`, {
    method: "POST",
    body: JSON.stringify({ arguments: argumentsPayload, confirm_risk: confirmRisk }),
  });
}

export function listTerminalJobs() {
  return request<TerminalJob[]>("/terminal/jobs");
}

export function createTerminalJob(payload: {
  shell: string;
  command: string;
  working_directory: string;
  shell_mode?: string | null;
  confirm_risk: boolean;
}) {
  return request<TerminalJob>("/terminal/jobs", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function cancelTerminalJob(jobId: string) {
  return request<TerminalJob>(`/terminal/jobs/${jobId}/cancel`, { method: "POST" });
}

export function getTerminalJob(jobId: string) {
  return request<TerminalJob>(`/terminal/jobs/${jobId}`);
}

export function getTerminalJobLogs(jobId: string) {
  return request<TerminalJobLog[]>(`/terminal/jobs/${jobId}/logs`);
}
