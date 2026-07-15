from __future__ import annotations

import argparse
import html
import io
import json
import platform
import re
import sys
import time
import unittest
import warnings
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "evals" / "instrumentation_tasks.json"
REPORT_DATE = "2026-07-15"
CASE_KEYS = frozenset({"name", "expected", "test_id"})
SHA_PATTERN = re.compile(r"[0-9a-f]{40}")

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def load_cases(path: Path = MANIFEST) -> list[dict[str, str]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list) or len(raw) != 10:
        raise ValueError("instrumentation manifest must contain exactly ten records")

    cases: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict) or set(item) != CASE_KEYS:
            raise ValueError("instrumentation records must use the exact manifest keys")
        if any(not isinstance(item[key], str) or not item[key].strip() for key in CASE_KEYS):
            raise ValueError("instrumentation manifest values must be nonempty strings")
        cases.append({key: item[key] for key in ("name", "expected", "test_id")})

    if len({case["name"] for case in cases}) != 10:
        raise ValueError("instrumentation case names must be unique")
    if len({case["test_id"] for case in cases}) != 10:
        raise ValueError("instrumentation test IDs must be unique")
    return cases


def load_measured_json(path: Path) -> object:
    payload = path.read_bytes()
    encoding = "utf-16" if payload.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
    return json.loads(payload.decode(encoding))


def _single_test(test_id: str) -> unittest.TestSuite:
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromName(test_id)
    if loader.errors or suite.countTestCases() != 1:
        raise ValueError("instrumentation test ID must resolve to exactly one test")
    return suite


def _failure_type(result: unittest.TestResult) -> str | None:
    if result.testsRun != 1:
        return "TestCountFailure"
    if result.skipped:
        return "SkippedTest"
    if result.failures:
        return "AssertionFailure"
    if result.errors:
        return "TestError"
    if result.expectedFailures:
        return "ExpectedFailure"
    if result.unexpectedSuccesses:
        return "UnexpectedSuccess"
    if not result.wasSuccessful():
        return "TestFailure"
    return None


def run() -> dict[str, object]:
    results: list[dict[str, object]] = []
    for case in load_cases():
        stream = io.StringIO()
        started = time.perf_counter_ns()
        try:
            suite = _single_test(case["test_id"])
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", DeprecationWarning)
                test_result = unittest.TextTestRunner(
                    stream=stream,
                    verbosity=0,
                    buffer=True,
                ).run(suite)
            failure_type = _failure_type(test_result)
        except BaseException as exc:
            failure_type = "LoadFailure"
            stream.write(f"{type(exc).__name__}: {exc}")

        elapsed_ms = round((time.perf_counter_ns() - started) / 1_000_000, 3)
        passed = failure_type is None
        detail = (
            f"expected_contract={case['expected']}; test_result=pass; "
            f"test={case['test_id']}; "
            f"elapsed_ms={elapsed_ms:.3f}"
            if passed
            else f"test_result=fail; failure_type={failure_type}; elapsed_ms={elapsed_ms:.3f}"
        )
        record: dict[str, object] = {
            "name": case["name"],
            "expected": case["expected"],
            "test_id": case["test_id"],
            "passed": passed,
            "elapsed_ms": elapsed_ms,
            "detail": detail,
            "failure_type": failure_type,
        }
        debug = stream.getvalue().strip()
        if not passed and debug:
            record["debug"] = debug
        results.append(record)

    failures = [str(record["name"]) for record in results if not record["passed"]]
    return {
        "total": len(results),
        "passed": len(results) - len(failures),
        "failures": failures,
        "results": results,
    }


def _safe_cell(value: object) -> str:
    text = str(value).replace("\r", " ").replace("\n", " ")
    return html.escape(text, quote=True).replace("|", "&#124;").replace("`", "&#96;")


def _validated_measured_results(measured: object) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not isinstance(measured, dict):
        raise ValueError("measured result must be an object")
    records = measured.get("results")
    if not isinstance(records, list) or len(records) != 10:
        raise ValueError("measured result must contain exactly ten case results")

    cases = load_cases()
    failures: list[str] = []
    passed_count = 0
    validated: list[dict[str, Any]] = []
    for case, record in zip(cases, records, strict=True):
        if not isinstance(record, dict):
            raise ValueError("measured case result must be an object")
        for key in ("name", "expected", "test_id", "passed", "elapsed_ms", "detail"):
            if key not in record:
                raise ValueError("measured case result is incomplete")
        if any(record[key] != case[key] for key in ("name", "expected", "test_id")):
            raise ValueError("measured case order or identity differs from the manifest")
        if type(record["passed"]) is not bool:
            raise ValueError("measured pass state must be boolean")
        if type(record["elapsed_ms"]) not in {int, float} or record["elapsed_ms"] < 0:
            raise ValueError("measured elapsed time must be nonnegative")

        if record["passed"]:
            expected_detail = (
                f"expected_contract={case['expected']}; test_result=pass; "
                f"test={case['test_id']}; "
                f"elapsed_ms={record['elapsed_ms']:.3f}"
            )
            if record["detail"] != expected_detail or record.get("failure_type") is not None:
                raise ValueError("successful measured detail is not canonical")
            passed_count += 1
        else:
            failure_type = record.get("failure_type")
            if failure_type not in {
                "TestCountFailure",
                "SkippedTest",
                "AssertionFailure",
                "TestError",
                "ExpectedFailure",
                "UnexpectedSuccess",
                "TestFailure",
                "LoadFailure",
            }:
                raise ValueError("measured failure type is not safe")
            failures.append(case["name"])
        validated.append(record)

    if measured.get("total") != 10 or measured.get("passed") != passed_count:
        raise ValueError("measured totals do not match case results")
    if measured.get("failures") != failures:
        raise ValueError("measured failures do not match case results")
    return measured, validated


def render_markdown(measured: object, *, commit_sha: str) -> str:
    if not SHA_PATTERN.fullmatch(commit_sha):
        raise ValueError("commit SHA must be a full lowercase Git object ID")
    summary, records = _validated_measured_results(measured)

    lines = [
        "# v0.3 Instrumentation Pilot — 2026-07-15",
        "",
        "## Scope",
        "",
        "This deterministic pilot selects ten checked-in unit and integration tests for fingerprinting, policy retries, multi-process budget contention, bounded dispatcher failure, recovery, and SQLite reopen persistence. It makes no network, browser, external API, or model call.",
        "",
        "## Environment",
        "",
        f"- Base commit (working-tree evaluation): `{commit_sha}`",
        f"- Operating system: `{platform.system()}`",
        f"- Python: `{platform.python_version()}`",
        f"- Run date: `{REPORT_DATE}`",
        "- Selection: exactly ten unique manifest records, each resolving to one non-skipped unittest",
        "",
        "## Results",
        "",
        f"- Total: **{summary['total']}**",
        f"- Passed: **{summary['passed']}**",
        f"- Failed: **{len(summary['failures'])}**",
        "",
        "| # | Case | Expected | State | Test ID | Measured safe detail |",
        "| ---: | --- | --- | --- | --- | --- |",
    ]
    for index, record in enumerate(records, start=1):
        state = "PASS" if record["passed"] else "FAIL"
        if record["passed"]:
            detail = record["detail"]
        else:
            detail = (
                f"failure_type={record['failure_type']}; "
                f"elapsed_ms={record['elapsed_ms']:.3f}"
            )
        lines.append(
            "| "
            + " | ".join(
                (
                    str(index),
                    _safe_cell(record["name"]),
                    _safe_cell(record["expected"]),
                    state,
                    _safe_cell(record["test_id"]),
                    _safe_cell(detail),
                )
            )
            + " |"
        )

    lines.extend(("", "## Failures", ""))
    if not summary["failures"]:
        lines.append("None. The measured runner returned `failures: []`.")
    else:
        for record in records:
            if not record["passed"]:
                lines.append(
                    f"- {_safe_cell(record['name'])}: {_safe_cell(record['failure_type'])}"
                )

    lines.extend(
        (
            "",
            "## Interpretation",
            "",
            "This pilot tests instrumentation and deterministic governance accuracy; it does not measure model response quality.",
            "",
            "The result is directional evidence for these ten selected contracts only. It is not a production benchmark, proof of firewall enforcement, semantic or near-duplicate detection, policy replay, cost savings, latency improvement, or model-answer quality. Agent Call Governor v0.3's bundled Codex hook remains observe/warn-only even though the host supports denial for a supported `PreToolUse` call.",
            "",
        )
    )
    return "\n".join(lines)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run or render the v0.3 instrumentation pilot")
    parser.add_argument("--render-json", type=Path)
    parser.add_argument("--markdown-output", type=Path)
    parser.add_argument("--commit")
    return parser


def main() -> int:
    args = _parser().parse_args()
    rendering = args.render_json is not None or args.markdown_output is not None or args.commit is not None
    if rendering:
        if args.render_json is None or args.markdown_output is None or args.commit is None:
            raise SystemExit("--render-json, --markdown-output, and --commit are required together")
        measured = load_measured_json(args.render_json)
        markdown = render_markdown(measured, commit_sha=args.commit)
        args.markdown_output.write_text(markdown, encoding="utf-8", newline="\n")
        return 1 if measured.get("failures") else 0

    result = run()
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if result["failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
