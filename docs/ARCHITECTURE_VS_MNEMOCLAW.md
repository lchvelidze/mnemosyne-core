# mnemosyne-core vs. Mnemoclaw — Architectural Comparison

_Date: 2026-06-30. A structural comparison of mnemosyne-core (the local-first MVP)
against the Mnemoclaw Cognitive Platform (the event-sourced production system),
based on reading both codebases and Mnemoclaw's architecture docs (Blueprint v2,
the 8 ADRs, LOOP/MEMORY/SKILLS docs)._

## TL;DR — how the two relate

They are **the same idea at two very different maturity levels**. Both are agent
harnesses built around the same conceptual pillars — a run **loop**, **memory**,
**tools**, **skills**, **evals**, and a **model client** — exposed over an API
with a live event timeline. The shared vocabulary (runs, events, threads, skills
as trigger-keyed reusable instructions, hybrid vector+FTS retrieval, rubric evals,
permission categories, confirm-risk gating, WSL/elevated commands) makes it clear
mnemosyne-core is the **precursor / prototype** and Mnemoclaw is the **ground-up
re-architecture** of the same author's mental model.

The single decision that separates them is the **state model**:

- **mnemosyne-core** stores *current state* directly: rows in SQLite that are
  updated and overwritten (`runs`, `memories`, `skills`, `eval_results`). The
  `events` table is an append-only *log of what happened in a run* for the UI —
  but it is not the source of truth; the tables are.
- **Mnemoclaw** stores *history* as the source of truth: an append-only,
  immutable **event spine** (Postgres, `REVOKE UPDATE/DELETE`), and **everything
  else is a projection** rebuildable from the log — vector memory, facts, the
  knowledge graph, goals, read models.

Almost every other difference below falls out of that one choice. Event-sourcing
is what buys Mnemoclaw its headline properties: memory can't be corrupted (only
mis-projected and replayed), every prompt is explainable to the token, the whole
platform is rebuildable from Git + a log backup, and self-evolution is safe
because nothing mutates live — it only proposes PRs gated by replay evals.

## At a glance

| Dimension | mnemosyne-core | Mnemoclaw |
|---|---|---|
| Shape | Single FastAPI process + SQLite + React | Event-sourced microservice platform (6 packages, 11 apps) |
| State model | Mutable current-state rows in SQLite | Append-only event spine; all reads are projections |
| The loop runs in | `asyncio.Task` in the web process | Durable **Temporal** workflow (`AgentSession`) |
| Durability | Run survives in DB rows; in-flight loop dies with the process | Loop survives worker crash (replay from event log), continue-as-new recycling |
| Memory write | Insert row + 128-dim hash vector + FTS | events → salience → projectors → facts w/ **belief revision** → bi-temporal KG |
| Memory read | Cosine(hash-vec) + FTS, blended, top-5 | dense(pgvector) + BM25 + graph **PPR** + RRF + cross-encoder rerank + HyDE query expansion |
| Contradictions | Both rows persist; no resolution | Trust-tier + recency **belief revision**; superseded facts close, never deleted |
| Context build | f-string concatenation in `model_client.py` | **ContextCompiler**: declarative spec, per-section token budgets, degrade policies, **provenance map** |
| Tool safety | Path allow-list + regex deny-list + confirm-risk | **Capability tokens** (Ed25519, per-call, allow-list) + **budget envelopes** + sandbox + SSRF guard + guard model |
| Skills | CRUD records, vector+FTS match, injected as text | Full lifecycle (draft→offered→verified→deprecated), **auto-mining** from successful runs → PR, validation gates |
| Evals | Local heuristic rubric (word counts), 7 dims | **Cassette replay** + frontier-model judge + best-of-N test oracle + CI eval gate (paired bootstrap) |
| Models | One LiteLLM model (or none) | Multi-route fallback chains + $0 subscription-CLI bridges (Claude Max, Codex, Kimi, Gemini) |
| Multi-agent | None | Sub-agents, **Conductor** (async swarm), **Council** (sealed deliberation), budget cascade |
| Self-improvement | None | **Autoresearcher** proposes PRs (GEPA/MIPROv2/Opus), **reconciler** GitOps canary+rollback |
| Self-healing | None (just retry/cancel) | **Watchdog** (health score, circuit breakers, loop detection) + auto-**doctor** mode |
| Infra needed | SQLite file only (+ a model API) | Postgres 16, Redis+FalkorDB, MinIO, Temporal, vLLM/llama.cpp, LiteLLM, Docker Compose |
| Audience | Single local power user | Always-on, self-operating single-operator platform |

## Dimension by dimension

### 1. Persistence & state — the root difference

mnemosyne-core: `db.py` is a ~1,500-line SQLite layer with mutable tables
(`runs`, `threads`, `memories`, `skills`, `eval_results`, `tool_calls`,
`terminal_jobs`). Updates overwrite. The per-run `events` table is a UI timeline,
not an authority. Knowledge export/import is a JSON dump of current memories +
skills. This is simple, debuggable, and perfectly adequate for one user — but it
means **state can be silently wrong and there's no way to reconstruct how it got
there**.

Mnemoclaw: the spine (`packages/spine`) is the kernel. Events are immutable,
typed, versioned, causation/correlation-linked, and carry provenance + a budget
snapshot. Fan-out is a transactional outbox → Redis Streams → idempotent
projectors. Any read model (vectors, facts, graph, goals) can be dropped and
rebuilt from the log. Disaster recovery = repo + log backup. This is the ADR-001
/ ADR-006 foundation everything else stands on.

### 2. The loop & harness

The loops are conceptually similar — plan → call model → run tools → record →
synthesize → score — but the **harness durability** is night and day.

- mnemosyne-core (`agent.py` `run_goal`): a clean linear pipeline. Builds a plan,
  searches memory (5) and skills (5), calls the model with the last 12 thread
  messages, executes allowed tools, re-synthesizes up to 3 model calls total,
  scores, finalizes. It runs as an `asyncio.Task`; cancel/retry are task ops. If
  the process dies mid-run, the in-flight loop is gone (the DB row is left
  `running`). Termination = synthesis done, model timeout (120s), or exception.

- Mnemoclaw (`apps/orchestrator/workflow.py` `_agentic_turn`): a Temporal
  workflow with an iteration cap (150 normal / 400 doctor) and a dense set of
  **loop guards** that mnemosyne-core has no equivalent of — mid-turn deadline
  checks, history-bytes watchdogs (O(N²) growth control), n-gram loop detection,
  diagnostic-spin ceilings, stall breakers, and storm control. Durability is
  real: a worker crash mid-tool replays the event history on another worker and
  resumes. **Continue-as-new** recycles unbounded history every ~256 KiB. This is
  also where the hard-won operational lessons live (the entire
  `DEADLOCK_HARDENING_PLAN.md` saga: 2-second deadlock detector vs. growing
  histories).

### 3. Memory

Both do **hybrid retrieval** (vector + FTS) — but at very different fidelity, and
only one resolves contradictions.

- mnemosyne-core (`memory.py`, `vectors.py`, `db.py`): memories are flat rows
  with `importance`. "Embeddings" are **128-dim locally-hashed bag-of-tokens**
  with a *hardcoded synonym map* (e.g. `lfp → lithium/iron/phosphate`), blake2b
  mod 128. Retrieval blends cosine + FTS bonus + importance, threshold 0.12.
  Self-contained and zero-infra — genuinely clever for an MVP — but crude
  semantically, and crucially **there is no notion of a fact, supersession, or
  belief revision**: if the user says "I use a ThinkPad" and later "I use a
  MacBook," both rows persist and both can be retrieved. No consolidation, no
  decay, no audit.

- Mnemoclaw (`apps/projectors`, `apps/retrieval`, `apps/sleeptime`,
  `apps/reconciler`): memory is event-sourced. Salient events become candidates →
  embedded into pgvector (4096-dim real embeddings) **and** extracted into
  **facts** (subject/predicate/object + confidence + **trust tier** + bi-temporal
  validity + supporting events). A deterministic **belief-revision** rule
  (`user_stated > agent_inferred > tool_observed > web_sourced`, recency
  tiebreak) decides ADD/UPDATE/NOOP/REJECT and supersedes losers atomically
  (including a race-safe path). Retrieval adds **graph PPR** and **cross-encoder
  reranking** and **HyDE query expansion**. `sleeptime` does nightly
  reflect/consolidate/densify/decay plus a poisoning **audit**. Documented recall
  is ~0.95 with **0 stale confusions**. (Its one known weakness — abstract
  reflections out-ranking source facts — is a problem mnemosyne-core doesn't even
  have yet because it has no reflections.)

### 4. Context assembly

- mnemosyne-core: context is **string concatenation** in `model_client.py` —
  goal + memory text + skill text + tool catalog + recent turns, in a fixed
  system/user prompt. No token budgeting beyond "last 12 messages"; no
  provenance.
- Mnemoclaw: the **ContextCompiler** (ADR-002) compiles a declarative
  `ContextSpec` — ordered sections each with min/max/weight token budgets and a
  degrade policy (truncate/summarize/drop), pinned-first allocation, local-model
  summarization on overflow — and emits a **provenance map** so any token span is
  traceable to the event/record/rule that produced it. This is also what makes
  context machine-optimizable by the autoresearcher.

### 5. Tools & safety

Same intent (sandbox the agent), very different rigor.

- mnemosyne-core (`tools.py`): a fixed registry (read/write file, calculator,
  http_get, web_search, skills, terminal, elevated PS/WSL). Safety = path
  **allow-list** (`MNEMOSYNE_ALLOWED_FILE_ROOTS`), a **regex deny-list** of
  dangerous commands, private-IP blocking on http_get, and `confirm_risk` for
  write/modify/terminal/elevated categories. Reasonable for a trusted local user,
  but the deny-list is fundamentally bypassable and there's no per-call authority.
- Mnemoclaw: **capability tokens** (ADR-003) — every side effect needs a
  short-lived Ed25519-signed grant minted per call, checked **allow-list** style
  against the agent's grants ∩ the skill's declared needs, enforced in the
  sandbox/egress proxy. Plus **budget envelopes** (ADR-004) that structurally cap
  cost/tokens/depth/deadline and cascade to sub-agents; a **protected floor**
  (never write `.git`, secrets, caps); SSRF guard that fail-closes on DNS; a
  guard model; and a layered computer-use policy (block/confirm/allow). Security
  rests on the capability system, not on the model behaving.

### 6. Skills

- mnemosyne-core (`skills.py`): skills are CRUD records (name, description,
  instructions, trigger_terms, tool_names, enabled), retrieved by vector+FTS and
  injected into the prompt as text. The agent can `create_skill` mid-run. Clean
  and useful — but skills are static; nothing learns or verifies them.
- Mnemoclaw (`SKILLS_ARCHITECTURE.md`, `apps/reconciler`, `apps/autoresearcher`):
  skills are spec files with a **full lifecycle** (draft → offered → verified →
  deprecated), **auto-mined** from successful runs (n-gram tool sequences with
  ≥3-distinct-session support, intent-clustered, dedup/cooldown-gated) into PRs,
  **validated** by live-probe/replay/script tests, promoted only by operator
  approval, and selected via progressive disclosure (tier-1 catalog + tier-2 body
  on match). A skill is strictly advisory — it can never widen capability.

### 7. Evals

- mnemosyne-core (`evals.py`): a deterministic **local heuristic rubric** — 7
  weighted dimensions scored largely by word counts and keyword presence (e.g.
  "≥8 words → task complete", risky-phrase regex for safety). Runs without a
  model, persists per run. Good for a cheap signal; not a real quality measure.
- Mnemoclaw (ADR-005/007, `packages/evalkit`, `scripts/eval_gate.py`):
  **deterministic cassette replay** (record every LLM call, replay identical
  traffic with the candidate substituted, judge with a cross-family frontier
  model, accept only at Δ>0 95% bootstrap CI), **best-of-N with a test oracle**
  for coding, a **completeness critic** for research, and a CI **eval gate** that
  blocks planted regressions. Evals are the merge gate for self-improvement.

### 8. Model routing

- mnemosyne-core: one configured LiteLLM model (or none, in which case `/health`
  reports it). Tool-call extraction handles native + fallback JSON/XML.
- Mnemoclaw: many routes with **fallback chains** (reasoning: GPT-5.5 → Opus →
  Gemini) and, notably, **$0 subscription-CLI bridges** (Claude Max, Codex, Kimi,
  Antigravity) driven over WSL via a cold per-session MCP STDIO bridge that
  reloads real grants from Redis. Background routes for classify/salience/judge.

### 9–11. The capabilities mnemosyne-core simply doesn't have

- **Multi-agent**: Mnemoclaw has sub-agents with budget cascade (child = 0.5×
  parent, 8× swarm cap), **Conductor** mode (async swarm, operator never blocks),
  and **Council** mode (sealed heterogeneous panel → auto-critic → vote →
  verdict/dissent). mnemosyne-core is strictly single-agent.
- **Self-improvement**: Mnemoclaw's autoresearcher + reconciler form a closed
  loop — curriculum picks a weak target, a worker proposes mutations, evals gate
  them, a PR is opened, the reconciler canaries and auto-reverts. mnemosyne-core
  has none.
- **Self-healing & ops**: Mnemoclaw's watchdog (health score, per-tool/agent
  circuit breakers, loop/taint detection) and auto-doctor mode (a platform-
  engineer session that diagnoses and repairs with gated writes) have no
  mnemosyne-core analog beyond manual retry/cancel.

## What mnemosyne-core does well (and Mnemoclaw gave up)

This is not a one-way scoreboard. mnemosyne-core has real virtues that the
platform traded away for power:

- **Zero infrastructure.** One Python process + a SQLite file + a browser. No
  Postgres, Redis, MinIO, Temporal, GPU inference stack, or Docker Compose. It
  starts in seconds and is trivially portable. Mnemoclaw needs a homelab.
- **Local-first and fully offline-capable** (hash vectors + FTS need no embedding
  service; a model is optional). The platform assumes a running model fleet.
- **Comprehensible in an afternoon.** ~5k LOC, linear control flow, one DB file.
  Mnemoclaw is a genuine distributed system with replay-determinism discipline.
- **Terminal jobs** (`jobs.py`): long-running subprocesses decoupled from run
  timeouts, with live SSE log streaming — a concrete, nicely-built feature.
- **Knowledge export/import** as portable JSON.

If the goal is "a hackable personal agent on my laptop," mnemosyne-core is
arguably the *better-shaped* tool; Mnemoclaw's strengths only pay off for an
always-on, self-operating, self-improving system.

## The shared DNA (continuity of thinking)

The overlap is striking and shows a clear evolutionary line — both have: a run
loop with a plan/memory/skills/tools/synthesis shape; a per-run **event timeline
streamed over SSE**; **threads** of durable conversation; **skills** as
trigger-keyed reusable instructions retrieved by **vector + FTS**; **hybrid
retrieval**; **rubric evals** persisted per run; **LiteLLM** as the model
adapter; **permission categories** + **confirm-risk** gating; and first-class
**WSL / elevated-command** support. Mnemoclaw is recognizably what you get when
you take mnemosyne-core's ideas and ask "how do I make every one of these
correct, durable, safe, and self-improving at the cost of a lot of infra."

## What's "missing" in mnemosyne-core — ordered by leverage

If you wanted to push mnemosyne-core toward the platform's properties without
rebuilding it, this is the order that buys the most correctness per unit effort:

1. **Belief revision / fact model (highest correctness payoff).** Today
   contradictory memories coexist and both surface. Add a minimal fact notion
   (subject/predicate/object + source trust + recency) and a supersede-on-write
   rule. This is the single biggest *quality* gap and doesn't require
   event-sourcing to start.
2. **Make `events` the source of truth (or at least append-only + replayable).**
   Even a lightweight "rebuild projections from the event log" path would unlock
   debuggability and safe migrations. Full spine is overkill for one process; an
   append-only events table you can fold into current state is not.
3. **A real context budget.** Replace the f-string + "last 12 messages" with a
   token-budgeted section assembler (even without provenance). Prevents silent
   context overflow as threads grow.
4. **Real embeddings behind the same interface.** `vectors.py` already isolates
   embedding; swapping the 128-dim hash for an optional local/remote embedding
   model (keeping the hash as offline fallback) would sharply improve recall.
5. **Capability-style tool authority.** Move from regex deny-list to an
   allow-list of (action, resource) grants per run. Even unsigned, an allow-list
   is far more robust than pattern-matching dangerous strings.
6. **An eval that uses a judge.** Keep the cheap heuristic as a fast gate, but add
   an optional model-graded score for real quality signal; record both.
7. **Consolidation / decay** (sleeptime-lite) once a fact model exists.
8. **Self-improvement & multi-agent** are the platform's crown jewels but the
   *least* worth porting to an MVP — they presuppose the durable spine, evals,
   and budget machinery above. Treat them as "the reason Mnemoclaw exists,"
   not as mnemosyne-core retrofits.

## Bottom line

mnemosyne-core is a coherent, well-built **MVP of the same vision** Mnemoclaw
realizes in full. Mnemoclaw didn't add features so much as it **changed the
substrate** — from mutable rows to an immutable event log — and then everything
that's hard about agents (memory correctness, durability, safety, evaluation,
self-improvement) became expressible. The honest framing: mnemosyne-core is
*superseded as a destination* but *valuable as a lightweight tool and as the
proving ground for the ideas*. If you keep evolving it, do it for the niche
Mnemoclaw abandoned (zero-infra, local, hackable) and steal exactly two ideas
first — a **fact model with belief revision** and an **append-only,
replayable event log** — because those are the foundation the rest of Mnemoclaw's
advantages are built on.
