#!/usr/bin/env python3
"""Root-directed, no-tools MCP bridge to sealed Claude subscription models.

The managed policy reserves stateless Planner and Advisor operations for the
root; MCP requests do not carry caller identity, so the server cannot enforce
that caller boundary. Each model call reloads and authorizes its seat from
routing state, rechecks first-party Claude Code authentication, and uses a fresh
no-tools/no-persistence process.
"""

from __future__ import annotations

import json
import hashlib
import math
import os
from pathlib import Path
import re
import signal
import stat
import shutil
import subprocess
import sys
import time
from typing import Any, Literal

import routing_state


STATE_FILENAME = ".codex-orchestration-routing.json"
MANAGED_MARKER = routing_state.MANAGED_MARKER
FABLE_MODEL = routing_state.FABLE_MODEL
OPUS_MODEL = routing_state.OPUS_MODEL
FABLE_SERVERS = routing_state.FABLE_SERVERS
SUPPORTED_EFFORTS = routing_state.FABLE_EFFORTS
# Claude Code currently reports this exact internal helper alongside Fable for
# some calls. Keep the runtime policy explicit and fail closed if that identity
# rotates or any other model appears.
FABLE_HELPER_MODEL = "claude-haiku-4-5-20251001"
FABLE_RESOLVED_PRIMARY_MODEL = "claude-opus-4-8"
REVIEWED_PRIMARY_MODELS_BY_ROUTE = {
    FABLE_MODEL: frozenset({FABLE_MODEL, FABLE_RESOLVED_PRIMARY_MODEL}),
    # The resolved Fable identity is not an alias for the separately sealed
    # Opus route. Opus remains primary-only until independently re-qualified.
    OPUS_MODEL: frozenset({OPUS_MODEL}),
}
ALLOWED_RUNTIME_MODELS = frozenset(
    {*REVIEWED_PRIMARY_MODELS_BY_ROUTE[FABLE_MODEL], FABLE_HELPER_MODEL}
)
ALLOWED_RUNTIME_MODELS_BY_PRIMARY = {
    FABLE_MODEL: ALLOWED_RUNTIME_MODELS,
    # No Opus helper identity has been independently verified. Fail closed if
    # Claude Code reports anything beyond the sealed primary.
    OPUS_MODEL: frozenset({OPUS_MODEL}),
}
CLAUDE_TIMEOUT_SECONDS = 570
PROCESS_TEARDOWN_GRACE_SECONDS = 1.0
PROCESS_TEARDOWN_RESERVE_SECONDS = 20
AUTH_TIMEOUT_SECONDS = 20
CLAUDE_MIN_VERSION = (2, 1, 220)
# Applies to the combined user-controlled text sent by one model operation.
MAX_INPUT_CHARS = 200_000
MAX_DIAGNOSTIC_OUTPUT_CHARS = 8_192
MAX_REVIEW_SESSIONS = 128
MAX_REVIEW_ROUNDS = 5
SHA256_PATTERN = "^[0-9a-f]{64}$"
REVIEW_REQUEST_FIELDS = (
    "review_session_id",
    "round_number",
    "previous_review_sha256",
    "original_scope_sha256",
    "task_goal",
    "approved_scope",
    "non_goals",
    "acceptance_criteria",
    "safety_invariants",
    "plan_version",
    "plan_sha256",
    "current_plan",
    "changed_surface",
    "findings_ledger",
)
# Process-local safety boundary. Values deliberately contain only bounded hashes,
# versions, finding IDs, sizes, counters, and terminal state.
_REVIEW_SESSIONS: dict[str, dict[str, Any]] = {}
PLAN_REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "signal": {
            "type": "string",
            "enum": ["PLAN_APPROVED", "PLAN_REVISE"],
        },
        "summary": {"type": "string", "minLength": 1},
        "scope_status": {"type": "string", "enum": ["closed", "open"]},
        "blocking_findings": {"type": "array", "items": {"type": "object"}},
        "c_backlog": {"type": "array", "items": {"type": "object"}},
        "new_scope_requests": {"type": "array", "items": {"type": "object"}},
    },
    "required": [
        "signal",
        "summary",
        "scope_status",
        "blocking_findings",
        "c_backlog",
        "new_scope_requests",
    ],
    "additionalProperties": False,
}
SENSITIVE_ENV = {
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_AWS_API_KEY",
    "ANTHROPIC_AWS_BASE_URL",
    "ANTHROPIC_AWS_WORKSPACE_ID",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_BEDROCK_BASE_URL",
    "ANTHROPIC_BEDROCK_MANTLE_BASE_URL",
    "ANTHROPIC_BETAS",
    "ANTHROPIC_CUSTOM_HEADERS",
    "ANTHROPIC_CUSTOM_MODEL_OPTION",
    "ANTHROPIC_CUSTOM_MODEL_OPTION_DESCRIPTION",
    "ANTHROPIC_CUSTOM_MODEL_OPTION_NAME",
    "ANTHROPIC_CUSTOM_MODEL_OPTION_SUPPORTED_CAPABILITIES",
    "ANTHROPIC_DEFAULT_FABLE_MODEL",
    "ANTHROPIC_DEFAULT_FABLE_MODEL_DESCRIPTION",
    "ANTHROPIC_DEFAULT_FABLE_MODEL_NAME",
    "ANTHROPIC_DEFAULT_FABLE_MODEL_SUPPORTED_CAPABILITIES",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL_DESCRIPTION",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL_NAME",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL_SUPPORTED_CAPABILITIES",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL_DESCRIPTION",
    "ANTHROPIC_DEFAULT_OPUS_MODEL_NAME",
    "ANTHROPIC_DEFAULT_OPUS_MODEL_SUPPORTED_CAPABILITIES",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL_DESCRIPTION",
    "ANTHROPIC_DEFAULT_SONNET_MODEL_NAME",
    "ANTHROPIC_DEFAULT_SONNET_MODEL_SUPPORTED_CAPABILITIES",
    "ANTHROPIC_FOUNDRY_API_KEY",
    "ANTHROPIC_FOUNDRY_AUTH_TOKEN",
    "ANTHROPIC_FOUNDRY_BASE_URL",
    "ANTHROPIC_FOUNDRY_RESOURCE",
    "ANTHROPIC_MODEL",
    "ANTHROPIC_SMALL_FAST_MODEL",
    "ANTHROPIC_VERTEX_BASE_URL",
    "ANTHROPIC_VERTEX_PROJECT_ID",
    "CLAUDE_CODE_EFFORT_LEVEL",
    "CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY",
    "CLAUDE_CODE_SKIP_BEDROCK_AUTH",
    "CLAUDE_CODE_SKIP_FOUNDRY_AUTH",
    "CLAUDE_CODE_SKIP_MANTLE_AUTH",
    "CLAUDE_CODE_SKIP_VERTEX_AUTH",
    "CLAUDE_CODE_SUBAGENT_MODEL",
    "CLAUDE_CODE_USE_ANTHROPIC_AWS",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_MANTLE",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
}
STALE_BRIDGE_RECOVERY = (
    "If Codex Orchestration changed after this task started, run fresh native status. "
    "When status reports first-party login ready, fully quit and reopen Codex and "
    "start a new task; do not re-authenticate solely for this loaded-bridge failure."
)

ADVISOR_SYSTEM_PROMPT = """You are the configured Claude model acting only as a plan advisor to Codex's root orchestrator.
Optimize for closure of the supplied user-approved task, not global risk elimination. Review only the approved criteria and safety invariants. Classes A, A-uncertain, and B may block when evidenced and tied to an approved basis. Put optional hardening, refactors, theoretical edges, and reviewer-added criteria in non-blocking C backlog. Put proposed scope changes in non-blocking new_scope_requests. Do not edit, call tools, spawn, contact other seats, or implement.

Return exactly signal, summary, scope_status, blocking_findings, c_backlog, and new_scope_requests under the supplied schema. PLAN_REVISE requires a valid blocker; PLAN_APPROVED requires none. Each blocker needs a stable ID, approved basis ID, evidence, concrete failure scenario, smallest sufficient correction, causal source/reference, superseded IDs, and any new evidence. Preserve IDs. A later new or reopened blocker needs new evidence or a changed-surface causal link. Report only to the root."""

PLANNER_CREATE_SYSTEM_PROMPT = """You are the configured Claude model acting only as a plan author for Codex's root orchestrator.
Create a concrete implementation plan from the supplied self-contained packet. Include constraints, ownership, sequencing, acceptance criteria, security and compatibility boundaries, and behavioral plus regression verification. Do not edit files, call tools, spawn agents, contact the Advisor or executors, or attempt implementation.

Your first non-empty line must be exactly PLAN_DRAFT. Return the complete draft plan after that signal. Report only to the root orchestrator."""

PLANNER_REVISE_SYSTEM_PROMPT = """You are the configured Claude model acting only as a stateless plan reviser for Codex's root orchestrator.
Revise the supplied canonical current plan using the original task, its source plan version, the latest Advisor critique, and the compact cumulative history. Do not edit files, call tools, spawn agents, contact the Advisor or executors, or attempt implementation.

Your response must use exactly this top-level structure:
PLAN_REVISION

## FINDINGS_LEDGER
For every finding in the latest critique, include its stable Advisor finding ID exactly once and mark it INCORPORATED or REJECTED. Give a concrete reason for either disposition. Preserve relevant cumulative-history IDs.

## REVISED_PLAN
Provide the complete revised plan, clearly identifying its source plan version and revised version.

Both sections must be non-empty. Your first non-empty line must be exactly PLAN_REVISION. The root orchestrator, not you, validates finding coverage and plan-version semantics. Report only to the root orchestrator."""

# Backward-compatible public constant for existing importers.
SYSTEM_PROMPT = ADVISOR_SYSTEM_PROMPT

Seat = Literal["planner", "advisor"]
FailureKind = Literal[
    "usage_limit",
    "rate_limited",
    "model_unavailable",
    "authentication_failed",
    "provider_unavailable",
    "unknown_cli_failure",
]
FAILURE_KIND_PRECEDENCE: tuple[FailureKind, ...] = (
    "usage_limit",
    "rate_limited",
    "model_unavailable",
    "authentication_failed",
    "provider_unavailable",
)
# These whole-message shapes were conservatively audited against the installed
# Claude Code 2.1.212 binary. They are not a stable provider API: wording drift,
# including an unaudited fast-limit reset suffix, safely falls back to unknown.
_FAILURE_SIGNATURES: dict[FailureKind, frozenset[str]] = {
    "usage_limit": frozenset(
        {
            "you've hit your monthly spend limit.",
            (
                "you've hit your monthly spend limit. run /usage-credits to manage "
                "your limit and keep using fable 5 or switch models to continue "
                "this chat."
            ),
            "you've hit your monthly spend limit. /model to switch models.",
            "you've hit your fast limit",
        }
    ),
    "rate_limited": frozenset(
        {
            "server is temporarily limiting requests (not your usage limit)",
            "rate limited (429). polling too frequently.",
        }
    ),
    "model_unavailable": frozenset(
        {
            f"api model not found: {FABLE_MODEL}",
            f"api model not found: {OPUS_MODEL}",
        }
    ),
    "authentication_failed": frozenset(
        {
            "authentication failed",
            "authentication failed: invalid or missing api key",
        }
    ),
    "provider_unavailable": frozenset(
        {
            "service unavailable",
            "serviceunavailable",
            "serviceunavailableexception",
        }
    ),
    "unknown_cli_failure": frozenset(),
}
_FAILURE_POLICY: dict[FailureKind, tuple[bool, str]] = {
    "usage_limit": (
        True,
        "Wait for the Claude usage window to reset, then retry the same sealed route.",
    ),
    "rate_limited": (
        True,
        "Wait before retrying the same sealed Claude route.",
    ),
    "model_unavailable": (
        True,
        "Wait for the pinned Claude model to become available; do not substitute "
        "another model.",
    ),
    "authentication_failed": (
        False,
        "Run `claude auth login` in a trusted local terminal, then retry the same "
        "sealed route.",
    ),
    "provider_unavailable": (
        True,
        "Wait for the Claude provider to recover, then retry the same sealed route.",
    ),
    "unknown_cli_failure": (
        False,
        "The Claude CLI/provider failure could not be safely classified; wait or "
        "diagnose the Claude CLI in a trusted local terminal before retrying.",
    ),
}
_STRUCTURED_FABLE_USAGE_LIMIT_MESSAGE = (
    "You've reached your Fable 5 limit. Run /usage-credits to continue or "
    "switch models with /model."
)
OPUS_USAGE_FIELDS = {
    "inputTokens",
    "outputTokens",
    "cacheReadInputTokens",
    "cacheCreationInputTokens",
    "webSearchRequests",
    "costUSD",
    "contextWindow",
    "maxOutputTokens",
    "canonicalModel",
    "provider",
}


class AdvisorError(RuntimeError):
    """Fail-closed error for any bundled Claude bridge operation."""


class ClaudeProcessFailure(AdvisorError):
    """Bounded projection of a nonzero Claude model subprocess exit."""

    failure_label = "model subprocess"

    def __init__(self, failure_kind: FailureKind, exit_code: int) -> None:
        if failure_kind not in _FAILURE_POLICY:
            raise ValueError("invalid Claude process failure kind")
        if type(exit_code) is not int:
            raise ValueError("Claude process exit code must be an integer")
        retryable, operator_action = _FAILURE_POLICY[failure_kind]
        self.failure_kind = failure_kind
        self.exit_code = exit_code
        self.retryable = retryable
        self.operator_action = operator_action
        super().__init__()

    def __str__(self) -> str:
        return (
            f"Claude {self.failure_label} failed "
            f"(exit {self.exit_code}; output withheld)."
        )


class ClaudeAuthenticationProcessFailure(ClaudeProcessFailure):
    """Bounded projection of a nonzero Claude authentication-check exit."""

    failure_label = "Code authentication check"


def _normalize_failure_output(value: object) -> str | None:
    if not isinstance(value, str) or len(value) > MAX_DIAGNOSTIC_OUTPUT_CHARS:
        return None
    if any(
        ord(character) > 0x7E
        or ord(character) < 0x20
        and character not in {"\t", "\n", "\r"}
        for character in value
    ):
        return None
    return " ".join(value.casefold().split())


def _reject_duplicate_json_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    payload: dict[str, object] = {}
    for key, value in pairs:
        if key in payload:
            raise ValueError("duplicate JSON key")
        payload[key] = value
    return payload


def _reject_nonfinite_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant: {value}")


def _is_structured_fable_usage_limit(value: object) -> bool:
    if _normalize_failure_output(value) is None:
        return False
    try:
        payload = json.loads(
            value,
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_nonfinite_json_constant,
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        return False
    if type(payload) is not dict:
        return False
    return (
        payload.get("type") == "result"
        and payload.get("subtype") == "success"
        and type(payload.get("is_error")) is bool
        and payload["is_error"] is True
        and type(payload.get("api_error_status")) is int
        and payload["api_error_status"] == 429
        and payload.get("terminal_reason") == "api_error"
        and payload.get("result") == _STRUCTURED_FABLE_USAGE_LIMIT_MESSAGE
        and type(payload.get("modelUsage")) is dict
        and payload["modelUsage"] == {}
    )


def classify_claude_process_failure(
    stdout: object, stderr: object
) -> FailureKind:
    """Classify only complete, exact, normalized diagnostics without returning them."""

    normalized_stdout = _normalize_failure_output(stdout)
    normalized_stderr = _normalize_failure_output(stderr)
    if normalized_stdout is None or normalized_stderr is None:
        return "unknown_cli_failure"
    if stderr == "" and _is_structured_fable_usage_limit(stdout):
        return "usage_limit"

    matched: set[FailureKind] = set()
    for normalized in (normalized_stdout, normalized_stderr):
        if not normalized:
            continue
        channel_matches = {
            kind
            for kind in FAILURE_KIND_PRECEDENCE
            if normalized in _FAILURE_SIGNATURES[kind]
        }
        if len(channel_matches) != 1:
            return "unknown_cli_failure"
        matched.update(channel_matches)
    if len(matched) != 1:
        return "unknown_cli_failure"
    for kind in FAILURE_KIND_PRECEDENCE:
        if matched == {kind}:
            return kind
    return "unknown_cli_failure"


def _process_failure(
    result: subprocess.CompletedProcess[str], *, authentication_check: bool
) -> ClaudeProcessFailure:
    kind = classify_claude_process_failure(result.stdout, result.stderr)
    failure_type = (
        ClaudeAuthenticationProcessFailure
        if authentication_check
        else ClaudeProcessFailure
    )
    return failure_type(kind, result.returncode)


def codex_home() -> Path:
    value = os.environ.get("CODEX_HOME")
    return Path(value).expanduser() if value else Path.home() / ".codex"


def _canonical_posix_identity() -> str:
    try:
        import pwd

        name = pwd.getpwuid(os.getuid()).pw_name
    except (ImportError, KeyError, OSError, AttributeError) as exc:
        raise AdvisorError(
            "Could not determine the canonical POSIX login identity for Claude Code."
        ) from exc
    if not isinstance(name, str) or not name.strip():
        raise AdvisorError(
            "Could not determine the canonical POSIX login identity for Claude Code."
        )
    return name


def sanitized_environment() -> dict[str, str]:
    common_names = ("PATH", "LANG", "LC_ALL", "LC_CTYPE")
    if os.name == "nt":
        canonical_names = (
            *common_names,
            "SystemRoot",
            "ComSpec",
            "PATHEXT",
            "TEMP",
            "TMP",
            "USERPROFILE",
        )
        inherited = {name.casefold(): value for name, value in os.environ.items()}
        env = {
            canonical: inherited[canonical.casefold()]
            for canonical in canonical_names
            if canonical.casefold() in inherited
        }
    else:
        env = {
            name: os.environ[name]
            for name in (*common_names, "HOME", "TMPDIR")
            if name in os.environ
        }
        identity = _canonical_posix_identity()
        env["USER"] = identity
        env["LOGNAME"] = identity
    env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
    return env


def resolve_claude() -> Path:
    found = shutil.which("claude")
    if found:
        return Path(found).resolve()
    candidates = (
        Path.home() / ".local" / "bin" / "claude",
        Path("/usr/local/bin/claude"),
        Path("/opt/homebrew/bin/claude"),
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise AdvisorError("Claude Code is not installed or `claude` is not on PATH.")


def _terminate_process_group(process: subprocess.Popen[str]) -> str:
    if process.poll() is not None:
        process.communicate()
        return "completed"
    try:
        if os.name == "nt":
            process.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            os.killpg(process.pid, signal.SIGTERM)
        process.communicate(timeout=PROCESS_TEARDOWN_GRACE_SECONDS)
        return "terminated"
    except (OSError, subprocess.TimeoutExpired):
        try:
            if os.name == "nt":
                process.kill()
            else:
                os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.communicate()
        return "killed"


def _run_claude_process(
    command: list[str],
    *,
    input_text: str | None,
    timeout_seconds: float,
    timeout_message: str,
    start_error_message: str,
    environment: dict[str, str] | None = None,
) -> tuple[subprocess.CompletedProcess[str], int, str]:
    popen_options: dict[str, Any] = {}
    if os.name == "nt":
        popen_options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_options["start_new_session"] = True
    started = time.monotonic()
    try:
        process = subprocess.Popen(
            command,
            env=environment if environment is not None else sanitized_environment(),
            text=True,
            stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            **popen_options,
        )
    except OSError as exc:
        raise AdvisorError(start_error_message) from exc
    try:
        stdout, stderr = process.communicate(input=input_text, timeout=timeout_seconds)
        termination_status = "completed"
    except subprocess.TimeoutExpired as exc:
        _terminate_process_group(process)
        raise AdvisorError(timeout_message) from exc
    elapsed_ms = max(0, round((time.monotonic() - started) * 1000))
    return (
        subprocess.CompletedProcess(command, process.returncode, stdout, stderr),
        elapsed_ms,
        termination_status,
    )


def _run_json(command: list[str], *, timeout: int) -> dict[str, Any]:
    result, _elapsed_ms, _termination_status = _run_claude_process(
        command,
        input_text=None,
        timeout_seconds=timeout,
        timeout_message="Claude Code authentication check timed out.",
        start_error_message="Could not run Claude Code authentication check.",
    )
    if result.returncode != 0:
        raise _process_failure(result, authentication_check=True)
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise AdvisorError("Claude Code returned malformed JSON.") from exc
    if not isinstance(payload, dict):
        raise AdvisorError("Claude Code returned an unexpected JSON value.")
    return payload


def _claude_version(executable: Path) -> str:
    result, _elapsed_ms, _termination_status = _run_claude_process(
        [str(executable), "--version"],
        input_text=None,
        timeout_seconds=AUTH_TIMEOUT_SECONDS,
        timeout_message="Claude Code version check timed out.",
        start_error_message="Could not run Claude Code version check.",
    )
    if result.returncode != 0:
        raise _process_failure(result, authentication_check=True)
    match = re.fullmatch(r"\s*(\d+)\.(\d+)\.(\d+)(?:\s+\(Claude Code\))?\s*", result.stdout)
    if match is None:
        raise AdvisorError("Claude Code returned an unparseable version.")
    version = tuple(int(part) for part in match.groups())
    if version < CLAUDE_MIN_VERSION:
        required = ".".join(map(str, CLAUDE_MIN_VERSION))
        raise AdvisorError(f"Claude Code {required} or newer is required.")
    return ".".join(map(str, version))


def check_claude_auth(claude: Path | None = None) -> dict[str, str]:
    executable = claude or resolve_claude()
    payload = _run_json(
        [str(executable), "auth", "status", "--json"],
        timeout=AUTH_TIMEOUT_SECONDS,
    )
    subscription = payload.get("subscriptionType")
    if not (
        payload.get("loggedIn") is True
        and payload.get("authMethod") == "claude.ai"
        and payload.get("apiProvider") == "firstParty"
        and isinstance(subscription, str)
        and subscription in {"pro", "max", "team"}
    ):
        raise AdvisorError(
            "Claude Code must be logged in through a first-party Pro, Max, or Team "
            "account; run `claude auth login` and try again."
        )
    return {"auth_method": "claude.ai", "api_provider": "firstParty"}


def _read_routing_state(home: Path | None = None) -> dict[str, Any]:
    root = home or codex_home()
    path = root / STATE_FILENAME
    try:
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise AdvisorError("The saved routing state is not a regular file.")
        if info.st_nlink != 1:
            raise AdvisorError("The saved routing state has multiple hard links.")
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise AdvisorError(
            "A bundled Claude planning model is not configured; run setup first."
        ) from exc
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AdvisorError("Could not read valid routing state.") from exc
    try:
        state = routing_state.validate_routing_state(payload)
    except routing_state.RoutingStateError as exc:
        raise AdvisorError("The saved routing state is invalid.") from exc
    config_file = state["config_file"]
    try:
        belongs_to_home = (
            Path(config_file).expanduser().resolve()
            == (root / "config.toml").expanduser().resolve()
        )
    except (OSError, RuntimeError) as exc:
        raise AdvisorError("The saved routing state belongs to another Codex home.") from exc
    if not belongs_to_home:
        raise AdvisorError("The saved routing state belongs to another Codex home.")
    return state


def _validate_seat(seat: str) -> Seat:
    if seat not in {"planner", "advisor"}:
        raise AdvisorError("Claude planning seat must be `planner` or `advisor`.")
    return seat  # type: ignore[return-value]


def _validate_fable_route(route: Any, *, seat: Seat) -> dict[str, str]:
    if not isinstance(route, dict) or route.get("kind") not in {
        "fable",
        "claude_subscription",
    }:
        raise AdvisorError(
            f"A bundled Claude model is not the configured {seat}."
        )
    return {"model": route["model"], "effort": route["effort"]}


def load_fable_route(
    home: Path | None = None, *, seat: str = "advisor"
) -> dict[str, str]:
    """Load and validate one explicitly authorized bundled Claude seat.

    ``seat`` defaults to Advisor for compatibility with the original bridge.
    It is deliberately constrained and resolved from disk on every invocation.
    """

    selected = _validate_seat(seat)
    payload = _read_routing_state(home)
    return _validate_fable_route(payload.get(selected), seat=selected)


def _validate_inputs(operation: str, **values: Any) -> dict[str, str]:
    checked: dict[str, str] = {}
    for name, value in values.items():
        if not isinstance(value, str) or not value.strip():
            raise AdvisorError(f"`{name}` must be a non-empty string for {operation}.")
        checked[name] = value
    if sum(len(value) for value in checked.values()) > MAX_INPUT_CHARS:
        raise AdvisorError(
            f"{operation} input exceeds the {MAX_INPUT_CHARS}-character combined limit."
        )
    return checked


def _first_non_empty_line(response: str) -> str:
    return next((line.strip() for line in response.splitlines() if line.strip()), "")


def _validate_runtime_models(
    usage: Any, primary_model: str = FABLE_MODEL
) -> list[str]:
    allowed_models = ALLOWED_RUNTIME_MODELS_BY_PRIMARY.get(primary_model)
    reviewed_primaries = REVIEWED_PRIMARY_MODELS_BY_ROUTE.get(primary_model)
    if allowed_models is None or reviewed_primaries is None:
        raise AdvisorError("The configured Claude primary model is not sealed.")
    policy_label = "Fable" if primary_model == FABLE_MODEL else "Claude"
    primary_label = (
        "Claude Fable 5" if primary_model == FABLE_MODEL else "Claude Opus 5"
    )
    if not isinstance(usage, dict):
        raise AdvisorError("Runtime metadata has a malformed modelUsage mapping.")
    raw_models = list(usage)
    if not all(isinstance(model, str) and bool(model.strip()) for model in raw_models):
        raise AdvisorError(
            f"Runtime metadata reported a model outside the allowed {policy_label} "
            "runtime policy."
        )
    opus_identity_fields = {"canonicalModel", "provider"}
    for model_usage in usage.values():
        if not isinstance(model_usage, dict) or not model_usage:
            raise AdvisorError("Runtime metadata has a malformed modelUsage value.")
        if primary_model == OPUS_MODEL and set(model_usage) != OPUS_USAGE_FIELDS:
            raise AdvisorError(
                "Claude Opus runtime metadata must contain the exact ten-key identity record."
            )
        present_identity_fields = opus_identity_fields.intersection(model_usage)
        if present_identity_fields and (
            primary_model != OPUS_MODEL
            or present_identity_fields != opus_identity_fields
            or model_usage["canonicalModel"] != OPUS_MODEL
            or model_usage["provider"] != "firstParty"
        ):
            raise AdvisorError("Runtime metadata has a malformed modelUsage value.")
        numeric_field_count = 0
        for field, value in model_usage.items():
            if field in opus_identity_fields:
                continue
            is_nonnegative_finite_number = (
                type(value) is int
                and value >= 0
                or type(value) is float
                and math.isfinite(value)
                and value >= 0
            )
            if (
                not isinstance(field, str)
                or not field.strip()
                or not is_nonnegative_finite_number
            ):
                raise AdvisorError(
                    "Runtime metadata has a malformed modelUsage value."
                )
            numeric_field_count += 1
        if numeric_field_count == 0:
            raise AdvisorError("Runtime metadata has a malformed modelUsage value.")
    used_models = sorted(raw_models)
    if not set(used_models).intersection(reviewed_primaries):
        raise AdvisorError(
            f"Runtime metadata did not confirm the pinned {primary_label} primary "
            "model or a reviewed resolved identity."
        )
    if not set(used_models).issubset(allowed_models):
        raise AdvisorError(
            f"Runtime metadata reported a model outside the allowed {policy_label} "
            "runtime policy."
        )
    return used_models


def _normalize_model_payload(
    payload: Any, *, display_name: str, operation: str
) -> dict[str, Any]:
    """Accept one legacy result object or one unambiguous result event."""

    message = f"{display_name} {operation} returned an unexpected response."
    if isinstance(payload, dict):
        if "type" in payload and payload.get("type") != "result":
            raise AdvisorError(message)
        if "subtype" in payload and payload.get("subtype") != "success":
            raise AdvisorError(message)
        return payload
    if not isinstance(payload, list) or not payload:
        raise AdvisorError(message)
    if not all(
        isinstance(event, dict)
        and isinstance(event.get("type"), str)
        and bool(event["type"])
        for event in payload
    ):
        raise AdvisorError(message)
    result_events = [event for event in payload if event.get("type") == "result"]
    if len(result_events) != 1:
        raise AdvisorError(message)
    selected = result_events[0]
    if selected.get("subtype") not in (None, "success"):
        raise AdvisorError(message)
    content_fields = {"result", "modelUsage", "structured_output"}
    if any(
        event is not selected and content_fields.intersection(event)
        for event in payload
    ):
        raise AdvisorError(message)
    return selected


def _validate_review_output(value: Any, *, display_name: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != set(PLAN_REVIEW_SCHEMA["required"]):
        raise AdvisorError(
            f"{display_name} plan review returned invalid structured output."
        )
    signal = value.get("signal")
    if (
        not isinstance(signal, str)
        or signal not in {"PLAN_APPROVED", "PLAN_REVISE"}
        or not isinstance(value.get("summary"), str)
        or not value["summary"].strip()
        or value.get("scope_status") not in {"closed", "open"}
        or any(
            not isinstance(value.get(field), list)
            for field in ("blocking_findings", "c_backlog", "new_scope_requests")
        )
    ):
        raise AdvisorError(
            f"{display_name} plan review returned invalid structured output."
        )
    return {**value, "summary": value["summary"].strip()}


def _review_response(payload: dict[str, Any], *, display_name: str) -> tuple[dict[str, Any], str]:
    structured_present = "structured_output" in payload
    result_present = "result" in payload
    structured = (
        _validate_review_output(
            payload.get("structured_output"),
            display_name=display_name,
        )
        if structured_present
        else None
    )
    legacy: dict[str, Any] | None = None
    if result_present:
        raw_result = payload.get("result")
        if not isinstance(raw_result, str):
            raise AdvisorError(
                f"{display_name} plan review returned invalid structured output."
            )
        try:
            decoded_result = json.loads(raw_result)
        except json.JSONDecodeError:
            if structured is None:
                raise AdvisorError(
                    f"{display_name} plan review returned invalid structured output."
                )
        else:
            legacy = _validate_review_output(
                decoded_result,
                display_name=display_name,
            )
    if structured is None and legacy is None:
        raise AdvisorError(
            f"{display_name} plan review returned invalid structured output."
        )
    if structured is not None and legacy is not None and structured != legacy:
        raise AdvisorError(
            f"{display_name} plan review returned conflicting structured output."
        )
    selected = structured or legacy
    assert selected is not None
    return selected, _canonical_json(selected)


def _invoke_fable(
    *,
    operation: str,
    seat: Seat,
    prompt: str,
    system_prompt: str,
    allowed_signals: set[str],
) -> tuple[Any, str, dict[str, str], dict[str, str], list[str], dict[str, Any]]:
    """Run one stateless, seat-authorized, no-tools Claude operation."""

    route = load_fable_route(seat=seat)
    display_name = (
        "Claude Fable 5" if route["model"] == FABLE_MODEL else "Claude Opus 5"
    )
    claude = resolve_claude()
    auth = check_claude_auth(claude)
    claude_version = _claude_version(claude)
    command = [
        str(claude),
        "--print",
        "--model",
        route["model"],
        "--effort",
        route["effort"],
        "--safe-mode",
        "--tools",
        "",
        "--permission-mode",
        "dontAsk",
        "--no-session-persistence",
        "--prompt-suggestions",
        "false",
        "--output-format",
        "json",
        "--system-prompt",
        system_prompt,
    ]
    if operation == "plan review":
        command.extend(
            (
                "--json-schema",
                json.dumps(
                    PLAN_REVIEW_SCHEMA,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            )
        )
    result, elapsed_ms, termination_status = _run_claude_process(
        command,
        input_text=prompt,
        timeout_seconds=CLAUDE_TIMEOUT_SECONDS,
        timeout_message=f"{display_name} {operation} timed out.",
        start_error_message=f"Could not start {display_name} {operation}.",
    )
    if result.returncode != 0:
        raise _process_failure(result, authentication_check=False)
    try:
        decoded = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise AdvisorError(f"{display_name} {operation} returned malformed JSON.") from exc
    payload = _normalize_model_payload(
        decoded,
        display_name=display_name,
        operation=operation,
    )
    # Authorize the complete runtime identity set before interpreting or
    # returning any model-authored plan/review content.
    used_models = _validate_runtime_models(payload.get("modelUsage"), route["model"])
    if operation == "plan review":
        decision, response = _review_response(payload, display_name=display_name)
        signal = decision["signal"]
    else:
        if "structured_output" in payload or not isinstance(
            payload.get("result"), str
        ):
            raise AdvisorError(
                f"{display_name} {operation} returned an unexpected response."
            )
        response = payload["result"].strip()
        signal = _first_non_empty_line(response)
        decision = signal
    if signal not in allowed_signals:
        if operation == "plan review":
            raise AdvisorError(
                f"{display_name} returned an invalid structured plan decision."
            )
        expected = " or ".join(sorted(allowed_signals))
        raise AdvisorError(
            f"{display_name} {operation} omitted the required {expected} signal."
        )
    runtime_attestation = {
        "claude_code_version": claude_version,
        "configured_model": route["model"],
        "canonical_model": route["model"],
        "provider": auth["api_provider"],
        "effort": route["effort"],
        "used_models": used_models,
        "elapsed_ms": elapsed_ms,
        "timeout_seconds": CLAUDE_TIMEOUT_SECONDS,
        "termination_status": termination_status,
    }
    return decision, response, route, auth, used_models, runtime_attestation


def _base_result(
    *, route: dict[str, str], auth: dict[str, str], used_models: list[str]
) -> dict[str, Any]:
    return {
        # ``model`` is the route's pinned primary identity; ``used_models``
        # preserves every runtime-reported model, including an allowed helper.
        "model": route["model"],
        "effort": route["effort"],
        "auth_method": auth["auth_method"],
        "used_models": used_models,
    }


def create_plan(packet: str) -> dict[str, Any]:
    values = _validate_inputs("plan creation", packet=packet)
    signal, response, route, auth, used_models, _runtime = _invoke_fable(
        operation="plan creation",
        seat="planner",
        prompt=values["packet"],
        system_prompt=PLANNER_CREATE_SYSTEM_PROMPT,
        allowed_signals={"PLAN_DRAFT"},
    )
    return {
        "signal": signal,
        "plan": response,
        **_base_result(route=route, auth=auth, used_models=used_models),
    }


def _validate_revision_structure(response: str) -> None:
    lines = response.splitlines()
    ledger_positions = [
        i for i, line in enumerate(lines) if line.strip() == "## FINDINGS_LEDGER"
    ]
    plan_positions = [
        i for i, line in enumerate(lines) if line.strip() == "## REVISED_PLAN"
    ]
    if len(ledger_positions) != 1 or len(plan_positions) != 1:
        raise AdvisorError(
            "Claude plan revision must contain exactly one FINDINGS_LEDGER "
            "and one REVISED_PLAN section."
        )
    ledger_index = ledger_positions[0]
    plan_index = plan_positions[0]
    if ledger_index >= plan_index:
        raise AdvisorError(
            "Claude plan revision sections are in the wrong order."
        )
    ledger = "\n".join(lines[ledger_index + 1 : plan_index]).strip()
    revised_plan = "\n".join(lines[plan_index + 1 :]).strip()
    if not ledger or not revised_plan:
        raise AdvisorError(
            "Claude plan revision has an empty FINDINGS_LEDGER or REVISED_PLAN section."
        )


def revise_plan(
    task: str, current_plan: str, critique: str, history: str
) -> dict[str, Any]:
    values = _validate_inputs(
        "plan revision",
        task=task,
        current_plan=current_plan,
        critique=critique,
        history=history,
    )
    prompt = "\n\n".join(
        (
            "# ORIGINAL_TASK\n" + values["task"],
            "# CANONICAL_CURRENT_PLAN_WITH_SOURCE_VERSION\n" + values["current_plan"],
            "# LATEST_ADVISOR_CRITIQUE_WITH_STABLE_FINDING_IDS\n" + values["critique"],
            "# COMPACT_CUMULATIVE_FINDINGS_HISTORY\n" + values["history"],
        )
    )
    signal, response, route, auth, used_models, _runtime = _invoke_fable(
        operation="plan revision",
        seat="planner",
        prompt=prompt,
        system_prompt=PLANNER_REVISE_SYSTEM_PROMPT,
        allowed_signals={"PLAN_REVISION"},
    )
    _validate_revision_structure(response)
    return {
        "signal": signal,
        "revision": response,
        **_base_result(route=route, auth=auth, used_models=used_models),
    }


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _stable_id(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 128
        or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-" for character in value)
    ):
        raise AdvisorError(f"`{field}` must be a bounded stable ID.")
    return value


def _bounded_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_INPUT_CHARS:
        raise AdvisorError(f"`{field}` must be a bounded non-empty string.")
    return value


def _string_array(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or len(value) > 100:
        raise AdvisorError(f"`{field}` must be a bounded string array.")
    result: list[str] = []
    for index, item in enumerate(value):
        result.append(_bounded_string(item, f"{field}[{index}]"))
    return result


def _basis_array(value: Any, field: str) -> list[dict[str, str]]:
    if not isinstance(value, list) or not 1 <= len(value) <= 100:
        raise AdvisorError(f"`{field}` must be a non-empty bounded array.")
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        if not isinstance(item, dict) or set(item) != {"id", "description"}:
            raise AdvisorError(f"`{field}[{index}]` has invalid fields.")
        basis_id = _stable_id(item["id"], f"{field}[{index}].id")
        if basis_id in seen:
            raise AdvisorError(f"`{field}` contains a duplicate stable ID.")
        seen.add(basis_id)
        result.append(
            {
                "id": basis_id,
                "description": _bounded_string(
                    item["description"], f"{field}[{index}].description"
                ),
            }
        )
    return result


def _validate_review_request(
    *,
    review_session_id: Any,
    round_number: Any,
    previous_review_sha256: Any,
    original_scope_sha256: Any,
    task_goal: Any,
    approved_scope: Any,
    non_goals: Any,
    acceptance_criteria: Any,
    safety_invariants: Any,
    plan_version: Any,
    plan_sha256: Any,
    current_plan: Any,
    changed_surface: Any,
    findings_ledger: Any,
) -> dict[str, Any]:
    session_id = _stable_id(review_session_id, "review_session_id")
    if type(round_number) is not int or not 1 <= round_number <= MAX_REVIEW_ROUNDS:
        raise AdvisorError("Advisor review round must be between one and five.")
    if type(plan_version) is not int or plan_version < 1:
        raise AdvisorError("Advisor plan version must be a positive integer.")
    if not isinstance(previous_review_sha256, str) or (
        previous_review_sha256
        and (
            len(previous_review_sha256) != 64
            or any(character not in "0123456789abcdef" for character in previous_review_sha256)
        )
    ):
        raise AdvisorError("Previous review hash is malformed.")
    if not isinstance(original_scope_sha256, str) or len(original_scope_sha256) != 64:
        raise AdvisorError("Original scope hash is malformed.")
    if not isinstance(plan_sha256, str) or len(plan_sha256) != 64:
        raise AdvisorError("Plan hash is malformed.")

    criteria = _basis_array(acceptance_criteria, "acceptance_criteria")
    invariants = _basis_array(safety_invariants, "safety_invariants")
    basis_ids = {item["id"] for item in (*criteria, *invariants)}
    changed = _string_array(changed_surface, "changed_surface")
    if len(set(changed)) != len(changed) or not set(changed).issubset(basis_ids):
        raise AdvisorError("Changed surface contains an unknown or duplicate basis ID.")
    if not isinstance(findings_ledger, list) or len(findings_ledger) > 500:
        raise AdvisorError("Findings ledger must be a bounded array.")
    for index, item in enumerate(findings_ledger):
        if not isinstance(item, dict):
            raise AdvisorError(f"Findings ledger item {index} must be an object.")

    request = {
        "review_session_id": session_id,
        "round_number": round_number,
        "previous_review_sha256": previous_review_sha256,
        "original_scope_sha256": original_scope_sha256,
        "task_goal": _bounded_string(task_goal, "task_goal"),
        "approved_scope": _string_array(approved_scope, "approved_scope"),
        "non_goals": _string_array(non_goals, "non_goals"),
        "acceptance_criteria": criteria,
        "safety_invariants": invariants,
        "plan_version": plan_version,
        "plan_sha256": plan_sha256,
        "current_plan": _bounded_string(current_plan, "current_plan"),
        "changed_surface": changed,
        "findings_ledger": findings_ledger,
    }
    if len(_canonical_json(request)) > MAX_INPUT_CHARS:
        raise AdvisorError("Plan review input exceeds the combined character limit.")

    scope = {
        "task_goal": request["task_goal"],
        "approved_scope": request["approved_scope"],
        "non_goals": request["non_goals"],
        "acceptance_criteria": criteria,
        "safety_invariants": invariants,
    }
    if _sha256_text(_canonical_json(scope)) != original_scope_sha256:
        raise AdvisorError("Original scope hash does not match the supplied scope.")
    if _sha256_text(request["current_plan"]) != plan_sha256:
        raise AdvisorError("Plan hash does not match the supplied current plan.")
    return request


def _validate_session_predecessor(request: dict[str, Any]) -> dict[str, Any] | None:
    session_id = request["review_session_id"]
    round_number = request["round_number"]
    state = _REVIEW_SESSIONS.get(session_id)
    if round_number == 1:
        if request["previous_review_sha256"]:
            raise AdvisorError("Round one must begin with an empty predecessor hash.")
        if state is not None:
            raise AdvisorError("Advisor review round one cannot replay an existing session.")
        if len(_REVIEW_SESSIONS) >= MAX_REVIEW_SESSIONS:
            raise AdvisorError("Advisor review session capacity is exhausted.")
        return None
    if state is None:
        raise AdvisorError("A bridge restart requires a new Advisor session at round one.")
    if state["terminal"]:
        raise AdvisorError("The Advisor review session is terminal.")
    if round_number != state["round_number"] + 1:
        raise AdvisorError("Advisor review rounds must be strictly sequential.")
    if request["previous_review_sha256"] != state["previous_review_sha256"]:
        raise AdvisorError("Advisor predecessor attestation does not match.")
    if request["original_scope_sha256"] != state["scope_sha256"]:
        raise AdvisorError("Advisor immutable scope hash changed during the session.")
    if request["plan_version"] <= state["plan_version"]:
        raise AdvisorError("Advisor plan version must increase monotonically.")
    return state


def _validate_review_result(
    value: Any,
    *,
    request: dict[str, Any],
    previous_state: dict[str, Any] | None,
) -> dict[str, Any]:
    required = {
        "signal",
        "summary",
        "scope_status",
        "blocking_findings",
        "c_backlog",
        "new_scope_requests",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise AdvisorError("Claude plan review returned invalid structured output.")
    if value["signal"] not in {"PLAN_APPROVED", "PLAN_REVISE"}:
        raise AdvisorError("Claude plan review returned invalid structured output.")
    if (
        not isinstance(value["summary"], str)
        or not value["summary"].strip()
        or value["scope_status"] not in {"closed", "open"}
        or any(
            not isinstance(value[field], list) or len(value[field]) > 500
            for field in ("blocking_findings", "c_backlog", "new_scope_requests")
        )
    ):
        raise AdvisorError("Claude plan review returned invalid structured output.")
    basis_ids = {
        item["id"]
        for item in (*request["acceptance_criteria"], *request["safety_invariants"])
    }
    prior_ids = set(previous_state["all_finding_ids"]) if previous_state else set()
    prior_blocking_ids = (
        set(previous_state["blocking_ids"]) if previous_state else set()
    )
    seen_ids: set[str] = set()
    normalized_blockers: list[dict[str, Any]] = []
    for index, item in enumerate(value["blocking_findings"]):
        fields = {
            "id",
            "class",
            "basis_id",
            "evidence",
            "failure_scenario",
            "smallest_correction",
            "causal_source",
            "causal_reference",
            "supersedes_ids",
            "new_evidence",
        }
        if not isinstance(item, dict) or set(item) != fields:
            raise AdvisorError(f"Blocking finding {index} has invalid fields.")
        finding_id = _stable_id(item["id"], f"blocking_findings[{index}].id")
        if finding_id in seen_ids:
            raise AdvisorError("Claude plan review contains duplicate finding IDs.")
        seen_ids.add(finding_id)
        if item["class"] not in {"A", "A-uncertain", "B"}:
            raise AdvisorError("Claude plan review contains an invalid blocking class.")
        if item["basis_id"] not in basis_ids:
            raise AdvisorError("Claude plan review contains an unknown basis ID.")
        evidence = _string_array(item["evidence"], "evidence")
        if not evidence:
            raise AdvisorError("Blocking finding evidence must be non-empty.")
        failure_scenario = _bounded_string(
            item["failure_scenario"], "failure scenario"
        )
        smallest_correction = _bounded_string(
            item["smallest_correction"], "smallest correction"
        )
        causal_source = item["causal_source"]
        if causal_source not in {"initial_scope", "new_evidence", "changed_surface"}:
            raise AdvisorError("Blocking finding has an invalid causal source.")
        causal_reference = _stable_id(
            item["causal_reference"], "blocking finding causal reference"
        )
        supersedes = _string_array(item["supersedes_ids"], "supersedes IDs")
        if len(set(supersedes)) != len(supersedes) or not set(supersedes).issubset(
            prior_ids
        ):
            raise AdvisorError("Blocking finding supersedes an unknown finding ID.")
        new_evidence = _string_array(item["new_evidence"], "new evidence")
        if previous_state is not None and finding_id not in prior_blocking_ids:
            evidenced = causal_source == "new_evidence" and bool(new_evidence)
            changed = (
                causal_source == "changed_surface"
                and causal_reference in request["changed_surface"]
            )
            if not (evidenced or changed):
                raise AdvisorError(
                    "A later-round finding requires new evidence or a changed-surface cause."
                )
        normalized_blockers.append(
            {
                "id": finding_id,
                "class": item["class"],
                "basis_id": item["basis_id"],
                "evidence": evidence,
                "failure_scenario": failure_scenario,
                "smallest_correction": smallest_correction,
                "causal_source": causal_source,
                "causal_reference": causal_reference,
                "supersedes_ids": supersedes,
                "new_evidence": new_evidence,
            }
        )

    normalized_c: list[dict[str, Any]] = []
    for index, item in enumerate(value["c_backlog"]):
        if not isinstance(item, dict) or set(item) != {"id", "summary", "basis_id"}:
            raise AdvisorError(f"C backlog item {index} has invalid fields.")
        finding_id = _stable_id(item["id"], f"c_backlog[{index}].id")
        if finding_id in seen_ids:
            raise AdvisorError("Claude plan review contains duplicate finding IDs.")
        seen_ids.add(finding_id)
        basis_id = item["basis_id"]
        if basis_id is not None and basis_id not in basis_ids:
            raise AdvisorError("C backlog item contains an unknown basis ID.")
        normalized_c.append(
            {
                "id": finding_id,
                "summary": _bounded_string(item["summary"], "C backlog summary"),
                "basis_id": basis_id,
            }
        )

    normalized_scope: list[dict[str, str]] = []
    for index, item in enumerate(value["new_scope_requests"]):
        if not isinstance(item, dict) or set(item) != {"id", "summary"}:
            raise AdvisorError(f"New scope request {index} has invalid fields.")
        request_id = _stable_id(item["id"], f"new_scope_requests[{index}].id")
        if request_id in seen_ids:
            raise AdvisorError("Claude plan review contains duplicate finding IDs.")
        seen_ids.add(request_id)
        normalized_scope.append(
            {
                "id": request_id,
                "summary": _bounded_string(
                    item["summary"], "new scope request summary"
                ),
            }
        )

    if value["signal"] == "PLAN_REVISE" and not normalized_blockers:
        raise AdvisorError("PLAN_REVISE requires at least one blocking finding.")
    if value["signal"] == "PLAN_APPROVED" and normalized_blockers:
        raise AdvisorError("PLAN_APPROVED cannot include blocking findings.")
    if value["signal"] == "PLAN_APPROVED" and value["scope_status"] != "closed":
        raise AdvisorError("PLAN_APPROVED requires closed approved scope.")
    return {
        "signal": value["signal"],
        "summary": value["summary"].strip(),
        "scope_status": value["scope_status"],
        "blocking_findings": normalized_blockers,
        "c_backlog": normalized_c,
        "new_scope_requests": normalized_scope,
    }


def review_plan(
    *,
    review_session_id: Any,
    round_number: Any,
    previous_review_sha256: Any,
    original_scope_sha256: Any,
    task_goal: Any,
    approved_scope: Any,
    non_goals: Any,
    acceptance_criteria: Any,
    safety_invariants: Any,
    plan_version: Any,
    plan_sha256: Any,
    current_plan: Any,
    changed_surface: Any,
    findings_ledger: Any,
) -> dict[str, Any]:
    request = _validate_review_request(
        review_session_id=review_session_id,
        round_number=round_number,
        previous_review_sha256=previous_review_sha256,
        original_scope_sha256=original_scope_sha256,
        task_goal=task_goal,
        approved_scope=approved_scope,
        non_goals=non_goals,
        acceptance_criteria=acceptance_criteria,
        safety_invariants=safety_invariants,
        plan_version=plan_version,
        plan_sha256=plan_sha256,
        current_plan=current_plan,
        changed_surface=changed_surface,
        findings_ledger=findings_ledger,
    )
    previous_state = _validate_session_predecessor(request)
    invocation = _invoke_fable(
        operation="plan review",
        seat="advisor",
        prompt=_canonical_json(request),
        system_prompt=ADVISOR_SYSTEM_PROMPT,
        allowed_signals={"PLAN_APPROVED", "PLAN_REVISE"},
    )
    if len(invocation) != 6:
        raise AdvisorError("Claude plan review omitted runtime attestation.")
    provider_value, _response, route, auth, used_models, runtime_attestation = invocation
    provider = _validate_review_result(
        provider_value,
        request=request,
        previous_state=previous_state,
    )
    blocking_ids = [
        item.get("id")
        for item in provider["blocking_findings"]
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    ]
    current_blockers = set(blocking_ids)
    prior_blockers = (
        set(previous_state["blocking_ids"]) if previous_state is not None else set()
    )
    new_blockers = current_blockers - prior_blockers
    carried_blockers = current_blockers.intersection(prior_blockers)
    closed_blockers = prior_blockers - current_blockers
    prior_size = previous_state["plan_size"] if previous_state is not None else 0
    plan_growth_ratio = (
        (len(current_plan) - prior_size) / prior_size if prior_size else 0.0
    )
    high_closure_with_new = bool(prior_blockers) and (
        len(closed_blockers) / len(prior_blockers) >= 0.8
        and bool(new_blockers)
    )
    consecutive_non_converging = (
        previous_state["non_converging_rounds"] + 1
        if previous_state is not None and high_closure_with_new
        else 0
    )
    stop_reason: str | None = None
    if provider["signal"] == "PLAN_APPROVED":
        stop_reason = "PLAN_APPROVED"
    elif consecutive_non_converging >= 2:
        stop_reason = "NON_CONVERGING_REVIEW"
    elif plan_growth_ratio > 0.25 and new_blockers and not changed_surface:
        stop_reason = "UNAUTHORIZED_PLAN_GROWTH"
    elif round_number == MAX_REVIEW_ROUNDS:
        stop_reason = "MAX_REVIEW_ROUNDS"
    terminal = stop_reason is not None
    blocking_by_class = {
        finding_class: sum(
            finding["class"] == finding_class
            for finding in provider["blocking_findings"]
        )
        for finding_class in ("A", "A-uncertain", "B")
        if any(
            finding["class"] == finding_class
            for finding in provider["blocking_findings"]
        )
    }
    convergence = {
        "blocking_by_class": blocking_by_class,
        "new_blocker_count": len(new_blockers),
        "closed_blocker_count": len(closed_blockers),
        "carried_blocker_count": len(carried_blockers),
        "plan_growth_ratio": plan_growth_ratio,
        "consecutive_non_converging_rounds": consecutive_non_converging,
    }
    result: dict[str, Any] = {
        "decision": provider["signal"],
        "summary": provider["summary"],
        "scope_status": provider["scope_status"],
        "blocking_findings": provider["blocking_findings"],
        "c_backlog": provider["c_backlog"],
        "new_scope_requests": provider["new_scope_requests"],
        "review_session": {
            "id": review_session_id,
            "round_number": round_number,
            "plan_version": plan_version,
            "original_scope_sha256": original_scope_sha256,
            "plan_sha256": plan_sha256,
            "previous_review_sha256": previous_review_sha256,
        },
        "convergence": convergence,
        "runtime_attestation": runtime_attestation,
        "terminal": terminal,
        "stop_reason": stop_reason,
        **_base_result(route=route, auth=auth, used_models=used_models),
    }
    result["review_attestation_sha256"] = _sha256_text(_canonical_json(result))
    all_finding_ids = {
        item["id"] for item in (*provider["blocking_findings"], *provider["c_backlog"])
    }
    if previous_state is not None:
        all_finding_ids.update(previous_state["all_finding_ids"])
    _REVIEW_SESSIONS[review_session_id] = {
        "round_number": round_number,
        "plan_version": plan_version,
        "previous_review_sha256": result["review_attestation_sha256"],
        "terminal": terminal,
        "scope_sha256": original_scope_sha256,
        "plan_sha256": plan_sha256,
        "plan_size": len(current_plan),
        "blocking_ids": sorted(blocking_ids),
        "all_finding_ids": sorted(all_finding_ids),
        "non_converging_rounds": consecutive_non_converging,
    }
    return result


def _configured_fable_seats() -> dict[str, dict[str, str]]:
    """Return configured bundled Claude seats (legacy public name)."""

    payload = _read_routing_state()
    routes: dict[str, dict[str, str]] = {}
    for seat in ("planner", "advisor"):
        value = payload.get(seat)
        if value is None:
            continue
        if not isinstance(value, dict):
            raise AdvisorError(f"The saved {seat} route is invalid.")
        if value.get("kind") not in {"fable", "claude_subscription"}:
            continue
        routes[seat] = _validate_fable_route(value, seat=_validate_seat(seat))
    if not routes:
        raise AdvisorError(
            "No bundled Claude model is configured for Planner or Advisor."
        )
    return routes


def status() -> dict[str, Any]:
    routes = _configured_fable_seats()
    auth = check_claude_auth()
    seats = {
        seat: {"model": route["model"], "effort": route["effort"]}
        for seat, route in routes.items()
    }
    result: dict[str, Any] = {
        "available": True,
        "configured_seats": list(seats),
        "seats": seats,
        **auth,
    }
    # Preserve the unambiguous legacy Advisor status fields.
    if "advisor" in seats:
        result.update(seats["advisor"])
    return result


def tool_definitions() -> list[dict[str, Any]]:
    stateless_annotations = {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    }
    review_annotations = {**stateless_annotations, "idempotentHint": False}
    string_property = {"type": "string", "maxLength": MAX_INPUT_CHARS}
    stable_id = {"type": "string", "minLength": 1, "maxLength": 128}
    hash_property = {"type": "string", "pattern": SHA256_PATTERN}
    scope_item = {
        "type": "object",
        "properties": {
            "id": stable_id,
            "description": string_property,
        },
        "required": ["id", "description"],
        "additionalProperties": False,
    }
    return [
        {
            "name": "create_plan",
            "title": "Create a plan with the configured Claude model",
            "description": "Create one stateless plan draft with the configured Claude Planner.",
            "inputSchema": {
                "type": "object",
                "properties": {"packet": {**string_property, "description": "Complete planning packet."}},
                "required": ["packet"],
                "additionalProperties": False,
            },
            "annotations": stateless_annotations,
        },
        {
            "name": "revise_plan",
            "title": "Revise a plan with the configured Claude model",
            "description": "Create one stateless revision with a findings ledger and complete revised plan.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "task": {**string_property, "description": "Original task."},
                    "current_plan": {**string_property, "description": "Canonical current plan with source version."},
                    "critique": {**string_property, "description": "Latest Advisor critique with stable finding IDs."},
                    "history": {**string_property, "description": "Compact cumulative findings history."},
                },
                "required": ["task", "current_plan", "critique", "history"],
                "additionalProperties": False,
            },
            "annotations": stateless_annotations,
        },
        {
            "name": "review_plan",
            "title": "Review a plan with the configured Claude model",
            "description": "Review one structured, hash-bound round with the configured Claude Advisor.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "review_session_id": stable_id,
                    "round_number": {"type": "integer", "minimum": 1, "maximum": MAX_REVIEW_ROUNDS},
                    "previous_review_sha256": {"type": "string", "pattern": "^(?:|[0-9a-f]{64})$"},
                    "original_scope_sha256": hash_property,
                    "task_goal": string_property,
                    "approved_scope": {"type": "array", "items": string_property, "maxItems": 100},
                    "non_goals": {"type": "array", "items": string_property, "maxItems": 100},
                    "acceptance_criteria": {"type": "array", "items": scope_item, "minItems": 1, "maxItems": 100},
                    "safety_invariants": {"type": "array", "items": scope_item, "minItems": 1, "maxItems": 100},
                    "plan_version": {"type": "integer", "minimum": 1},
                    "plan_sha256": hash_property,
                    "current_plan": string_property,
                    "changed_surface": {"type": "array", "items": stable_id, "maxItems": 100, "uniqueItems": True},
                    "findings_ledger": {"type": "array", "items": {"type": "object"}, "maxItems": 500},
                },
                "required": list(REVIEW_REQUEST_FIELDS),
                "additionalProperties": False,
            },
            "annotations": review_annotations,
        },
        {
            "name": "status",
            "title": "Check bundled Claude Planner and Advisor status",
            "description": "Check configured Claude seats and first-party login without a model call.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            "annotations": stateless_annotations,
        },
    ]


def _tool_result(payload: dict[str, Any], *, is_error: bool = False) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(payload, sort_keys=True)}],
        "isError": is_error,
    }


def _tool_arguments(arguments: Any, allowed: set[str]) -> dict[str, Any]:
    if not isinstance(arguments, dict):
        raise AdvisorError("Tool arguments must be an object.")
    unexpected = sorted(set(arguments) - allowed)
    if unexpected:
        raise AdvisorError(f"Unexpected tool argument(s): {', '.join(unexpected)}.")
    return arguments


def handle_request(request: dict[str, Any]) -> dict[str, Any] | None:
    method = request.get("method")
    request_id = request.get("id")
    if request_id is None:
        return None
    if method == "initialize":
        result = {
            "protocolVersion": "2025-06-18",
            "capabilities": {"tools": {"listChanged": False}},
            # Preserve the historical launcher identity for loaded-plugin
            # compatibility; the tool metadata describes either sealed model.
            "serverInfo": {
                "name": "codex-orchestration-fable-advisor",
                "version": "2.0.0",
            },
        }
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": tool_definitions()}
    elif method == "tools/call":
        params = request.get("params")
        name = params.get("name") if isinstance(params, dict) else None
        arguments = params.get("arguments", {}) if isinstance(params, dict) else {}
        try:
            if name == "create_plan":
                args = _tool_arguments(arguments, {"packet"})
                result = _tool_result(create_plan(args.get("packet")))
            elif name == "revise_plan":
                args = _tool_arguments(arguments, {"task", "current_plan", "critique", "history"})
                result = _tool_result(
                    revise_plan(
                        args.get("task"),
                        args.get("current_plan"),
                        args.get("critique"),
                        args.get("history"),
                    )
                )
            elif name == "review_plan":
                args = _tool_arguments(arguments, set(REVIEW_REQUEST_FIELDS))
                result = _tool_result(review_plan(**args))
            elif name == "status":
                _tool_arguments(arguments, set())
                result = _tool_result(status())
            else:
                raise AdvisorError(f"Unknown tool: {name!r}.")
        except ClaudeProcessFailure as exc:
            result = _tool_result(
                {
                    "available": False,
                    "error": str(exc),
                    "failure_kind": exc.failure_kind,
                    "exit_code": exc.exit_code,
                    "retryable": exc.retryable,
                    "operator_action": exc.operator_action,
                },
                is_error=True,
            )
        except AdvisorError as exc:
            result = _tool_result(
                {
                    "available": False,
                    "error": str(exc),
                    "recovery": STALE_BRIDGE_RECOVERY,
                },
                is_error=True,
            )
    else:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        }
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def main() -> int:
    for line in sys.stdin:
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError("request must be an object")
            response = handle_request(request)
        except (json.JSONDecodeError, ValueError) as exc:
            response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": str(exc)},
            }
        if response is not None:
            print(json.dumps(response, separators=(",", ":")), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
