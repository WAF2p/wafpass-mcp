"""Tests for the backend-response explain layer."""

from __future__ import annotations

from wafpass_mcp.explain import (
    explain_backend_error,
    explain_classify_response,
    explain_response,
    explain_skipped_finding,
)


def test_explain_backend_error_known_codes() -> None:
    assert "rejected the access token" in explain_backend_error(401, "nope")
    assert "not authorized" in explain_backend_error(403, "nope")
    assert "not found" in explain_backend_error(404, "nope")
    assert "failed validation" in explain_backend_error(422, "bad body")
    assert "unavailable" in explain_backend_error(503, "down")
    assert "server encountered an error" in explain_backend_error(500, "boom")


def test_explain_skipped_finding_known_reason() -> None:
    explained = explain_skipped_finding(
        {
            "check_id": "chk-1",
            "control_id": "WAF-X",
            "address": "aws_foo.bar",
            "attribute": "tags",
            "op": "not_empty",
            "reason": "cannot auto-generate a meaningful non-empty value",
        }
    )
    assert "chk-1" in explained
    assert "aws_foo.bar" in explained
    assert "no registered default" in explained


def test_explain_skipped_finding_unknown_reason() -> None:
    explained = explain_skipped_finding(
        {
            "check_id": "chk-2",
            "control_id": "WAF-Y",
            "address": "aws_baz.qux",
            "attribute": "encryption",
            "op": "custom",
            "reason": "something weird",
        }
    )
    assert "chk-2" in explained
    assert "something weird" in explained


def test_explain_classify_response_all_skipped() -> None:
    response = {
        "patches_count": 0,
        "skipped_count": 2,
        "patches": [],
        "skipped": [
            {
                "check_id": "c1",
                "control_id": "WAF-X",
                "reason": "cannot auto-generate a meaningful non-empty value",
            },
            {
                "check_id": "c2",
                "control_id": "WAF-Y",
                "reason": "no unambiguous default value can be derived",
            },
        ],
        "warnings": [],
    }
    result = explain_classify_response(response)
    assert "none are auto-fixable" in result["explanation"]
    assert len(result["skipped_explanations"]) == 2


def test_explain_classify_response_no_matches() -> None:
    response = {
        "patches_count": 0,
        "skipped_count": 0,
        "patches": [],
        "skipped": [],
        "warnings": [],
    }
    result = explain_classify_response(response)
    assert "No findings could be classified" in result["explanation"]


def test_explain_classify_response_with_patches() -> None:
    response = {
        "patches_count": 3,
        "skipped_count": 1,
        "patches": [{}, {}, {}],
        "skipped": [{"check_id": "c1", "reason": "foo"}],
        "warnings": [],
    }
    result = explain_classify_response(response)
    assert "3 patch(es) can be applied" in result["explanation"]


def test_explain_classify_response_no_snapshot() -> None:
    response = {
        "patches_count": 0,
        "skipped_count": 0,
        "warnings": [
            "This run was pushed from CI/CD pipeline, so source snapshot unavailable."
        ],
    }
    result = explain_classify_response(response)
    assert "Unified diffs are not available" in result["explanation"]


def test_explain_response_error_flag() -> None:
    response = {"error": True, "status_code": 422, "detail": "bad"}
    result = explain_response("any_tool", response)
    assert "failed validation" in result["explanation"]


def test_explain_response_classify() -> None:
    response = {
        "patches_count": 0,
        "skipped_count": 1,
        "skipped": [{"check_id": "c1", "reason": "foo"}],
        "warnings": [],
    }
    result = explain_response(
        "api_auto_fix_classify_api_v1_auto_fix_classify_post", response
    )
    assert "explanation" in result


def test_explain_response_passthrough() -> None:
    response = {"foo": "bar"}
    result = explain_response("list_runs_api_v1_runs_get", response)
    assert result == {"foo": "bar"}
