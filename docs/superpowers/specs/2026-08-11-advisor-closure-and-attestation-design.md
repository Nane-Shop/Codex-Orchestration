# Advisor Closure, Convergence, and Attestation Design

## Goal

Make the bundled Claude Advisor optimize for completion of the user-approved
task, enforce a maximum of five reviews in runtime, stop non-converging review
loops, attest the exact Claude runtime identity, and terminate the complete
Claude process group on timeout.

## Existing defects

1. `review_plan` accepts one opaque `packet`, so the bridge cannot validate the
   original scope, criteria, plan version, round number, or cumulative ledger.
2. `PLAN_APPROVED` means “no material gap” globally rather than “the approved
   task is sufficiently closed”. Optional hardening can therefore block work.
3. The five-review maximum exists only in generated policy text. A sixth MCP
   call is mechanically accepted.
4. Stateless reviews have no attested predecessor chain or convergence state.
   Closing one set of findings while adding another can grow a plan indefinitely.
5. Opus `modelUsage` identity fields are optional, so a legacy-shaped payload
   lacks a first-party canonical-model attestation.
6. `subprocess.run` times out the direct child but does not provide explicit,
   tested process-group teardown for descendants.
7. The generated managed policy is long enough to be truncated in a developer
   message, which can remove important review controls from the active context.
8. User-facing quick-start/default surfaces still lead with Fable even though
   Opus 5 is the requested standard bundled Advisor model.

## Review-session contract

`review_plan` becomes a structured operation. Every call supplies:

- `review_session_id`: stable ID for the complete approval loop;
- `round_number`: integer 1 through 5;
- `previous_review_sha256`: empty only for round 1, otherwise the exact prior
  response attestation;
- `original_scope_sha256`: canonical hash of the immutable task closure scope;
- `task_goal`, `approved_scope`, `non_goals`;
- structured `acceptance_criteria` and `safety_invariants`, each with stable IDs;
- `plan_version`, `plan_sha256`, and the complete `current_plan`;
- `changed_surface`: stable criterion/invariant IDs changed since the last round;
- `findings_ledger`: compact structured dispositions from prior rounds.

The bridge recomputes both scope and plan hashes. It keeps only bounded,
non-secret session metadata in memory: hashes, versions, finding IDs, plan size,
round count, convergence count, and terminal state. A bridge restart loses this
metadata and therefore requires a new session beginning at round 1; it never
resumes a later round from caller claims alone.

## Advisor result contract

The model returns structured fields:

- `signal`: `PLAN_APPROVED` or `PLAN_REVISE`;
- `summary` and `scope_status`;
- `blocking_findings`: only classes `A`, `A-uncertain`, or `B`;
- `c_backlog`: optional hardening, refactors, theoretical edges, and
  reviewer-added criteria;
- `new_scope_requests`: non-blocking proposals requiring user authority.

Every blocking finding has a stable ID, an approved criterion or safety
invariant ID, evidence references, a concrete failure scenario, the smallest
sufficient correction, a causal source, and any superseded IDs. A later-round
new or reopened finding is valid only when it identifies new evidence or a
changed-surface causal link. `C` and scope-change proposals never justify
`PLAN_REVISE`.

The bridge enforces these invariants after provider-schema validation:

- `PLAN_REVISE` requires at least one valid blocking finding;
- `PLAN_APPROVED` requires zero blocking findings;
- unknown basis IDs, duplicate IDs, stale hashes/versions, invalid predecessor
  attestations, and reopened findings without new evidence fail closed;
- round 6 is rejected before any Claude process starts;
- a fifth `PLAN_REVISE` is terminal;
- two consecutive rounds that close at least 80 percent of the prior blockers
  while adding new blockers halt as `NON_CONVERGING_REVIEW`;
- plan growth above 25 percent without an authorized scope change, together
  with new blockers, halts as `UNAUTHORIZED_PLAN_GROWTH`.

The returned result includes round/session hashes, convergence telemetry,
terminal/halt state, exact route model and effort, first-party provider,
canonical runtime model, Claude Code version, process timing, and a canonical
`review_attestation_sha256`.

The stateful review tool advertises `idempotentHint=false`. Approval, a fifth
revision, a convergence halt, and a policy halt are terminal; later replay of
that session fails before model execution. The bounded store refuses new
sessions when full rather than silently evicting an active safety boundary.

## Model and process safety

Opus reviews require the exact current ten-key `modelUsage` record, including
`canonicalModel=claude-opus-5` and `provider=firstParty`. Legacy identity-free
usage is rejected for Opus. The Claude Code minimum becomes 2.1.220.

Opus becomes the leading/default model in bundled Advisor setup examples and
plugin prompts. Omitting the Advisor seat still means no Advisor; the release
does not silently create a subscription route.

Every Claude invocation runs in a new process group. On timeout the bridge sends
bounded graceful termination, escalates to a hard group kill, reaps the child,
and returns only stable redacted diagnostics. No prompt, model output, auth data,
environment value, or absolute executable path enters telemetry.

## Compact policy

The generated managed policy keeps routing isolation and user authority but
compresses the review loop into a task-closure recipe. The runtime contract is
authoritative; prose does not duplicate schema details. A test enforces a
bounded policy size and verifies that the closure, `C` backlog, convergence,
five-round, and best-effort semantics survive generation.

Release attestation gains an exact-head Opus runtime qualification record. A
local fake-model pass or configured route is readiness evidence, never proof of
a live Opus invocation.

## Threat model

- **Caller lies about hashes, round, or scope:** recomputation and in-memory
  predecessor state reject the call before model execution.
- **Model invents global hardening:** it is accepted only as non-blocking `C`
  backlog unless tied to an approved basis or evidenced safety invariant.
- **Model emits a false approval:** bridge rejects approval with blockers and
  attests the exact normalized result.
- **Caller replays or skips a round:** predecessor hash and monotonic
  round/version checks reject it.
- **Caller starts new sessions to bypass the cap:** policy treats a new session
  as a new approval loop; telemetry exposes the new session. Per-process storage
  is bounded and refuses overflow rather than evicting active safety state.
- **Claude or a hook leaves descendants:** process-group teardown and reap tests
  prove timeout containment.
- **Loaded policy is truncated:** compact-size tests keep all critical controls
  inside a conservative bound.

## Compatibility and release

The structured `review_plan` request is a deliberate breaking contract change,
released as Codex Orchestration 0.10.0. Planner operations remain compatible.
The skill, routing hints, README, changelog, release checklist, lifecycle smoke,
packaging tests, and installed versioned cache are updated together.

## Acceptance evidence

- focused RED then GREEN tests for every invariant and malformed path;
- regression replay representing the S2-C finding treadmill;
- process-group timeout test proving the descendant is gone;
- exact Opus/first-party attestation tests and legacy rejection;
- generated policy size and semantics tests;
- quick and full preflight, release check, plugin and skill validators;
- source/cache hash equality and live MCP configuration readback;
- committed and pushed source with remote SHA equality.
