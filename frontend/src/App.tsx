import {
  Activity,
  BookOpenCheck,
  Brain,
  ChevronDown,
  CheckCircle2,
  Copy,
  History,
  Plus,
  Play,
  RotateCcw,
  Search,
  ServerCrash,
  ShieldCheck,
  Square,
  Wrench,
} from "lucide-react";
import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import {
  API_BASE,
  AgentRun,
  MemoryRecord,
  RunEvent,
  SkillRecord,
  ToolSpec,
  cancelRun,
  createMemory,
  createRun,
  createSkill,
  getRun,
  getRunEvents,
  getRunMemory,
  listMemory,
  listRuns,
  listSkills,
  listTools,
  retryRun,
} from "./api";
import { asPayloadText, cleanFinalAnswerForDisplay } from "./display";

type TimelineEvent = RunEvent & { id: string };

function eventIcon(type: string) {
  if (type.startsWith("memory")) return <Brain aria-hidden="true" />;
  if (type.startsWith("skills")) return <BookOpenCheck aria-hidden="true" />;
  if (type.startsWith("tool")) return <Wrench aria-hidden="true" />;
  if (type.startsWith("eval") || type.includes("completed")) return <CheckCircle2 aria-hidden="true" />;
  if (type.includes("failed")) return <ServerCrash aria-hidden="true" />;
  return <Activity aria-hidden="true" />;
}

export default function App() {
  const [goal, setGoal] = useState("");
  const [runs, setRuns] = useState<AgentRun[]>([]);
  const [selectedRun, setSelectedRun] = useState<AgentRun | null>(null);
  const [events, setEvents] = useState<TimelineEvent[]>([]);
  const [memories, setMemories] = useState<MemoryRecord[]>([]);
  const [allMemories, setAllMemories] = useState<MemoryRecord[]>([]);
  const [skills, setSkills] = useState<SkillRecord[]>([]);
  const [tools, setTools] = useState<ToolSpec[]>([]);
  const [selectedTools, setSelectedTools] = useState<string[]>([]);
  const [isToolMenuOpen, setIsToolMenuOpen] = useState(false);
  const [newMemory, setNewMemory] = useState("");
  const [newSkillName, setNewSkillName] = useState("");
  const [newSkillDescription, setNewSkillDescription] = useState("");
  const [newSkillInstructions, setNewSkillInstructions] = useState("");
  const [newSkillTriggers, setNewSkillTriggers] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [isCreating, setIsCreating] = useState(false);
  const eventSourceRef = useRef<EventSource | null>(null);

  const statusLabel = selectedRun?.status ?? "idle";
  const evalPercent = useMemo(() => {
    if (!selectedRun?.eval) return null;
    return Math.round(selectedRun.eval.score * 100);
  }, [selectedRun]);
  const toolGroups = useMemo(() => {
    const groups = new Map<string, ToolSpec[]>();
    for (const tool of tools) {
      const existing = groups.get(tool.permission_category) ?? [];
      groups.set(tool.permission_category, [...existing, tool]);
    }
    return Array.from(groups.entries()).map(([permission, items]) => ({
      permission,
      tools: items,
    }));
  }, [tools]);
  const selectedToolSummary = useMemo(() => {
    if (tools.length === 0) return "No tools loaded";
    if (selectedTools.length === tools.length) return `All ${tools.length} tools enabled`;
    if (selectedTools.length === 0) return "No tools selected";
    return `${selectedTools.length} of ${tools.length} tools enabled`;
  }, [selectedTools.length, tools.length]);

  const loadRun = useCallback(async (runId: string) => {
    const [run, runMemory, runEvents] = await Promise.all([getRun(runId), getRunMemory(runId), getRunEvents(runId)]);
    const timelineEvents = runEvents.map((event, index) => ({
      ...event,
      id: event.id ?? `${event.event_type}-${event.sequence ?? index + 1}`,
    }));
    setSelectedRun(run);
    setMemories(runMemory);
    setEvents(timelineEvents);
  }, []);

  const refreshRuns = useCallback(async () => {
    const history = await listRuns();
    setRuns(history);
  }, []);

  const initializeRuns = useCallback(async () => {
    const [toolCatalog, memoryRecords, skillRecords, history] = await Promise.all([
      listTools(),
      listMemory(),
      listSkills(),
      listRuns(),
    ]);
    setTools(toolCatalog);
    setSelectedTools(toolCatalog.map((tool) => tool.name));
    setAllMemories(memoryRecords);
    setSkills(skillRecords);
    setRuns(history);
    if (history.length > 0) {
      await loadRun(history[0].id);
    }
  }, [loadRun]);

  useEffect(() => {
    initializeRuns().catch((caught: unknown) => setError(String(caught)));
    return () => eventSourceRef.current?.close();
  }, [initializeRuns]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmed = goal.trim();
    if (!trimmed) return;
    setError(null);
    setEvents([]);
    setMemories([]);
    setIsCreating(true);
    try {
      const allowedTools = selectedTools.length > 0 ? selectedTools : tools.map((tool) => tool.name);
      const run = await createRun(trimmed, {
        constraints: "Use only selected safe tools and local memory visible in the control plane.",
        allowed_tools: allowedTools,
        success_criteria: ["Produce a final answer", "Persist timeline events", "Create an eval record"],
        expected_output: "Markdown answer with source links when web evidence is used.",
      });
      setSelectedRun(run);
      setGoal("");
      setIsToolMenuOpen(false);
      await refreshRuns();
      attachEventStream(run.id);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setIsCreating(false);
    }
  }

  function attachEventStream(runId: string) {
    eventSourceRef.current?.close();
    const source = new EventSource(`${API_BASE}/runs/${runId}/events`);
    eventSourceRef.current = source;
    const eventTypes = [
      "run.created",
      "plan.created",
      "memory.retrieved",
      "skills.retrieved",
      "model.started",
      "model.completed",
      "tool.started",
      "tool.completed",
      "tool.failed",
      "tool.blocked",
      "model.synthesis_started",
      "model.synthesis_tool_calls_detected",
      "model.synthesis_retry_started",
      "model.synthesis_completed",
      "eval.completed",
      "run.completed",
      "run.failed",
      "run.cancelled",
    ];
    for (const type of eventTypes) {
      source.addEventListener(type, (message) => {
        const payload = JSON.parse((message as MessageEvent).data) as Record<string, unknown>;
        setEvents((current) => [
          ...current,
          {
            id: `${type}-${current.length + 1}`,
            event_type: type,
            payload,
          },
        ]);
        if (type === "memory.retrieved") {
          const records = Array.isArray(payload.records) ? (payload.records as MemoryRecord[]) : [];
          setMemories(records);
        }
        if (type === "run.completed" || type === "run.failed") {
          source.close();
          void loadRun(runId);
          void refreshRuns();
        }
      });
    }
    source.onerror = () => {
      source.close();
    };
  }

  async function handleHistoryClick(runId: string) {
    setError(null);
    setEvents([]);
    eventSourceRef.current?.close();
    try {
      await loadRun(runId);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    }
  }

  function handleToolToggle(toolName: string) {
    setSelectedTools((current) =>
      current.includes(toolName) ? current.filter((name) => name !== toolName) : [...current, toolName],
    );
  }

  function handleSelectAllTools() {
    setSelectedTools(tools.map((tool) => tool.name));
  }

  function handleClearTools() {
    setSelectedTools([]);
  }

  async function handleAddMemory(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const text = newMemory.trim();
    if (!text) return;
    setError(null);
    try {
      const created = await createMemory(text);
      setAllMemories((current) => [created, ...current]);
      setNewMemory("");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    }
  }

  async function handleAddSkill(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const name = newSkillName.trim();
    const description = newSkillDescription.trim();
    const instructions = newSkillInstructions.trim();
    if (!name || !description || !instructions) return;
    setError(null);
    try {
      const created = await createSkill({
        name,
        description,
        instructions,
        trigger_terms: splitCommaList(newSkillTriggers),
        tool_names: selectedTools,
        enabled: true,
      });
      setSkills((current) => [created, ...current]);
      setNewSkillName("");
      setNewSkillDescription("");
      setNewSkillInstructions("");
      setNewSkillTriggers("");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    }
  }

  async function handleRetry() {
    if (!selectedRun) return;
    setError(null);
    try {
      const run = await retryRun(selectedRun.id);
      setSelectedRun(run);
      setEvents([]);
      setMemories([]);
      await refreshRuns();
      attachEventStream(run.id);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    }
  }

  async function handleCancel() {
    if (!selectedRun) return;
    setError(null);
    try {
      const run = await cancelRun(selectedRun.id);
      setSelectedRun(run);
      eventSourceRef.current?.close();
      await loadRun(run.id);
      await refreshRuns();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    }
  }

  function handleDuplicate() {
    if (selectedRun) setGoal(selectedRun.goal);
  }

  return (
    <main className="app-shell">
      <section className="top-band">
        <div>
          <p className="eyebrow">Mnemosyne Core</p>
          <h1>Run Console</h1>
        </div>
        <div className={`status-pill status-${statusLabel}`}>{statusLabel}</div>
      </section>

      <section className="console-grid" aria-label="Run console workspace">
        <aside className="history-panel" aria-label="Run history">
          <div className="panel-heading">
            <History aria-hidden="true" />
            <h2>History</h2>
          </div>
          <div className="history-list">
            {runs.length === 0 ? (
              <p className="muted">No runs yet.</p>
            ) : (
              runs.map((run) => (
                <button
                  className={`history-row ${selectedRun?.id === run.id ? "selected" : ""}`}
                  key={run.id}
                  onClick={() => void handleHistoryClick(run.id)}
                  type="button"
                >
                  <span>{run.goal}</span>
                  <small>{run.status}</small>
                </button>
              ))
            )}
          </div>
        </aside>

        <section className="work-panel">
          <form className="goal-form" onSubmit={(event) => void handleSubmit(event)}>
            <label htmlFor="goal">Research goal</label>
            <div className="goal-entry">
              <textarea
                id="goal"
                value={goal}
                onChange={(event) => setGoal(event.target.value)}
                placeholder="Compare LFP and NMC batteries for home storage safety."
                rows={3}
              />
              <button disabled={isCreating || goal.trim().length === 0} type="submit">
                <Play aria-hidden="true" />
                <span>{isCreating ? "Starting" : "Start Run"}</span>
              </button>
            </div>
            <fieldset className="tool-selector">
              <legend>Allowed Tools</legend>
              <button
                aria-controls="allowed-tools-menu"
                aria-expanded={isToolMenuOpen}
                className="tool-menu-trigger"
                onClick={() => setIsToolMenuOpen((open) => !open)}
                type="button"
              >
                <span className="tool-trigger-icon">
                  <Wrench aria-hidden="true" />
                </span>
                <span className="tool-trigger-copy">
                  <strong>Allowed Tools</strong>
                  <small>{selectedToolSummary}</small>
                </span>
                <ChevronDown aria-hidden="true" className={isToolMenuOpen ? "open" : ""} />
              </button>
              {isToolMenuOpen ? (
                <div className="tool-menu-panel" id="allowed-tools-menu">
                  <div className="tool-menu-toolbar">
                    <span>{selectedToolSummary}</span>
                    <div>
                      <button onClick={handleSelectAllTools} type="button">
                        Select all
                      </button>
                      <button onClick={handleClearTools} type="button">
                        Clear
                      </button>
                    </div>
                  </div>
                  <div className="tool-menu-list">
                    {toolGroups.map((group) => (
                      <section className="tool-group" key={group.permission}>
                        <h3>{group.permission}</h3>
                        <div className="tool-group-list">
                          {group.tools.map((tool) => (
                            <label className="tool-option" key={tool.name} title={tool.description}>
                              <input
                                checked={selectedTools.includes(tool.name)}
                                onChange={() => handleToolToggle(tool.name)}
                                type="checkbox"
                              />
                              <span>
                                <strong>{tool.name}</strong>
                                <small>{tool.description}</small>
                              </span>
                            </label>
                          ))}
                        </div>
                      </section>
                    ))}
                  </div>
                </div>
              ) : null}
            </fieldset>
          </form>

          {error ? <div className="error-strip">{error}</div> : null}

          <div className="result-grid">
            <section className="timeline-panel" aria-label="Run timeline">
              <div className="panel-heading">
                <Activity aria-hidden="true" />
                <h2>Timeline</h2>
              </div>
              {selectedRun ? (
                <div className="selected-run-strip">
                  <strong>Selected run</strong>
                  <span>{selectedRun.goal}</span>
                </div>
              ) : null}
              {events.length === 0 ? (
                <div className="empty-state">
                  <Search aria-hidden="true" />
                  <p>Start a run to watch model steps, tool calls, memory hits, and eval output.</p>
                </div>
              ) : (
                <ol className="timeline">
                  {events.map((event) => (
                    <li key={event.id}>
                      <div className="event-icon">{eventIcon(event.event_type)}</div>
                      <div className="event-body">
                        <strong>{event.event_type}</strong>
                        <pre>{asPayloadText(event.event_type, event.payload)}</pre>
                      </div>
                    </li>
                  ))}
                </ol>
              )}
            </section>

            <section className="details-panel" aria-label="Run details">
              <div className="panel-heading">
                <Brain aria-hidden="true" />
                <h2>Inspection</h2>
              </div>
              <div className="run-actions" aria-label="Run controls">
                <button onClick={handleDuplicate} type="button">
                  <Copy aria-hidden="true" />
                  <span>Duplicate</span>
                </button>
                <button onClick={() => void handleRetry()} type="button">
                  <RotateCcw aria-hidden="true" />
                  <span>Retry</span>
                </button>
                <button disabled={selectedRun?.status !== "running"} onClick={() => void handleCancel()} type="button">
                  <Square aria-hidden="true" />
                  <span>Cancel</span>
                </button>
              </div>

              <div className="inspection-block">
                <h3>Contract</h3>
                {selectedRun?.contract ? (
                  <div className="contract-box">
                    <ShieldCheck aria-hidden="true" />
                    <p>{selectedRun.contract.constraints}</p>
                    <small>{selectedRun.contract.allowed_tools.join(", ")}</small>
                  </div>
                ) : (
                  <p className="muted">No contract recorded for this run.</p>
                )}
              </div>

              <div className="inspection-block">
                <h3>Final Answer</h3>
                {selectedRun?.final_answer ? (
                  <div className="markdown-answer">
                    <ReactMarkdown>{cleanFinalAnswerForDisplay(selectedRun.final_answer)}</ReactMarkdown>
                  </div>
                ) : (
                  <p>No final answer yet.</p>
                )}
              </div>

              <div className="inspection-block">
                <h3>Memory Hits</h3>
                {memories.length === 0 ? (
                  <p className="muted">No memory records attached to this run.</p>
                ) : (
                  <ul className="memory-list">
                    {memories.map((memory) => (
                      <li key={memory.id}>
                        <span>{memory.text}</span>
                        <small>{memory.source}</small>
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              <div className="inspection-block">
                <h3>Memory Manager</h3>
                <form className="memory-form" onSubmit={(event) => void handleAddMemory(event)}>
                  <label htmlFor="new-memory">New memory</label>
                  <textarea
                    id="new-memory"
                    onChange={(event) => setNewMemory(event.target.value)}
                    rows={2}
                    value={newMemory}
                  />
                  <button disabled={!newMemory.trim()} type="submit">
                    <Plus aria-hidden="true" />
                    <span>Add Memory</span>
                  </button>
                </form>
                <ul className="memory-list compact">
                  {allMemories.slice(0, 6).map((memory) => (
                    <li key={memory.id}>
                      <span>{memory.text}</span>
                      <small>{memory.source}</small>
                    </li>
                  ))}
                </ul>
              </div>

              <div className="inspection-block">
                <h3>Skill Manager</h3>
                <form className="skill-form" onSubmit={(event) => void handleAddSkill(event)}>
                  <label htmlFor="new-skill-name">Skill name</label>
                  <input
                    id="new-skill-name"
                    onChange={(event) => setNewSkillName(event.target.value)}
                    value={newSkillName}
                  />
                  <label htmlFor="new-skill-description">Description</label>
                  <input
                    id="new-skill-description"
                    onChange={(event) => setNewSkillDescription(event.target.value)}
                    value={newSkillDescription}
                  />
                  <label htmlFor="new-skill-instructions">Instructions</label>
                  <textarea
                    id="new-skill-instructions"
                    onChange={(event) => setNewSkillInstructions(event.target.value)}
                    rows={3}
                    value={newSkillInstructions}
                  />
                  <label htmlFor="new-skill-triggers">Trigger terms</label>
                  <input
                    id="new-skill-triggers"
                    onChange={(event) => setNewSkillTriggers(event.target.value)}
                    placeholder="openclaw, model run"
                    value={newSkillTriggers}
                  />
                  <button disabled={!newSkillName.trim() || !newSkillDescription.trim() || !newSkillInstructions.trim()} type="submit">
                    <Plus aria-hidden="true" />
                    <span>Add Skill</span>
                  </button>
                </form>
                <ul className="skill-list compact">
                  {skills.slice(0, 5).map((skill) => (
                    <li key={skill.id}>
                      <strong>{skill.name}</strong>
                      <span>{skill.description}</span>
                      <small>{skill.trigger_terms.join(", ") || "manual"}</small>
                    </li>
                  ))}
                </ul>
              </div>

              <div className="inspection-block">
                <h3>Eval</h3>
                {selectedRun?.eval ? (
                  <div className="eval-box">
                    <span className={selectedRun.eval.passed ? "pass" : "fail"}>
                      {selectedRun.eval.passed ? "Pass" : "Fail"}
                    </span>
                    <strong>{evalPercent}%</strong>
                    <p>{selectedRun.eval.notes}</p>
                  </div>
                ) : (
                  <p className="muted">No eval result yet.</p>
                )}
              </div>
            </section>
          </div>
        </section>
      </section>
    </main>
  );
}

function splitCommaList(value: string) {
  return value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}
