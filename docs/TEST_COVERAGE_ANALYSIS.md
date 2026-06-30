# Test Coverage Analysis — mnemosyne-core

_Date: 2026-06-30. Static analysis (no coverage instrumentation exists yet)._

## Summary

mnemosyne-core has ~4,900 LOC of backend Python and a ~1,200 LOC React frontend,
backed by 64 backend test functions (6 files) and 20 frontend test cases. The
existing tests are solid on the happy path for `tools.py`, `api.py`, and
`agent.py`, but several high-risk modules have **no dedicated test file at all**
(`jobs.py`, `evals.py`, `vectors.py`, `models.py`, `config.py`), and the
security-sensitive sandbox logic in `tools.py` is under-tested on its
adversarial paths.

Two structural gaps amplify the risk:

- **No coverage measurement.** Neither `pytest-cov` nor `coverage` is declared
  in `pyproject.toml`. We are flying blind on what is actually exercised.
- **No CI.** There is no `.github/` directory; tests are never run
  automatically. A regression only surfaces if a developer remembers to run
  `pytest` locally.

## Module-by-module

| Module | LOC | Dedicated tests | Key untested areas |
|---|---|---|---|
| `tools.py` | 1,276 | `test_tools.py` (33) | path-traversal/symlink escape in `_allowed_path`, `_block_dangerous_command` bypass (case/chaining), HTTP tool MIME/SSRF, subprocess timeout paths |
| `db.py` | 1,504 | indirect via fixtures | concurrent writes, sequence integrity, migrations, `import_knowledge` dedupe/rollback, vector backfill errors |
| `api.py` | 553 | `test_api.py` (15) | error paths (404/409/422/500), `confirm_risk` gating, SSE stream disconnect/timeout, malformed payloads |
| `agent.py` | 392 | `test_agent.py` (7) | exceptions during event append, memory-search failure, serialization errors |
| `models.py` | 266 | none | dataclass invariants, edge-value serialization |
| `evals.py` | 264 | none | rubric boundary scoring, None/empty answers, safety phrase detection, tool-count bug |
| `model_client.py` | 221 | `test_model_client.py` (2) | streaming, provider failures/timeouts, tool-call extraction on malformed XML/JSON |
| `jobs.py` | 202 | none | process cleanup on crash, cancellation races, orphan/zombie reaping, pipe close |
| `vectors.py` | 71 | none | empty/short queries, hash collisions, synonym expansion, FTS fallback threshold |
| `skills.py` | 77 | `test_skills.py` (4) | Unicode name normalization, circular trigger terms |
| `memory.py` | 22 | `test_memory.py` (3) | embedding error paths |
| `config.py` | 34 | none | env var parsing, invalid paths, default overrides |
| `main.py` | 34 | indirect | startup/init error recovery |
| `frontend/App.tsx` | 1,231 | `App.test.tsx` (20) | error-state UI (5xx/timeout), pagination, accessibility/keyboard nav |

## Prioritized recommendations

Ranked by risk (security / data-loss / correctness first).

1. **Path-traversal & sandbox escape in `tools.py` (CRITICAL).** Add adversarial
   tests for `_allowed_path`: symlink to an out-of-root target, `..`
   normalization, hardlink crossing the boundary, WSL `/mnt/c/..` cases. These
   guard the filesystem sandbox; a bypass is arbitrary read/write.
2. **`_block_dangerous_command` robustness (HIGH).** Case-insensitive variants
   (`GIT reset --HARD`), command chaining (`ls; rm -rf /`, `ls && …`, `ls | …`),
   and quoted-but-inert strings. Partial regex bypass = destructive ops.
3. **`confirm_risk` / permission gating on `api.py` (HIGH).** Risky operations
   (`POST /tools/{name}/execute`, `/terminal/jobs`, `/runs`) without confirmation
   must return 409; permission categories must map to the right gate. These
   endpoints are unauthenticated, so the gate is the only guard.
4. **`db.py` concurrency & integrity (HIGH).** Parallel thread-message appends
   (sequence uniqueness), run+contract FK integrity, vector backfill racing new
   writes. SQLite WAL mitigates but does not eliminate lost-update/corruption.
5. **`import_knowledge` dedupe & rollback (HIGH, data-loss).** Merge mode must
   not silently drop or double-insert; schema-validation failure must roll back
   the whole batch; vectors backfilled without duplicate embedding calls.
6. **`jobs.py` process lifecycle (MEDIUM).** New `test_jobs.py`: zombie reaping
   when the monitor thread dies, pipe close on reader crash, SIGTERM/SIGKILL
   cancellation races, restart recovery of orphaned running jobs.
7. **`model_client.py` adapter resilience (MEDIUM).** Malformed/unclosed tool-call
   XML, invalid JSON in `tool_calls`, mixed native+fallback extraction, provider
   timeouts. Silent extraction failure blocks agent self-correction.
8. **`evals.py` scoring edge cases (MEDIUM).** None/empty answers, dimension
   weighting when some dimensions fail, FTS operator words in criteria,
   case-insensitive safety detection, tool-count miscount. Bad scores skew
   quality metrics and can mask safety failures.
9. **`vectors.py` ranking edge cases (LOW-MEDIUM).** Empty/all-stopword queries,
   very short queries, synonym expansion, hash-collision wrap, FTS fallback at
   the similarity threshold.
10. **SSE streaming integration (MEDIUM).** Client disconnect mid-stream, long
    idle gaps, reconnect/replay, stream termination after run completion.

### Foundational fixes (do these first; they multiply the value of everything above)

- Add `pytest-cov` and a `--cov=mnemosyne_core --cov-report=term-missing` run;
  record a baseline number so gaps become visible and trackable.
- Add a minimal GitHub Actions workflow that runs `ruff`, `pytest` (backend) and
  `npm test` (frontend) on PRs. Today nothing runs automatically.

_Rough effort to a meaningful coverage lift: ~40–50 dev-hours across ~5 new test
modules (`test_jobs.py`, `test_evals.py`, `test_vectors.py`, `test_db_concurrent.py`,
`test_api_streaming.py`) plus additions to `test_tools.py` and `test_api.py`._
