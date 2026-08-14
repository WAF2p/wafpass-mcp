"""Translate backend responses into user-friendly MCP text.

This is intentionally a thin, rule-based layer. It does not change what the
backend returns; it only rewrites the most common error shapes and auto-fix
skipped reasons so the LLM (and the user) can act on them without reading raw
HTTP bodies.
"""

from __future__ import annotations

from typing import Any

# Reason templates emitted by wafpass-core classify_findings / build_fix_plan.
_SKIPPED_REASONS: dict[str, str] = {
    "cannot auto-generate a meaningful non-empty value": (
        "The control has no registered default for this attribute, so the "
        "auto-fixer does not know what value to set. Add a default template "
        "or remediation example to the control, or fix it manually."
    ),
    "no unambiguous default value can be derived": (
        "The assertion needs a block or value that the auto-fixer cannot "
        "construct automatically. This usually happens for provider-level "
        "settings with no registered template."
    ),
    "nested attribute depth > 2 is not supported by the auto-fixer": (
        "The failing attribute is more than two levels deep (e.g. "
        "'a.b.c'). The current auto-fixer only patches top-level or "
        "one-level-nested attributes."
    ),
    "nested attribute depth > 3 is not supported by the auto-fixer": (
        "The failing attribute is more than three levels deep. The auto-fixer "
        "does not support this depth."
    ),
    "operator 'attribute_exists' is not handled by the auto-fixer": (
        "Auto-fix for this assertion type is not implemented. Remediate the "
        "resource manually."
    ),
    "operator 'references_cloudtrail_bucket' is not handled by the auto-fixer": (
        "Auto-fix cannot create a cross-resource reference to a CloudTrail "
        "bucket. Add the aws_s3_bucket_logging resource manually."
    ),
    "operator 'has_associated_resource' is not handled by the auto-fixer": (
        "Auto-fix cannot add a missing associated resource automatically. "
        "Create the associated resource manually."
    ),
}


def explain_backend_error(status_code: int, detail: Any) -> str:
    """Turn a backend HTTP failure into a short, actionable message."""
    if status_code == 401:
        return (
            "The WAFpass API rejected the access token (401). "
            "Log in again through the WAFpass dashboard to refresh your token."
        )
    if status_code == 403:
        return (
            "Your account is not authorized for this operation (403). "
            "Ask an admin to grant the required role, or use a tool that "
            "matches your current role."
        )
    if status_code == 404:
        return (
            "The requested resource was not found on the WAFpass server (404). "
            "Check the ID and try again."
        )
    if status_code == 422:
        return (
            f"The request body failed validation (422): {detail}. "
            "Compare your arguments with the tool's input schema."
        )
    if status_code == 503:
        return (
            f"A required WAFpass service is unavailable (503). "
            f"Server said: {detail}"
        )
    if status_code >= 500:
        return (
            f"The WAFpass server encountered an error ({status_code}). "
            "Please try again later."
        )
    return f"WAFpass API returned {status_code}: {detail}"


def explain_skipped_finding(skipped: dict[str, Any]) -> str:
    """Return a human-readable explanation for one skipped auto-fix entry."""
    check_id = skipped.get("check_id", "unknown check")
    control_id = skipped.get("control_id", "unknown control")
    address = skipped.get("address", "unknown resource")
    attribute = skipped.get("attribute", "")
    op = skipped.get("op", "")
    reason = skipped.get("reason", "")

    template = _SKIPPED_REASONS.get(reason)
    if template is None:
        # Fallback that still gives more context than the raw dict.
        template = (
            f"Auto-fix skipped this finding (check '{check_id}', control "
            f"'{control_id}') because assertion '{op}' on '{attribute}' could "
            f"not be handled: {reason}"
        )
        return template

    return (
        f"Skipped '{check_id}' on {address}"
        + (f" ({attribute})" if attribute else "")
        + f": {template}"
    )


def explain_classify_response(response: dict[str, Any]) -> dict[str, Any]:
    """Annotate an auto-fix/classify response with a top-level explanation."""
    explanation_parts: list[str] = []

    patches_count = response.get("patches_count", 0)
    skipped_count = response.get("skipped_count", 0)
    warnings = response.get("warnings") or []
    skipped = response.get("skipped") or []

    if patches_count == 0 and skipped_count == 0:
        explanation_parts.append(
            "No findings could be classified. Either the supplied findings are "
            "empty, their control/check IDs do not match any loaded controls, "
            "or they already pass."
        )
    elif patches_count == 0 and skipped_count > 0:
        explanation_parts.append(
            f"{skipped_count} finding(s) were reviewed but none are auto-fixable. "
            "See 'skipped_explanations' for why each one was skipped."
        )
    else:
        explanation_parts.append(
            f"{patches_count} patch(es) can be applied automatically; "
            f"{skipped_count} finding(s) must be fixed manually."
        )

    for warning in warnings:
        if "source snapshot" in warning.lower() or "snapshot" in warning.lower():
            explanation_parts.append(
                "Unified diffs are not available because the run has no source "
                "snapshot (common for CI/CD-triggered runs)."
            )
        elif "no run_id was provided" in warning.lower():
            explanation_parts.append(
                "No run_id was supplied, so only a patch list was returned; "
                "file-level diffs were not generated."
            )

    response["explanation"] = " ".join(explanation_parts)
    response["skipped_explanations"] = [explain_skipped_finding(s) for s in skipped]
    return response


def explain_response(tool_name: str, response: dict[str, Any]) -> dict[str, Any]:
    """Add a top-level explanation field to a backend response when helpful."""
    if isinstance(response, dict) and response.get("error"):
        response["explanation"] = explain_backend_error(
            response.get("status_code", 0), response.get("detail", "")
        )
        return response

    if "classify" in tool_name and isinstance(response, dict):
        return explain_classify_response(response)

    return response
