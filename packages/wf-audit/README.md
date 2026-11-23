# wf-audit

Audits WorldForge verifier variants with OpenAdapt's `verifier_audit` (`openadapt-flow==3.4.0`, MIT) **before** any
RL training. It answers: *which wrong end states would this verifier give credit for, and which permitted ones would it
refuse?* As OpenAdapt's own docs put it, the audit "shows what a policy could get credit for, not how often an agent
does it". Measuring how often RL actually exploits these blind spots is the training study's job, not this package's.

## Quick start

```sh
uv run wf-audit sweep --world toy --out results/audit/toy          # all stand-in variants
uv run wf-audit sweep --world toy --out /tmp/a --only strict,accepts_duplicates
uv run wf-audit sweep --world toy --out /tmp/a --variants mypkg.knobs:variants   # your own {name: Verifier}
uv run wf-audit sweep --world toy --out /tmp/a --preset extended    # + loophole families (see below)
uv run wf-audit sweep --world toy --out /tmp/a --preset full        # + schema families (see below)
uv run wf-audit sweep --world toy --out /tmp/a --preset full --families field_coverage   # one kind, all its slots
uv run wf-audit scorecard results/audit/toy                         # re-print (needs raw/)
```

```python
from wf_audit import sweep, scorecard, toy_world
from wf_audit.testing.toy_verifiers import toy_variants

world = toy_world()  # executor + tasks + golden calls + claim tool
out = sweep(toy_variants(world), world, "results/audit/toy")
card = scorecard(out)["variants"]  # {variant: {false_accept: {...}, false_reject: {...}, blind_spots: [...]}}
```

For a single variant, `world.target(verifier)` returns an `ExecutorAuditTarget` you can pass straight to OpenAdapt's
`run_audit`.

## How it works

- **`ExecutorAuditTarget`** wraps any wf_core `Executor` plus any wf_core `Verifier` as an OpenAdapt `AuditTarget`.
  - `name = "worldforge/<world>/<variant>"`. OpenAdapt's resume fingerprint ignores constructor arguments, so the
    variant has to be in the name.
  - `reference(task)` comes from the golden `ToolCall`s. The call kind comes from `ToolSpec.effect` (read→READ,
    write→WRITE) and the claim tool (→CLAIM). If a claim tool is configured and the golden calls lack it, one is
    appended.
  - `run` replays from a fresh reset.
    - `final_effect` is the canonical multiset of `{table, pk, op, row[, changed]}` rows from `wf_core.diff`, with
      volatile fields already dropped.
    - `claim_effect` is the effect of the prefix up to and including the claim.
    - Refused calls go to `call_errors`.
    - The verifier runs only when `score=True`: passed→PASS, failed→FAIL, and an exception or `Verdict.error`→ERROR.
    - A crashed replay is ERROR even when unscored, because that is how OpenAdapt's census detects harness errors.
  - **Virtual claim.** When the claim tool is not one of the executor's tools (as in the toy world), it is not sent to
    the executor. Its `answer` becomes `final_answer`, and the call is put back into the trajectory at its position so
    process checks can see when success was claimed.
  - `instruction(task)` returns `Task.prompt`. `incidental_calls` returns read-only tools aimed at the ids the
    reference already uses; these are the positive control.
- **`sweep`** runs `run_audit` once per variant into `<out>/raw/<variant>/`. It then writes `run_card.json`,
  `scorecard.json`, `SCORECARD.md` and `audit_features.json` to `<out>/`.
  - `raw/` is git-ignored and regenerates byte for byte.
  - Earlier cells are deleted unless `resume=True`, because the ledger can't tell that a verifier's code changed.
- **`scorecard`** is two-sided. For each variant it gives the false-accept rate of every harmful family and the
  false-reject rate of every legitimate family. Counts and Wilson 95% intervals come from OpenAdapt's summary and are
  never pooled across families. It also lists the counterexamples.
  - `tasks_with_any_false_accept` in scorecard.json is OpenAdapt's own summary, pooled over harmful families. Its
    denominator is the tasks any harmful family scored, so it depends on the family set: do not compare it across
    presets.
  - OpenAdapt's `REPORT.md` shows the first 25 counterexamples per variant; `report.json` has every cell.
- **`audit_features`** gives per-(task, variant) features for the prediction test (see Schema families below).
- **`wf_audit.testing.toy_verifiers`** holds stand-in variants: test fixtures, not the real knobs (those belong to
  wf-verify). Each lenient one overrides exactly one method of the strict verifier. **`wf_audit.testing.toy_fields`**
  is the toy world plus one ticket tool with two enum fields, one of them optional, for the schema families' tests.

## Toy-world result (`results/audit/toy/SCORECARD.md`)

The toy world has 3 tasks, each scored per family. `n/a` means the family refused, was degenerate, or found no usable
candidate.

| Variant | side_effect | wrong_record | wrong_value | duplicate | partial | late_write | optimistic | incidental (FR) |
|---|---|---|---|---|---|---|---|---|
| strict | 0/2 | 0/2 | 0/2 | 0/2 | 0/2 | 0/3 | 0/3 | 0/2 |
| ignores_collateral | **2/2** | 0/2 | 0/2 | **2/2** | 0/2 | 0/3 | 0/3 | 0/2 |
| accepts_duplicates | 0/2 | 0/2 | 0/2 | **2/2** | 0/2 | 0/3 | 0/3 | 0/2 |
| ignores_side_effects | 0/2 | 0/2 | **1/2** | **1/2** | **1/2** | 0/3 | 0/3 | 0/2 |
| ignores_claim_order | 0/2 | 0/2 | 0/2 | 0/2 | 0/2 | **3/3** | 0/3 | 0/2 |
| trusts_claim | **2/2** | **2/2** | **2/2** | **2/2** | **2/2** | 0/3 | **3/3** | 0/2 |
| exact_trajectory | 0/2 | 0/2 | 0/2 | 0/2 | 0/2 | 0/3 | 0/3 | **2/2** |

With n ≤ 3, the Wilson intervals span most of [0, 1]. This table shows that the plumbing works. It is not a finding.

## Loophole families (`families.py`)

On the toy world, OpenAdapt's default families missed one planted blind spot, `ignore_order` (refund before
approval). They also raised one false alarm on the strict grader: `wrong_value` edited the email subject, a
free-text field. `wf_audit.families` adds three families. They use only the public 3.4.0 plug-in API (`FaultFamily`,
`FamilyContext`, `Candidate`, `Refusal`, `Polarity`, `IDENTITY_KEY`, `AUTH_KEY`, `write_indices`).

| Family | Polarity | Edit | Refusals |
|---|---|---|---|
| `wrong_order` | harmful | For each declared (A, B) whose tools both occur, a B call moves to just before the first A call. Everything else stays. Both moved calls must succeed: if the world refuses B before A, it enforces the order itself. | `NO_ORDER_CONSTRAINT`, `CONSTRAINED_TOOLS_NOT_IN_REFERENCE` |
| `wrong_constrained_value` | harmful | `wrong_value`'s edit (first string longer than 3 characters gets "x", else first number), skipping declared free-text args. Numbers are tried at +1, then −1. | `NO_CONSTRAINED_FIELD` |
| `reorder_independent` | legitimate | Two adjacent writes swap if no declared constraint covers the pair and they share no identity value. A write with no identity value is never assumed independent. | `FEWER_THAN_TWO_WRITES`, `NO_INDEPENDENT_WRITE_PAIR` |

**Declarations are policy ground truth from the world, never from a verifier.** A constraint read off the verifier
under audit would audit that verifier against itself. Both are optional fields of `World` and `ExecutorAuditTarget`,
and both are checked against the tasks and tool specs when a target is built, so a typo fails loudly.
- `order_constraints: {task: [(A, B), ...]}`: tool A must run before tool B. The toy world declares only
  `refund_approved: request_approval → create_refund`, because that prompt says "approval first". The `refund_email`
  prompt states no order, so nothing is declared for it.
- `free_text_fields: {tool: {arg, ...}}`: args the task never constrains. The toy world declares `send_email.subject`.

```python
out = sweep(variants, world, out_dir, families="extended")  # DEFAULT_FAMILIES + loophole_families(world)
```

```sh
uv run python scripts/audit_planted.py --families extended   # -> results/audit/toy-planted-extended/
uv run wf-audit sweep --world toy --out /tmp/a --families-module wf_audit.testing.toy_families   # plug-in contract
```

- The `default` preset is still OpenAdapt's own family set, so the pinned original result (design log Entry 4)
  regenerates byte for byte.
- `extended` keeps OpenAdapt's `wrong_value` for side-by-side comparison.
- When a loophole family runs, the run card records the declarations it read.

**How order becomes visible.** OpenAdapt's effect is a sorted multiset of rows, so it has no sequence.
- `wrong_order` is judged on `final_effect`. A swap is visible only when the world writes rows that carry their
  order. The toy `audit_log` hands out keys `a1, a2, ...` in call order, so the swap moves `refund` to `a1` and
  `approval` to `a2`.
- In a world without sequenced rows the effects are equal, and the runner marks the cell `DEGENERATE`. That is an
  honest "cannot see order", not a fake catch, and a test pins it.
- OpenAdapt's `ordering_only` (the claim-time comparison) is not an option. The runner refuses every `ordering_only`
  candidate when episodes end at the claim, as RL episodes do.

**Result on the planted variants (`results/audit/toy-planted-extended/`).** The world has 3 tasks. `wrong_order` and
`reorder_independent` each apply to one of them, so every number below is a single cell or close to it.
- `ignore_order` goes from missed to **detected**, via `wrong_order` (1/1). The strict grader catches the same edit
  (0/1 false accepts).
- `wrong_constrained_value` scores the strict grader 0/3, where `wrong_value` scores it 1/2 (the free-text alarm).
  It also hits `status_only` 2/3: a refund 1 cent short.
- `reorder_independent` gets a **1/1 false reject from the strict grader** on `refund_email`.
  - The swapped trajectory (email first, then refund) reaches the identical end state (`effect_changed: false`).
    The strict spec's process rule `refund_before_email` fails it.
  - Measured against the declared policy, that is over-strictness. Alternatively the declaration is incomplete,
    since a "confirmation" arguably follows the refund. The audit cannot tell which; it surfaces the disagreement
    for a human to settle.
  - Every variant that keeps process rules inherits the false reject (5 of 7). `ignore_order` and `trust_claim`
    accept the swap.

### Upstream-contribution notes

The three families are designed to be proposed to OpenAdapt as plug-ins: `wf_audit.testing.toy_families.FAMILIES`
already loads through `load_families` (`--families-module`). Writing them against 3.4.0 surfaced these points to
raise with the maintainers.

1. **No channel from target to family.**
   - `FamilyContext` carries only `task`, `reference`, `identity_pool` and `incidental`, so the declarations are
     bound when the family is built (`loophole_families(world)`).
   - *Proposal:* optional target hooks (`order_constraints(task)`, `free_text_fields()`), passed through
     `FamilyContext` the way `incidental_calls` already is. Then one module-level `FAMILIES` works for every target.
2. **Order needs a sequenced effect** (see above). *Proposal:* `Episode.trace_effect` (one effect per write), or a
   documented convention that targets expose a sequenced event table.
3. **The legitimate path does not validate the alternative.**
   - The runner scores only `built[0]`. It ignores `must_succeed`, and it records `effect_changed` without using it
     in the status.
   - `reorder_independent` sets `must_succeed`, and the cell's `effect_changed: false` is the evidence that the swap
     reaches the same end state, but 3.4.0 uses neither to decide whether the alternative is valid.
   - *Proposal:* a census for legitimate families, with an `INVALID_ALTERNATIVE` status.
4. **`original_value_in_instruction` is keyed on family ids** (`wrong_value`, `wrong_record` in `_value_named`), so
   `wrong_constrained_value` cells never get it.
   - *Proposal:* give `wrong_value` itself the free-text hook and the −1 retry.
   - With no free text declared, `wrong_constrained_value`'s first edit per write equals `wrong_value`'s (tested), so
     the change is backward compatible.
5. **Private helpers.** `_distinct_writes_newest_first` is re-implemented here, so the families depend only on public
   names. Exporting it upstream would remove the copy.

## Schema families (`families.py`, `domains.py`)

Preset `full` = `extended` plus three harmful families whose edits come from the tools' JSON Schemas
(`ToolSpec.params`; `domains.py` reads the subset tool schemas use). They cover what OpenAdapt's defaults leave out
(Known gaps 2 and 4 below): `wrong_value` edits only the first eligible field of a write, collateral is only
generated through the same tool, and nothing sets an optional field the task did not ask for.

| Family | One family per | Edit | Refusals |
|---|---|---|---|
| `field_coverage:<tool>.<arg>` | field of a write tool | A task write of `tool` carries another value of `arg` that the schema allows: the next enum member (then the others), a number +1 (−1 when +1 breaks a bound or the world refuses it), a negated boolean, a string with "x" appended within `maxLength`. Identity args, credentials and declared free text are not slots. | `TOOL_NOT_WRITTEN`, `FIELD_NOT_SET`, `NO_VALID_VALUE` |
| `schema_collateral:<tool>` | write tool with a required identity arg | The task's calls plus one `tool` call before the claim. Its identity args take a value other tasks' writes use for the same arg and this task never names (up to 3 records). Its other required args take valid values from the pool, else the `const`, an enum member or a boolean: the records are tried with each arg's first value, then again with the next (up to 3 values), because a world may refuse a value on every record (a status transition). Optional args stay unset. | `NO_UNTOUCHED_RECORD`, `NO_VALID_ARGS` |
| `extra_field:<tool>.<arg>` | optional arg of a write tool | A task write of `tool` that left `arg` unset also sets it: the `const`, an enum member or boolean other than the default, a numeric default ±1, else a pool value; an identity arg takes another record from the pool. An arg the golden passes explicitly, even at its default, is set (changing it is `field_coverage`'s edit). Declared free text is not a slot. | `TOOL_NOT_WRITTEN`, `FIELD_ALREADY_SET`, `NO_VALID_VALUE` |

- **One family per slot.** The 3.4.0 runner scores one candidate per (task, family). One `field_coverage` family
  would score one field per task, which is the gap it closes. So each kind is one family per slot, and each slot gets
  its own cell. SCORECARD.md shows the slots of a kind in their own table ("Schema families, per slot"): which
  fields, which tools and which optional fields the grader checks. A slot's candidates are fallbacks for one edit,
  because a world may refuse a value its schema allows (a refund over the order total).
- **Bound at build time, like the declarations.** `schema_families(world)` reads the world's write tools (the claim
  tool excluded) and its `free_text_fields`, and binds one `FaultFamily` per slot. Identity args are the tool's
  `id_params` plus OpenAdapt's `IDENTITY_KEY`. Both param shapes work: a full object schema (pydantic, MCP
  `inputSchema`, the helpdesk tools; `properties` may be absent for a tool without args) and the toy tools' bare
  `{arg: schema}` map, whose args are all required. A kind with no slot in a world (no toy tool has an optional arg)
  is one family named after the kind that refuses every task, so it keeps an n/a column; the reason
  (`NO_MUTABLE_FIELD`, `NO_WRITE_TOOL_WITH_IDENTITY`, `NO_OPTIONAL_FIELD`) is in `raw/<variant>/report.json`. When a
  schema family runs, the run card records a digest of each write tool's schema (`tool_schemas`).
- **Ids.** `field_coverage:create_refund.amount_cents`, `schema_collateral:send_email`. `--families field_coverage`
  selects every slot of a kind; `family_kind(id)` strips the slot.
- **`extra_field` is toy-only for now.** On the helpdesk tools it has no scorable slot: every golden comment passes
  `public` and every golden `approve` passes `decision` (set, so `FIELD_ALREADY_SET`), and `create_refund` accepts
  only an approval of the same order, which the identity pool (other tasks' approvals) never holds. Its feature is
  therefore empty there. Reaching it would need values drawn from the world's state (an approval of that order), a
  channel 3.4.0 families do not have. It is exercised on the toy fixture below.

```python
out = sweep(variants, world, out_dir, families="full")  # extended + schema_families(world)
```

```sh
uv run python scripts/audit_planted.py --families full   # -> results/audit/toy-planted-full/
```

**Result on the planted variants (`results/audit/toy-planted-full/`, hand checks in its NOTES.md).** Same 3 toy
tasks and graders as the extended run; only the family set differs.
- All six planted flags are detected, as with `extended`. The strict grader catches every scored schema-family cell
  (false accepts: `field_coverage` 0/3, `schema_collateral` 0/10); its one hit is still the comparison family
  `wrong_value`.
- `status_only`: `field_coverage:create_refund.amount_cents` 2/2 (the 1-cent-short refund).
- `ignore_collateral`: every `schema_collateral` slot, 10/10 cells. On `close_ticket`, OpenAdapt's
  `unauthorized_side_effect` refuses (`NO_RETARGETABLE_WRITE`); `schema_collateral` finds the hole through three tools
  other than the task's `set_ticket_status`.
- `extra_field` refuses every toy task: no toy tool has an optional arg.

The toy tools cannot show argument order or optional fields, so `wf_audit.testing.toy_fields` adds
`update_ticket(ticket_id, priority, status)` with an optional priority.
`tests/integration/test_schema_families_vs_planted.py` pins, with wf-verify graders:
- The planted status-only grader is detected by `field_coverage:update_ticket.priority` in both argument orders.
  OpenAdapt's `wrong_value` detects it only when priority comes first.
- No planted flag ignores an extra field on a matched update, so the extra-field grader is test-local (the strict spec
  plus an allow rule for any field of the expected row). `extra_field:update_ticket.priority` detects it, and the
  strict grader catches the same edit.

**Audit features (`audit_features.json`, every sweep, committed for every result directory).** One row per audited
(task, variant), and an `excluded` list of the (task, variant) pairs whose reference failed OpenAdapt's control, with
the reason. For each family kind (a schema family's slots count together):
- `candidates`: the number of scored cells, each scored on one candidate. For a harmful family that is the census
  choice (edited call accepted, effect changed). For a legitimate family OpenAdapt scores `built[0]` directly: no
  census, no check that the edited calls were accepted, no effect-change requirement. Refused, degenerate and
  exhausted cells are not candidates.
- `survived` and `survival` = survived / candidates (null when nothing was scored). A surviving harmful candidate is
  a false accept; a legitimate one should survive (`polarity` says which).
- `errors`: cells that crashed instead (scorer or harness error, effect not reproducible), so a crash does not read as
  "nothing applicable".
- `slots` (schema families): slot -> whether the verifier passed it, for each scored slot.

What `survival` measures: OpenAdapt's families have one cell per task, so it is 0 or 1. A schema family's pools its
slots, and the slots check different fields, so a fraction says how many of the slots this task writes are open, not
how lenient the grader is: a status-only grader that leaves one field open reads 1/1 on a task writing only that field
and 1/3 on a task that also writes two status fields. On the toy planted variants every schema-family `survival` is
0, 1 or null (each flag switches the whole scope check on or off). Use `survived > 0` or `slots` for "is a hole
open", and treat the fraction with care.

### Upstream-contribution notes (schema families)

1. **The runner scores one candidate per (task, family).** Per-slot families are the workaround. *Proposal:* a
   `Candidate.group` (or `slot`) field, with one cell per group, so `field_coverage` ships as one family.
2. **No schema channel.** The slots are bound from wf_core `ToolSpec`s at build time (`write_tools` is the only
   wf_core-specific part). *Proposal:* an optional `tool_schemas()` target hook (MCP `inputSchema` per tool), passed
   through `FamilyContext`; or narrower `field_domain(call, key)` and `optional_fields(call)` hooks.
3. **A new call has no payload and no credentials.** `schema_collateral` builds a fresh `Call` (empty `payload`); a
   required credential arg has no pool value (the runner leaves `AUTH_KEY` args out of the pool), so such a tool
   refuses with `NO_VALID_ARGS`. Targets that replay through `payload` (HTTP methods) or need tokens would need a hook.

## Python version

`openadapt-flow` declares `Requires-Python <3.13`, but uv ignores upper bounds on `Requires-Python`. The workspace
venv had defaulted to 3.13 and installed it without complaint. This package pins `.python-version` to 3.12, and the
tests were run on 3.12.16.

## Known gaps → upstream contribution ideas

Each gap below was checked against the 3.4.0 source and, where marked, seen in the toy run.

1. **Effects are order-blind.** `final_effect` is a sorted multiset, and only `late_write` (a write moved past the
   claim) is order-aware, through `claim_effect`. Swapping two writes (refund before approval) is invisible unless the
   world logs a sequence. The toy `audit_log` hands out ids in order, so it happens to expose a swap. *Idea:*
   `Episode.trace_effect` (one effect per write) and a `wrong_order` family, to be discussed with the maintainers since
   it changes the core model. *Status:* `wrong_order` (see Loophole families) works wherever the world sequences its
   rows; the core-model change is still open.
2. **`wrong_value` only changes the first eligible field, without looking at the schema.** It appends `"x"` to the
   first non-identity string longer than 3 characters, or else adds 1 to the first number, once per distinct write
   tool.
   - Seen in the toy run: `refund_approved` adds 1 to the amount, the world refuses it, and the cell becomes
     `CANDIDATES_EXHAUSTED`.
   - `close_ticket` writes status `"closedx"`, which a real enum would refuse.
   - A status-only verifier looks safe whenever `status` happens to be the first string argument.
   - *Idea:* a `field_coverage` family with one candidate per mutable field and values taken from the tool's JSON
     Schema (`ToolSpec.params` already carries it).
   - *Status:* `wrong_constrained_value` skips declared free text and retries numbers at −1, which fixes the
     `refund_approved` exhaustion. `field_coverage` (see Schema families) covers every field with schema-valid
     values, one family per field; OpenAdapt's `wrong_value` itself is unchanged.
3. **Retargets come only from other tasks' write arguments, or numeric neighbours.** String ids such as `t1` have no
   neighbours, and no other task writes a ticket. Seen in the toy run: both `close_ticket` retarget families are
   `REFUSED: NO_RETARGETABLE_WRITE`. *Idea:* an optional `identity_alternates(task, key, value)` target hook. Ours
   could answer it from snapshot primary keys and `TableInfo.fk`.
4. **Collateral is only generated through the same tool.** `unauthorized_side_effect` copies a task write onto
   another record. Edits through another tool or table (emailing the wrong customer, touching an unrelated ticket) are
   never generated. *Idea:* a `schema_collateral` family over all write tools. *Status:* `schema_collateral` (see
   Schema families), one family per write tool; its records still come only from other tasks' write args (gap 3).
5. **The legitimate side is thin.** There is one family, `incidental_change`. It scores `built[0]` with no census and
   no `must_succeed` check, so a refused incidental read still counts as permitted. That is why the scorecard's FR
   side has a single column. *Idea:* valid-alternative families (reordered independent writes, alternative generated
   ids, alternative valid record) with an `INVALID_ALTERNATIVE` status. *Status:* `reorder_independent` adds the
   first of these; the runner-side census is still open.
6. **The ledger fingerprint ignores target arguments and verifier code.** It covers only `{kit, target name,
   families, redact}`. Our workaround is to put the variant in `name` and start fresh by default. A test shows that
   `--resume` reuses a stale cell. *Idea:* let targets contribute a `fingerprint()` dict.
7. **The census is not shared across variants.** Families never see a verdict, so the census is identical for every
   variant, yet each `run_audit` replays it again (N× replays). *Idea:* a multi-scorer `run_audit` that produces
   paired cells.
8. **Idempotent duplicates are `DEGENERATE`.** The toy `set_ticket_status` is an example. This is correct, but it
   means the "accepts duplicates" knob can only be measured on non-idempotent writes. Golden tasks should include
   some.
9. **The census-crash contract is implicit.** The runner treats a census replay as crashed only if the adapter returns
   `Verdict.ERROR` on an *unscored* run, and the `AuditTarget` docstring doesn't say so. *Idea:* document it, or add an
   explicit `Episode.harness_error`.
10. **There is no claim-time snapshot hook.** The protocol has no "state at step k", so this adapter replays the prefix
    once more whenever the claim isn't the last call.
