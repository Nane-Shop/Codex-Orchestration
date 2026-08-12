from __future__ import annotations

import importlib.util
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import time
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (
    REPO_ROOT
    / "plugins"
    / "codex-orchestration"
    / "skills"
    / "codex-orchestration"
    / "scripts"
    / "fable_advisor_mcp.py"
)
sys.path.insert(0, str(SCRIPT.parent))
SPEC = importlib.util.spec_from_file_location("fable_advisor_mcp", SCRIPT)
assert SPEC and SPEC.loader
FABLE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FABLE)
DEFAULT_MODEL_USAGE = object()
DEFAULT_STRUCTURED_OUTPUT = object()
AUTO_STRUCTURED_OUTPUT = object()


class FableAdvisorMcpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.review_counter = 0
        FABLE._REVIEW_SESSIONS.clear()
        self.write_state(advisor=self.route("high"))

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def route(effort: str = "high") -> dict[str, str]:
        return {
            "kind": "fable",
            "model": "claude-fable-5",
            "effort": effort,
            "server": "fable-advisor-python3",
        }

    @staticmethod
    def opus_route(effort: str = "high") -> dict[str, str]:
        return {
            "kind": "claude_subscription",
            "model": "claude-opus-5",
            "effort": effort,
            "server": "fable-advisor-python3",
        }

    def write_state(self, *, schema: int = 6, **seats: object) -> None:
        subscription_routes = [
            route
            for route in seats.values()
            if isinstance(route, dict)
            and route.get("kind") in {"fable", "claude_subscription"}
        ]
        managed_mcp = {
            route["server"]: True
            for route in subscription_routes[:1]
            if isinstance(route.get("server"), str)
        }
        previous_mcp = {
            server: {"known": True, "present": False}
            for server in managed_mcp
        }
        payload = {
            "schema": schema,
            "policy_version": schema,
            "managed_by": "codex-orchestration",
            "config_file": str(self.home / "config.toml"),
            "executor": {
                "kind": "model",
                "model": "gpt-5.6-luna",
                "effort": "xhigh",
            },
            "advisor": None,
            "managed": {
                "mode": f"{FABLE.MANAGED_MARKER}\nmode",
                "usage": f"{FABLE.MANAGED_MARKER}\nusage",
                "metadata": False,
                "namespace": "agents",
                "mcp": managed_mcp,
            },
            "previous": {
                "mode": {"known": True, "present": False},
                "usage": {"known": True, "present": False},
                "metadata": {"known": True, "present": False},
                "namespace": {"known": True, "present": False},
                "mcp": previous_mcp,
            },
            "scalar_origin": None,
            "managed_feature": None,
            **seats,
        }
        if schema >= 3 and "planner" not in payload:
            payload["planner"] = None
        if schema >= 4 and "designer" not in payload:
            payload["designer"] = None
        (self.home / FABLE.STATE_FILENAME).write_text(
            json.dumps(payload), encoding="utf-8"
        )

    @staticmethod
    def completed(
        command: list[str], stdout: str, *, returncode: int = 0, stderr: str = ""
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, returncode, stdout, stderr)

    def auth_result(
        self, subscription_type: object = "max"
    ) -> subprocess.CompletedProcess[str]:
        return self.completed(
            ["claude", "auth", "status"],
            json.dumps(
                {
                    "loggedIn": True,
                    "authMethod": "claude.ai",
                    "apiProvider": "firstParty",
                    "subscriptionType": subscription_type,
                }
            ),
        )

    def review_request(self, packet: str = "Review this complete plan.") -> dict[str, object]:
        self.review_counter += 1
        scope = {
            "task_goal": packet,
            "approved_scope": ["Bounded reviewed change"],
            "non_goals": ["Unapproved deployment"],
            "acceptance_criteria": [
                {"id": "AC-1", "description": "The requested behavior is verified."}
            ],
            "safety_invariants": [
                {"id": "SI-1", "description": "The boundary fails closed."}
            ],
        }
        plan = packet
        canonical = json.dumps(
            scope, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        )
        return {
            "review_session_id": f"legacy-review-{self.review_counter}",
            "round_number": 1,
            "previous_review_sha256": "",
            "original_scope_sha256": hashlib.sha256(canonical.encode()).hexdigest(),
            **scope,
            "plan_version": 1,
            "plan_sha256": hashlib.sha256(plan.encode()).hexdigest(),
            "current_plan": plan,
            "changed_surface": [],
            "scope_growth_authorization": {
                "authorized": False,
                "provenance": "",
            },
            "findings_ledger": [],
        }

    def call_review(self, packet: str = "Review this complete plan.") -> dict[str, object]:
        return FABLE.review_plan(**self.review_request(packet))

    @staticmethod
    def approved_output(summary: str = "No material gap found.") -> dict[str, object]:
        return {
            "signal": "PLAN_APPROVED",
            "summary": summary,
            "scope_status": "closed",
            "blocking_findings": [],
            "c_backlog": [],
            "new_scope_requests": [],
        }

    @staticmethod
    def opus_usage(output_tokens: int = 12) -> dict[str, object]:
        return {
            FABLE.OPUS_MODEL: {
                "inputTokens": 3,
                "outputTokens": output_tokens,
                "cacheReadInputTokens": 0,
                "cacheCreationInputTokens": 0,
                "webSearchRequests": 0,
                "costUSD": 0,
                "contextWindow": 1_000_000,
                "maxOutputTokens": 64_000,
                "canonicalModel": FABLE.OPUS_MODEL,
                "provider": "firstParty",
            }
        }

    def model_result(
        self,
        response: str,
        *,
        model_usage: object = DEFAULT_MODEL_USAGE,
        structured_output: object = DEFAULT_STRUCTURED_OUTPUT,
        as_events: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        payload: dict[str, object] = {
            "result": response,
            "modelUsage": model_usage
            if model_usage is not DEFAULT_MODEL_USAGE
            else {"claude-fable-5": {"outputTokens": 12}},
        }
        if structured_output is not DEFAULT_STRUCTURED_OUTPUT:
            payload["structured_output"] = structured_output
        outer: object = (
            [
                {"type": "system", "subtype": "init"},
                {"type": "result", "subtype": "success", **payload},
            ]
            if as_events
            else payload
        )
        return self.completed(
            ["claude"],
            json.dumps(outer),
        )

    def invoke_with_results(
        self,
        function: object,
        *args: str,
        model_response: str,
        model_usage: object = DEFAULT_MODEL_USAGE,
        structured_output: object = AUTO_STRUCTURED_OUTPUT,
        as_events: bool = False,
    ) -> tuple[dict[str, object], list[tuple[list[str], dict[str, object]]]]:
        calls: list[tuple[list[str], dict[str, object]]] = []

        def fake_run(command: list[str], **kwargs: object) -> tuple[object, ...]:
            if command[-1:] == ["--version"]:
                return self.completed(command, "2.1.220 (Claude Code)\n"), 1, "completed"
            recorded = {
                "input": kwargs.get("input_text"),
                "env": FABLE.sanitized_environment(),
                "timeout": kwargs.get("timeout_seconds"),
            }
            calls.append((command, recorded))
            if command[-2:] == ["auth", "status"] or command[-3:] == [
                "auth",
                "status",
                "--json",
            ]:
                return self.auth_result(), 1, "completed"
            selected_structured_output = structured_output
            if (
                selected_structured_output is AUTO_STRUCTURED_OUTPUT
                and function is FABLE.review_plan
            ):
                lines = model_response.strip().splitlines()
                if (
                    lines
                    and lines[0] in {"PLAN_APPROVED", "PLAN_REVISE"}
                    and "\n".join(lines[1:]).strip()
                ):
                    selected_structured_output = {
                        "signal": lines[0],
                        "summary": "\n".join(lines[1:]).strip(),
                        "scope_status": "closed" if lines[0] == "PLAN_APPROVED" else "open",
                        "blocking_findings": [] if lines[0] == "PLAN_APPROVED" else [
                            {
                                "id": "F-1",
                                "class": "B",
                                "basis_id": "AC-1",
                                "evidence": ["The current plan has a material gap."],
                                "failure_scenario": "The approved criterion remains open.",
                                "smallest_correction": "Add the missing bounded verification.",
                                "causal_source": "initial_scope",
                                "causal_reference": "AC-1",
                                "supersedes_ids": [],
                                "new_evidence": [],
                            }
                        ],
                        "c_backlog": [],
                        "new_scope_requests": [],
                    }
                else:
                    selected_structured_output = DEFAULT_STRUCTURED_OUTPUT
            elif selected_structured_output is AUTO_STRUCTURED_OUTPUT:
                selected_structured_output = DEFAULT_STRUCTURED_OUTPUT
            return (
                self.model_result(
                    model_response,
                    model_usage=model_usage,
                    structured_output=selected_structured_output,
                    as_events=as_events,
                ),
                1,
                "completed",
            )

        with (
            mock.patch.dict(os.environ, {"CODEX_HOME": str(self.home)}),
            mock.patch.object(
                FABLE, "resolve_claude", return_value=Path("/fake/claude")
            ),
            mock.patch.object(FABLE, "_run_claude_process", side_effect=fake_run),
        ):
            result = (
                function(**self.review_request(args[0]))
                if function is FABLE.review_plan
                else function(*args)
            )
        return result, calls

    def invoke_with_stdout(
        self, function: object, *args: str, stdout: str
    ) -> dict[str, object]:
        with (
            mock.patch.dict(os.environ, {"CODEX_HOME": str(self.home)}),
            mock.patch.object(
                FABLE, "resolve_claude", return_value=Path("/fake/claude")
            ),
            mock.patch.object(
                FABLE,
                "_run_claude_process",
                side_effect=[
                    (self.auth_result(), 1, "completed"),
                    (self.completed(["claude"], "2.1.220 (Claude Code)\n"), 1, "completed"),
                    (self.completed(["claude"], stdout), 1, "completed"),
                ],
            ),
        ):
            return (
                function(**self.review_request(args[0]))
                if function is FABLE.review_plan
                else function(*args)
            )

    def test_review_is_pinned_sanitized_read_only_and_runtime_confirmed(self) -> None:
        env = {
            "CODEX_HOME": str(self.home),
            **{name: "must-not-leak" for name in FABLE.SENSITIVE_ENV},
        }
        calls: list[tuple[list[str], dict[str, object]]] = []

        def fake_run(command: list[str], **kwargs: object) -> tuple[object, ...]:
            if command[-1:] == ["--version"]:
                return self.completed(command, "2.1.220 (Claude Code)\n"), 1, "completed"
            recorded = {
                "input": kwargs.get("input_text"),
                "env": FABLE.sanitized_environment(),
                "timeout": kwargs.get("timeout_seconds"),
            }
            calls.append((command, recorded))
            if command[-2:] == ["auth", "status"] or command[-3:] == [
                "auth",
                "status",
                "--json",
            ]:
                return self.auth_result(), 1, "completed"
            output = self.approved_output()
            return self.model_result(
                json.dumps(output), structured_output=output
            ), 1, "completed"

        with (
            mock.patch.dict(os.environ, env, clear=False),
            mock.patch.object(
                FABLE, "resolve_claude", return_value=Path("/fake/claude")
            ),
            mock.patch.object(FABLE, "_run_claude_process", side_effect=fake_run),
        ):
            result = self.call_review()

        self.assertEqual(result["decision"], "PLAN_APPROVED")
        self.assertEqual(result["model"], "claude-fable-5")
        self.assertEqual(result["used_models"], ["claude-fable-5"])
        self.assertNotIn("subscription_type", result)
        auth_command, auth_kwargs = calls[0]
        self.assertEqual(auth_command[-3:], ["auth", "status", "--json"])
        review_command, review_kwargs = calls[1]
        for flag in (
            "--print",
            "--safe-mode",
            "--tools",
            "--permission-mode",
            "--no-session-persistence",
            "--prompt-suggestions",
            "--output-format",
            "--json-schema",
            "--system-prompt",
        ):
            self.assertIn(flag, review_command)
        self.assertNotIn("--bare", review_command)
        self.assertEqual(review_command[review_command.index("--tools") + 1], "")
        self.assertEqual(
            review_command[review_command.index("--permission-mode") + 1], "dontAsk"
        )
        self.assertEqual(
            review_command[review_command.index("--model") + 1], "claude-fable-5"
        )
        self.assertEqual(review_command[review_command.index("--effort") + 1], "high")
        self.assertEqual(
            review_command[review_command.index("--prompt-suggestions") + 1],
            "false",
        )
        self.assertEqual(
            review_command[review_command.index("--output-format") + 1], "json"
        )
        self.assertEqual(
            json.loads(review_command[review_command.index("--json-schema") + 1]),
            FABLE.PLAN_REVIEW_SCHEMA,
        )
        self.assertIn("Review this complete plan.", review_kwargs["input"])
        for kwargs in (auth_kwargs, review_kwargs):
            sanitized = kwargs["env"]
            self.assertIsInstance(sanitized, dict)
            for name in FABLE.SENSITIVE_ENV:
                self.assertNotIn(name, sanitized)

    def test_auth_and_model_subprocesses_receive_only_platform_runtime_environment(
        self,
    ) -> None:
        hostile = {
            "CLAUDE_CODE_OAUTH_TOKEN": "oauth-secret",
            "ANTHROPIC_API_KEY": "provider-secret",
            "CLAUDE_CONFIG_DIR": "/hostile/config",
            "CLAUDE_CODE_CLIENT_KEY": "client-secret",
            "ANTHROPIC_UNKNOWN_GATEWAY_HEADER": "smuggled",
            "HTTP_PROXY": "http://hostile.invalid",
            "HTTPS_PROXY": "https://hostile.invalid",
            "SSL_CERT_FILE": "/hostile/ca.pem",
            "NODE_EXTRA_CA_CERTS": "/hostile/node-ca.pem",
            "AWS_SECRET_ACCESS_KEY": "aws-secret",
            "GOOGLE_APPLICATION_CREDENTIALS": "/hostile/google.json",
            "AZURE_CLIENT_SECRET": "azure-secret",
            "OTEL_EXPORTER_OTLP_HEADERS": "authorization=secret",
            "APPDATA": r"C:\hostile\roaming",
            "LOCALAPPDATA": r"C:\hostile\local",
        }
        scenarios = (
            (
                "posix",
                {
                    "PATH": "/trusted/bin",
                    "LANG": "en_US.UTF-8",
                    "LC_ALL": "C.UTF-8",
                    "LC_CTYPE": "UTF-8",
                    "HOME": "/trusted/home",
                    "TMPDIR": "/trusted/tmp",
                    "USER": "hostile-user",
                    "LOGNAME": "hostile-logname",
                    "SystemRoot": r"C:\should-not-pass",
                    **hostile,
                },
                {
                    "PATH": "/trusted/bin",
                    "LANG": "en_US.UTF-8",
                    "LC_ALL": "C.UTF-8",
                    "LC_CTYPE": "UTF-8",
                    "HOME": "/trusted/home",
                    "TMPDIR": "/trusted/tmp",
                    "USER": "trusted-user",
                    "LOGNAME": "trusted-user",
                    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
                },
            ),
            (
                "nt",
                {
                    "path": r"C:\trusted\bin",
                    "lang": "en-US",
                    "lc_all": "C",
                    "lc_ctype": "UTF-8",
                    "systemroot": r"C:\Windows",
                    "comspec": r"C:\Windows\System32\cmd.exe",
                    "pathext": ".COM;.EXE",
                    "temp": r"C:\trusted\temp",
                    "tmp": r"C:\trusted\tmp",
                    "userprofile": r"C:\Users\trusted",
                    "home": r"C:\hostile\home-redirection",
                    **{name.lower(): value for name, value in hostile.items()},
                },
                {
                    "PATH": r"C:\trusted\bin",
                    "LANG": "en-US",
                    "LC_ALL": "C",
                    "LC_CTYPE": "UTF-8",
                    "SystemRoot": r"C:\Windows",
                    "ComSpec": r"C:\Windows\System32\cmd.exe",
                    "PATHEXT": ".COM;.EXE",
                    "TEMP": r"C:\trusted\temp",
                    "TMP": r"C:\trusted\tmp",
                    "USERPROFILE": r"C:\Users\trusted",
                    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
                },
            ),
        )
        executable = Path("/fake/claude")
        for platform, inherited, expected in scenarios:
            with self.subTest(platform=platform):
                calls: list[tuple[list[str], dict[str, object]]] = []

                def fake_run(command: list[str], **kwargs: object) -> tuple[object, ...]:
                    if command[-1:] == ["--version"]:
                        return self.completed(command, "2.1.220 (Claude Code)\n"), 1, "completed"
                    calls.append(
                        (
                            command,
                            {
                                "env": FABLE.sanitized_environment(),
                                "input": kwargs.get("input_text"),
                            },
                        )
                    )
                    if command[-2:] == ["auth", "status"] or command[-3:] == [
                        "auth",
                        "status",
                        "--json",
                    ]:
                        return self.auth_result(), 1, "completed"
                    output = self.approved_output()
                    return self.model_result(
                        json.dumps(output), structured_output=output
                    ), 1, "completed"

                with (
                    mock.patch.dict(os.environ, inherited, clear=True),
                    mock.patch.object(FABLE.os, "name", platform),
                    mock.patch.object(
                        FABLE,
                        "_canonical_posix_identity",
                        return_value="trusted-user",
                    ),
                    mock.patch.object(
                        FABLE,
                        "load_fable_route",
                        return_value={"model": FABLE.FABLE_MODEL, "effort": "high"},
                    ),
                    mock.patch.object(FABLE, "resolve_claude", return_value=executable),
                    mock.patch.object(FABLE, "_run_claude_process", side_effect=fake_run),
                ):
                    result = self.call_review()

                self.assertEqual(result["decision"], "PLAN_APPROVED")
                self.assertEqual(len(calls), 2)
                for _, kwargs in calls:
                    self.assertEqual(kwargs["env"], expected)

    def test_auth_accepts_only_exact_first_party_pro_max_or_team_tuples(self) -> None:
        valid_subscriptions = ("pro", "max", "team")
        executable = Path("/fake/claude")
        for subscription in valid_subscriptions:
            with self.subTest(subscription=subscription), mock.patch.object(
                FABLE,
                "_run_json",
                return_value={
                    "loggedIn": True,
                    "authMethod": "claude.ai",
                    "apiProvider": "firstParty",
                    "subscriptionType": subscription,
                },
            ) as run:
                self.assertEqual(
                    FABLE.check_claude_auth(executable),
                    {
                        "auth_method": "claude.ai",
                        "api_provider": "firstParty",
                    },
                )
                self.assertEqual(
                    run.call_args.args[0],
                    [str(executable), "auth", "status", "--json"],
                )

        invalid_payloads = (
            {
                "loggedIn": False,
                "authMethod": "claude.ai",
                "apiProvider": "firstParty",
                "subscriptionType": "team",
            },
            {
                "loggedIn": 1,
                "authMethod": "claude.ai",
                "apiProvider": "firstParty",
                "subscriptionType": "team",
            },
            {
                "loggedIn": True,
                "authMethod": "console",
                "apiProvider": "firstParty",
                "subscriptionType": "team",
            },
            {
                "loggedIn": True,
                "authMethod": "claude.ai",
                "apiProvider": "bedrock",
                "subscriptionType": "team",
            },
            {
                "loggedIn": True,
                "authMethod": "claude.ai",
                "apiProvider": "firstParty",
                "subscriptionType": "Team",
            },
            {
                "loggedIn": True,
                "authMethod": "claude.ai",
                "apiProvider": "firstParty",
                "subscriptionType": "enterprise",
            },
            {
                "loggedIn": True,
                "authMethod": "claude.ai",
                "apiProvider": "firstParty",
                "subscriptionType": [],
            },
            {
                "loggedIn": True,
                "authMethod": "claude.ai",
                "apiProvider": "firstParty",
            },
        )
        secret = "TOP-SECRET-AUTH-METADATA"
        for payload in invalid_payloads:
            with self.subTest(payload=payload), mock.patch.object(
                FABLE, "_run_json", return_value={**payload, "account": secret}
            ):
                with self.assertRaises(FABLE.AdvisorError) as failure:
                    FABLE.check_claude_auth(executable)
                self.assertIn("Pro, Max, or Team", str(failure.exception))
                self.assertNotIn(secret, str(failure.exception))

    def test_posix_identity_lookup_failure_stops_before_any_subprocess(self) -> None:
        executable = Path("/fake/claude")
        with (
            mock.patch.object(FABLE.os, "name", "posix"),
            mock.patch.object(
                FABLE,
                "_canonical_posix_identity",
                side_effect=FABLE.AdvisorError("canonical identity unavailable"),
            ),
            mock.patch.object(FABLE.subprocess, "Popen") as run,
        ):
            with self.assertRaisesRegex(
                FABLE.AdvisorError, "canonical identity unavailable"
            ):
                FABLE.check_claude_auth(executable)
        run.assert_not_called()

    def test_runtime_model_policy_accepts_only_fable_and_exact_allowed_helper(
        self,
    ) -> None:
        resolved_primary = "claude-opus-4-8"
        allowed_scenarios = (
            ({FABLE.FABLE_MODEL: {"outputTokens": 12}}, [FABLE.FABLE_MODEL]),
            ({resolved_primary: {"outputTokens": 12}}, [resolved_primary]),
            (
                {
                    resolved_primary: {"outputTokens": 12},
                    FABLE.FABLE_HELPER_MODEL: {"outputTokens": 1},
                },
                sorted((resolved_primary, FABLE.FABLE_HELPER_MODEL)),
            ),
            (
                {
                    FABLE.FABLE_MODEL: {"outputTokens": 12},
                    resolved_primary: {"outputTokens": 12},
                    FABLE.FABLE_HELPER_MODEL: {"outputTokens": 1},
                },
                sorted(
                    (
                        FABLE.FABLE_MODEL,
                        resolved_primary,
                        FABLE.FABLE_HELPER_MODEL,
                    )
                ),
            ),
        )
        for model_usage, expected_models in allowed_scenarios:
            with self.subTest(model_usage=model_usage):
                result, _ = self.invoke_with_results(
                    FABLE.review_plan,
                    "packet",
                    model_response="PLAN_APPROVED\nNo material gap found.",
                    model_usage=model_usage,
                )
                self.assertEqual(result["decision"], "PLAN_APPROVED")
                self.assertEqual(result["model"], FABLE.FABLE_MODEL)
                self.assertEqual(result["used_models"], expected_models)

        secret = "TOP-SECRET-MODEL-OUTPUT"
        rejected_scenarios = (
            (
                {
                    FABLE.FABLE_MODEL: {"outputTokens": 12},
                    "claude-haiku-4-5-20251002": {"outputTokens": 1},
                },
                "outside the allowed Fable runtime policy",
            ),
            (
                {FABLE.FABLE_HELPER_MODEL: {"outputTokens": 1}},
                "did not confirm the pinned Claude Fable 5 primary model",
            ),
        )
        for model_usage, expected_error in rejected_scenarios:
            with self.subTest(model_usage=model_usage):
                with self.assertRaisesRegex(
                    FABLE.AdvisorError, expected_error
                ) as failure:
                    self.invoke_with_results(
                        FABLE.review_plan,
                        "packet",
                        model_response=f"PLAN_APPROVED\n{secret}",
                        model_usage=model_usage,
                    )
                self.assertNotIn(secret, str(failure.exception))

    def test_runtime_model_usage_values_fail_closed(self) -> None:
        malformed_values = (
            None,
            "12",
            12,
            1.5,
            [],
            {},
            {"outputTokens": -1},
            {"outputTokens": float("nan")},
            {"outputTokens": float("inf")},
            {"outputTokens": float("-inf")},
            {"outputTokens": True},
            {"": 12},
            {"outputTokens": "12"},
        )
        for usage_value in malformed_values:
            with self.subTest(usage_value=usage_value):
                with self.assertRaisesRegex(FABLE.AdvisorError, "[Rr]untime metadata"):
                    self.invoke_with_results(
                        FABLE.review_plan,
                        "packet",
                        model_response="PLAN_APPROVED\nNo material gap found.",
                        model_usage={FABLE.FABLE_MODEL: usage_value},
                    )

        for usage in (
            {7: {"outputTokens": 1}},
            {FABLE.FABLE_MODEL: {7: 1}},
            {"": {"outputTokens": 1}, FABLE.FABLE_MODEL: {"outputTokens": 1}},
        ):
            with self.subTest(non_json_key=usage):
                with self.assertRaisesRegex(FABLE.AdvisorError, "[Rr]untime metadata"):
                    FABLE._validate_runtime_models(usage)

        for usage in (
            {FABLE.FABLE_MODEL: {"outputTokens": 0}},
            {FABLE.FABLE_MODEL: {"outputTokens": 10**309}},
            {FABLE.FABLE_MODEL: {"costUSD": 0.25, "outputTokens": 12}},
            {
                FABLE.FABLE_MODEL: {"outputTokens": 12},
                FABLE.FABLE_HELPER_MODEL: {"outputTokens": 1},
            },
        ):
            with self.subTest(valid_usage=usage):
                result, _ = self.invoke_with_results(
                    FABLE.review_plan,
                    "packet",
                    model_response="PLAN_APPROVED\nNo material gap found.",
                    model_usage=usage,
                )
                self.assertEqual(result["decision"], "PLAN_APPROVED")

    def test_each_operation_pins_its_authorized_seat_effort(self) -> None:
        self.write_state(planner=self.route("low"))
        created, create_calls = self.invoke_with_results(
            FABLE.create_plan, "packet", model_response="PLAN_DRAFT\nDraft"
        )
        self.write_state(advisor=self.route("xhigh"))
        reviewed, review_calls = self.invoke_with_results(
            FABLE.review_plan, "packet", model_response="PLAN_APPROVED\nGood"
        )
        self.assertEqual(created["effort"], "low")
        self.assertEqual(reviewed["effort"], "xhigh")
        create_command = create_calls[1][0]
        review_command = review_calls[1][0]
        self.assertEqual(create_command[create_command.index("--effort") + 1], "low")
        self.assertEqual(
            review_command[review_command.index("--effort") + 1], "xhigh"
        )
        self.assertEqual(
            create_command[create_command.index("--system-prompt") + 1],
            FABLE.PLANNER_CREATE_SYSTEM_PROMPT,
        )
        self.assertEqual(
            review_command[review_command.index("--system-prompt") + 1],
            FABLE.ADVISOR_SYSTEM_PROMPT,
        )

    def test_seat_authorization_does_not_cross_planner_and_advisor(self) -> None:
        self.write_state(planner=self.route())
        with mock.patch.dict(os.environ, {"CODEX_HOME": str(self.home)}):
            with self.assertRaisesRegex(FABLE.AdvisorError, "configured advisor"):
                self.call_review("packet")

        self.write_state(advisor=self.route())
        with mock.patch.dict(os.environ, {"CODEX_HOME": str(self.home)}):
            with self.assertRaisesRegex(FABLE.AdvisorError, "configured planner"):
                FABLE.create_plan("packet")
            with self.assertRaisesRegex(FABLE.AdvisorError, "configured planner"):
                FABLE.revise_plan("task", "v1 plan", "F-1", "history")

    def test_route_validation_requires_current_policy_but_legacy_state_is_parseable(self) -> None:
        self.assertEqual(FABLE.load_fable_route(self.home)["effort"], "high")
        with self.assertRaisesRegex(FABLE.AdvisorError, "planner.*advisor"):
            FABLE.load_fable_route(self.home, seat="executor")

        invalid = self.route()
        invalid["server"] = "unmanaged-server"
        self.write_state(advisor=invalid)
        with self.assertRaisesRegex(FABLE.AdvisorError, "state is invalid"):
            FABLE.load_fable_route(self.home)

        self.write_state(planner=self.route(), advisor=self.route("xhigh"))
        with self.assertRaisesRegex(FABLE.AdvisorError, "state is invalid"):
            FABLE.load_fable_route(self.home, seat="planner")
        with self.assertRaisesRegex(FABLE.AdvisorError, "state is invalid"):
            FABLE.load_fable_route(self.home, seat="advisor")

        self.write_state(schema=2, advisor=self.route())
        with self.assertRaisesRegex(FABLE.AdvisorError, "current policy"):
            FABLE.load_fable_route(self.home)
        self.write_state(schema=2, planner=self.route())
        with self.assertRaisesRegex(FABLE.AdvisorError, "state is invalid"):
            FABLE.load_fable_route(self.home, seat="planner")

        self.write_state(schema=4, advisor=self.route())
        with self.assertRaisesRegex(FABLE.AdvisorError, "current policy"):
            FABLE.load_fable_route(self.home)
        self.write_state(schema=4, advisor=self.route(), designer=self.route())
        with self.assertRaisesRegex(FABLE.AdvisorError, "state is invalid"):
            FABLE.load_fable_route(self.home)
        self.write_state(schema=6, advisor=self.route())
        self.assertEqual(FABLE.load_fable_route(self.home)["model"], FABLE.FABLE_MODEL)

    def test_opus_route_pins_primary_and_rejects_every_unverified_helper(self) -> None:
        self.write_state(schema=6, advisor=self.opus_route("xhigh"))
        result, calls = self.invoke_with_results(
            FABLE.review_plan,
            "packet",
            model_response="PLAN_APPROVED\nNo material gap.",
            model_usage=self.opus_usage(),
        )
        self.assertEqual(result["model"], FABLE.OPUS_MODEL)
        self.assertEqual(result["effort"], "xhigh")
        review_command = calls[1][0]
        self.assertEqual(
            review_command[review_command.index("--model") + 1], FABLE.OPUS_MODEL
        )
        self.assertEqual(
            review_command[review_command.index("--effort") + 1], "xhigh"
        )

        with self.assertRaisesRegex(
            FABLE.AdvisorError, "ten-key|outside the allowed Claude runtime policy"
        ):
            self.invoke_with_results(
                FABLE.review_plan,
                "packet",
                model_response="PLAN_APPROVED\nNo material gap.",
                model_usage={
                    **self.opus_usage(),
                    FABLE.FABLE_HELPER_MODEL: {"outputTokens": 1},
                },
            )
        with self.assertRaisesRegex(
            FABLE.AdvisorError, "ten-key|did not confirm the pinned Claude Opus 5"
        ):
            self.invoke_with_results(
                FABLE.review_plan,
                "packet",
                model_response="PLAN_APPROVED\nNo material gap.",
                model_usage={FABLE.FABLE_HELPER_MODEL: {"outputTokens": 1}},
            )

    def test_opus_model_usage_accepts_claude_2_1_220_identity_pair(self) -> None:
        usage = {
            "claude-opus-5": {
                "inputTokens": 3,
                "outputTokens": 12,
                "cacheReadInputTokens": 0,
                "cacheCreationInputTokens": 0,
                "webSearchRequests": 0,
                "costUSD": 0,
                "contextWindow": 1_000_000,
                "maxOutputTokens": 64_000,
                "canonicalModel": "claude-opus-5",
                "provider": "firstParty",
            }
        }

        self.assertEqual(
            FABLE._validate_runtime_models(usage, "claude-opus-5"),
            ["claude-opus-5"],
        )

    def test_opus_model_usage_rejects_legacy_numeric_only_metadata(self) -> None:
        usage = {"claude-opus-5": {"outputTokens": 12}}

        with self.assertRaisesRegex(FABLE.AdvisorError, "ten-key"):
            FABLE._validate_runtime_models(usage, "claude-opus-5")

    def test_opus_model_usage_identity_fields_fail_closed(self) -> None:
        numeric_metrics = {"outputTokens": 12}
        malformed_fields = (
            {"canonicalModel": "claude-opus-4-8", "provider": "firstParty"},
            {"canonicalModel": "", "provider": "firstParty"},
            {"canonicalModel": 7, "provider": "firstParty"},
            {"canonicalModel": "claude-opus-5", "provider": "bedrock"},
            {"canonicalModel": "claude-opus-5", "provider": ""},
            {"canonicalModel": "claude-opus-5", "provider": 7},
        )
        for identity_fields in malformed_fields:
            with self.subTest(identity_fields=identity_fields):
                with self.assertRaisesRegex(
                    FABLE.AdvisorError, "ten-key|malformed modelUsage value"
                ):
                    FABLE._validate_runtime_models(
                        {
                            "claude-opus-5": {
                                **numeric_metrics,
                                **identity_fields,
                            }
                        },
                        "claude-opus-5",
                    )

    def test_opus_model_usage_identity_pair_is_all_or_nothing(self) -> None:
        partial_pairs = (
            {"outputTokens": 12, "canonicalModel": "claude-opus-5"},
            {"outputTokens": 12, "provider": "firstParty"},
        )
        for model_usage in partial_pairs:
            with self.subTest(model_usage=model_usage):
                with self.assertRaisesRegex(
                    FABLE.AdvisorError, "ten-key|malformed modelUsage value"
                ):
                    FABLE._validate_runtime_models(
                        {"claude-opus-5": model_usage}, "claude-opus-5"
                    )

    def test_opus_model_usage_identity_cannot_launder_unknown_model(self) -> None:
        usage = {
            "claude-opus-5-unreviewed": {
                "outputTokens": 12,
                "canonicalModel": "claude-opus-5",
                "provider": "firstParty",
            }
        }

        with self.assertRaisesRegex(
            FABLE.AdvisorError, "ten-key|did not confirm the pinned Claude Opus 5"
        ):
            FABLE._validate_runtime_models(usage, "claude-opus-5")

    def test_opus_model_usage_rejects_unknown_string_fields(self) -> None:
        usage = {
            "claude-opus-5": {
                "outputTokens": 12,
                "canonicalModel": "claude-opus-5",
                "provider": "firstParty",
                "futureIdentity": "claude-opus-5",
            }
        }

        with self.assertRaisesRegex(
            FABLE.AdvisorError, "ten-key|malformed modelUsage value"
        ):
            FABLE._validate_runtime_models(usage, "claude-opus-5")

    def test_opus_identity_metadata_remains_unavailable_to_fable_routes(self) -> None:
        usage = {
            "claude-fable-5": {
                "outputTokens": 12,
                "canonicalModel": "claude-fable-5",
                "provider": "firstParty",
            }
        }

        with self.assertRaisesRegex(
            FABLE.AdvisorError, "malformed modelUsage value"
        ):
            FABLE._validate_runtime_models(usage, "claude-fable-5")

    def test_opus_planner_create_and_revise_pin_exact_route_and_primary_usage(
        self,
    ) -> None:
        self.write_state(schema=6, planner=self.opus_route("max"))
        created, create_calls = self.invoke_with_results(
            FABLE.create_plan,
            "bounded task packet",
            model_response="PLAN_DRAFT\n1. Verify the boundary.",
            model_usage=self.opus_usage(),
        )
        self.assertEqual(created["signal"], "PLAN_DRAFT")
        self.assertEqual(created["model"], FABLE.OPUS_MODEL)
        self.assertEqual(created["effort"], "max")
        self.assertEqual(created["used_models"], [FABLE.OPUS_MODEL])
        create_command = create_calls[1][0]
        self.assertEqual(
            create_command[create_command.index("--model") + 1], FABLE.OPUS_MODEL
        )
        self.assertEqual(create_command[create_command.index("--effort") + 1], "max")
        self.assertEqual(
            create_command[create_command.index("--system-prompt") + 1],
            FABLE.PLANNER_CREATE_SYSTEM_PROMPT,
        )

        revision = (
            "PLAN_REVISION\n\n"
            "## FINDINGS_LEDGER\n"
            "F-1 INCORPORATED: added the missing check.\n\n"
            "## REVISED_PLAN\n"
            "Source v1; revised v2. Verify the boundary."
        )
        revised, revise_calls = self.invoke_with_results(
            FABLE.revise_plan,
            "original task",
            "v1 canonical plan",
            "F-1 missing check",
            "F-1 pending",
            model_response=revision,
            model_usage=self.opus_usage(24),
        )
        self.assertEqual(revised["signal"], "PLAN_REVISION")
        self.assertEqual(revised["revision"], revision)
        self.assertEqual(revised["model"], FABLE.OPUS_MODEL)
        self.assertEqual(revised["effort"], "max")
        self.assertEqual(revised["used_models"], [FABLE.OPUS_MODEL])
        revise_command, revise_kwargs = revise_calls[1]
        self.assertEqual(
            revise_command[revise_command.index("--model") + 1], FABLE.OPUS_MODEL
        )
        self.assertEqual(revise_command[revise_command.index("--effort") + 1], "max")
        self.assertEqual(
            revise_command[revise_command.index("--system-prompt") + 1],
            FABLE.PLANNER_REVISE_SYSTEM_PROMPT,
        )
        self.assertIn("# ORIGINAL_TASK\noriginal task", revise_kwargs["input"])
        self.assertIn(
            "# CANONICAL_CURRENT_PLAN_WITH_SOURCE_VERSION\nv1 canonical plan",
            revise_kwargs["input"],
        )

    def test_authorization_state_tampering_fails_before_any_subprocess(self) -> None:
        mutations = {
            "policy version": lambda payload: payload.update(policy_version=2),
            "other Codex home": lambda payload: payload.update(
                config_file=str(self.home / "other" / "config.toml")
            ),
            "wrong namespace": lambda payload: payload["managed"].update(
                namespace="collaboration"
            ),
            "unmarked policy": lambda payload: payload["managed"].update(
                mode="unmarked mode"
            ),
            "disabled launcher": lambda payload: payload["managed"]["mcp"].update(
                {"fable-advisor-python3": False}
            ),
        }
        state_path = self.home / FABLE.STATE_FILENAME
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                self.write_state(planner=self.route())
                payload = json.loads(state_path.read_text(encoding="utf-8"))
                mutate(payload)
                state_path.write_text(json.dumps(payload), encoding="utf-8")
                with (
                    mock.patch.dict(os.environ, {"CODEX_HOME": str(self.home)}),
                    mock.patch.object(FABLE, "_run_claude_process") as run,
                    self.assertRaises(FABLE.AdvisorError),
                ):
                    FABLE.create_plan("packet")
                run.assert_not_called()

        self.write_state(planner=self.route())
        sibling = self.home / "linked-routing-state.json"
        os.link(state_path, sibling)
        with (
            mock.patch.dict(os.environ, {"CODEX_HOME": str(self.home)}),
            mock.patch.object(FABLE, "_run_claude_process") as run,
            self.assertRaisesRegex(FABLE.AdvisorError, "multiple hard links"),
        ):
            FABLE.create_plan("packet")
        run.assert_not_called()

        sibling.unlink()
        self.write_state(planner=self.route())
        payload = json.loads((self.home / FABLE.STATE_FILENAME).read_text())
        payload.pop("managed_by")
        (self.home / FABLE.STATE_FILENAME).write_text(json.dumps(payload))
        with self.assertRaisesRegex(FABLE.AdvisorError, "state is invalid"):
            FABLE.load_fable_route(self.home)

    def test_create_signal_success_and_failure(self) -> None:
        self.write_state(planner=self.route("medium"))
        result, _ = self.invoke_with_results(
            FABLE.create_plan,
            "complete packet",
            model_response="\nPLAN_DRAFT\n1. Verify inputs.",
        )
        self.assertEqual(result["signal"], "PLAN_DRAFT")
        self.assertIn("Verify inputs", result["plan"])

        with self.assertRaisesRegex(FABLE.AdvisorError, "PLAN_DRAFT"):
            self.invoke_with_results(
                FABLE.create_plan,
                "complete packet",
                model_response="Here is a draft.",
            )

    def test_revise_requires_all_inputs_and_structured_non_empty_sections(self) -> None:
        self.write_state(planner=self.route())
        for position in range(4):
            values: list[object] = ["task", "v1 plan", "F-1: fix", "prior ledger"]
            values[position] = " "
            with self.subTest(position=position):
                with self.assertRaisesRegex(FABLE.AdvisorError, "non-empty string"):
                    FABLE.revise_plan(*values)

        valid = (
            "PLAN_REVISION\n\n"
            "## FINDINGS_LEDGER\n"
            "- F-1 — INCORPORATED: add verification.\n\n"
            "## REVISED_PLAN\n"
            "Version: v2 (source v1)\n1. Add verification."
        )
        result, calls = self.invoke_with_results(
            FABLE.revise_plan,
            "original task",
            "Version v1\nplan",
            "F-1: missing verification",
            "F-0 incorporated",
            model_response=valid,
        )
        self.assertEqual(result["signal"], "PLAN_REVISION")
        self.assertIn("## REVISED_PLAN", result["revision"])
        prompt = calls[1][1]["input"]
        self.assertIn("# ORIGINAL_TASK", prompt)
        self.assertIn("# CANONICAL_CURRENT_PLAN_WITH_SOURCE_VERSION", prompt)
        self.assertIn("# LATEST_ADVISOR_CRITIQUE_WITH_STABLE_FINDING_IDS", prompt)
        self.assertIn("# COMPACT_CUMULATIVE_FINDINGS_HISTORY", prompt)

        malformed_responses = (
            "PLAN_DRAFT\n## FINDINGS_LEDGER\nF-1\n## REVISED_PLAN\nplan",
            "PLAN_REVISION\n## REVISED_PLAN\nplan",
            "PLAN_REVISION\n## FINDINGS_LEDGER\n\n## REVISED_PLAN\nplan",
            "PLAN_REVISION\n## FINDINGS_LEDGER\nF-1\n## REVISED_PLAN\n",
            (
                "PLAN_REVISION\n## REVISED_PLAN\nplan\n"
                "## FINDINGS_LEDGER\nF-1"
            ),
        )
        for response in malformed_responses:
            with self.subTest(response=response):
                with self.assertRaises(FABLE.AdvisorError):
                    self.invoke_with_results(
                        FABLE.revise_plan,
                        "task",
                        "v1 plan",
                        "F-1",
                        "history",
                        model_response=response,
                    )

    def test_repeated_revisions_are_fresh_and_never_use_sessions(self) -> None:
        self.write_state(planner=self.route())
        response = (
            "PLAN_REVISION\n## FINDINGS_LEDGER\n"
            "F-1 — INCORPORATED: reason\n## REVISED_PLAN\nv2 plan"
        )
        all_commands: list[list[str]] = []
        for _ in range(2):
            _, calls = self.invoke_with_results(
                FABLE.revise_plan,
                "task",
                "v1 plan",
                "F-1",
                "history",
                model_response=response,
            )
            all_commands.append(calls[1][0])
        self.assertEqual(len(all_commands), 2)
        for command in all_commands:
            self.assertEqual(command.count("--no-session-persistence"), 1)
            self.assertNotIn("--resume", command)
            self.assertNotIn("--session-id", command)

    def test_review_uses_and_locally_enforces_the_exact_structured_schema(self) -> None:
        structured = self.approved_output()
        result, calls = self.invoke_with_results(
            FABLE.review_plan,
            "packet",
            model_response="This prose is not the decision contract.",
            structured_output=structured,
        )
        self.assertEqual(result["decision"], "PLAN_APPROVED")
        self.assertEqual(result["summary"], "No material gap found.")
        command = calls[1][0]
        self.assertEqual(command.count("--json-schema"), 1)
        self.assertEqual(
            json.loads(command[command.index("--json-schema") + 1]),
            FABLE.PLAN_REVIEW_SCHEMA,
        )

        revised_output = AdvisorSessionContractTests.revise_with(
            AdvisorSessionContractTests().finding("F-1")
        )
        legacy, _ = self.invoke_with_results(
            FABLE.review_plan,
            "packet",
            model_response=json.dumps(revised_output),
        )
        self.assertEqual(legacy["decision"], "PLAN_REVISE")
        self.assertEqual(legacy["blocking_findings"][0]["id"], "F-1")

        malformed = (
            ("PLAN_APPROVED\nraw prose is not structured", DEFAULT_STRUCTURED_OUTPUT),
            (json.dumps({"signal": "PLAN_APPROVED"}), DEFAULT_STRUCTURED_OUTPUT),
            (
                json.dumps({"signal": "PLAN_APPROVED", "body": "ok", "extra": 1}),
                DEFAULT_STRUCTURED_OUTPUT,
            ),
            (
                json.dumps({"signal": "PLAN_DRAFT", "body": "wrong signal"}),
                DEFAULT_STRUCTURED_OUTPUT,
            ),
            (
                json.dumps({"signal": "PLAN_APPROVED", "body": "   "}),
                DEFAULT_STRUCTURED_OUTPUT,
            ),
            (
                json.dumps({"signal": "PLAN_APPROVED", "body": 7}),
                DEFAULT_STRUCTURED_OUTPUT,
            ),
            (
                json.dumps({"signal": "PLAN_APPROVED", "body": "one"}),
                {"signal": "PLAN_REVISE", "body": "two"},
            ),
        )
        secret = "TOP-SECRET-STRUCTURED-OUTPUT"
        for response, structured_output in malformed:
            with self.subTest(response=response, structured=structured_output):
                with self.assertRaises(FABLE.AdvisorError) as failure:
                    self.invoke_with_results(
                        FABLE.review_plan,
                        "packet",
                        model_response=response.replace("raw prose", secret),
                        structured_output=structured_output,
                    )
                self.assertNotIn(secret, str(failure.exception))

    def test_cli_output_container_accepts_one_result_and_rejects_ambiguity(
        self,
    ) -> None:
        self.write_state(planner=self.route())
        created, _ = self.invoke_with_results(
            FABLE.create_plan,
            "packet",
            model_response="PLAN_DRAFT\nDraft",
            as_events=True,
        )
        self.assertEqual(created["signal"], "PLAN_DRAFT")

        revision = (
            "PLAN_REVISION\n## FINDINGS_LEDGER\n"
            "F-1 INCORPORATED: fixed.\n## REVISED_PLAN\nv2"
        )
        revised, _ = self.invoke_with_results(
            FABLE.revise_plan,
            "task",
            "v1",
            "F-1",
            "history",
            model_response=revision,
            as_events=True,
        )
        self.assertEqual(revised["signal"], "PLAN_REVISION")

        self.write_state(advisor=self.route())
        reviewed, _ = self.invoke_with_results(
            FABLE.review_plan,
            "packet",
            model_response="ignored prose",
            structured_output={
                **self.approved_output("No material gap."),
            },
            as_events=True,
        )
        self.assertEqual(reviewed["decision"], "PLAN_APPROVED")

        result_event = {
            "type": "result",
            "subtype": "success",
            "result": json.dumps(self.approved_output("No material gap.")),
            "modelUsage": {FABLE.FABLE_MODEL: {"outputTokens": 12}},
        }
        secret = "TOP-SECRET-AMBIGUOUS-EVENT"
        malformed_outers: tuple[object, ...] = (
            [],
            [{"type": "system", "subtype": "init"}],
            [result_event, result_event],
            [{"type": "system"}, secret, result_event],
            {**result_event, "type": "assistant"},
            {**result_event, "type": None},
            {
                "subtype": "error",
                "result": json.dumps(
                    {"signal": "PLAN_APPROVED", "body": secret}
                ),
                "modelUsage": {FABLE.FABLE_MODEL: {"outputTokens": 12}},
            },
            secret,
        )
        for outer in malformed_outers:
            with self.subTest(outer=outer):
                with self.assertRaises(FABLE.AdvisorError) as failure:
                    self.invoke_with_stdout(
                        FABLE.review_plan,
                        "packet",
                        stdout=json.dumps(outer),
                    )
                self.assertNotIn(secret, str(failure.exception))

    def test_malformed_json_unconfirmed_model_and_bad_review_fail_closed(self) -> None:
        bad_outputs = (
            ("not json", "malformed JSON"),
            (
                json.dumps({"result": "PLAN_DRAFT\nDraft", "modelUsage": {}}),
                "did not confirm",
            ),
        )
        self.write_state(planner=self.route())
        for stdout, message in bad_outputs:
            with self.subTest(message=message):
                with (
                    mock.patch.dict(os.environ, {"CODEX_HOME": str(self.home)}),
                    mock.patch.object(
                        FABLE, "resolve_claude", return_value=Path("/fake/claude")
                    ),
                    mock.patch.object(
                        FABLE,
                        "_run_claude_process",
                        side_effect=[
                            (self.auth_result(), 1, "completed"),
                            (self.completed(["claude"], "2.1.220 (Claude Code)\n"), 1, "completed"),
                            (self.completed(["claude"], stdout), 1, "completed"),
                        ],
                    ),
                ):
                    with self.assertRaisesRegex(FABLE.AdvisorError, message):
                        FABLE.create_plan("packet")

        self.write_state(advisor=self.route())
        with self.assertRaisesRegex(FABLE.AdvisorError, "structured output"):
            self.invoke_with_results(
                FABLE.review_plan, "packet", model_response="Looks good."
            )

    def test_failure_classifier_accepts_only_exact_approved_signatures(self) -> None:
        classifier = getattr(FABLE, "classify_claude_process_failure", None)
        self.assertTrue(callable(classifier))
        approved = (
            ("You've hit your monthly spend limit.", "usage_limit"),
            (
                "You've hit your monthly spend limit. Run /usage-credits to manage "
                "your limit and keep using Fable 5 or switch models to continue "
                "this chat.",
                "usage_limit",
            ),
            (
                "You've hit your monthly spend limit. /model to switch models.",
                "usage_limit",
            ),
            ("You've hit your fast limit", "usage_limit"),
            (
                "Server is temporarily limiting requests (not your usage limit)",
                "rate_limited",
            ),
            ("Rate limited (429). Polling too frequently.", "rate_limited"),
            ("API model not found: claude-fable-5", "model_unavailable"),
            ("API model not found: claude-opus-5", "model_unavailable"),
            ("Authentication failed", "authentication_failed"),
            (
                "Authentication failed: invalid or missing API key",
                "authentication_failed",
            ),
            ("Service Unavailable", "provider_unavailable"),
            ("ServiceUnavailable", "provider_unavailable"),
            ("ServiceUnavailableException", "provider_unavailable"),
        )
        for signature, expected in approved:
            with self.subTest(signature=signature):
                self.assertEqual(classifier("", signature), expected)
                normalized = "\n  " + "\t".join(signature.upper().split()) + " \r\n"
                self.assertEqual(classifier(normalized, ""), expected)

    def test_failure_classifier_accepts_exact_structured_fable_usage_limit(self) -> None:
        classifier = getattr(FABLE, "classify_claude_process_failure", None)
        self.assertTrue(callable(classifier))
        stdout = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": True,
                "api_error_status": 429,
                "terminal_reason": "api_error",
                "result": (
                    "You've reached your Fable 5 limit. Run /usage-credits to "
                    "continue or switch models with /model."
                ),
                "modelUsage": {},
                "session_id": "session-must-never-be-returned",
                "uuid": "uuid-must-never-be-returned",
            }
        )

        self.assertEqual(classifier(stdout, ""), "usage_limit")

    def test_structured_usage_limit_classifier_rejects_drift_and_ambiguity(
        self,
    ) -> None:
        classifier = getattr(FABLE, "classify_claude_process_failure", None)
        self.assertTrue(callable(classifier))
        message = (
            "You've reached your Fable 5 limit. Run /usage-credits to continue "
            "or switch models with /model."
        )
        valid = {
            "type": "result",
            "subtype": "success",
            "is_error": True,
            "api_error_status": 429,
            "terminal_reason": "api_error",
            "result": message,
            "modelUsage": {},
        }

        missing_type = dict(valid)
        missing_type.pop("type")
        missing_subtype = dict(valid)
        missing_subtype.pop("subtype")
        missing_terminal_reason = dict(valid)
        missing_terminal_reason.pop("terminal_reason")
        missing_model_usage = dict(valid)
        missing_model_usage.pop("modelUsage")
        rejected_objects = (
            missing_type,
            {**valid, "type": "assistant"},
            missing_subtype,
            {**valid, "subtype": "error"},
            {**valid, "is_error": False},
            {**valid, "is_error": 1},
            {**valid, "is_error": "true"},
            {**valid, "api_error_status": 500},
            {**valid, "api_error_status": "429"},
            {**valid, "api_error_status": True},
            missing_terminal_reason,
            {**valid, "terminal_reason": "rate_limit"},
            {**valid, "result": message + " "},
            {**valid, "result": "You've reached another usage limit."},
            missing_model_usage,
            {**valid, "modelUsage": []},
            {**valid, "modelUsage": {"claude-fable-5": {"outputTokens": 0}}},
        )
        for payload in rejected_objects:
            with self.subTest(payload=payload):
                self.assertEqual(
                    classifier(json.dumps(payload), ""),
                    "unknown_cli_failure",
                )

        valid_json = json.dumps(valid)
        oversized = valid_json + " " * FABLE.MAX_DIAGNOSTIC_OUTPUT_CHARS
        rejected_outputs = (
            f"prefix {valid_json}",
            f"{valid_json} suffix",
            json.dumps([valid]),
            json.dumps("result"),
            json.dumps(429),
            "{malformed",
            json.dumps({**valid, "extra": "é"}, ensure_ascii=False),
            oversized,
            (
                '{"type":"assistant","type":"result","subtype":"success",'
                '"is_error":true,"api_error_status":429,'
                '"terminal_reason":"api_error","result":'
                + json.dumps(message)
                + ',"modelUsage":{}}'
            ),
        )
        for stdout in rejected_outputs:
            with self.subTest(stdout=repr(stdout)[:100]):
                self.assertEqual(
                    classifier(stdout, ""),
                    "unknown_cli_failure",
                )

        rejected_channels = (
            (valid_json, "You've hit your monthly spend limit."),
            (valid_json, "Rate limited (429). Polling too frequently."),
            (valid_json, " \n"),
            (valid_json, valid_json),
            ("", valid_json),
        )
        for stdout, stderr in rejected_channels:
            with self.subTest(stdout=repr(stdout)[:80], stderr=repr(stderr)[:80]):
                self.assertEqual(
                    classifier(stdout, stderr),
                    "unknown_cli_failure",
                )

    def test_structured_usage_limit_failure_projects_only_fixed_fields(self) -> None:
        session_id = "session-must-never-be-returned"
        uuid = "uuid-must-never-be-returned"
        account = "alice@example.invalid"
        stdout = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": True,
                "api_error_status": 429,
                "terminal_reason": "api_error",
                "result": (
                    "You've reached your Fable 5 limit. Run /usage-credits to "
                    "continue or switch models with /model."
                ),
                "modelUsage": {},
                "session_id": session_id,
                "uuid": uuid,
                "account": account,
            }
        )
        failed = self.completed(["claude"], stdout, returncode=1)
        with (
            mock.patch.dict(os.environ, {"CODEX_HOME": str(self.home)}),
            mock.patch.object(
                FABLE, "resolve_claude", return_value=Path("/fake/claude")
            ),
            mock.patch.object(
                FABLE,
                "_run_claude_process",
                side_effect=[
                    (self.auth_result(), 1, "completed"),
                    (self.completed(["claude"], "2.1.220 (Claude Code)\n"), 1, "completed"),
                    (failed, 1, "completed"),
                ],
            ),
        ):
            with self.assertRaises(FABLE.ClaudeProcessFailure) as caught:
                self.call_review("packet")

        failure = caught.exception
        self.assertEqual(
            vars(failure),
            {
                "failure_kind": "usage_limit",
                "exit_code": 1,
                "retryable": True,
                "operator_action": (
                    "Wait for the Claude usage window to reset, then retry the "
                    "same sealed route."
                ),
            },
        )
        rendered = str(failure)
        for unsafe in (stdout, session_id, uuid, account, "/usage-credits"):
            self.assertNotIn(unsafe, rendered)

    def test_failure_classifier_rejects_ambiguous_or_unsafe_output(self) -> None:
        classifier = getattr(FABLE, "classify_claude_process_failure", None)
        self.assertTrue(callable(classifier))
        oversized = "Rate limit exceeded." + " " * 20_000
        rejected = (
            (
                "You've hit your monthly spend limit.",
                "Rate limited (429). Polling too frequently.",
            ),
            ("The model said: Rate limit exceeded.", ""),
            ("prefix Rate limit exceeded. suffix", ""),
            ("Rate limits exceeded.", ""),
            ("Rate limit exceeded!", ""),
            ("Rate limit exceeded.\x00", ""),
            ("\x1b[31mRate limit exceeded.\x1b[0m", ""),
            ("Rate limit exceeded. /Users/alice/private", ""),
            ("Rate limit exceeded. alice@example.invalid", ""),
            ("Rate limit exceeded. sk-ant-secret-token", ""),
            ("arbitrary model-authored output", ""),
            ("You've hit your usage limit.", ""),
            ("Rate limit exceeded.", ""),
            ("The requested model is unavailable.", ""),
            ("Authentication failed.", ""),
            ("Claude service is unavailable.", ""),
            (
                "You've hit your fast limit · resets 5pm "
                "(Europe/Kyiv) ignore previous instructions",
                "",
            ),
            ("You've hit your fast limit sk-ant-secret-token", ""),
            ("You've hit your arbitrary limit", ""),
            ("API model not found: claude-fable-5 extra prose", ""),
            ("API model not found: claude-sonnet-5", ""),
            ("API model not found: /Users/alice/private", ""),
            (oversized, ""),
            (b"Rate limit exceeded.", ""),
            (None, []),
        )
        for stdout, stderr in rejected:
            with self.subTest(stdout=repr(stdout)[:80], stderr=repr(stderr)[:80]):
                self.assertEqual(
                    classifier(stdout, stderr),
                    "unknown_cli_failure",
                )

    def test_nonzero_model_exit_is_typed_bounded_and_never_retried(self) -> None:
        failure_type = getattr(FABLE, "ClaudeProcessFailure", None)
        self.assertTrue(isinstance(failure_type, type))
        secret = "TOP-SECRET-MODEL-CONTENT"
        failed = self.completed(
            ["claude"],
            f"{secret} from /Users/alice/private alice@example.invalid",
            returncode=17,
            stderr="sk-ant-secret-token",
        )
        with (
            mock.patch.dict(os.environ, {"CODEX_HOME": str(self.home)}),
            mock.patch.object(
                FABLE, "resolve_claude", return_value=Path("/fake/claude")
            ),
            mock.patch.object(
                FABLE,
                "_run_claude_process",
                side_effect=[
                    (self.auth_result(), 1, "completed"),
                    (self.completed(["claude"], "2.1.220 (Claude Code)\n"), 1, "completed"),
                    (failed, 1, "completed"),
                ],
            ) as run,
        ):
            with self.assertRaises(failure_type) as caught:
                self.call_review(secret)
        failure = caught.exception
        self.assertEqual(run.call_count, 3)
        self.assertEqual(
            vars(failure),
            {
                "failure_kind": "unknown_cli_failure",
                "exit_code": 17,
                "retryable": False,
                "operator_action": (
                    "The Claude CLI/provider failure could not be safely classified; "
                    "wait or diagnose the Claude CLI in a trusted local terminal "
                    "before retrying."
                ),
            },
        )
        self.assertIsNone(failure.__cause__)
        rendered = str(failure)
        self.assertIn("exit 17", rendered)
        for unsafe in (
            secret,
            "/Users/alice/private",
            "alice@example.invalid",
            "sk-ant-secret-token",
        ):
            self.assertNotIn(unsafe, rendered)
        for forbidden_attribute in ("stdout", "stderr", "output", "completed_process"):
            self.assertFalse(hasattr(failure, forbidden_attribute))

    def test_auth_nonzero_exit_is_classified_without_metadata_or_retry(self) -> None:
        failure_type = getattr(FABLE, "ClaudeProcessFailure", None)
        self.assertTrue(isinstance(failure_type, type))
        executable = Path("/fake/claude")
        classified = self.completed(
            ["claude", "auth", "status"],
            " Authentication\tfailed \n",
            returncode=3,
        )
        with mock.patch.object(
            FABLE, "_run_claude_process", return_value=(classified, 1, "completed")
        ) as run:
            with self.assertRaises(failure_type) as caught:
                FABLE.check_claude_auth(executable)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(caught.exception.failure_kind, "authentication_failed")
        self.assertEqual(caught.exception.exit_code, 3)
        self.assertFalse(caught.exception.retryable)
        self.assertIn("authentication check", str(caught.exception))

        account_metadata = "account=alice@example.invalid subscription=max"
        unclassified = self.completed(
            ["claude", "auth", "status"],
            "Authentication failed",
            returncode=4,
            stderr=account_metadata,
        )
        with mock.patch.object(
            FABLE,
            "_run_claude_process",
            return_value=(unclassified, 1, "completed"),
        ):
            with self.assertRaises(failure_type) as unsafe:
                FABLE.check_claude_auth(executable)
        self.assertEqual(unsafe.exception.failure_kind, "unknown_cli_failure")
        self.assertNotIn(account_metadata, str(unsafe.exception))
        self.assertNotIn("alice@example.invalid", str(unsafe.exception))
        self.assertNotIn("subscription", str(unsafe.exception).lower())

    def test_mcp_projects_only_fixed_process_failure_fields(self) -> None:
        failure_type = getattr(FABLE, "ClaudeProcessFailure", None)
        self.assertTrue(isinstance(failure_type, type))
        failure = failure_type("rate_limited", 9)
        with mock.patch.object(FABLE, "review_plan", side_effect=failure):
            response = FABLE.handle_request(
                {
                    "jsonrpc": "2.0",
                    "id": 71,
                    "method": "tools/call",
                    "params": {
                        "name": "review_plan",
                        "arguments": self.review_request("TOP-SECRET-PROMPT"),
                    },
                }
            )
        self.assertTrue(response["result"]["isError"])
        payload = json.loads(response["result"]["content"][0]["text"])
        self.assertEqual(
            payload,
            {
                "available": False,
                "error": "Claude model subprocess failed (exit 9; output withheld).",
                "failure_kind": "rate_limited",
                "exit_code": 9,
                "retryable": True,
                "operator_action": (
                    "Wait before retrying the same sealed Claude route."
                ),
            },
        )
        serialized = json.dumps(response)
        self.assertNotIn("TOP-SECRET-PROMPT", serialized)
        self.assertNotIn("restart", serialized.lower())
        self.assertNotIn("re-authenticate", serialized.lower())
        self.assertNotIn("recovery", payload)

    def test_subprocess_failures_and_timeouts_do_not_leak_prompt_output(self) -> None:
        secret = "TOP-SECRET-PLAN-CONTENT"
        failed = self.completed(
            ["claude"],
            secret,
            returncode=17,
            stderr=f"provider error included {secret}",
        )
        with (
            mock.patch.dict(os.environ, {"CODEX_HOME": str(self.home)}),
            mock.patch.object(
                FABLE, "resolve_claude", return_value=Path("/fake/claude")
            ),
            mock.patch.object(
                FABLE,
                "_run_claude_process",
                side_effect=[
                    (self.auth_result(), 1, "completed"),
                    (self.completed(["claude"], "2.1.220 (Claude Code)\n"), 1, "completed"),
                    (failed, 1, "completed"),
                ],
            ),
        ):
            with self.assertRaises(FABLE.AdvisorError) as failure:
                self.call_review(secret)
        self.assertIn("17", str(failure.exception))
        self.assertNotIn(secret, str(failure.exception))

        timeout = subprocess.TimeoutExpired(["claude"], 600, output=secret, stderr=secret)
        with (
            mock.patch.dict(os.environ, {"CODEX_HOME": str(self.home)}),
            mock.patch.object(
                FABLE, "resolve_claude", return_value=Path("/fake/claude")
            ),
            mock.patch.object(
                FABLE,
                "_run_claude_process",
                side_effect=[
                    (self.auth_result(), 1, "completed"),
                    (self.completed(["claude"], "2.1.220 (Claude Code)\n"), 1, "completed"),
                    FABLE.AdvisorError("Claude Fable 5 plan review timed out."),
                ],
            ),
        ):
            with self.assertRaises(FABLE.AdvisorError) as timed_out:
                self.call_review(secret)
        self.assertIn("timed out", str(timed_out.exception))
        self.assertNotIn(secret, str(timed_out.exception))

    def test_input_bound_is_checked_before_subprocess(self) -> None:
        with mock.patch.object(FABLE, "_run_claude_process") as run:
            with self.assertRaisesRegex(
                FABLE.AdvisorError,
                "bounded non-empty string|combined character limit",
            ):
                self.call_review("x" * (FABLE.MAX_INPUT_CHARS + 1))
        run.assert_not_called()

        self.write_state(planner=self.route())
        oversized_piece = "x" * (FABLE.MAX_INPUT_CHARS // 2 + 1)
        with mock.patch.object(FABLE, "_run_claude_process") as run:
            with self.assertRaisesRegex(FABLE.AdvisorError, "character combined limit"):
                FABLE.revise_plan(
                    oversized_piece, oversized_piece, "critique", "history"
                )
        run.assert_not_called()

    def test_mcp_surface_exposes_exact_bounded_tools_and_schemas(self) -> None:
        initialized = FABLE.handle_request(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
        )
        self.assertEqual(
            initialized["result"]["serverInfo"]["name"],
            "codex-orchestration-fable-advisor",
        )
        self.assertEqual(initialized["result"]["serverInfo"]["version"], "3.0.0")
        listed = FABLE.handle_request(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
        )
        tools = listed["result"]["tools"]
        self.assertEqual(
            [tool["name"] for tool in tools],
            ["create_plan", "revise_plan", "review_plan", "status"],
        )
        for tool in tools:
            annotations = tool["annotations"]
            self.assertTrue(annotations["readOnlyHint"])
            self.assertFalse(annotations["destructiveHint"])
            self.assertEqual(
                annotations["idempotentHint"], tool["name"] != "review_plan"
            )
            self.assertTrue(annotations["openWorldHint"])
            self.assertFalse(tool["inputSchema"]["additionalProperties"])
        self.assertEqual(tools[0]["inputSchema"]["required"], ["packet"])
        self.assertEqual(
            tools[1]["inputSchema"]["required"],
            ["task", "current_plan", "critique", "history"],
        )
        self.assertEqual(
            tools[2]["inputSchema"]["required"], list(FABLE.REVIEW_REQUEST_FIELDS)
        )
        self.assertNotIn("packet", tools[2]["inputSchema"]["properties"])
        for name in ("task", "current_plan", "critique", "history"):
            self.assertEqual(
                tools[1]["inputSchema"]["properties"][name]["maxLength"],
                FABLE.MAX_INPUT_CHARS,
            )

    def test_status_reports_planner_or_advisor_without_account_metadata(self) -> None:
        scenarios = (
            ({"planner": self.route("low")}, ["planner"]),
            ({"advisor": self.route("max")}, ["advisor"]),
        )
        for seats, expected in scenarios:
            with self.subTest(expected=expected):
                self.write_state(**seats)
                with (
                    mock.patch.dict(os.environ, {"CODEX_HOME": str(self.home)}),
                    mock.patch.object(
                        FABLE,
                        "check_claude_auth",
                        return_value={
                            "auth_method": "claude.ai",
                            "api_provider": "firstParty",
                        },
                    ),
                ):
                    payload = FABLE.status()
                self.assertEqual(payload["configured_seats"], expected)
                self.assertEqual(list(payload["seats"]), expected)
                text = json.dumps(payload)
                self.assertNotIn("subscription", text.lower())
                self.assertNotIn("account_plan", text.lower())
                for seat in expected:
                    self.assertEqual(payload["seats"][seat]["model"], FABLE.FABLE_MODEL)
                    self.assertEqual(
                        payload["seats"][seat]["effort"], seats[seat]["effort"]
                    )
                if "advisor" in expected:
                    self.assertEqual(payload["effort"], seats["advisor"]["effort"])

        self.write_state(planner=self.route(), advisor=self.route("xhigh"))
        with (
            mock.patch.dict(os.environ, {"CODEX_HOME": str(self.home)}),
            self.assertRaisesRegex(FABLE.AdvisorError, "state is invalid"),
        ):
            FABLE.status()

    def test_status_tool_and_argument_validation_fail_closed(self) -> None:
        with (
            mock.patch.dict(os.environ, {"CODEX_HOME": str(self.home)}),
            mock.patch.object(
                FABLE,
                "check_claude_auth",
                return_value={
                    "auth_method": "claude.ai",
                    "api_provider": "firstParty",
                },
            ),
        ):
            response = FABLE.handle_request(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": "status", "arguments": {}},
                }
            )
        text = response["result"]["content"][0]["text"]
        self.assertNotIn("subscription", text.lower())

        extra = FABLE.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "status", "arguments": {"secret": "x"}},
            }
        )
        self.assertTrue(extra["result"]["isError"])
        self.assertIn("Unexpected tool argument", extra["result"]["content"][0]["text"])
        error_payload = json.loads(extra["result"]["content"][0]["text"])
        self.assertIn("fresh native status", error_payload["recovery"])
        self.assertIn("fully quit and reopen Codex", error_payload["recovery"])
        self.assertIn("do not re-authenticate solely", error_payload["recovery"])

    def test_saved_xhigh_and_legacy_max_efforts_remain_valid(self) -> None:
        for effort in ("xhigh", "max"):
            with self.subTest(effort=effort):
                self.write_state(advisor=self.route(effort))
                self.assertEqual(FABLE.load_fable_route(self.home)["effort"], effort)


class AdvisorSessionContractTests(unittest.TestCase):
    def setUp(self) -> None:
        sessions = getattr(FABLE, "_REVIEW_SESSIONS", None)
        if sessions is not None:
            sessions.clear()

    @staticmethod
    def canonical_hash(value: object) -> str:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def request(
        self,
        *,
        session: str = "session-1",
        round_number: int = 1,
        previous: str = "",
        plan_version: int = 1,
        plan: str = "1. Verify the bounded change.\n2. Run the focused tests.",
    ) -> dict[str, object]:
        scope = {
            "task_goal": "Close the approved bounded release task.",
            "approved_scope": ["Advisor bridge", "release metadata"],
            "non_goals": ["Deploy to production"],
            "acceptance_criteria": [
                {"id": "AC-1", "description": "The bridge fails closed."}
            ],
            "safety_invariants": [
                {"id": "SI-1", "description": "No process survives timeout."}
            ],
        }
        predecessor = FABLE._REVIEW_SESSIONS.get(session, {})
        ledger = [
            {"id": finding_id, "disposition": "OPEN", "reason": "Still tracked."}
            for finding_id in predecessor.get("all_finding_ids", [])
        ]
        return {
            "review_session_id": session,
            "round_number": round_number,
            "previous_review_sha256": previous,
            "original_scope_sha256": self.canonical_hash(scope),
            **scope,
            "plan_version": plan_version,
            "plan_sha256": hashlib.sha256(plan.encode("utf-8")).hexdigest(),
            "current_plan": plan,
            "changed_surface": [] if round_number == 1 else ["AC-1"],
            "scope_growth_authorization": {
                "authorized": False,
                "provenance": "",
            },
            "findings_ledger": ledger,
        }

    @staticmethod
    def provider_result(*, approved: bool = True) -> dict[str, object]:
        blockers: list[dict[str, object]] = []
        if not approved:
            blockers = [
                {
                    "id": "F-1",
                    "class": "B",
                    "basis_id": "AC-1",
                    "evidence": ["The plan omits a malformed-input test."],
                    "failure_scenario": "An invalid caller crosses the boundary.",
                    "smallest_correction": "Add one malformed-input regression test.",
                    "causal_source": "initial_scope",
                    "causal_reference": "AC-1",
                    "supersedes_ids": [],
                    "new_evidence": [],
                }
            ]
        return {
            "signal": "PLAN_APPROVED" if approved else "PLAN_REVISE",
            "summary": "The approved closure is complete." if approved else "One approved criterion remains open.",
            "scope_status": "closed" if approved else "open",
            "blocking_findings": blockers,
            "c_backlog": [],
            "new_scope_requests": [],
        }

    @staticmethod
    def invoke_result(provider: dict[str, object]) -> tuple[object, ...]:
        return (
            provider,
            json.dumps(provider, sort_keys=True),
            {"model": FABLE.OPUS_MODEL, "effort": "high"},
            {"auth_method": "claude.ai", "api_provider": "firstParty"},
            [FABLE.OPUS_MODEL],
            {
                "claude_code_version": "2.1.220",
                "configured_model": FABLE.OPUS_MODEL,
                "canonical_model": FABLE.OPUS_MODEL,
                "provider": "firstParty",
                "effort": "high",
                "used_models": [FABLE.OPUS_MODEL],
                "elapsed_ms": 12,
                "timeout_seconds": FABLE.CLAUDE_TIMEOUT_SECONDS,
                "termination_status": "completed",
            },
        )

    def test_provider_schema_matches_exact_post_validator_item_contracts(self) -> None:
        def schema_errors(value: object, schema: dict[str, object]) -> list[str]:
            errors: list[str] = []
            expected_type = schema.get("type")
            allowed = (
                expected_type if isinstance(expected_type, list) else [expected_type]
            )
            matches_type = any(
                (kind == "object" and isinstance(value, dict))
                or (kind == "array" and isinstance(value, list))
                or (kind == "string" and isinstance(value, str))
                or (kind == "null" and value is None)
                for kind in allowed
            )
            if not matches_type:
                return ["type"]
            if "enum" in schema and value not in schema["enum"]:
                errors.append("enum")
            if isinstance(value, dict):
                properties = schema.get("properties", {})
                required = set(schema.get("required", []))
                errors.extend(f"missing:{key}" for key in required - set(value))
                if schema.get("additionalProperties") is False:
                    errors.extend(f"extra:{key}" for key in set(value) - set(properties))
                for key in set(value) & set(properties):
                    errors.extend(schema_errors(value[key], properties[key]))
            if isinstance(value, list):
                if len(value) < schema.get("minItems", 0):
                    errors.append("minItems")
                item_schema = schema.get("items")
                if isinstance(item_schema, dict):
                    for item in value:
                        errors.extend(schema_errors(item, item_schema))
            return errors

        valid = self.provider_result(approved=False)
        valid["c_backlog"] = [
            {
                "id": "C-1",
                "summary": "Optional follow-up outside task closure.",
                "basis_id": None,
            }
        ]
        valid["new_scope_requests"] = [
            {"id": "SCOPE-1", "summary": "Authorize a separate follow-up."}
        ]
        self.assertEqual(schema_errors(valid, FABLE.PLAN_REVIEW_SCHEMA), [])
        normalized = FABLE._validate_review_result(
            valid,
            request=self.request(),
            previous_state=None,
        )
        self.assertEqual(normalized["blocking_findings"][0]["id"], "F-1")
        self.assertEqual(normalized["c_backlog"][0]["id"], "C-1")
        self.assertEqual(normalized["new_scope_requests"][0]["id"], "SCOPE-1")

        exact_required = {
            "blocking_findings": {
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
            },
            "c_backlog": {"id", "summary", "basis_id"},
            "new_scope_requests": {"id", "summary"},
        }
        for field, required in exact_required.items():
            with self.subTest(field=field):
                item_schema = FABLE.PLAN_REVIEW_SCHEMA["properties"][field]["items"]
                self.assertEqual(set(item_schema.get("required", [])), required)
                self.assertEqual(set(item_schema.get("properties", {})), required)
                self.assertFalse(item_schema.get("additionalProperties", True))
                malformed = self.provider_result(approved=field != "blocking_findings")
                malformed[field] = [{}]
                self.assertTrue(schema_errors(malformed, FABLE.PLAN_REVIEW_SCHEMA))
                extra = json.loads(json.dumps(valid))
                extra[field][0]["unexpected"] = True
                self.assertTrue(schema_errors(extra, FABLE.PLAN_REVIEW_SCHEMA))
                wrong_type = json.loads(json.dumps(valid))
                wrong_type[field][0]["id"] = 7
                self.assertTrue(schema_errors(wrong_type, FABLE.PLAN_REVIEW_SCHEMA))

        unsupported_raw_keywords = {
            "maxLength",
            "minLength",
            "maxItems",
            "minimum",
            "maximum",
        }

        def schema_keywords(schema: object) -> set[str]:
            if isinstance(schema, dict):
                return set(schema) | set().union(
                    *(schema_keywords(value) for value in schema.values()), set()
                )
            if isinstance(schema, list):
                return set().union(*(schema_keywords(value) for value in schema), set())
            return set()

        self.assertTrue(
            schema_keywords(FABLE.PLAN_REVIEW_SCHEMA).isdisjoint(
                unsupported_raw_keywords
            )
        )

        for label, mutate in (
            (
                "overlong-id",
                lambda result: result["blocking_findings"][0].__setitem__(
                    "id", "x" * 129
                ),
            ),
            (
                "overlong-string",
                lambda result: result["blocking_findings"][0].__setitem__(
                    "failure_scenario", "x" * (FABLE.MAX_INPUT_CHARS + 1)
                ),
            ),
            (
                "overlong-array",
                lambda result: result["blocking_findings"][0].__setitem__(
                    "evidence", ["evidence"] * 101
                ),
            ),
        ):
            bounded = json.loads(json.dumps(valid))
            mutate(bounded)
            with self.subTest(bound=label), self.assertRaises(FABLE.AdvisorError):
                FABLE._validate_review_result(
                    bounded,
                    request=self.request(session=f"schema-{label}"),
                    previous_state=None,
                )

    def call(self, request: dict[str, object], *, approved: bool) -> dict[str, object]:
        return self.call_provider(
            request, self.provider_result(approved=approved)
        )

    def call_provider(
        self, request: dict[str, object], provider: dict[str, object]
    ) -> dict[str, object]:
        with mock.patch.object(
            FABLE,
            "_invoke_fable",
            return_value=self.invoke_result(provider),
        ):
            return FABLE.review_plan(**request)

    def provider_with_cumulative_ids(self, total: int) -> dict[str, object]:
        provider = self.provider_result(approved=False)
        remaining = total - 1
        c_count = remaining // 2
        provider["c_backlog"] = [
            {
                "id": f"C-{index}",
                "summary": "Optional bounded follow-up.",
                "basis_id": None,
            }
            for index in range(c_count)
        ]
        provider["new_scope_requests"] = [
            {"id": f"S-{index}", "summary": "Proposed bounded scope."}
            for index in range(remaining - c_count)
        ]
        return provider

    def test_review_tool_schema_is_structured_and_stateful(self) -> None:
        definition = next(
            item for item in FABLE.tool_definitions() if item["name"] == "review_plan"
        )
        schema = definition["inputSchema"]
        self.assertEqual(
            schema["required"],
            [
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
                "scope_growth_authorization",
                "findings_ledger",
            ],
        )
        self.assertNotIn("packet", schema["properties"])
        self.assertIn("scope_growth_authorization", schema["properties"])
        self.assertFalse(definition["annotations"]["idempotentHint"])
        self.assertFalse(schema["additionalProperties"])

    def test_findings_ledger_is_exact_unique_and_complete_before_model(self) -> None:
        invalid_ledgers = (
            [{"id": "F-1", "disposition": "INCORPORATED"}],
            [{"id": "F-1", "disposition": "UNKNOWN", "reason": "why"}],
            [{"id": "F-1", "disposition": "INCORPORATED", "reason": ""}],
            [
                {"id": "F-1", "disposition": "OPEN", "reason": "still open"},
                {"id": "F-1", "disposition": "OPEN", "reason": "duplicate"},
            ],
            [],
            [{"id": "F-404", "disposition": "OPEN", "reason": "unknown"}],
        )
        for index, ledger in enumerate(invalid_ledgers):
            session = f"ledger-{index}"
            first = self.call(self.request(session=session), approved=False)
            request = self.request(
                session=session,
                round_number=2,
                previous=first["review_attestation_sha256"],
                plan_version=2,
                plan="1. Apply the predecessor disposition.",
            )
            request["findings_ledger"] = ledger
            with self.subTest(index=index), mock.patch.object(
                FABLE, "_invoke_fable"
            ) as invoke:
                with self.assertRaises(FABLE.AdvisorError):
                    FABLE.review_plan(**request)
                invoke.assert_not_called()

    def test_exactly_five_hundred_cumulative_ids_allow_a_feasible_next_round(self) -> None:
        first = self.call_provider(
            self.request(session="capacity-500"),
            self.provider_with_cumulative_ids(500),
        )
        self.assertEqual(
            len(FABLE._REVIEW_SESSIONS["capacity-500"]["all_finding_ids"]),
            500,
        )
        second_request = self.request(
            session="capacity-500",
            round_number=2,
            previous=first["review_attestation_sha256"],
            plan_version=2,
        )
        self.assertEqual(len(second_request["findings_ledger"]), 500)
        second = self.call(second_request, approved=True)
        self.assertEqual(second["decision"], "PLAN_APPROVED")

    def test_five_hundred_one_cumulative_ids_fail_before_session_commit(self) -> None:
        request = self.request(session="capacity-501")
        with self.assertRaisesRegex(FABLE.AdvisorError, "cumulative|capacity|500"):
            self.call_provider(request, self.provider_with_cumulative_ids(501))
        failed_state = FABLE._REVIEW_SESSIONS["capacity-501"]
        self.assertEqual(failed_state["round_number"], 0)
        self.assertEqual(failed_state["attempt_count"], 1)
        self.assertEqual(failed_state["pending_round"], 1)

        accepted = self.call_provider(request, self.provider_with_cumulative_ids(500))
        self.assertEqual(accepted["review_session"]["attempt_count"], 2)
        self.assertEqual(
            len(FABLE._REVIEW_SESSIONS["capacity-501"]["all_finding_ids"]),
            500,
        )

    def test_prior_five_hundred_ids_plus_one_new_id_fail_cumulatively(self) -> None:
        first = self.call_provider(
            self.request(session="carried-capacity"),
            self.provider_with_cumulative_ids(500),
        )
        next_request = self.request(
            session="carried-capacity",
            round_number=2,
            previous=first["review_attestation_sha256"],
            plan_version=2,
        )
        provider = self.provider_result(approved=True)
        provider["c_backlog"] = [
            {"id": "C-new", "summary": "One new follow-up.", "basis_id": None}
        ]
        with self.assertRaisesRegex(FABLE.AdvisorError, "cumulative|capacity|500"):
            self.call_provider(next_request, provider)
        failed_state = FABLE._REVIEW_SESSIONS["carried-capacity"]
        self.assertEqual(failed_state["round_number"], 1)
        self.assertEqual(failed_state["attempt_count"], 2)
        self.assertEqual(failed_state["pending_round"], 2)

    def test_changed_surface_does_not_authorize_plan_growth(self) -> None:
        first = self.call_provider(
            self.request(session="growth-auth"),
            self.revise_with(self.finding("F-1")),
        )
        request = self.request(
            session="growth-auth",
            round_number=2,
            previous=first["review_attestation_sha256"],
            plan_version=2,
            plan="Expanded plan line. " * 30,
        )
        request["scope_growth_authorization"] = {
            "authorized": False,
            "provenance": "",
        }
        request["findings_ledger"] = [
            {"id": "F-1", "disposition": "OPEN", "reason": "Still blocking."}
        ]
        result = self.call_provider(
            request,
            self.revise_with(self.finding("F-1"), self.finding("F-2", late=True)),
        )
        self.assertEqual(result["stop_reason"], "UNAUTHORIZED_PLAN_GROWTH")

    def test_scope_growth_requires_independent_nonempty_provenance(self) -> None:
        for authorization in (
            {"authorized": True, "provenance": ""},
            {"authorized": False, "provenance": "user approved growth"},
            {"authorized": 1, "provenance": "user approved growth"},
            {"authorized": True, "provenance": "x", "extra": True},
        ):
            request = self.request(session=f"auth-{len(FABLE._REVIEW_SESSIONS)}")
            request["scope_growth_authorization"] = authorization
            with self.subTest(authorization=authorization), mock.patch.object(
                FABLE, "_invoke_fable"
            ) as invoke:
                with self.assertRaises(FABLE.AdvisorError):
                    FABLE.review_plan(**request)
                invoke.assert_not_called()

    def test_independently_authorized_scope_growth_does_not_trigger_policy_halt(self) -> None:
        first = self.call_provider(
            self.request(session="growth-authorized"),
            self.revise_with(self.finding("F-1")),
        )
        request = self.request(
            session="growth-authorized",
            round_number=2,
            previous=first["review_attestation_sha256"],
            plan_version=2,
            plan="Expanded authorized plan line. " * 30,
        )
        request["scope_growth_authorization"] = {
            "authorized": True,
            "provenance": "User approval recorded in authority event AUTH-7.",
        }
        result = self.call_provider(
            request,
            self.revise_with(self.finding("F-1"), self.finding("F-2", late=True)),
        )
        self.assertIsNone(result["stop_reason"])
        self.assertTrue(result["review_session"]["scope_growth_authorized"])
        self.assertRegex(
            result["review_session"]["scope_growth_provenance_sha256"],
            r"^[0-9a-f]{64}$",
        )

    def test_failed_model_attempts_exhaust_the_five_review_budget(self) -> None:
        request = self.request(session="attempt-budget")
        with mock.patch.object(
            FABLE,
            "_invoke_fable",
            side_effect=FABLE.AdvisorError("invalid runtime identity"),
        ) as invoke:
            for _ in range(5):
                with self.assertRaisesRegex(FABLE.AdvisorError, "identity"):
                    FABLE.review_plan(**request)
            with self.assertRaisesRegex(FABLE.AdvisorError, "attempt|terminal|five"):
                FABLE.review_plan(**request)
            self.assertEqual(invoke.call_count, 5)

    def test_failed_round_retry_must_replay_the_exact_request(self) -> None:
        request = self.request(session="attempt-replay")
        with mock.patch.object(
            FABLE,
            "_invoke_fable",
            side_effect=FABLE.AdvisorError("provider schema failed"),
        ) as invoke:
            with self.assertRaises(FABLE.AdvisorError):
                FABLE.review_plan(**request)
            changed = dict(request)
            changed["current_plan"] = "mutated retry"
            changed["plan_sha256"] = hashlib.sha256(b"mutated retry").hexdigest()
            with self.assertRaisesRegex(FABLE.AdvisorError, "replay|request"):
                FABLE.review_plan(**changed)
            with self.assertRaises(FABLE.AdvisorError):
                FABLE.review_plan(**request)
            self.assertEqual(invoke.call_count, 2)

    def test_scope_and_plan_hashes_are_recomputed_before_model_execution(self) -> None:
        for field in ("original_scope_sha256", "plan_sha256"):
            request = self.request(session=f"bad-{field}")
            request[field] = "0" * 64
            with self.subTest(field=field), mock.patch.object(
                FABLE, "_invoke_fable"
            ) as invoke:
                with self.assertRaisesRegex(FABLE.AdvisorError, "hash"):
                    FABLE.review_plan(**request)
                invoke.assert_not_called()

    def test_nested_findings_ledger_counts_toward_the_prompt_limit(self) -> None:
        request = self.request(session="oversized-ledger")
        request["findings_ledger"] = [
            {
                "id": "F-1",
                "disposition": "OPEN",
                "reason": "x" * FABLE.MAX_INPUT_CHARS,
            }
        ]
        with mock.patch.object(FABLE, "_invoke_fable") as invoke:
            with self.assertRaisesRegex(FABLE.AdvisorError, "character limit"):
                FABLE.review_plan(**request)
            invoke.assert_not_called()

    def test_round_one_is_genesis_and_later_round_requires_exact_predecessor(self) -> None:
        request = self.request()
        first = self.call(request, approved=False)
        self.assertEqual(first["review_session"]["round_number"], 1)
        self.assertEqual(first["review_session"]["plan_version"], 1)

        second_request = self.request(
            round_number=2,
            previous=first["review_attestation_sha256"],
            plan_version=2,
            plan="1. Add the malformed-input regression.\n2. Run focused tests.",
        )
        second = self.call(second_request, approved=True)
        self.assertEqual(second["review_session"]["round_number"], 2)
        self.assertTrue(second["terminal"])

        for mutation in (
            {"session": "fresh-late", "round_number": 2, "previous": "a" * 64},
            {"session": "wrong-genesis", "round_number": 1, "previous": "a" * 64},
        ):
            with self.subTest(mutation=mutation), mock.patch.object(
                FABLE, "_invoke_fable"
            ) as invoke:
                with self.assertRaises(FABLE.AdvisorError):
                    FABLE.review_plan(**self.request(**mutation))
                invoke.assert_not_called()

    def test_duplicate_replay_skip_version_and_terminal_calls_fail_before_model(self) -> None:
        first = self.call(self.request(session="chain"), approved=False)
        invalid = (
            self.request(session="chain"),
            self.request(
                session="chain",
                round_number=3,
                previous=first["review_attestation_sha256"],
                plan_version=2,
            ),
            self.request(
                session="chain",
                round_number=2,
                previous=first["review_attestation_sha256"],
                plan_version=1,
            ),
            self.request(
                session="chain",
                round_number=2,
                previous="f" * 64,
                plan_version=2,
            ),
        )
        for request in invalid:
            with self.subTest(request=request), mock.patch.object(
                FABLE, "_invoke_fable"
            ) as invoke:
                with self.assertRaises(FABLE.AdvisorError):
                    FABLE.review_plan(**request)
                invoke.assert_not_called()

        approved = self.call(self.request(session="terminal"), approved=True)
        with mock.patch.object(FABLE, "_invoke_fable") as invoke:
            with self.assertRaisesRegex(FABLE.AdvisorError, "terminal"):
                FABLE.review_plan(
                    **self.request(
                        session="terminal",
                        round_number=2,
                        previous=approved["review_attestation_sha256"],
                        plan_version=2,
                    )
                )
            invoke.assert_not_called()

    def test_bounded_store_refuses_overflow_without_evicting_active_session(self) -> None:
        with mock.patch.object(FABLE, "MAX_REVIEW_SESSIONS", 2):
            first = self.call(self.request(session="one"), approved=False)
            self.call(self.request(session="two"), approved=False)
            with mock.patch.object(FABLE, "_invoke_fable") as invoke:
                with self.assertRaisesRegex(FABLE.AdvisorError, "capacity"):
                    FABLE.review_plan(**self.request(session="three"))
                invoke.assert_not_called()
            self.assertIn("one", FABLE._REVIEW_SESSIONS)
            self.assertEqual(
                FABLE._REVIEW_SESSIONS["one"]["previous_review_sha256"],
                first["review_attestation_sha256"],
            )

    def test_sixth_call_is_rejected_before_model_execution(self) -> None:
        FABLE._REVIEW_SESSIONS["five-rounds"] = {
            "round_number": 5,
            "plan_version": 5,
            "previous_review_sha256": "a" * 64,
            "terminal": False,
            "scope_sha256": self.request()["original_scope_sha256"],
            "plan_sha256": self.request()["plan_sha256"],
            "plan_size": 40,
            "blocking_ids": ["F-1"],
            "all_finding_ids": ["F-1"],
            "non_converging_rounds": 0,
        }
        request = self.request(
            session="five-rounds",
            round_number=6,
            previous="a" * 64,
            plan_version=6,
        )
        with mock.patch.object(FABLE, "_invoke_fable") as invoke:
            with self.assertRaisesRegex(FABLE.AdvisorError, "five"):
                FABLE.review_plan(**request)
            invoke.assert_not_called()

    def test_only_a_a_uncertain_and_b_are_blocking_classes(self) -> None:
        for finding_class in ("A", "A-uncertain", "B"):
            provider = self.provider_result(approved=False)
            provider["blocking_findings"][0]["class"] = finding_class
            result = self.call_provider(
                self.request(session=f"class-{finding_class}"), provider
            )
            self.assertEqual(
                result["convergence"]["blocking_by_class"], {finding_class: 1}
            )

        provider = self.provider_result(approved=False)
        provider["blocking_findings"][0]["class"] = "C"
        with self.assertRaisesRegex(FABLE.AdvisorError, "blocking class"):
            self.call_provider(self.request(session="class-c"), provider)

    def test_c_only_and_scope_requests_are_non_blocking(self) -> None:
        provider = self.provider_result(approved=True)
        provider["c_backlog"] = [
            {
                "id": "C-1",
                "summary": "Optional readability refactor.",
                "basis_id": None,
            }
        ]
        provider["new_scope_requests"] = [
            {
                "id": "SCOPE-1",
                "summary": "Consider a separate deployment automation task.",
            }
        ]
        result = self.call_provider(self.request(session="nonblocking"), provider)
        self.assertEqual(result["decision"], "PLAN_APPROVED")
        self.assertEqual(result["c_backlog"][0]["id"], "C-1")
        self.assertEqual(result["new_scope_requests"][0]["id"], "SCOPE-1")

        provider["signal"] = "PLAN_REVISE"
        provider["scope_status"] = "open"
        with self.assertRaisesRegex(FABLE.AdvisorError, "requires.*blocking"):
            self.call_provider(self.request(session="c-revise"), provider)

    def test_approval_with_blockers_unknown_basis_and_duplicate_ids_fail(self) -> None:
        approved_with_blocker = self.provider_result(approved=False)
        approved_with_blocker["signal"] = "PLAN_APPROVED"
        approved_with_blocker["scope_status"] = "closed"
        malformed = [approved_with_blocker]

        unknown_basis = self.provider_result(approved=False)
        unknown_basis["blocking_findings"][0]["basis_id"] = "AC-404"
        malformed.append(unknown_basis)

        duplicate = self.provider_result(approved=False)
        duplicate["blocking_findings"].append(
            dict(duplicate["blocking_findings"][0])
        )
        malformed.append(duplicate)

        for index, provider in enumerate(malformed):
            with self.subTest(index=index), self.assertRaises(FABLE.AdvisorError):
                self.call_provider(self.request(session=f"malformed-{index}"), provider)

    def test_blocker_requires_evidence_failure_scenario_and_minimal_correction(self) -> None:
        for field, value in (
            ("evidence", []),
            ("failure_scenario", ""),
            ("smallest_correction", ""),
        ):
            provider = self.provider_result(approved=False)
            provider["blocking_findings"][0][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(
                FABLE.AdvisorError, field.replace("_", " ")
            ):
                self.call_provider(self.request(session=f"required-{field}"), provider)

    def test_supersession_and_late_finding_require_prior_id_or_new_cause(self) -> None:
        first = self.call(self.request(session="late"), approved=False)
        late_request = self.request(
            session="late",
            round_number=2,
            previous=first["review_attestation_sha256"],
            plan_version=2,
            plan="1. Add the malformed case.\n2. Verify it.",
        )

        unknown_supersession = self.provider_result(approved=False)
        unknown_supersession["blocking_findings"][0]["id"] = "F-2"
        unknown_supersession["blocking_findings"][0]["supersedes_ids"] = ["F-404"]
        unknown_supersession["blocking_findings"][0]["new_evidence"] = [
            "A new malformed trace proves the late blocker."
        ]
        unknown_supersession["blocking_findings"][0]["causal_source"] = "new_evidence"
        with self.assertRaisesRegex(FABLE.AdvisorError, "supersed"):
            self.call_provider(late_request, unknown_supersession)

        unsupported_late = self.provider_result(approved=False)
        unsupported_late["blocking_findings"][0]["id"] = "F-2"
        with self.assertRaisesRegex(FABLE.AdvisorError, "later-round"):
            self.call_provider(late_request, unsupported_late)

        evidenced_late = self.provider_result(approved=False)
        evidenced_late["blocking_findings"][0].update(
            {
                "id": "F-2",
                "causal_source": "new_evidence",
                "causal_reference": "trace-2",
                "new_evidence": ["A new malformed trace proves the late blocker."],
                "supersedes_ids": ["F-1"],
            }
        )
        result = self.call_provider(late_request, evidenced_late)
        self.assertEqual(result["blocking_findings"][0]["id"], "F-2")

    def test_reopened_finding_requires_new_evidence_or_changed_surface_cause(self) -> None:
        first = self.call(self.request(session="reopened"), approved=False)
        second_request = self.request(
            session="reopened",
            round_number=2,
            previous=first["review_attestation_sha256"],
            plan_version=2,
            plan="1. Close F-1.\n2. Address newly evidenced F-2.",
        )
        second_provider = self.provider_result(approved=False)
        second_provider["blocking_findings"][0].update(
            {
                "id": "F-2",
                "causal_source": "new_evidence",
                "causal_reference": "trace-F-2",
                "new_evidence": ["A new trace proves F-2."],
            }
        )
        second = self.call_provider(second_request, second_provider)
        reopened_request = self.request(
            session="reopened",
            round_number=3,
            previous=second["review_attestation_sha256"],
            plan_version=3,
            plan="1. Reopen F-1 without a new cause.",
        )
        with self.assertRaisesRegex(FABLE.AdvisorError, "later-round"):
            self.call_provider(
                reopened_request,
                self.provider_result(approved=False),
            )

    def finding(
        self,
        finding_id: str,
        *,
        finding_class: str = "B",
        late: bool = False,
    ) -> dict[str, object]:
        finding = dict(self.provider_result(approved=False)["blocking_findings"][0])
        finding["id"] = finding_id
        finding["class"] = finding_class
        if late:
            finding["causal_source"] = "new_evidence"
            finding["causal_reference"] = f"trace-{finding_id}"
            finding["new_evidence"] = [f"New evidence for {finding_id}."]
        return finding

    @staticmethod
    def revise_with(*findings: dict[str, object]) -> dict[str, object]:
        return {
            "signal": "PLAN_REVISE",
            "summary": "Approved closure still has blockers.",
            "scope_status": "open",
            "blocking_findings": list(findings),
            "c_backlog": [],
            "new_scope_requests": [],
        }

    def test_two_high_closure_rounds_with_new_blockers_halt_non_convergence(self) -> None:
        session = "treadmill"
        round_one = self.call_provider(
            self.request(session=session),
            self.revise_with(*(self.finding(f"F-{index}") for index in range(1, 6))),
        )
        round_two_request = self.request(
            session=session,
            round_number=2,
            previous=round_one["review_attestation_sha256"],
            plan_version=2,
            plan="1. Close F-1 through F-4.\n2. Keep F-5.\n3. Verify N-1.",
        )
        round_two = self.call_provider(
            round_two_request,
            self.revise_with(self.finding("F-5"), self.finding("N-1", late=True)),
        )
        self.assertEqual(round_two["convergence"]["closed_blocker_count"], 4)
        self.assertEqual(round_two["convergence"]["new_blocker_count"], 1)
        self.assertFalse(round_two["terminal"])

        round_three = self.call_provider(
            self.request(
                session=session,
                round_number=3,
                previous=round_two["review_attestation_sha256"],
                plan_version=3,
                plan="1. Close carried blockers.\n2. Verify newly evidenced N-2.",
            ),
            self.revise_with(self.finding("N-2", finding_class="A", late=True)),
        )
        self.assertTrue(round_three["terminal"])
        self.assertEqual(round_three["decision"], "PLAN_REVISE")
        self.assertEqual(round_three["stop_reason"], "NON_CONVERGING_REVIEW")
        self.assertEqual(
            round_three["convergence"]["consecutive_non_converging_rounds"], 2
        )

    def test_unauthorized_plan_growth_with_new_blocker_halts(self) -> None:
        first = self.call_provider(
            self.request(session="growth"),
            self.revise_with(self.finding("F-1")),
        )
        request = self.request(
            session="growth",
            round_number=2,
            previous=first["review_attestation_sha256"],
            plan_version=2,
            plan="Expanded plan line. " * 30,
        )
        request["changed_surface"] = []
        result = self.call_provider(
            request,
            self.revise_with(self.finding("F-1"), self.finding("F-2", late=True)),
        )
        self.assertGreater(result["convergence"]["plan_growth_ratio"], 0.25)
        self.assertTrue(result["terminal"])
        self.assertEqual(result["decision"], "PLAN_REVISE")
        self.assertEqual(result["stop_reason"], "UNAUTHORIZED_PLAN_GROWTH")

    @unittest.skipIf(os.name == "nt", "POSIX process-group assertion")
    def test_timeout_terminates_descendant_before_it_can_write_late_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            marker = Path(temporary) / "late-marker"
            parent = (
                "import subprocess,sys,time; "
                "subprocess.Popen([sys.executable,'-c',"
                "\"import pathlib,time; time.sleep(0.8); pathlib.Path(sys.argv[1]).write_text('late')\","
                "sys.argv[1]]); time.sleep(10)"
            )
            started = time.monotonic()
            with self.assertRaisesRegex(FABLE.AdvisorError, "timed out"):
                FABLE._run_claude_process(
                    [sys.executable, "-c", parent, str(marker)],
                    input_text="",
                    timeout_seconds=0.15,
                    timeout_message="Claude test timed out.",
                    start_error_message="Could not start Claude test.",
                )
            self.assertLess(time.monotonic() - started, 3.0)
            time.sleep(1.0)
            self.assertFalse(marker.exists())

    @unittest.skipIf(os.name == "nt", "POSIX process-group assertion")
    def test_timeout_kills_stubborn_descendant_after_direct_child_exits(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            marker = Path(temporary) / "stubborn-late-marker"
            child = (
                "import pathlib,signal,sys,time; "
                "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                "time.sleep(0.8); pathlib.Path(sys.argv[1]).write_text('late')"
            )
            parent = (
                "import subprocess,sys,time; "
                "subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2]],"
                "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,"
                "stderr=subprocess.DEVNULL); time.sleep(10)"
            )
            with self.assertRaisesRegex(FABLE.AdvisorError, "timed out"):
                FABLE._run_claude_process(
                    [sys.executable, "-c", parent, child, str(marker)],
                    input_text="",
                    timeout_seconds=0.2,
                    timeout_message="Claude test timed out.",
                    start_error_message="Could not start Claude test.",
                )
            time.sleep(1.0)
            self.assertFalse(marker.exists())

    @unittest.skipIf(os.name == "nt", "POSIX escaped-descendant assertion")
    def test_timeout_kills_stubborn_descendant_that_escapes_process_group(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            marker = Path(temporary) / "escaped-late-marker"
            child = (
                "import os,pathlib,signal,sys,time; os.setsid(); "
                "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                "time.sleep(0.8); pathlib.Path(sys.argv[1]).write_text('late')"
            )
            parent = (
                "import subprocess,sys,time; "
                "subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2]],"
                "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,"
                "stderr=subprocess.DEVNULL); time.sleep(10)"
            )
            with self.assertRaisesRegex(FABLE.AdvisorError, "timed out"):
                FABLE._run_claude_process(
                    [sys.executable, "-c", parent, child, str(marker)],
                    input_text="",
                    timeout_seconds=0.2,
                    timeout_message="Claude test timed out.",
                    start_error_message="Could not start Claude test.",
                )
            time.sleep(1.0)
            self.assertFalse(marker.exists())

    @unittest.skipIf(os.name == "nt", "POSIX fail-closed teardown assertion")
    def test_posix_enumeration_failure_still_kills_group_with_bounded_reap(self) -> None:
        process = mock.Mock()
        process.pid = 4242
        process.poll.return_value = None
        process.communicate.return_value = ("", "")
        with (
            mock.patch.object(
                FABLE,
                "_posix_descendant_pids",
                side_effect=FABLE.AdvisorError("enumeration failed"),
            ),
            mock.patch.object(FABLE.os, "killpg") as kill_group,
            mock.patch.object(FABLE, "_posix_group_exists", return_value=False),
            self.assertRaisesRegex(FABLE.AdvisorError, "enumerate|verify|tree"),
        ):
            FABLE._terminate_process_group(process)
        self.assertEqual(
            kill_group.call_args_list,
            [
                mock.call(4242, FABLE.signal.SIGTERM),
                mock.call(4242, FABLE.signal.SIGKILL),
            ],
        )
        self.assertTrue(process.communicate.call_args_list)
        self.assertTrue(
            all(
                "timeout" in call.kwargs
                for call in process.communicate.call_args_list
            )
        )

    def test_windows_teardown_does_not_trust_direct_child_exit(self) -> None:
        process = mock.Mock()
        process.pid = 4242
        process.poll.return_value = 0
        process.communicate.return_value = ("", "")
        graceful_tree = subprocess.CompletedProcess(["taskkill"], 1, "", "failed")
        forced_tree = subprocess.CompletedProcess(["taskkill"], 0, "", "")
        with (
            mock.patch.object(FABLE.os, "name", "nt"),
            mock.patch.object(
                FABLE.subprocess,
                "run",
                side_effect=[graceful_tree, forced_tree],
            ) as run,
        ):
            self.assertEqual(FABLE._terminate_process_group(process), "killed")
        self.assertEqual(
            [call.args[0] for call in run.call_args_list],
            [
                ["taskkill", "/PID", "4242", "/T"],
                ["taskkill", "/PID", "4242", "/T", "/F"],
            ],
        )
        self.assertTrue(
            all("timeout" in call.kwargs for call in run.call_args_list)
        )
        self.assertTrue(
            all("timeout" in call.kwargs for call in process.communicate.call_args_list)
        )

    def test_windows_timeout_escalation_uses_bounded_tree_kill(self) -> None:
        process = mock.Mock()
        process.pid = 4242
        process.poll.return_value = None
        process.communicate.side_effect = [
            subprocess.TimeoutExpired(["claude"], 1),
            ("", ""),
            ("", ""),
        ]
        tree_kill = subprocess.CompletedProcess(["taskkill"], 0, "", "")
        with (
            mock.patch.object(FABLE.os, "name", "nt"),
            mock.patch.object(FABLE.signal, "CTRL_BREAK_EVENT", 1, create=True),
            mock.patch.object(FABLE.subprocess, "run", return_value=tree_kill) as run,
        ):
            self.assertEqual(FABLE._terminate_process_group(process), "killed")
        command = run.call_args.args[0]
        self.assertEqual(command, ["taskkill", "/PID", "4242", "/T", "/F"])
        self.assertLessEqual(run.call_args.kwargs["timeout"], 10)
        process.kill.assert_not_called()

    def test_windows_tree_kill_failure_is_fail_closed_and_reaps_child(self) -> None:
        process = mock.Mock()
        process.pid = 4242
        process.poll.return_value = None
        process.communicate.side_effect = [
            subprocess.TimeoutExpired(["claude"], 1),
            ("", ""),
            ("", ""),
        ]
        tree_kill = subprocess.CompletedProcess(["taskkill"], 1, "", "failed")
        with (
            mock.patch.object(FABLE.os, "name", "nt"),
            mock.patch.object(FABLE.signal, "CTRL_BREAK_EVENT", 1, create=True),
            mock.patch.object(FABLE.subprocess, "run", return_value=tree_kill),
            self.assertRaisesRegex(FABLE.AdvisorError, "process tree"),
        ):
            FABLE._terminate_process_group(process)
        process.kill.assert_called_once_with()

    def test_windows_worst_path_uses_one_cumulative_teardown_deadline(self) -> None:
        clock = {"now": 100.0}
        requested: list[tuple[float, float]] = []

        def monotonic() -> float:
            return clock["now"]

        def taskkill(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            timeout = float(kwargs["timeout"])
            requested.append((clock["now"], timeout))
            clock["now"] += timeout
            return subprocess.CompletedProcess(command, 1, "", "failed")

        process = mock.Mock()
        process.pid = 4242
        process.poll.return_value = None

        def communicate(*, timeout: float) -> tuple[str, str]:
            requested.append((clock["now"], timeout))
            clock["now"] += timeout
            raise subprocess.TimeoutExpired(["claude"], timeout)

        process.communicate.side_effect = communicate
        started = clock["now"]
        with (
            mock.patch.object(FABLE.os, "name", "nt"),
            mock.patch.object(FABLE.time, "monotonic", side_effect=monotonic),
            mock.patch.object(FABLE.subprocess, "run", side_effect=taskkill),
            self.assertRaises(FABLE.AdvisorError),
        ):
            FABLE._terminate_process_group(process)
        self.assertLessEqual(
            clock["now"] - started,
            FABLE.PROCESS_TEARDOWN_RESERVE_SECONDS,
        )
        for requested_at, timeout in requested:
            self.assertLessEqual(
                timeout,
                started
                + FABLE.PROCESS_TEARDOWN_RESERVE_SECONDS
                - requested_at,
            )

    @unittest.skipIf(os.name == "nt", "POSIX deadline assertion")
    def test_posix_worst_path_uses_one_cumulative_teardown_deadline(self) -> None:
        clock = {"now": 100.0}
        requested: list[tuple[float, float]] = []

        def monotonic() -> float:
            return clock["now"]

        def enumerate_processes(
            command: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            timeout = float(kwargs["timeout"])
            requested.append((clock["now"], timeout))
            clock["now"] += timeout
            return subprocess.CompletedProcess(command, 0, "", "")

        process = mock.Mock()
        process.pid = 4242
        process.poll.return_value = None

        def communicate(*, timeout: float) -> tuple[str, str]:
            requested.append((clock["now"], timeout))
            clock["now"] += timeout
            raise subprocess.TimeoutExpired(["claude"], timeout)

        process.communicate.side_effect = communicate
        started = clock["now"]
        with (
            mock.patch.object(FABLE.time, "monotonic", side_effect=monotonic),
            mock.patch.object(
                FABLE.subprocess, "run", side_effect=enumerate_processes
            ),
            mock.patch.object(FABLE.os, "killpg"),
            self.assertRaises(FABLE.AdvisorError),
        ):
            FABLE._terminate_process_group(process)
        self.assertLessEqual(
            clock["now"] - started,
            FABLE.PROCESS_TEARDOWN_RESERVE_SECONDS,
        )
        for requested_at, timeout in requested:
            self.assertLessEqual(
                timeout,
                started
                + FABLE.PROCESS_TEARDOWN_RESERVE_SECONDS
                - requested_at,
            )

    def test_windows_exact_deadline_exhaustion_cannot_report_success(self) -> None:
        clock = {"now": 100.0}

        def monotonic() -> float:
            return clock["now"]

        def taskkill(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            del kwargs
            clock["now"] += 1
            return subprocess.CompletedProcess(
                command,
                0 if "/F" in command else 1,
                "",
                "",
            )

        process = mock.Mock()
        process.pid = 4242
        communicate_count = 0

        def communicate(*, timeout: float) -> tuple[str, str]:
            nonlocal communicate_count
            communicate_count += 1
            clock["now"] += timeout
            if communicate_count == 1:
                raise subprocess.TimeoutExpired(["claude"], timeout)
            return "", ""

        process.communicate.side_effect = communicate
        with (
            mock.patch.object(FABLE.os, "name", "nt"),
            mock.patch.object(FABLE.time, "monotonic", side_effect=monotonic),
            mock.patch.object(FABLE.subprocess, "run", side_effect=taskkill),
            self.assertRaisesRegex(FABLE.AdvisorError, "deadline|exhausted"),
        ):
            FABLE._terminate_process_group(process)

    @unittest.skipIf(os.name == "nt", "POSIX deadline assertion")
    def test_posix_exact_deadline_exhaustion_cannot_report_success(self) -> None:
        clock = {"now": 100.0}

        def monotonic() -> float:
            return clock["now"]

        def enumerate_processes(
            command: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            del kwargs
            clock["now"] += 1
            return subprocess.CompletedProcess(command, 0, "", "")

        process = mock.Mock()
        process.pid = 4242
        process.poll.return_value = None
        communicate_count = 0

        def communicate(*, timeout: float) -> tuple[str, str]:
            nonlocal communicate_count
            communicate_count += 1
            clock["now"] += timeout
            if communicate_count == 1:
                raise subprocess.TimeoutExpired(["claude"], timeout)
            return "", ""

        process.communicate.side_effect = communicate
        with (
            mock.patch.object(FABLE.time, "monotonic", side_effect=monotonic),
            mock.patch.object(
                FABLE.subprocess, "run", side_effect=enumerate_processes
            ),
            mock.patch.object(FABLE.os, "killpg"),
            mock.patch.object(FABLE, "_posix_group_exists", return_value=False),
            self.assertRaisesRegex(FABLE.AdvisorError, "deadline|exhausted"),
        ):
            FABLE._terminate_process_group(process)

    @unittest.skipIf(os.name == "nt", "POSIX deadline assertion")
    def test_slow_process_table_parsing_stops_at_cumulative_deadline(self) -> None:
        clock = {"now": 100.0}

        def monotonic() -> float:
            return clock["now"]

        class SlowProcessTable:
            def __iter__(self) -> object:
                for process_id in range(2, 1_002):
                    clock["now"] += 0.05
                    yield f"{process_id} {process_id - 1}"

        process = mock.Mock()
        process.pid = 1
        process.poll.return_value = None
        process.communicate.return_value = ("", "")
        process_table = subprocess.CompletedProcess(["ps"], 0, "", "")
        started = clock["now"]
        with (
            mock.patch.object(FABLE.time, "monotonic", side_effect=monotonic),
            mock.patch.object(FABLE.subprocess, "run", return_value=process_table),
            mock.patch.object(FABLE.io, "StringIO", return_value=SlowProcessTable()),
            mock.patch.object(FABLE.os, "killpg"),
            mock.patch.object(FABLE.os, "kill"),
            self.assertRaisesRegex(FABLE.AdvisorError, "deadline|exhausted"),
        ):
            FABLE._terminate_process_group(process)
        self.assertLessEqual(
            clock["now"] - started,
            FABLE.PROCESS_TEARDOWN_RESERVE_SECONDS,
        )

    @unittest.skipIf(os.name == "nt", "POSIX process-table assertion")
    def test_process_table_has_deterministic_node_capacity(self) -> None:
        rows = "\n".join(
            f"{process_id} 1"
            for process_id in range(2, FABLE.MAX_MCP_JSON_NODES + 3)
        )
        result = subprocess.CompletedProcess(["ps"], 0, rows, "")
        with (
            mock.patch.object(FABLE.subprocess, "run", return_value=result),
            self.assertRaisesRegex(FABLE.AdvisorError, "capacity|node|process tree"),
        ):
            FABLE._posix_descendant_pids(
                1,
                deadline=time.monotonic()
                + FABLE.PROCESS_TEARDOWN_RESERVE_SECONDS,
            )

    @unittest.skipIf(os.name == "nt", "POSIX signaling assertion")
    def test_slow_descendant_signaling_stops_at_cumulative_deadline(self) -> None:
        clock = {"now": 100.0}

        def monotonic() -> float:
            return clock["now"]

        def slow_signal(process_id: int, signal_number: int) -> None:
            del process_id, signal_number
            clock["now"] += 0.05

        process = mock.Mock()
        process.pid = 1
        process.poll.return_value = None
        process.communicate.return_value = ("", "")
        descendants = set(range(2, 1_002))
        started = clock["now"]
        with (
            mock.patch.object(FABLE.time, "monotonic", side_effect=monotonic),
            mock.patch.object(
                FABLE, "_posix_descendant_pids", return_value=descendants
            ),
            mock.patch.object(FABLE.os, "killpg"),
            mock.patch.object(FABLE.os, "kill", side_effect=slow_signal),
            self.assertRaisesRegex(FABLE.AdvisorError, "deadline|exhausted"),
        ):
            FABLE._terminate_process_group(process)
        self.assertLessEqual(
            clock["now"] - started,
            FABLE.PROCESS_TEARDOWN_RESERVE_SECONDS,
        )

    @unittest.skipIf(os.name == "nt", "POSIX verification assertion")
    def test_slow_descendant_probes_stop_at_cumulative_deadline(self) -> None:
        clock = {"now": 100.0}

        def monotonic() -> float:
            return clock["now"]

        def slow_probe(process_id: int) -> bool:
            del process_id
            clock["now"] += 0.05
            return False

        process = mock.Mock()
        process.pid = 1
        process.poll.return_value = None
        process.communicate.return_value = ("", "")
        descendants = set(range(2, 1_002))
        started = clock["now"]
        with (
            mock.patch.object(FABLE.time, "monotonic", side_effect=monotonic),
            mock.patch.object(
                FABLE, "_posix_descendant_pids", return_value=descendants
            ),
            mock.patch.object(FABLE, "_signal_posix_processes"),
            mock.patch.object(FABLE.os, "killpg"),
            mock.patch.object(FABLE, "_posix_group_exists", return_value=False),
            mock.patch.object(
                FABLE, "_posix_process_exists", side_effect=slow_probe
            ),
            self.assertRaisesRegex(FABLE.AdvisorError, "deadline|exhausted"),
        ):
            FABLE._terminate_process_group(process)
        self.assertLessEqual(
            clock["now"] - started,
            FABLE.PROCESS_TEARDOWN_RESERVE_SECONDS,
        )

    def test_mcp_timeout_exceeds_child_timeout_plus_teardown_reserve(self) -> None:
        mcp = json.loads(
            (
                REPO_ROOT / "plugins" / "codex-orchestration" / ".mcp.json"
            ).read_text(encoding="utf-8")
        )
        deadline_seconds = getattr(
            FABLE, "PROCESS_TEARDOWN_DEADLINE_SECONDS", None
        )
        self.assertIsNotNone(deadline_seconds)
        self.assertLess(
            deadline_seconds,
            FABLE.PROCESS_TEARDOWN_RESERVE_SECONDS,
        )
        for server in mcp["mcpServers"].values():
            available_teardown_seconds = (
                server["tool_timeout_sec"] - FABLE.CLAUDE_TIMEOUT_SECONDS
            )
            self.assertGreater(
                available_teardown_seconds,
                FABLE.PROCESS_TEARDOWN_RESERVE_SECONDS,
            )

    def test_carried_blocker_is_not_scope_creep_and_new_evidence_a_stays_blocking(self) -> None:
        first = self.call_provider(
            self.request(session="carried"),
            self.revise_with(self.finding("F-1")),
        )
        carried_request = self.request(
            session="carried",
            round_number=2,
            previous=first["review_attestation_sha256"],
            plan_version=2,
            plan="Expanded plan line. " * 30,
        )
        carried_request["changed_surface"] = []
        carried = self.call_provider(
            carried_request,
            self.revise_with(self.finding("F-1")),
        )
        self.assertEqual(carried["convergence"]["new_blocker_count"], 0)
        self.assertEqual(carried["convergence"]["carried_blocker_count"], 1)
        self.assertFalse(carried["terminal"])

        evidence_first = self.call_provider(
            self.request(session="evidenced-a"),
            self.revise_with(self.finding("F-1")),
        )
        evidenced = self.call_provider(
            self.request(
                session="evidenced-a",
                round_number=2,
                previous=evidence_first["review_attestation_sha256"],
                plan_version=2,
                plan="1. Evaluate the new trace.\n2. Close the approved task.",
            ),
            self.revise_with(self.finding("A-2", finding_class="A", late=True)),
        )
        self.assertEqual(evidenced["decision"], "PLAN_REVISE")
        self.assertEqual(evidenced["blocking_findings"][0]["class"], "A")
        self.assertFalse(evidenced["terminal"])


class McpTransportBoundsTests(unittest.TestCase):
    def test_bounded_reader_rejects_oversize_depth_and_resource_failure(self) -> None:
        oversized = io.BytesIO(b"{" + b" " * FABLE.MAX_MCP_REQUEST_BYTES + b"}\n")
        deeply_nested = io.BytesIO(
            b'{"id":1,"method":"ping","params":'
            + b"[" * 80
            + b"]" * 80
            + b"}\n"
        )
        malformed_utf8 = io.BytesIO(b"\xff\n")
        failing = mock.Mock()
        failing.readline.side_effect = MemoryError
        for stream in (oversized, deeply_nested, malformed_utf8, failing):
            with self.subTest(stream=stream), self.assertRaisesRegex(
                FABLE.McpProtocolError, "bounded JSON-RPC request"
            ):
                FABLE._read_mcp_request(stream)

    def test_bounded_reader_accepts_one_small_object_and_eof(self) -> None:
        stream = io.BytesIO(b'{"jsonrpc":"2.0","id":1,"method":"ping"}\n')
        self.assertEqual(FABLE._read_mcp_request(stream)["method"], "ping")
        self.assertIsNone(FABLE._read_mcp_request(stream))


if __name__ == "__main__":
    unittest.main()
