# One-Shot Advisor and Reviewer Workflow Implementation Brief

**Status:** Revised for user review

**Date:** 2026-08-12

**Repository:** `codex-orchestration`

**Current baseline:** plugin `0.10.2`, commit
`bfb248d98d389f3c8094b19347a620505d20f834`

## 1. Goal

Replace the automatic multi-round plan-review policy with one simple workflow:

- at most one Advisor call when the task has a written implementation plan;
- at most one Reviewer call after implementation;
- at most one consolidated correction after each review;
- deterministic verification after the final correction;
- no automatic model retry or re-review.

The root Sol model keeps responsibility for deciding whether a written plan is
useful, evaluating findings, implementing corrections, debugging tests,
integrating work, and reporting truthful final status.

## 2. Explicit non-goals

Do not introduce:

- Fast, Balanced, or Strict profiles;
- a prevalidator, classifier, risk engine, or risk-tier state;
- a cross-plugin workflow state machine;
- a shared runtime package between Advisor and Reviewer;
- new Advisor bridge fields, session behavior, JSON schemas, result fields, or
  MCP protocol; the two exact saved-policy compatibility constants may advance
  from 6 to 7 with the installed policy;
- new Reviewer request/result fields, attestation fields, or runtime behavior;
- automatic second reviews for security, production, migration, payment, or
  other high-impact tasks;
- a new durable task registry or telemetry subsystem.

The existing delivery circuit, authority checks, permissions, source-integrity
checks, model identity, timeout handling, process containment, and release gates
remain unchanged.

## 3. Canonical workflow

### 3.1 Planning

1. Sol decides whether the task benefits from a written implementation plan.
2. If there is no separate written plan, Advisor is not called.
3. If there is a written plan and Advisor is configured, Sol sends that exact
   plan for one review.
4. The single Advisor response is treated as one consolidated finding set.
5. Sol validates every finding against the user request, approved scope,
   acceptance criteria, safety invariants, repository evidence, and authority.
6. Sol applies at most one consolidated plan-correction batch.
7. Sol proceeds to implementation without a second Advisor call.

Canonical rule:

> No separate written plan to review means no Advisor call.

### 3.2 Implementation

1. Sol implements the original or once-corrected plan.
2. Sol preserves unrelated dirty state and stays inside user-approved authority.
3. Sol completes the implementation and runs the minimum focused checks needed
   to establish that the result is reviewable.
4. Sol binds Reviewer input to one exact completed technical SHA and complete
   required evidence.

### 3.3 Result review and correction

1. Sol invokes Fable Code Reviewer once with one fresh invocation ID.
2. Sol validates the consolidated findings against the approved task and
   evidence.
3. Sol applies at most one consolidated code-correction batch for confirmed
   actionable findings.
4. Sol does not invoke Reviewer again automatically.
5. Sol runs the full deterministic gate set on the final corrected result.
6. Sol performs root self-review and reports the final status truthfully.

### 3.4 Deterministic final gate

The exact commands are repository-specific. The policy must require every
configured acceptance check and, where applicable:

- focused tests;
- full tests;
- typecheck;
- build;
- lint;
- `git diff --check`;
- packaging, release, or lifecycle checks;
- root self-review of the final diff and task acceptance criteria.

Failed deterministic checks start normal debugging inside the existing task.
They do not authorize another Advisor or Reviewer call. Debugging may use as
many local code/test iterations as necessary, subject to the existing delivery
circuit and user authority; it is not a model-review loop.

## 4. Advisor outcomes

### `PLAN_APPROVED`

- Record `ADVISOR_APPROVED`.
- Implement the reviewed plan.
- Do not call Advisor again.

### `PLAN_REVISE`

- Validate the findings rather than applying them blindly.
- Apply one consolidated plan-correction batch.
- Record `ADVISOR_REVIEWED_WITH_CORRECTIONS`.
- Continue to implementation.
- Do not call Advisor again.
- Never claim that the corrected plan received `PLAN_APPROVED` or that Advisor
  reviewed its corrected hash.

### Advisor runtime, provider, schema, identity, timeout, or semantic failure

- Do not replay automatically, even when the bridge would permit an exact retry.
- Continue with Sol's own plan.
- Record `NOT_ADVISOR_APPROVED` and the bounded failure category.
- Do not persist best-effort as a user-global permission; this continuation is
  the configured one-shot policy for plan-review failure only and does not
  create deployment, destructive, credential, or external-mutation authority.

### Advisor not configured

- Sol owns planning.
- Record `ADVISOR_NOT_CONFIGURED` only when the task report needs to distinguish
  omission from failure.
- Do not present omission as approval.

## 5. Reviewer outcomes

### `RESULT_ACCEPTED`

- If source remains identical to the reviewed technical SHA and final local
  gates remain green, record `REVIEW_ACCEPTED`.
- Any later code change makes that exact-SHA acceptance stale.

### `RESULT_FIX_REQUIRED`

- Validate the findings rather than applying them blindly.
- Apply one consolidated correction batch for confirmed P0, P1, and blocking
  P2 findings tied to approved criteria.
- Keep non-blocking or out-of-scope suggestions in backlog unless the user
  authorizes them.
- Run the full deterministic gate set and root self-review.
- Record `REVIEWED_WITH_CORRECTIONS_LOCAL_VERIFIED` when all final gates pass.
- Explicitly state that Reviewer reviewed the earlier SHA, not the final
  corrected SHA.
- Do not call Reviewer again automatically.

### Reviewer runtime, provider, schema, identity, timeout, source-integrity,
cleanup, or attestation failure

- Do not retry automatically.
- Record `REVIEW_UNVERIFIED`.
- Finish the technical work only with deterministic verification and an explicit
  disclosure that no valid Reviewer result was obtained.
- Do not represent the result as Reviewer-accepted.

### Reviewer not configured or unavailable

- Record `REVIEW_UNVERIFIED` with reason `NOT_CONFIGURED` or `UNAVAILABLE`.
- Do not substitute readiness or a different model as acceptance.

### Final deterministic checks remain red

- Record `LOCAL_VERIFICATION_FAILED`.
- Continue ordinary debugging if the existing delivery circuit and authority
  permit it.
- Do not start another model review automatically.
- Do not complete, integrate, publish, deploy, or externally mutate until the
  required checks pass or the user changes the acceptance requirement.

## 6. User-authorized additional review

An additional Advisor or Reviewer call is allowed only after a direct,
task-specific user instruction such as:

> For this task, perform another review after the corrections.

Rules:

- Sol may warn that security, production, migration, payment, destructive, or
  similarly consequential work would benefit from another review.
- Sol must not launch that review without the user's direct instruction.
- A user-authorized additional review uses a fresh session/invocation ID and
  exact current plan/SHA.
- The authorization applies only to the named task and review role.
- It does not reset the delivery circuit or broaden mutation authority.
- Failure of the additional call is reported without another automatic retry.

## 7. Why the runtime bridges stay unchanged

The Advisor bridge currently permits up to five rounds and exact replay of a
failed request, but it does not initiate those calls. The generated
Codex Orchestration policy creates the loop. Setting policy to one call removes
the automatic loop without changing:

- `fable_advisor_mcp.py` session state;
- `MAX_REVIEW_ROUNDS` or `MAX_REVIEW_ATTEMPTS`;
- `review_plan` request or response schema;
- findings ledger and attestation semantics;
- MCP serverInfo `3.0.0`;
- provider/model identity and process-safety controls.

One narrow bridge compatibility edit is required: advance
`CURRENT_STATE_SCHEMA` and `CURRENT_POLICY_VERSION` from 6 to 7 so the bridge
accepts the newly installed saved policy. These constants only attest the loaded
policy generation. They must not alter the five-call bridge ceiling, replay
mechanics, request/result schema, session state, tool definitions, or MCP version.

The policy must accurately call this boundary instruction-enforced: a direct
caller can still invoke the bridge again, but the configured root workflow must
not do so automatically.

Fable Code Reviewer is already atomic and exact-SHA-bound. Its existing
`review_result` call, replay registry, acceptance gate, result attestation,
workspace isolation, and cleanup remain unchanged. No changes or new release are
required in the `fable-code-reviewer` repository for this feature.

## 8. Codex Orchestration changes

Target release: `0.11.0`. This is a user-visible policy behavior change, so use a
minor version rather than reusing or patching the installed `0.10.2` cache.

### `configure_native_routing.py`

- Set `ADVISOR_REVIEW_LIMIT = 1` as the generated-policy contract value.
- Replace every recursive Advisor instruction in `build_policy()` with the
  canonical workflow above.
- Add the natural written-plan condition.
- After `PLAN_REVISE`, instruct Sol to validate and apply one correction batch,
  record `ADVISOR_REVIEWED_WITH_CORRECTIONS`, and proceed.
- On Advisor failure, prohibit replay and proceed as `NOT_ADVISOR_APPROVED`.
- Add one final Fable Code Reviewer instruction for completed implementation:
  one `review_result`, one correction batch, deterministic final gates, no
  automatic re-review, and truthful exact-SHA wording.
- Allow another model review only on direct current-task user instruction.
- Keep readiness, model outcome, local verification, and release authority
  distinct.
- Advance `POLICY_VERSION` and `STATE_SCHEMA` from 6 to 7 only to identify and
  reinstall the changed saved policy; add no workflow-state fields.
- Advance installer `clientInfo.version` to `0.11.0`.

### `fable_advisor_mcp.py`

- Advance only `CURRENT_STATE_SCHEMA` and `CURRENT_POLICY_VERSION` from 6 to 7.
- Keep `MAX_REVIEW_ROUNDS = 5` and `MAX_REVIEW_ATTEMPTS = 5` unchanged as a
  transport/session safety ceiling rather than a workflow instruction.
- Keep `review_plan`, tool definitions, session state, exact replay capability,
  JSON schemas, results, and MCP serverInfo `3.0.0` unchanged.
- Add regression tests proving policy 7 is accepted and policy 6/future/mismatched
  policy identity fails closed before a model call.

### `routing_state.py`

- Accept exact schema/policy pair `7:7` with the same persisted field shape as
  schema 6.
- Keep schemas 1-6 readable for safe status, upgrade, and disable.
- Reject unknown future schemas and mismatched policy versions.
- Preserve the original pre-plugin restore snapshot when upgrading schema 6 to
  7.

### Skill and operator documentation

Update active contract text in:

- `plugins/codex-orchestration/skills/codex-orchestration/SKILL.md`;
- `plugins/codex-orchestration/skills/codex-orchestration/references/providers-and-models.md`;
- `README.md`;
- `RELEASE.md`;
- `CHANGELOG.md`.

Remove active claims about five automatic rounds. Historical changelog entries
and dated historical plans remain unchanged.

### Release identity

Update together:

- `plugins/codex-orchestration/.codex-plugin/plugin.json` to `0.11.0`;
- `configure_native_routing.py` client identity;
- `tests/plugin_lifecycle_smoke.py` current version;
- packaging and release-check version expectations;
- changelog release entry.

Do not bump Advisor MCP protocol/server version because its tool contract does
not change.

## 9. Test-first implementation plan

### Task A: Generated one-shot policy

Write RED tests in `tests/test_native_routing.py` proving:

- `ADVISOR_REVIEW_LIMIT == 1`;
- no written plan means no Advisor call;
- a written plan permits at most one Advisor call;
- `PLAN_REVISE` leads to one correction and implementation, not another review;
- Advisor failure leads to `NOT_ADVISOR_APPROVED` with no replay;
- one completed implementation leads to one `review_result`;
- `RESULT_FIX_REQUIRED` leads to one correction and deterministic gates;
- policy contains no automatic second Advisor or Reviewer route;
- only direct current-task user instruction permits another review.

Run the focused tests and observe failure against the five-round policy before
implementation. Then make the minimum `build_policy()` changes and observe
GREEN.

### Task B: Policy identity upgrade without a new state machine

Write RED tests in `tests/test_routing_state.py` and
`tests/test_native_routing.py` proving:

- schemas 1-7 are parsed with exact schema-specific fields;
- schema 7 uses the schema-6 data shape;
- policy version must equal schema version;
- schema 6 upgrades to 7 while preserving original restore snapshots, route
  selection, MCP overrides, and unrelated config;
- status distinguishes an old installed policy from the current one;
- disable still restores the original pre-plugin values;
- unknown/future schema fails closed.

Implement only the new schema pair, constants, and existing upgrade-path
extension. Do not add workflow counters, risk state, or cross-plugin state.

### Task C: Skill and packaging contract

Write RED assertions in:

- `tests/test_skill_contract.py`;
- `tests/test_packaging.py`;
- `tests/test_release_check.py`;
- `tests/plugin_lifecycle_smoke.py`.

The tests must require exact active phrases for:

- one Advisor call only when a written plan exists;
- one consolidated plan correction;
- one Reviewer call for completed implementation;
- one consolidated code correction;
- no automatic replay or re-review;
- deterministic final gates;
- direct user instruction as the only additional-review trigger;
- truthful statuses from sections 4 and 5;
- plugin `0.11.0`, policy/schema 7, unchanged MCP `3.0.0`.

Update documentation and release surfaces only after the focused contract tests
are RED.

### Task D: Regression and release gates

Run:

1. focused native-routing, routing-state, skill, packaging, and release tests;
2. the complete Advisor MCP suite to prove runtime/session/schema behavior is
   unchanged apart from exact policy-7 compatibility;
3. `python3 scripts/preflight.py quick`;
4. `python3 scripts/preflight.py full`;
5. `python3 scripts/release_check.py`;
6. `git diff --check`;
7. fresh exact-head final-tree review.

Because packaged skill and routing policy are security/state surfaces, include
malformed and negative policy/state tests and the repository-required exact-head
release attestation. Local mocks do not replace hosted Python/Windows/CodeQL or
required live Opus release qualification.

## 10. Acceptance matrix

| Scenario | Required behavior |
|---|---|
| No written plan | No Advisor call |
| Written plan + `PLAN_APPROVED` | One Advisor call; implementation proceeds as `ADVISOR_APPROVED` |
| Written plan + `PLAN_REVISE` | One Advisor call; one correction; `ADVISOR_REVIEWED_WITH_CORRECTIONS`; no second call |
| Advisor call fails | No retry; implementation proceeds as `NOT_ADVISOR_APPROVED` |
| Completed implementation | At most one Reviewer call with fresh ID and exact SHA |
| `RESULT_ACCEPTED`, unchanged SHA | `REVIEW_ACCEPTED` after final gates |
| `RESULT_FIX_REQUIRED` | One code-correction batch; local gates; `REVIEWED_WITH_CORRECTIONS_LOCAL_VERIFIED` |
| Reviewer fails or unavailable | No retry; `REVIEW_UNVERIFIED` |
| Tests fail after correction | Normal debugging; `LOCAL_VERIFICATION_FAILED` until green; no model call |
| Reviewer-accepted SHA changes | Acceptance becomes stale; no false final-SHA claim |
| Model suggests new scope | Backlog until user authority; no automatic expansion/review |
| Sol considers an extra review useful | Sol may propose it but does not call it |
| User explicitly requests another review | One fresh call for the exact current artifact; no further automatic retry |

## 11. Policy installation and activation

Publishing plugin `0.11.0` does not hot-reload the currently saved schema-6
policy or the MCP process loaded in an existing task.

After release and versioned-cache installation:

1. fully quit and reopen Codex Desktop;
2. start a new task;
3. run native status and read back the exact current seats;
4. preview normal setup using those same seats and efforts;
5. confirm that preview changes only plugin-owned policy/state from schema 6 to
   7 and preserves the original restore snapshot;
6. apply the setup through the existing App Server compare-and-swap path;
7. run `--status --require-effective`;
8. fully quit and reopen Codex Desktop again;
9. start a fresh task and verify the new one-shot policy text is active.

For the current known seat selection, the expected preview is equivalent to:

```bash
python3 <installed-skill>/scripts/configure_native_routing.py \
  --codex-bin /Applications/ChatGPT.app/Contents/Resources/codex \
  --executor-model gpt-5.6-sol \
  --executor-effort high \
  --advisor-opus \
  --advisor-effort high
```

Use the live status readback rather than blindly trusting this historical
example. Add `--apply` only after the preview is clean. If write, state
persistence, readback, or rollback fails, stop with the existing recovery status
and make no further policy mutation.

## 12. Delivery and verification boundaries

- Implement only in the Codex Orchestration repository.
- Leave Fable Code Reviewer source and installed cache unchanged.
- Preserve `.codex-marketplace-install.json` and unrelated dirty state.
- Use TDD for every behavioral change.
- Commit and push each verified logical release intentionally.
- Install through the canonical native plugin marketplace manager.
- Verify local HEAD, `origin/main`, independent remote readback, installed
  version, and source/cache byte parity.
- Do not call the feature active until policy schema 7 is effective after a full
  restart and fresh task.
- Do not create a formal tag or GitHub release until all required release
  qualifications, including any exact-head live-model gate, are satisfied.

## 13. Expected practical effect

- zero automatic review loops;
- zero automatic identical retries;
- maximum two external model calls in the normal planned implementation path;
- no Advisor cost for tasks that do not need a written plan;
- predictable elapsed time;
- independent plan and implementation critique retained;
- no new risk-classification architecture;
- no new cross-plugin runtime coupling;
- truthful status when corrections were not reviewed again;
- final authority, integration, and deterministic verification remain with Sol.
