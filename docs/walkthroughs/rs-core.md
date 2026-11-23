# rs-core — how it works

**What it is:** the shared vocabulary of the project. Plain data models plus one algorithm (state diff).

- `models.py` — frozen Pydantic models: `Snapshot` (table → pk → row), `SchemaInfo`/`TableInfo`
  (primary key, table kind such as `side_effect`/`audit`, volatile fields, foreign keys), `ToolSpec`
  (read/write, idempotent, which args name a record), `ToolCall`, `Trajectory`, `Task`, `Verdict`, `Episode`.
- `diff.py` — `diff(a, b, schema)` returns row-level changes (insert/update/delete) with JSON-pointer field paths;
  volatile fields are ignored; `apply` replays a diff; `snapshot_digest` hashes canonical JSON.
- `protocols.py` — `Executor` (reset + replay tool calls deterministically) and `Verifier` (episode → verdict).
- `testing/toyworld.py` — a dict-backed world (orders, refunds, approvals, tickets, emails, audit log) with 3 tasks
  and their golden calls; every other package tests against it first.

**Questions and answers**
1. *Why diff states instead of comparing hashes?* A hash says "different"; a diff says *what* changed, which is what
   verifiers and mutation operators reason about (collateral edits, duplicates, wrong record).
2. *Why ignore volatile fields?* Timestamps change on every run; including them would make every replay "different"
   and every exact-match verifier over-strict.
3. *Why frozen models?* Episodes are evidence; nothing downstream should be able to mutate them silently.
4. *How do you know the diff is right?* Hypothesis property tests: `apply(normalize(a), diff(a, b)) == normalize(b)`
   and `diff(a, a)` is empty, over thousands of random nested states.
5. *Why record tool errors instead of raising?* Agents make bad calls; the trajectory must show the failed call so
   process checks and mutation operators see exactly what happened.
