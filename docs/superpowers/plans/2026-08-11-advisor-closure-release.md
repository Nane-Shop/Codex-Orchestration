# Advisor Closure and Convergence Release Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:test-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Do not invoke any Advisor or Reviewer skill/tool.

**Goal:** Release Codex Orchestration 0.10.0 with a structured task-bound Advisor contract, runtime five-round and convergence enforcement, exact Opus 5 attestation, bounded process lifecycle, compact policy, and telemetry.

**Architecture:** Replace opaque Advisor review packets with a strict stateful session controller inside the existing MCP bridge. The controller recomputes immutable hashes, validates typed findings, tracks a bounded predecessor chain and convergence counters, and wraps one no-tools Claude process in an explicit process-group lifecycle. Keep Planner calls compatible and make runtime enforcement authoritative over prose.

**Tech Stack:** Python 3.11+ standard library, unittest, JSON-RPC MCP, Claude Code 2.1.220+, Codex native routing state.

## Global Constraints

- Preserve unrelated untracked `.codex-marketplace-install.json` exactly.
- Do not call live Advisor or Reviewer operations; local fake-model tests only.
- Every behavior change follows witnessed RED then GREEN.
- Plugin version is exactly `0.10.0`; MCP serverInfo is exactly `3.0.0`; `POLICY_VERSION=6`.
- Standard bundled Advisor examples/prompts lead with Opus 5 while omitted Advisor still means none.
- Exact Opus runtime identity is `claude-opus-5` plus provider `firstParty`; minimum CLI is `2.1.220`.
- Review maximum is exactly five, but non-convergence may halt earlier.
- Model timeout remains 600 seconds; host/MCP timeout includes explicit teardown headroom.
- No prompt, output, auth/account data, environment values, secrets, or executable path appears in telemetry.

---

### Task 1: Implement and publish the 0.10.0 closure release

**Files:**
- Modify: `plugins/codex-orchestration/skills/codex-orchestration/scripts/fable_advisor_mcp.py`
- Modify: `plugins/codex-orchestration/skills/codex-orchestration/scripts/configure_native_routing.py`
- Modify: `plugins/codex-orchestration/skills/codex-orchestration/SKILL.md`
- Modify: `plugins/codex-orchestration/skills/codex-orchestration/references/providers-and-models.md`
- Modify: `plugins/codex-orchestration/.mcp.json`
- Modify: `plugins/codex-orchestration/.codex-plugin/plugin.json`
- Modify: `plugins/codex-orchestration/skills/codex-orchestration/agents/openai.yaml`
- Modify: `scripts/review_attestation.py`
- Modify: `tests/test_fable_advisor_mcp.py`, `tests/test_native_routing.py`, `tests/test_skill_contract.py`, `tests/test_packaging.py`, `tests/test_review_attestation.py`, `tests/test_release_check.py`, `tests/plugin_lifecycle_smoke.py`
- Modify: `README.md`, `CHANGELOG.md`, `RELEASE.md`

**Interfaces:**
- `review_plan(...)` accepts the complete structured session contract from the design spec and rejects opaque `packet` calls.
- A process-local bounded session controller stores only hashes, versions, finding IDs, plan sizes, counters, and terminal state.
- The structured provider response contains `signal`, `summary`, `scope_status`, `blocking_findings`, `c_backlog`, and `new_scope_requests`.
- The public result adds `review_session`, `convergence`, `runtime_attestation`, `terminal`, `stop_reason`, and `review_attestation_sha256`.

- [ ] **Step 1: Add failing strict request/hash/session tests**

Cover exact tool schema, recomputed scope and plan hashes, round 1 genesis, predecessor attestation, monotonic plan version, duplicate/replayed/skipped rounds, terminal sessions, bounded-store overflow, and sixth-call rejection before a fake Claude log entry.

- [ ] **Step 2: Run session tests and record RED**

Run `/opt/homebrew/bin/python3 -m unittest -v tests.test_fable_advisor_mcp` and confirm the new cases fail because `review_plan` is still opaque/stateless.

- [ ] **Step 3: Implement strict request validation and bounded session state**

Use canonical sorted compact JSON for the immutable scope hash, UTF-8 SHA-256 for the current plan, exact stable IDs, and monotonic state. Refuse restart-resume at round greater than one and mark approval/halts terminal.

- [ ] **Step 4: Add failing typed-result and task-closure tests**

Cover A/A-uncertain/B blocking classes, C-only approval, C-only `PLAN_REVISE` rejection, approval-with-blockers rejection, unknown basis IDs, duplicate finding IDs, non-blocking scope requests, minimal correction, evidence requirements, supersession, and late finding requirements.

- [ ] **Step 5: Run typed-result tests and record RED**

Run the focused Advisor module and confirm failures point to the old `{signal, body}` result contract.

- [ ] **Step 6: Implement task-closure prompt, provider schema, and semantic validation**

Approve when all approved criteria and safety invariants are sufficiently closed, not when global risk is zero. Validate blocking/C separation locally after provider projection and compute the canonical response attestation only from validated fields.

- [ ] **Step 7: Add failing convergence replay tests**

Model the S2-C treadmill: two sequential rounds close at least 80 percent of prior blockers while adding new blockers and must halt; plan growth above 25 percent with new blockers and no authorized scope change must halt; unchanged carried blockers alone must not be mislabeled as scope creep; legitimate new-evidence A remains blocking.

- [ ] **Step 8: Implement convergence telemetry and early terminal halt**

Return counts by class, new/closed/carried blocker counts, plan growth ratio, consecutive non-converging rounds, terminal flag, and exact stop reason. Never treat a halt as approval.

- [ ] **Step 9: Add failing Opus and descendant-timeout tests**

Require exact current Opus `canonicalModel/provider`, reject legacy numeric-only identity and helpers, and prove a fake Claude descendant cannot survive timeout or emit a later marker. Assert the tool annotation is non-idempotent and MCP timeout exceeds child plus teardown reserve.

- [ ] **Step 10: Implement process-group lifecycle and runtime attestation**

Use `Popen`, a new process group, bounded communicate, graceful termination, escalation, and reap. Capture only Claude version, configured/canonical model, provider, effort, used models, elapsed milliseconds, timeout, and termination status.

- [ ] **Step 11: Add failing compact-policy and Opus-default tests**

Require closure/C/convergence/five-round/best-effort controls in generated policy, enforce a conservative maximum character count, reject duplicated detailed protocol, lead Advisor examples/prompts with Opus, and preserve omission as `advisor: none`.

- [ ] **Step 12: Implement compact policy, docs, attestation, and release identity**

Move schema detail to the bridge, bump policy/release versions, add exact-head Opus runtime qualification fields to the release attestation, and update README, skill, reference, changelog, release checklist, lifecycle smoke, and packaging identities.

- [ ] **Step 13: Run focused and full release gates**

Run `git diff --check`, `/opt/homebrew/bin/python3 scripts/preflight.py quick`, `/opt/homebrew/bin/python3 scripts/preflight.py full`, and `/opt/homebrew/bin/python3 scripts/release_check.py`. Preserve any hosted-only limitation as an explicit release note rather than a local PASS claim.

- [ ] **Step 14: Self-review, commit, and push**

Inspect complete diff and threat model coverage, confirm only owned files changed, commit with a `0.10.0` release message, push the current branch, and verify remote branch SHA equals local HEAD.
