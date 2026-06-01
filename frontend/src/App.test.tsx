import "@testing-library/jest-dom/vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { asPayloadText, cleanFinalAnswerForDisplay } from "./display";

class MockEventSource {
  static instances: MockEventSource[] = [];
  listeners: Record<string, Array<(event: MessageEvent) => void>> = {};
  url: string;

  constructor(url: string) {
    this.url = url;
    MockEventSource.instances.push(this);
  }

  addEventListener(type: string, listener: (event: MessageEvent) => void) {
    this.listeners[type] = [...(this.listeners[type] ?? []), listener];
  }

  close() {}

  emit(type: string, data: unknown) {
    for (const listener of this.listeners[type] ?? []) {
      listener(new MessageEvent(type, { data: JSON.stringify(data) }));
    }
  }
}

describe("Run Console", () => {
  beforeEach(() => {
    MockEventSource.instances = [];
    vi.stubGlobal("EventSource", MockEventSource);
    vi.stubGlobal("confirm", vi.fn(() => true));
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/runs") && init?.method === "POST") {
          const body = JSON.parse(String(init.body));
          return Response.json({
            id: "run-2",
            status: "running",
            goal: body.goal,
            contract: {
              goal: body.goal,
              constraints: body.constraints,
              allowed_tools: body.allowed_tools,
              success_criteria: body.success_criteria,
              expected_output: body.expected_output,
            },
          });
        }
        if (url.endsWith("/tools")) {
          return Response.json([
            {
              name: "calculator",
              description: "Evaluate deterministic arithmetic expressions.",
              permission_category: "compute.safe",
              input_schema: {},
            },
            {
              name: "web_search",
              description: "Search the public web.",
              permission_category: "network.public_search",
              input_schema: {},
            },
            {
              name: "run_elevated_wsl_command",
              description: "Launch a WSL command through a Windows UAC elevation prompt.",
              permission_category: "terminal.elevated",
              input_schema: {},
            },
          ]);
        }
        if (url.endsWith("/memory") && init?.method === "POST") {
          const body = JSON.parse(String(init.body));
          return Response.json({
            id: "mem-2",
            text: body.text,
            source: body.source,
            tags: body.tags,
            importance: body.importance,
          });
        }
        if (url.endsWith("/memory")) {
          return Response.json([
            { id: "mem-1", text: "Solar battery research prefers LFP chemistry.", source: "seed" },
          ]);
        }
        if (url.endsWith("/skills") && init?.method === "POST") {
          const body = JSON.parse(String(init.body));
          return Response.json({
            id: "skill-2",
            name: body.name.toLowerCase().replaceAll(" ", "_"),
            description: body.description,
            instructions: body.instructions,
            trigger_terms: body.trigger_terms,
            tool_names: body.tool_names,
            enabled: body.enabled,
            created_at: "2026-05-08T12:00:00Z",
            updated_at: "2026-05-08T12:00:00Z",
          });
        }
        if (url.endsWith("/skills/skill-1") && init?.method === "PUT") {
          const body = JSON.parse(String(init.body));
          return Response.json({
            id: "skill-1",
            name: body.name.toLowerCase().replaceAll(" ", "_"),
            description: body.description,
            instructions: body.instructions,
            trigger_terms: body.trigger_terms,
            tool_names: body.tool_names,
            enabled: body.enabled,
            created_at: "2026-05-08T12:00:00Z",
            updated_at: "2026-05-08T12:10:00Z",
          });
        }
        if (url.endsWith("/skills/skill-1") && init?.method === "DELETE") {
          return new Response(null, { status: 204 });
        }
        if (url.endsWith("/skills")) {
          return Response.json([
            {
              id: "skill-1",
              name: "openclaw_infer",
              description: "Run OpenClaw model inference.",
              instructions: "Use openclaw infer model run --prompt.",
              trigger_terms: ["openclaw"],
              tool_names: ["run_terminal_command"],
              enabled: true,
              created_at: "2026-05-08T12:00:00Z",
              updated_at: "2026-05-08T12:00:00Z",
            },
          ]);
        }
        if (url.endsWith("/tools/calculator/execute") && init?.method === "POST") {
          return Response.json({
            tool_name: "calculator",
            arguments: JSON.parse(String(init.body)).arguments,
            status: "completed",
            duration_ms: 2,
            result: { result: 4 },
            permission_category: "compute.safe",
            requires_confirmation: false,
          });
        }
        if (url.endsWith("/terminal/jobs") && init?.method === "POST") {
          const body = JSON.parse(String(init.body));
          return Response.json({
            id: "job-2",
            shell: body.shell,
            command: body.command,
            working_directory: body.working_directory,
            shell_mode: body.shell_mode,
            status: "running",
            pid: 1234,
            exit_code: null,
            error: null,
            created_at: "2026-05-08T12:00:00Z",
            started_at: "2026-05-08T12:00:00Z",
            completed_at: null,
            updated_at: "2026-05-08T12:00:00Z",
          });
        }
        if (url.endsWith("/terminal/jobs/job-1/cancel") || url.endsWith("/terminal/jobs/job-2/cancel")) {
          return Response.json({
            id: url.includes("job-2") ? "job-2" : "job-1",
            shell: "wsl",
            command: url.includes("job-2") ? "openclaw status --watch" : "openclaw status",
            working_directory: "/mnt/f",
            shell_mode: "interactive",
            status: "cancelling",
            pid: 1233,
            exit_code: null,
            error: null,
            created_at: "2026-05-08T12:00:00Z",
            started_at: "2026-05-08T12:00:00Z",
            completed_at: null,
            updated_at: "2026-05-08T12:01:00Z",
          });
        }
        if (url.endsWith("/terminal/jobs/job-1/logs")) {
          return Response.json([
            {
              id: "log-1",
              job_id: "job-1",
              sequence: 1,
              stream: "stdout",
              text: "OpenClaw is ready",
              created_at: "2026-05-08T12:00:01Z",
            },
          ]);
        }
        if (url.endsWith("/terminal/jobs/job-1")) {
          return Response.json({
            id: "job-1",
            shell: "wsl",
            command: "openclaw status",
            working_directory: "/mnt/f",
            shell_mode: "interactive",
            status: "completed",
            pid: 1233,
            exit_code: 0,
            error: null,
            created_at: "2026-05-08T12:00:00Z",
            started_at: "2026-05-08T12:00:00Z",
            completed_at: "2026-05-08T12:00:02Z",
            updated_at: "2026-05-08T12:00:02Z",
          });
        }
        if (url.endsWith("/terminal/jobs")) {
          return Response.json([
            {
              id: "job-1",
              shell: "wsl",
              command: "openclaw status",
              working_directory: "/mnt/f",
              shell_mode: "interactive",
              status: "completed",
              pid: 1233,
              exit_code: 0,
              error: null,
              created_at: "2026-05-08T12:00:00Z",
              started_at: "2026-05-08T12:00:00Z",
              completed_at: "2026-05-08T12:00:02Z",
              updated_at: "2026-05-08T12:00:02Z",
            },
          ]);
        }
        if (url.endsWith("/runs/run-1/retry")) {
          return Response.json({ id: "run-3", status: "running", goal: "research battery safety" });
        }
        if (url.endsWith("/runs/run-1/cancel") || url.endsWith("/runs/run-3/cancel")) {
          return Response.json({ id: "run-3", status: "cancelled", goal: "research battery safety" });
        }
        if (url.endsWith("/runs")) {
          return Response.json([
            {
              id: "run-1",
              goal: "research battery safety",
              status: "completed",
              created_at: "2026-05-08T12:00:00Z",
              final_answer: "LFP batteries are a safety-oriented choice.",
              contract: {
                goal: "research battery safety",
                constraints: "Use only safe tools.",
                allowed_tools: ["calculator", "web_search", "run_elevated_wsl_command"],
                success_criteria: ["Answer the goal"],
                expected_output: "Markdown answer",
              },
            },
          ]);
        }
        if (url.endsWith("/runs/run-1") || url.endsWith("/runs/run-2") || url.endsWith("/runs/run-3")) {
          return Response.json({
            id: url.split("/").pop(),
            goal: "research battery safety",
            status: "completed",
            created_at: "2026-05-08T12:00:00Z",
            final_answer:
              "## Battery Safety\n\n- [LFP batteries](https://example.com/lfp) are a safety-oriented choice.",
            eval: {
              score: 0.86,
              passed: true,
              notes: "Grounded and inspectable.",
              evaluator_version: "local-rubric-v2",
              rubric: [
                {
                  name: "grounding",
                  label: "Grounding",
                  score: 0.9,
                  weight: 0.15,
                  passed: true,
                  notes: "Source links are present.",
                },
              ],
            },
            contract: {
              goal: "research battery safety",
              constraints: "Use only safe tools.",
                allowed_tools: ["calculator", "web_search", "run_elevated_wsl_command"],
              success_criteria: ["Answer the goal"],
              expected_output: "Markdown answer",
            },
          });
        }
        if (
          url.endsWith("/runs/run-1/events.json") ||
          url.endsWith("/runs/run-2/events.json") ||
          url.endsWith("/runs/run-3/events.json")
        ) {
          return Response.json([
            {
              id: "evt-1",
              sequence: 1,
              event_type: "run.created",
              payload: { goal: "research battery safety", status: "running" },
              created_at: "2026-05-08T12:00:00Z",
            },
            {
              id: "evt-2",
              sequence: 2,
              event_type: "plan.created",
              payload: {
                steps: ["Review the run contract"],
                allowed_tools: ["calculator", "web_search", "run_elevated_wsl_command"],
              },
              created_at: "2026-05-08T12:00:01Z",
            },
            {
              id: "evt-3",
              sequence: 3,
              event_type: "memory.retrieved",
              payload: { records: [{ id: "mem-1", text: "Solar battery research prefers LFP chemistry." }] },
              created_at: "2026-05-08T12:00:02Z",
            },
            {
              id: "evt-4",
              sequence: 4,
              event_type: "tool.completed",
              payload: { tool_name: "web_search", result: { results: [{ title: "LFP safety" }] } },
              created_at: "2026-05-08T12:00:03Z",
            },
          ]);
        }
        if (
          url.endsWith("/runs/run-1/memory") ||
          url.endsWith("/runs/run-2/memory") ||
          url.endsWith("/runs/run-3/memory")
        ) {
          return Response.json([
            { id: "mem-1", text: "Solar battery research prefers LFP chemistry.", source: "seed" },
          ]);
        }
        return Response.json({});
      }),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("creates a run and renders timeline events, memory, answer, and eval", async () => {
    render(<App />);

    await userEvent.type(screen.getByLabelText(/research goal/i), "research battery safety");
    await userEvent.click(screen.getByRole("button", { name: /start run/i }));

    await waitFor(() => expect(MockEventSource.instances).toHaveLength(1));
    await waitFor(() => expect(MockEventSource.instances[0].listeners["run.completed"]).toHaveLength(1));
    act(() => {
      MockEventSource.instances[0].emit("memory.retrieved", {
        records: [{ id: "mem-1", text: "Solar battery research prefers LFP chemistry." }],
      });
      MockEventSource.instances[0].emit("tool.completed", {
        tool_name: "calculator",
        result: { result: 4 },
      });
    });

    expect(await screen.findByText("memory.retrieved")).toBeInTheDocument();
    expect(screen.getByText("tool.completed")).toBeInTheDocument();

    act(() => {
      MockEventSource.instances[0].emit("run.completed", {
        final_answer:
          "## Battery Safety\n\n- [LFP batteries](https://example.com/lfp) are a safety-oriented choice.",
      });
    });

    expect(await screen.findByRole("heading", { name: "Battery Safety" })).toBeInTheDocument();
    expect(await screen.findByRole("link", { name: /LFP batteries/i })).toHaveAttribute(
      "href",
      "https://example.com/lfp",
    );
    expect((await screen.findAllByText(/Solar battery research prefers LFP chemistry/i))[0]).toBeInTheDocument();
    expect(await screen.findByText(/Grounded and inspectable/i)).toBeInTheDocument();
    expect(await screen.findByText("Grounding")).toBeInTheDocument();
  });

  it("can reopen a previous run from history", async () => {
    render(<App />);

    await userEvent.click(await screen.findByRole("button", { name: /research battery safety/i }));

    expect(await screen.findByRole("heading", { name: "Battery Safety" })).toBeInTheDocument();
    expect(await screen.findByText("tool.completed")).toBeInTheDocument();
    expect(await screen.findByText(/Grounded and inspectable/i)).toBeInTheDocument();
  });

  it("loads the latest run details on startup", async () => {
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Battery Safety" })).toBeInTheDocument();
    expect(await screen.findByText("plan.created")).toBeInTheDocument();
    expect(await screen.findByText(/Use only safe tools/i)).toBeInTheDocument();
    expect(await screen.findByText("tool.completed")).toBeInTheDocument();
    expect(await screen.findByText(/Grounded and inspectable/i)).toBeInTheDocument();
  });

  it("submits selected tool permissions and adds memory records", async () => {
    const fetchMock = vi.mocked(fetch);
    render(<App />);

    await userEvent.click(await screen.findByRole("button", { name: /allowed tools/i }));
    await userEvent.click(await screen.findByLabelText(/web_search/i));
    await userEvent.type(screen.getByLabelText(/research goal/i), "research battery safety");
    await userEvent.click(screen.getByRole("button", { name: /start run/i }));

    await waitFor(() => {
      const createCall = fetchMock.mock.calls.find(
        ([url, init]) => String(url).endsWith("/runs") && init?.method === "POST",
      );
      expect(createCall).toBeDefined();
      const body = JSON.parse(String(createCall?.[1]?.body));
      expect(body.allowed_tools).toEqual(["calculator", "run_elevated_wsl_command"]);
    });

    await userEvent.type(screen.getByLabelText(/new memory/i), "Thermal runaway notes");
    await userEvent.click(screen.getByRole("button", { name: /add memory/i }));

    expect(await screen.findByText(/Thermal runaway notes/i)).toBeInTheDocument();
  });

  it("adds reusable skills from the skill manager", async () => {
    const fetchMock = vi.mocked(fetch);
    render(<App />);

    await userEvent.type(await screen.findByLabelText(/skill name/i), "OpenClaw Model Run");
    await userEvent.type(screen.getByLabelText(/description/i), "Run OpenClaw model inference.");
    await userEvent.type(
      screen.getByLabelText(/instructions/i),
      "Use openclaw infer model run --prompt for one-shot replies.",
    );
    await userEvent.type(screen.getByLabelText(/trigger terms/i), "openclaw, model run");
    await userEvent.type(screen.getByLabelText(/preferred tools/i), "run_terminal_command");
    await userEvent.click(screen.getByRole("button", { name: /add skill/i }));

    await waitFor(() => {
      const createCall = fetchMock.mock.calls.find(
        ([url, init]) => String(url).endsWith("/skills") && init?.method === "POST",
      );
      expect(createCall).toBeDefined();
      const body = JSON.parse(String(createCall?.[1]?.body));
      expect(body.trigger_terms).toEqual(["openclaw", "model run"]);
      expect(body.tool_names).toEqual(["run_terminal_command"]);
    });
    expect(await screen.findByText("openclaw_model_run")).toBeInTheDocument();
  });

  it("edits and deletes reusable skills from the skill manager", async () => {
    const fetchMock = vi.mocked(fetch);
    render(<App />);

    await userEvent.click(await screen.findByRole("button", { name: /edit/i }));
    await userEvent.clear(screen.getByLabelText(/description/i));
    await userEvent.type(screen.getByLabelText(/description/i), "Run OpenClaw with the correct model command.");
    await userEvent.clear(screen.getByLabelText(/preferred tools/i));
    await userEvent.type(screen.getByLabelText(/preferred tools/i), "run_terminal_command");
    await userEvent.click(screen.getByRole("button", { name: /update skill/i }));

    await waitFor(() => {
      const updateCall = fetchMock.mock.calls.find(
        ([url, init]) => String(url).endsWith("/skills/skill-1") && init?.method === "PUT",
      );
      expect(updateCall).toBeDefined();
      const body = JSON.parse(String(updateCall?.[1]?.body));
      expect(body.description).toBe("Run OpenClaw with the correct model command.");
      expect(body.tool_names).toEqual(["run_terminal_command"]);
    });

    await userEvent.click(screen.getByRole("button", { name: /delete/i }));
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining("/skills/skill-1"), {
        method: "DELETE",
      }),
    );
  });

  it("runs a direct tool execution from the inspection panel", async () => {
    const fetchMock = vi.mocked(fetch);
    render(<App />);

    const argumentsInput = await screen.findByLabelText(/arguments json/i);
    await userEvent.clear(argumentsInput);
    await userEvent.click(argumentsInput);
    await userEvent.paste('{"expression":"2 + 2"}');
    await userEvent.click(screen.getByRole("button", { name: /run tool/i }));

    await waitFor(() => {
      const executeCall = fetchMock.mock.calls.find(
        ([url, init]) => String(url).endsWith("/tools/calculator/execute") && init?.method === "POST",
      );
      expect(executeCall).toBeDefined();
      const body = JSON.parse(String(executeCall?.[1]?.body));
      expect(body.arguments).toEqual({ expression: "2 + 2" });
      expect(body.confirm_risk).toBe(false);
    });
    expect(await screen.findByText(/"status": "completed"/i)).toBeInTheDocument();
  });

  it("starts and streams long-running terminal jobs", async () => {
    const fetchMock = vi.mocked(fetch);
    render(<App />);

    expect(await screen.findByText(/OpenClaw is ready/i)).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText(/^command$/i), "openclaw status --watch");
    await userEvent.click(screen.getByRole("button", { name: /start job/i }));

    await waitFor(() => {
      const createCall = fetchMock.mock.calls.find(
        ([url, init]) => String(url).endsWith("/terminal/jobs") && init?.method === "POST",
      );
      expect(createCall).toBeDefined();
      const body = JSON.parse(String(createCall?.[1]?.body));
      expect(body.shell).toBe("wsl");
      expect(body.working_directory).toBe("/mnt/f");
      expect(body.confirm_risk).toBe(true);
    });
    await waitFor(() =>
      expect(MockEventSource.instances.some((source) => source.url.includes("/terminal/jobs/job-2/logs/stream"))).toBe(
        true,
      ),
    );
    const jobStream = MockEventSource.instances.find((source) =>
      source.url.includes("/terminal/jobs/job-2/logs/stream"),
    );
    act(() => {
      jobStream?.emit("terminal.job.log", {
        id: "log-2",
        job_id: "job-2",
        sequence: 1,
        stream: "stdout",
        text: "streamed status",
        created_at: "2026-05-08T12:00:01Z",
      });
      jobStream?.emit("terminal.job.status", {
        id: "job-2",
        shell: "wsl",
        command: "openclaw status --watch",
        working_directory: "/mnt/f",
        shell_mode: "interactive",
        status: "completed",
        pid: 1234,
        exit_code: 0,
        error: null,
        created_at: "2026-05-08T12:00:00Z",
        started_at: "2026-05-08T12:00:00Z",
        completed_at: "2026-05-08T12:00:03Z",
        updated_at: "2026-05-08T12:00:03Z",
      });
    });

    expect(await screen.findByText(/streamed status/i)).toBeInTheDocument();
    expect(await screen.findByText(/exit 0/i)).toBeInTheDocument();
  });

  it("shows allowed tools in a dropdown menu with elevated WSL visible", async () => {
    render(<App />);

    const trigger = await screen.findByRole("button", { name: /allowed tools/i });
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByLabelText(/run_elevated_wsl_command/i)).not.toBeInTheDocument();

    await userEvent.click(trigger);

    expect(trigger).toHaveAttribute("aria-expanded", "true");
    expect(await screen.findByLabelText(/run_elevated_wsl_command/i)).toBeChecked();
    expect(screen.getByText("terminal.elevated")).toBeInTheDocument();
  });

  it("supports duplicate, retry, and cancel run controls", async () => {
    const fetchMock = vi.mocked(fetch);
    render(<App />);

    await userEvent.click(await screen.findByRole("button", { name: /duplicate/i }));
    expect(screen.getByLabelText(/research goal/i)).toHaveValue("research battery safety");

    await userEvent.click(screen.getByRole("button", { name: /retry/i }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining("/runs/run-1/retry"), expect.anything()));

    await userEvent.click(screen.getByRole("button", { name: /^cancel$/i }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining("/runs/run-3/cancel"), expect.anything()));
  });

  it("cleans stale tool-call markup from final answer display text", () => {
    const cleaned = cleanFinalAnswerForDisplay(`
<tool_call tool_name="write_text_file">
  <arg name="path">/mnt/f/myagent_data.md</arg>
  <arg name="text"># Hidden File Body</arg>
</tool_call>

Created the benchmark summary file here:

\`/mnt/f/myagent_data.md\`
`);

    expect(cleaned).not.toContain("<tool_call");
    expect(cleaned).not.toContain("Hidden File Body");
    expect(cleaned).toContain("Created the benchmark summary file here");
  });

  it("redacts synthesis message payloads from timeline JSON", () => {
    expect(
      asPayloadText("model.synthesis_completed", {
        message: "<tool_call tool_name=\"write_text_file\">large payload</tool_call>",
      }),
    ).toBe(
      JSON.stringify(
        {
          message: "[shown in Final Answer]",
        },
        null,
        2,
      ),
    );
  });
});
