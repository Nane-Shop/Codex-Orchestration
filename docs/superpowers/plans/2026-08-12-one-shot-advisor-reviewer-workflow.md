# One-Shot Advisor and Reviewer Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship Codex Orchestration 0.11.0 with a natural written-plan gate, at most one automatic Advisor call, at most one automatic Reviewer call, one correction batch after each, and no automatic model retry or re-review.

**Architecture:** Change only the generated orchestration policy and its persisted identity from schema/policy 6 to 7. Keep the Advisor bridge's five-round/five-attempt transport ceiling and exact-replay machinery unchanged, and keep Fable Code Reviewer runtime unchanged. Reuse the schema-6 persisted state shape for schema 7, then update active skill/release documentation and package identity together.

**Tech Stack:** Python 3.14 standard library, `unittest`, Codex native policy/App Server configurator, MCP JSON-RPC, Markdown plugin/skill contracts, Git lifecycle smoke tests.

## Global Constraints

- No Fast/Balanced/Strict profiles, prevalidator, classifier, risk engine, cross-plugin state machine, shared runtime package, or durable task registry.
- No separate written plan means no Advisor call.
- Normal path permits at most one Advisor call and one Fable Code Reviewer `review_result` call.
- `PLAN_REVISE` and `RESULT_FIX_REQUIRED` each permit one consolidated correction batch and no automatic re-review.
- Advisor or Reviewer runtime/schema/provider failure is not retried automatically and never counts as approval.
- Additional model review requires a direct current-task user instruction.
- Set policy version and saved-state schema to 7; preserve the schema-6 state shape and original restore snapshots.
- Keep `MAX_REVIEW_ROUNDS = 5`, `MAX_REVIEW_ATTEMPTS = 5`, Advisor request/result/session schemas, replay behavior, and MCP server version `3.0.0` unchanged.
- Do not modify the Fable Code Reviewer repository or installed cache.
- Release plugin version `0.11.0`; do not reuse `0.10.2` cache identity.
- Preserve `.codex-marketplace-install.json` and all unrelated user state.
- Every behavior change starts with an exact failing regression test; run `python3 scripts/preflight.py quick` and `python3 scripts/preflight.py full` before handoff.

---

### Task 1: Generated one-shot policy

**Files:**
- Modify: `tests/test_native_routing.py`
- Modify: `plugins/codex-orchestration/skills/codex-orchestration/scripts/configure_native_routing.py`

**Interfaces:**
- Consumes: `build_policy(executor, planner, advisor, designer=None) -> tuple[str, str]`.
- Produces: schema-7 managed policy text with `ADVISOR_REVIEW_LIMIT = 1` and explicit one-shot Advisor/Reviewer outcomes.

- [ ] **Step 1: Write failing policy behavior tests**

Replace recursive-round assertions with consumer-visible policy assertions proving: no written plan means no Advisor call; a written plan permits at most one Advisor call; `PLAN_REVISE` produces `ADVISOR_REVIEWED_WITH_CORRECTIONS` and implementation without a second call; failure produces `NOT_ADVISOR_APPROVED` without replay; completed implementation permits one `review_result`; `RESULT_FIX_REQUIRED` produces one correction batch and deterministic gates; corrected final SHA is not falsely Reviewer-accepted; only a direct current-task user instruction permits another model review.

- [ ] **Step 2: Run the focused test and witness RED**

```bash
/opt/homebrew/bin/python3 -m unittest -v tests.test_native_routing.NativeRoutingTests.test_policy_keeps_root_authority_and_pins_fork_none tests.test_native_routing.NativeRoutingTests.test_policy_is_compact_and_preserves_closure_runtime_controls
```

Expected: failures cite the old value `5`, recursive rounds, and missing Reviewer/status rules.

- [ ] **Step 3: Implement the minimum policy change**

Set `POLICY_VERSION = 7`, `STATE_SCHEMA = 7`, and `ADVISOR_REVIEW_LIMIT = 1`. Rewrite only `build_policy()` policy prose and route hints. Keep root authority, provider guards, Planner/Advisor independence, child isolation, permissions, and Designer/Executor behavior. Do not encode a classifier. Remove recursive round, automatic replay, and fail-closed-before-implementation wording. Add one exact Reviewer call after a complete implementation plus one correction/local-gate path.

- [ ] **Step 4: Run the focused native-routing module GREEN**

```bash
/opt/homebrew/bin/python3 -m unittest -v tests.test_native_routing
```

- [ ] **Step 5: Commit the verified policy unit**

```bash
git add tests/test_native_routing.py plugins/codex-orchestration/skills/codex-orchestration/scripts/configure_native_routing.py
git commit -m "feat: make model review workflow one-shot"
```

### Task 2: Schema-7 state and bridge compatibility

**Files:**
- Modify: `tests/test_routing_state.py`
- Modify: `tests/test_native_routing.py`
- Modify: `tests/test_fable_advisor_mcp.py`
- Modify: `plugins/codex-orchestration/skills/codex-orchestration/scripts/routing_state.py`
- Modify: `plugins/codex-orchestration/skills/codex-orchestration/scripts/fable_advisor_mcp.py`

**Interfaces:**
- Consumes: `validate_routing_state(value) -> dict`, saved-state setup/status/disable paths, Advisor routing attestation.
- Produces: accepted exact pair `schema=7, policy_version=7`, readable legacy schemas 1-6, fail-closed mismatches/future schemas, and bridge policy-7 attestation.

- [ ] **Step 1: Write failing state/attestation tests**

Add real state fixtures proving schema 7 uses exactly the schema-6 key shape, 6→7 setup preserves original `previous` snapshots and unrelated config, disable restores those snapshots, and `7:6`, `6:7`, `8:8`, unknown fields, and malformed booleans fail closed. Add Advisor tests proving bridge constants are 7 while `MAX_REVIEW_ROUNDS` and `MAX_REVIEW_ATTEMPTS` remain 5, policy 7 is accepted, and stale/future/mismatched identity fails before a model call.

- [ ] **Step 2: Run focused tests and witness RED**

```bash
/opt/homebrew/bin/python3 -m unittest -v tests.test_routing_state tests.test_native_routing tests.test_fable_advisor_mcp
```

Expected: schema 7 is rejected and bridge/configurator still attest schema 6.

- [ ] **Step 3: Implement exact schema compatibility**

Add `7: 7` to `_SCHEMA_POLICY_PAIRS`; route schema 7 through the existing schema-6 key/route rules. Set only `CURRENT_STATE_SCHEMA = 7` and `CURRENT_POLICY_VERSION = 7` in `fable_advisor_mcp.py`. Do not alter the five-call ceilings, session registry, request/result schemas, tool definitions, replay mechanics, or server identity.

- [ ] **Step 4: Run state, native, and Advisor modules GREEN**

```bash
/opt/homebrew/bin/python3 -m unittest -v tests.test_routing_state tests.test_native_routing tests.test_fable_advisor_mcp
```

- [ ] **Step 5: Commit the verified state unit**

```bash
git add tests/test_routing_state.py tests/test_native_routing.py tests/test_fable_advisor_mcp.py plugins/codex-orchestration/skills/codex-orchestration/scripts/routing_state.py plugins/codex-orchestration/skills/codex-orchestration/scripts/fable_advisor_mcp.py
git commit -m "feat: advance orchestration policy state to schema 7"
```

### Task 3: Skill contract and release 0.11.0

**Files:**
- Modify: `tests/test_skill_contract.py`
- Modify: `tests/test_packaging.py`
- Modify: `tests/test_release_check.py`
- Modify: `tests/plugin_lifecycle_smoke.py`
- Modify: `plugins/codex-orchestration/.codex-plugin/plugin.json`
- Modify: `plugins/codex-orchestration/skills/codex-orchestration/SKILL.md`
- Modify: `plugins/codex-orchestration/skills/codex-orchestration/references/providers-and-models.md`
- Modify: `plugins/codex-orchestration/skills/codex-orchestration/scripts/configure_native_routing.py`
- Modify: `README.md`
- Modify: `RELEASE.md`
- Modify: `CHANGELOG.md`

**Interfaces:**
- Consumes: plugin package metadata and human/agent-facing active policy documentation.
- Produces: one coherent 0.11.0 release whose active docs match runtime policy and whose historical entries stay intact.

- [ ] **Step 1: Write failing contract/release tests**

Require version `0.11.0`, policy/schema 7, MCP `3.0.0`, one written-plan Advisor call, one consolidated plan correction, one Reviewer call, one consolidated code correction, deterministic final gates, no automatic replay/re-review, the direct-user-only exception, and truthful statuses. Require active docs to omit recursive automatic-round wording while permitting historical changelog text and the unchanged bridge ceiling.

- [ ] **Step 2: Run contract tests and witness RED**

```bash
/opt/homebrew/bin/python3 -m unittest -v tests.test_skill_contract tests.test_packaging tests.test_release_check
```

Expected: active docs and package identity still describe 0.10.2/five-round workflow.

- [ ] **Step 3: Update release and active documentation**

Set manifest, installer client identity, lifecycle current version, and release-test expectations to `0.11.0`. Add the `0.11.0` changelog entry. Rewrite only active workflow sections in SKILL, provider reference, README, and RELEASE. Preserve historical changelog/releases and unrelated five-hour product-limit wording.

- [ ] **Step 4: Run contract modules GREEN**

```bash
/opt/homebrew/bin/python3 -m unittest -v tests.test_skill_contract tests.test_packaging tests.test_release_check
```

- [ ] **Step 5: Commit the release unit**

```bash
git add CHANGELOG.md README.md RELEASE.md tests plugins/codex-orchestration
git commit -m "release: publish one-shot orchestration 0.11.0"
```

### Task 4: Verification, exact-head audit, publication, and activation boundary

**Files:**
- Verify: all changed files from Tasks 1-3
- Preserve: `.codex-marketplace-install.json`

**Interfaces:**
- Consumes: committed candidate release 0.11.0.
- Produces: verified pushed source, marketplace-installed versioned cache, and an explicit restart/fresh-task activation checkpoint.

- [ ] **Step 1: Run the complete local gates**

```bash
/opt/homebrew/bin/python3 -m unittest -v tests.test_native_routing tests.test_routing_state tests.test_fable_advisor_mcp tests.test_skill_contract tests.test_packaging tests.test_release_check
/opt/homebrew/bin/python3 scripts/preflight.py quick
/opt/homebrew/bin/python3 scripts/preflight.py full
/opt/homebrew/bin/python3 scripts/release_check.py
/opt/homebrew/bin/python3 tests/plugin_lifecycle_smoke.py
git diff --check
```

Record exact pass counts, declared hosted skips, and missing live-model qualification separately.

- [ ] **Step 2: Review the exact committed tree read-only**

Bind the audit to `git rev-parse HEAD`. Verify no Advisor/Reviewer runtime-contract drift, no unexpected state keys, schema-7 malformed/future negative coverage, version coherence, and no tracked dirty paths. Any later commit invalidates this audit and requires a fresh one.

- [ ] **Step 3: Publish verified commits**

Push the feature branch. After exact remote readback, fast-forward `main` only if repository protections and current remote state permit a non-destructive fast-forward. Independently read back branch and `origin/main` SHA.

- [ ] **Step 4: Install through the native marketplace manager**

Use the canonical `codex plugin marketplace upgrade`, `codex plugin install`, and `codex plugin list` flow. Verify installed cache directory `0.11.0`, manifest identity, and byte parity for packaged policy/skill sources. Do not overwrite the existing `0.10.2` cache in place.

- [ ] **Step 5: Stop at the hard reload boundary**

Do not claim policy schema 7 active in this already-running task. Report that the user must fully quit/reopen Codex Desktop and start a fresh task. In that fresh task, read native status, preview/apply the same live seats through App Server CAS, require effective schema 7, then restart once more and verify one-shot policy text. No formal tag/release claim is allowed without the repository's live exact-head qualification.
