# Advisor Provider Schema Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Claude's provider-facing plan-review JSON schema exactly constrain every nested finding item that the local semantic validator accepts, and publish the fix under version 0.10.1.

**Architecture:** Keep the existing semantic validator authoritative for cross-field and session-aware rules. Add three distinct syntactic item schemas beside `PLAN_REVIEW_SCHEMA`, compose them into the provider schema, and verify schema-valid outputs are structurally compatible with the local validator. Fix forward to a new cache identity rather than changing the published 0.10.0 bundle in place.

**Threat model:** Treat every model/provider response as untrusted input. The
provider schema must reject missing, extra, or wrongly typed nested finding
fields before semantic validation; the local validator remains defense in depth
for session lineage, basis membership, supersession, and convergence rules.

**Tech Stack:** Python 3.11+, JSON Schema Draft 7, unittest, Codex plugin packaging.

## Global Constraints

- Do not weaken `_validate_review_result` or bypass the Advisor gate.
- Do not invoke a live model while reproducing or testing this bootstrap repair.
- Preserve `.codex-marketplace-install.json` and unrelated repository state.
- Any packaged payload change uses semantic version 0.10.1 and a new versioned installed cache.
- Commit and push every completed change.

---

### Task 1: Exact provider item schemas and fix-forward release

**Files:**
- Modify: `plugins/codex-orchestration/skills/codex-orchestration/scripts/fable_advisor_mcp.py`
- Modify: `tests/test_fable_advisor_mcp.py`
- Modify: `.codex-plugin/marketplace.json`
- Modify: `plugins/codex-orchestration/.codex-plugin/plugin.json`
- Modify: `CHANGELOG.md`
- Modify: `README.md`
- Modify: release/lifecycle tests and fixtures selected by `rg '0.10.0'`

**Interfaces:**
- Consumes: `_validate_review_result(value, request, previous_state)` exact nested object contracts.
- Produces: `PLAN_REVIEW_SCHEMA` with distinct exact schemas for `blocking_findings`, `c_backlog`, and `new_scope_requests`; plugin version `0.10.1`.

- [ ] **Step 1: Write failing provider-schema regression tests**

Add tests that validate literal malformed `{}` and extra-key items against `PLAN_REVIEW_SCHEMA`, assert the exact required keys and `additionalProperties: false`, and pass representative schema-valid items through `_validate_review_result`.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `/opt/homebrew/bin/python3 -m unittest -v tests.test_fable_advisor_mcp.AdvisorSessionContractTests.test_provider_schema_matches_exact_post_validator_item_contracts`

Expected: FAIL because each item schema is only `{\"type\": \"object\"}`.

- [ ] **Step 3: Implement minimal distinct item schemas**

Encode only syntactic constraints already enforced locally: exact properties/required keys, stable-ID patterns and bounds, string/array types and bounds, enums, nullable `basis_id`, and `additionalProperties: false`. Retain semantic membership, duplicate, causality, ledger, signal, and session checks in `_validate_review_result`.

- [ ] **Step 4: Verify focused and affected GREEN**

Run the new regression test, the full Advisor module, packaging, release, native-routing, and lifecycle tests.

- [ ] **Step 5: Bump release to 0.10.1 and verify full release gates**

Update every release identity selected by repository tests, run `scripts/preflight.py quick`, `scripts/preflight.py full`, `scripts/release_check.py`, and `git diff --check`.

- [ ] **Step 6: Independent exact-SHA review, commit, publish, install, and read back**

Commit and push the feature branch, obtain a read-only exact-SHA review of the schema contract, fast-forward `main`, push, install 0.10.1 through the native plugin manager, compare tracked source/cache bytes, and verify MCP initialize/tools-list/status without a model call.
