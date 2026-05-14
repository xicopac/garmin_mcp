"""
Garmin strength workout planning tools.

These tools use Garmin Connect's generic workout-service endpoints with
strength-specific validation and curation. They intentionally do not use the
running/cycling typed workout helpers.
"""

import copy
import datetime as dt
import json
import logging
from typing import Any

try:
    from garmin_mcp import strength_exercise_catalog
except ModuleNotFoundError:  # pragma: no cover - direct module test import
    import strength_exercise_catalog  # type: ignore


garmin_client = None
logger = logging.getLogger("garmin_mcp_strength_workouts")

MAX_ERROR_RESPONSE_BYTES = 8000
ERROR_RESPONSE_TARGET_BYTES = 7500
MAX_MAPPING_FAILURES = 20
VERIFICATION_MODES = {"strict", "compatible", "lenient"}
GARMIN_EXERCISE_REWRITES = {
    "BARBELL_SHOULDER_PRESS": "OVERHEAD_BARBELL_PRESS",
}

STRENGTH_SPORT = {"sportTypeId": 5, "sportTypeKey": "strength_training", "displayOrder": 5}
NO_TARGET = {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target", "displayOrder": 1}
KG_UNIT = {"unitId": 8, "unitKey": "kilogram", "factor": 1000.0}
LB_UNIT = {"unitId": 9, "unitKey": "pound", "factor": 453.592}
LAP_BUTTON = {"conditionTypeId": 1, "conditionTypeKey": "lap.button", "displayOrder": 1, "displayable": True}
TIME_CONDITION = {"conditionTypeId": 2, "conditionTypeKey": "time", "displayOrder": 2, "displayable": True}
ITERATIONS_CONDITION = {"conditionTypeId": 7, "conditionTypeKey": "iterations", "displayOrder": 7, "displayable": False}
REPS_CONDITION = {"conditionTypeId": 10, "conditionTypeKey": "reps", "displayOrder": 10, "displayable": True}
STEP_TYPES = {
    "warmup": {"stepTypeId": 1, "stepTypeKey": "warmup", "displayOrder": 1},
    "cooldown": {"stepTypeId": 2, "stepTypeKey": "cooldown", "displayOrder": 2},
    "interval": {"stepTypeId": 3, "stepTypeKey": "interval", "displayOrder": 3},
    "recovery": {"stepTypeId": 4, "stepTypeKey": "recovery", "displayOrder": 4},
    "rest": {"stepTypeId": 5, "stepTypeKey": "rest", "displayOrder": 5},
    "repeat": {"stepTypeId": 6, "stepTypeKey": "repeat", "displayOrder": 6},
}


def configure(client: Any) -> None:
    global garmin_client
    garmin_client = client


def _redact_text(value: Any) -> str:
    text = str(value)
    redacted_markers = [
        "password",
        "passwd",
        "secret",
        "token",
        "access_token",
        "refresh_token",
        "authorization",
        "cookie",
        "session",
    ]
    for marker in redacted_markers:
        if marker.lower() in text.lower():
            return "<redacted>"
    return text


def _json(payload: dict[str, Any]) -> str:
    text = json.dumps(payload, indent=2, default=str)
    if payload.get("status") in {"error", "unsupported"} and len(text.encode("utf-8")) > MAX_ERROR_RESPONSE_BYTES:
        return _capped_error_json(payload, len(text.encode("utf-8")))
    return text


def _capped_error_json(payload: dict[str, Any], original_size: int) -> str:
    capped = {
        "status": "error",
        "tool": payload.get("tool"),
        "message": payload.get("message", "Error response exceeded safe size and was truncated."),
        "response_truncated": True,
        "original_response_size_bytes": original_size,
    }
    for key in ("preview_summary", "mapping_failures", "upload_ready", "upload_blocked", "note"):
        if key in payload:
            capped[key] = payload[key]
    if isinstance(capped.get("preview_summary"), dict):
        summary = dict(capped["preview_summary"])
        summary["mapped_exercises"] = (summary.get("mapped_exercises") or [])[:10]
        summary["unmapped_exercises"] = (summary.get("unmapped_exercises") or [])[:10]
        capped["preview_summary"] = summary

    while True:
        text = json.dumps({k: v for k, v in capped.items() if v is not None}, indent=2, default=str)
        if len(text.encode("utf-8")) <= ERROR_RESPONSE_TARGET_BYTES:
            return text
        failures = capped.get("mapping_failures")
        if isinstance(failures, list) and len(failures) > 1:
            capped["mapping_failures"] = failures[:-1]
            continue
        summary = capped.get("preview_summary")
        if isinstance(summary, dict) and summary.get("unmapped_exercises"):
            summary = dict(summary)
            summary["unmapped_exercises"] = summary["unmapped_exercises"][:-1]
            capped["preview_summary"] = summary
            continue
        capped["mapping_failures"] = capped.get("mapping_failures", [])[:1] if isinstance(failures, list) else []
        capped["message"] = str(capped["message"])[:1000]
        return json.dumps({k: v for k, v in capped.items() if v is not None}, indent=2, default=str)


def _error(tool: str, message: str, **extra: Any) -> str:
    payload = {"status": "unsupported" if extra.pop("unsupported", False) else "error", "tool": tool, "message": message}
    payload.update(extra)
    return _json(payload)


def _validate_date(value: str, field: str = "date") -> str:
    try:
        dt.date.fromisoformat(value)
    except Exception as exc:
        raise ValueError(f"{field} must be a real date in YYYY-MM-DD format") from exc
    return value


def _compact_response(result: dict[str, Any], fields: list[str] | None = None) -> dict[str, Any]:
    """Create compact response with key fields.
    
    Args:
        result: Full result dict
        fields: Specific fields to include. If None, uses default fields.
    """
    default_fields = ["status", "workout_id", "name", "date", "schedule_id", "message"]
    fields = fields or default_fields
    compact = {}
    for field in fields:
        if field in result:
            compact[field] = result[field]
    
    # Always include key warnings if present
    if result.get("warnings"):
        compact["warnings"] = result.get("warnings")[:3]
    
    # Include degraded info for status "degraded" or "success_with_warnings"
    status = result.get("status", "")
    if status in ("degraded", "success_with_warnings"):
        if result.get("degraded_exercises"):
            compact["degraded_exercises"] = result.get("degraded_exercises")[:3]  # Include up to 3
            compact["degraded_exercise_count"] = len(result.get("degraded_exercises"))
        if result.get("preserved_exercises"):
            compact["preserved_exercise_count"] = len(result.get("preserved_exercises"))
        if result.get("rewritten_exercises"):
            compact["rewritten_exercise_count"] = len(result.get("rewritten_exercises"))
    
    if "status" not in compact:
        compact["status"] = result.get("status", "success")
    
    return compact


def _scheduled_workout_id(scheduled: dict[str, Any]) -> int | str | None:
    for key in (
        "scheduledWorkoutId",
        "scheduled_workout_id",
        "workoutScheduleId",
        "scheduleId",
        "calendarWorkoutId",
        "id",
    ):
        if scheduled.get(key) is not None:
            return scheduled.get(key)
    return None


def _compact_scheduled_workout(scheduled: dict[str, Any]) -> dict[str, Any]:
    completed = scheduled.get("associatedActivityId") is not None
    sport = scheduled.get("workoutType") or ((scheduled.get("sportType") or {}).get("sportTypeKey") if isinstance(scheduled.get("sportType"), dict) else None)
    payload = {
        "date": scheduled.get("scheduleDate") or scheduled.get("date"),
        "scheduled_workout_id": _scheduled_workout_id(scheduled),
        "workout_id": scheduled.get("workoutId") or scheduled.get("workout_id"),
        "name": scheduled.get("workoutName") or scheduled.get("name"),
        "sport": sport,
        "completed": completed,
    }
    if completed:
        payload["activity_id"] = scheduled.get("associatedActivityId")
    return {key: value for key, value in payload.items() if value is not None}


def _fetch_scheduled_workouts(start_date: str, end_date: str) -> list[dict[str, Any]]:
    query = {
        "query": f'query{{workoutScheduleSummariesScalar(startDate:"{start_date}", endDate:"{end_date}")}}'
    }
    result = garmin_client.query_garmin_graphql(query)
    return (result or {}).get("data", {}).get("workoutScheduleSummariesScalar", []) or []


def _is_scheduled_strength_workout(scheduled: dict[str, Any]) -> bool:
    sport = str(scheduled.get("workoutType") or scheduled.get("sport") or "").lower()
    return sport in {"strength_training", "strength", "strengthtraining"} or "strength" in sport


def export_strength_workout_definition(workout_id: int) -> dict[str, Any]:
    """Export a Garmin strength workout as a simplified definition that can be used to recreate it.
    
    Returns a dict with: name, description, estimated_duration_seconds, blocks (with steps).
    """
    if not garmin_client:
        raise ValueError("Garmin client not configured")
    
    workout = garmin_client.get_workout_by_id(int(workout_id))
    if not workout:
        raise ValueError(f"Workout {workout_id} not found")
    
    if not _is_strength_workout(workout):
        raise ValueError(f"Workout {workout_id} is not a strength workout")
    
    # Extract the workout definition
    segments = workout.get("workoutSegments") or []
    blocks = []
    current_block = None
    round_counter = {}
    
    for segment in segments:
        steps = segment.get("workoutSteps") or []
        for step in steps:
            step_type = (step.get("stepType") or {}).get("stepTypeKey")
            
            if step_type == "repeat":
                # Start of a repeat group
                block_name = step.get("description") or f"Block {len(blocks) + 1}"
                rounds = step.get("numberOfIterations") or 1
                current_block = {
                    "name": block_name,
                    "rounds": rounds,
                    "steps": [],
                }
            elif step_type == "rest":
                # Skip rest steps in export - they're implicit
                continue
            elif current_block is not None:
                # Inside a repeat group
                end_condition = step.get("endCondition") or {}
                end_type = end_condition.get("conditionTypeKey")
                
                exercise_name = step.get("exerciseName") or ""
                category = step.get("category") or ""
                display_name = step.get("description") or exercise_name
                
                step_def = {
                    "name": display_name,
                    "reps": int(end_condition.get("endConditionValue")) if end_type == "reps" else None,
                    "duration_seconds": int(end_condition.get("endConditionValue")) if end_type == "time" else None,
                }
                
                weight_value = step.get("weightValue")
                if weight_value is not None:
                    step_def["weight"] = weight_value
                    unit = step.get("weightUnit") or {}
                    unit_key = unit.get("unitKey") if isinstance(unit, dict) else None
                    if unit_key == "pound":
                        step_def["weight_lb"] = weight_value
                    else:
                        step_def["weight"] = weight_value
                
                # Rest is implicit in repeat groups
                current_block["steps"].append(step_def)
                
                # Check if we should close this block (look ahead)
                # For now, collect until next repeat or end
            else:
                # Not in a repeat group - single step
                if not current_block:
                    block_name = f"Block {len(blocks) + 1}"
                    current_block = {
                        "name": block_name,
                        "rounds": 1,
                        "steps": [],
                    }
                
                end_condition = step.get("endCondition") or {}
                end_type = end_condition.get("conditionTypeKey")
                
                exercise_name = step.get("exerciseName") or ""
                display_name = step.get("description") or exercise_name
                
                step_def = {
                    "name": display_name,
                    "reps": int(end_condition.get("endConditionValue")) if end_type == "reps" else None,
                    "duration_seconds": int(end_condition.get("endConditionValue")) if end_type == "time" else None,
                }
                
                weight_value = step.get("weightValue")
                if weight_value is not None:
                    step_def["weight"] = weight_value
                
                current_block["steps"].append(step_def)
        
        # Close any open block
        if current_block and current_block.get("steps"):
            blocks.append(current_block)
            current_block = None
    
    return {
        "name": workout.get("workoutName"),
        "description": workout.get("description"),
        "estimated_duration_seconds": workout.get("estimatedDurationInSecs"),
        "blocks": blocks,
    }


def _is_strength_workout(workout: dict[str, Any]) -> bool:
    sport = workout.get("sportType") or {}
    return sport.get("sportTypeKey") == "strength_training" or sport.get("sportTypeId") == 5


def _walk_steps(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    flattened = []
    for step in steps or []:
        flattened.append(step)
        nested = step.get("workoutSteps")
        if isinstance(nested, list):
            flattened.extend(_walk_steps(nested))
    return flattened


def _infer_exercise_name_from_description(description: str | None, category: str | None) -> tuple[str | None, str | None]:
    """Infer exercise_name from description pattern like 'ENUM - Display Name' when Garmin strips exerciseName.
    
    Returns: (inferred_exercise_name, inferred_display_name, source)
    """
    if not description:
        return None, None, None
    if " - " not in description:
        return None, None, None
    parts = description.split(" - ", 1)
    if len(parts) != 2:
        return None, None, None
    enum_part = parts[0].strip()
    display_part = parts[1].strip()
    if not enum_part or enum_part.islower() or "_" not in enum_part:
        return None, None, None
    return enum_part, display_part, "inferred_from_description"


def _curate_strength_step(step: dict[str, Any]) -> dict[str, Any]:
    step_type = step.get("stepType") or {}
    end_condition = step.get("endCondition") or {}
    raw_exercise_name = step.get("exerciseName") or ""
    category = step.get("category")
    description = step.get("description")
    inferred_name, inferred_display, inference_source = _infer_exercise_name_from_description(description, category)
    exercise_name = raw_exercise_name if raw_exercise_name else inferred_name
    display_name = inferred_display
    source = "raw" if raw_exercise_name else (inference_source if inference_source else "unknown")
    app_likely_correct = bool(
        (raw_exercise_name and category) or
        (inferred_name and category == inferred_name)
    )
    curated = {
        "type": step.get("type"),
        "order": step.get("stepOrder"),
        "step_type": step_type.get("stepTypeKey"),
        "description": description,
        "end_condition": end_condition.get("conditionTypeKey"),
        "end_condition_value": step.get("endConditionValue"),
        "category": category,
        "exercise_name": exercise_name,
        "exercise_name_raw": raw_exercise_name if raw_exercise_name else None,
        "exercise_name_inferred": inferred_name,
        "display_name": display_name,
        "exercise_name_source": source,
        "app_display_likely_correct": app_likely_correct,
        "weight_value": step.get("weightValue"),
        "weight_unit": (step.get("weightUnit") or {}).get("unitKey"),
        "repeat_count": step.get("numberOfIterations"),
        "skip_last_rest_step": step.get("skipLastRestStep"),
    }
    nested = step.get("workoutSteps")
    if isinstance(nested, list):
        curated["steps"] = [_curate_strength_step(item) for item in nested]
    return {key: value for key, value in curated.items() if value is not None}


def _curate_strength_workout(workout: dict[str, Any], include_steps: bool = False) -> dict[str, Any]:
    segments = workout.get("workoutSegments") or []
    flat_steps = []
    for segment in segments:
        flat_steps.extend(_walk_steps(segment.get("workoutSteps") or []))

    exercise_steps = [
        step for step in flat_steps
        if step.get("exerciseName") or (step.get("endCondition") or {}).get("conditionTypeKey") == "reps"
    ]
    rest_steps = [
        step for step in flat_steps
        if (step.get("stepType") or {}).get("stepTypeKey") in {"rest", "recovery"}
    ]

    payload = {
        "workout_id": workout.get("workoutId"),
        "name": workout.get("workoutName"),
        "description": workout.get("description"),
        "sport": (workout.get("sportType") or {}).get("sportTypeKey"),
        "sub_sport": workout.get("subSportType"),
        "created_date": workout.get("createdDate"),
        "updated_date": workout.get("updatedDate") or workout.get("updateDate"),
        "estimated_duration_seconds": workout.get("estimatedDurationInSecs"),
    }
    if segments:
        payload.update(
            {
                "segment_count": len(segments),
                "step_count": len(flat_steps),
                "exercise_step_count": len(exercise_steps),
                "rest_step_count": len(rest_steps),
            }
        )
    if include_steps:
        payload["segments"] = [
            {
                "order": segment.get("segmentOrder"),
                "sport": (segment.get("sportType") or {}).get("sportTypeKey"),
                "steps": [_curate_strength_step(step) for step in segment.get("workoutSteps") or []],
            }
            for segment in segments
        ]
    return {key: value for key, value in payload.items() if value is not None}


def _fetch_all_workouts(limit: int) -> list[dict[str, Any]]:
    workouts = []
    page_size = 100
    for start in range(0, max(limit, 1), page_size):
        batch = garmin_client.get_workouts(start, min(page_size, limit - start))
        if not batch:
            break
        workouts.extend(batch)
        if len(batch) < page_size or len(workouts) >= limit:
            break
    return workouts[:limit]


def _call_delete(url: str) -> Any:
    if hasattr(garmin_client, "client") and hasattr(garmin_client.client, "delete"):
        return garmin_client.client.delete("connectapi", url, api=True)
    return garmin_client.garth.delete("connectapi", url, api=True)


def _delete_workout_by_id(workout_id: int) -> Any:
    url = f"{garmin_client.garmin_workouts}/workout/{int(workout_id)}"
    return _call_delete(url)


def _call_post(url: str, payload: dict[str, Any]) -> Any:
    if hasattr(garmin_client, "client") and hasattr(garmin_client.client, "post"):
        return garmin_client.client.post("connectapi", url, json=payload, api=True)
    return garmin_client.garth.post("connectapi", url, json=payload, api=True).json()


def _response_payload(result: Any) -> dict[str, Any]:
    if isinstance(result, dict):
        return result
    status_code = getattr(result, "status_code", None)
    if status_code == 204:
        return {"http_status": status_code}
    try:
        parsed = result.json()
        if isinstance(parsed, dict):
            return parsed
        return {"result": parsed, "http_status": status_code}
    except Exception:
        return {"http_status": status_code, "text": getattr(result, "text", None)}


def _roundtrip_fields_from_entry(entry: dict[str, Any] | None) -> dict[str, Any]:
    if not entry:
        return {
            "local_valid": False,
            "garmin_upload_accepts": None,
            "garmin_roundtrip_preserves": False,
            "roundtrip_status": "unknown",
            "safe_for_exact_tracking": False,
        }
    status = entry.get("roundtrip_status") or "unknown"
    return {
        "local_valid": bool(entry.get("local_valid", entry.get("exercise_name") and entry.get("category"))),
        "garmin_upload_accepts": entry.get("garmin_upload_accepts"),
        "garmin_roundtrip_preserves": bool(entry.get("garmin_roundtrip_preserves")),
        "roundtrip_status": status,
        "garmin_actual_exercise_name": entry.get("garmin_actual_exercise_name"),
        "garmin_rewrite_to": entry.get("garmin_rewrite_to"),
        "safe_for_exact_tracking": bool(entry.get("safe_for_exact_tracking")),
        "known_good": bool(entry.get("safe_for_exact_tracking")),
    }


def _resolve_exercise_for_step(step: dict[str, Any], path: str = "exercise") -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Resolve exercise using only Garmin's global catalog. Never uses local/guessed mappings."""
    warnings = []
    raw_name = step.get("exercise") or step.get("name") or step.get("display_name") or step.get("exercise_name")
    if not raw_name:
        return None, [{"severity": "error", "path": path, "message": "Missing exercise name.", "code": "exercise_mapping_failed"}]

    # Use GLOBAL resolver only - never fall back to local catalog
    resolved = strength_exercise_catalog.resolve_strength_exercise_global(str(raw_name))
    logger.info("resolve_exercise_global input=%s output=%s", raw_name, resolved)

    if not resolved:
        # Try exact Garmin exercise_name + category lookup if provided
        if step.get("exercise_name") and step.get("category"):
            for entry in strength_exercise_catalog.load_global_catalog().get("exercises", []):
                if entry.get("exercise_name") == step.get("exercise_name") and entry.get("category") == step.get("category"):
                    resolved = entry
                    break
        if not resolved:
            warnings.append(
                {
                    "severity": "error",
                    "path": path,
                    "code": "exercise_mapping_failed",
                    "requested_exercise": raw_name,
                    "message": f"Exercise '{raw_name}' is not in Garmin's global catalog.",
                }
            )
            return None, warnings

    # Build response with global catalog source
    result = {
        "display_name": resolved.get("display_name"),
        "exercise_name": resolved.get("exercise_name"),
        "category": resolved.get("category"),
        "canonical": resolved.get("canonical"),
        "local_valid": True,
        "garmin_upload_accepts": True,
        "garmin_roundtrip_preserves": False,
        "roundtrip_status": "unknown",
        "garmin_actual_exercise_name": None,
        "garmin_rewrite_to": None,
        "safe_for_exact_tracking": False,
        "known_good": False,
        "source": resolved.get("source", "garmin_global_catalog"),
    }

    if not result.get("exercise_name"):
        warnings.append(
            {"severity": "error", "path": path, "code": "exercise_incomplete_mapping", "requested_exercise": raw_name, "message": f"Exercise '{raw_name}' has no Garmin exerciseName."}
        )
    if not result.get("category"):
        warnings.append(
            {"severity": "error", "path": path, "code": "exercise_incomplete_mapping", "requested_exercise": raw_name, "message": f"Exercise '{raw_name}' has no Garmin category."}
        )

    return result, warnings


def _has_errors(issues: list[dict[str, Any]]) -> bool:
    return any(issue.get("severity") == "error" for issue in issues)


def _has_substitutions(issues: list[dict[str, Any]]) -> bool:
    return any(issue.get("code") == "exercise_substitution" for issue in issues)


def _compact_suggestion(match: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in {
            "display_name": match.get("display_name"),
            "exercise_name": match.get("exercise_name") or match.get("garmin_exercise_name"),
            "category": match.get("category"),
            "confidence": match.get("confidence"),
            "known_good": match.get("known_good"),
            "roundtrip_status": match.get("roundtrip_status"),
            "safe_for_exact_tracking": match.get("safe_for_exact_tracking"),
            "match_reason": match.get("match_reason"),
        }.items()
        if value is not None
    }


def _compact_issue(issue: dict[str, Any]) -> dict[str, Any]:
    compact = {
        "severity": issue.get("severity"),
        "path": issue.get("path"),
        "message": issue.get("message"),
    }
    for key in ("code", "requested_exercise", "exercise_name", "category", "roundtrip_status"):
        if issue.get(key) is not None:
            compact[key] = issue[key]
    if issue.get("suggestions"):
        compact["suggestions"] = [_compact_suggestion(match) for match in issue["suggestions"][:3]]
    return {key: value for key, value in compact.items() if value is not None}


def _mapping_failures(issues: list[dict[str, Any]], upload_blocked: bool = True) -> list[dict[str, Any]]:
    failures = []
    for issue in issues:
        if issue.get("code") not in {"exercise_mapping_failed", "exercise_incomplete_mapping", "exercise_substitution"}:
            continue
        suggestions = [_compact_suggestion(match) for match in (issue.get("suggestions") or [])[:3]]
        failures.append(
            {
                "requested_exercise": issue.get("requested_exercise"),
                "reason": issue.get("message"),
                "suggested_replacements": suggestions,
                "upload_blocked": bool(upload_blocked),
            }
        )
    return failures[:MAX_MAPPING_FAILURES]


def _compact_validation(validation: dict[str, Any], allow_substitutions: bool = False) -> dict[str, Any]:
    issues = validation.get("issues") or []
    upload_ready = not _has_errors(issues) and (allow_substitutions or not _has_substitutions(issues))
    compact_issues = [_compact_issue(issue) for issue in issues[:50]]
    return {
        "status": "success" if upload_ready else "error",
        "exercise_step_count": validation.get("exercise_step_count", 0),
        "repeat_groups": validation.get("repeat_groups"),
        "upload_ready": upload_ready,
        "allow_substitutions": bool(allow_substitutions),
        "issues": compact_issues,
        "mapping_failures": _mapping_failures(issues, upload_blocked=not upload_ready),
    }


def _collect_mapped_unmapped(blocks: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    mapped = []
    unmapped = []
    for block_index, block in enumerate(blocks):
        if not isinstance(block, dict):
            continue
        for step_index, step in enumerate(block.get("steps") or []):
            if not isinstance(step, dict):
                continue
            requested = step.get("exercise") or step.get("name") or step.get("display_name") or step.get("exercise_name")
            path = f"blocks[{block_index}].steps[{step_index}]"
            resolved, issues = _resolve_exercise_for_step(step, path)
            item = {
                "requested_exercise": requested,
                "path": path,
            }
            if resolved and resolved.get("exercise_name") and resolved.get("category") and not _has_errors(issues):
                item.update(
                    {
                        "display_name": resolved.get("display_name"),
                        "exercise_name": resolved.get("exercise_name"),
                        "category": resolved.get("category"),
                        "known_good": resolved.get("known_good"),
                        "roundtrip_status": resolved.get("roundtrip_status"),
                        "safe_for_exact_tracking": resolved.get("safe_for_exact_tracking"),
                    }
                )
                mapped.append({key: value for key, value in item.items() if value is not None})
            else:
                item["reason"] = "; ".join(issue.get("message", "") for issue in issues if issue.get("severity") == "error") or "Exercise could not be mapped."
                item["suggested_replacements"] = _mapping_failures(issues)[:1][0]["suggested_replacements"] if _mapping_failures(issues) else []
                unmapped.append({key: value for key, value in item.items() if value is not None})
    return mapped, unmapped


def _preview_summary(name: str, blocks: list[dict[str, Any]], validation: dict[str, Any], allow_substitutions: bool = False) -> dict[str, Any]:
    compact_validation = _compact_validation(validation, allow_substitutions=allow_substitutions)
    mapped, unmapped = _collect_mapped_unmapped(blocks)
    return {
        "workout_name": name,
        "exercise_count": validation.get("exercise_step_count", len(mapped) + len(unmapped)),
        "mapped_exercises": mapped,
        "unmapped_exercises": unmapped,
        "upload_ready": compact_validation["upload_ready"],
    }


def _format_duration(seconds: int | float | None) -> str | None:
    if seconds is None:
        return None
    seconds = int(seconds)
    minutes, secs = divmod(seconds, 60)
    return f"{minutes}:{secs:02d}"


def _step_summary(step: dict[str, Any]) -> str:
    name = step.get("exercise") or step.get("display_name") or step.get("name") or step.get("exercise_name")
    if step.get("reps") is not None:
        effort = f"{step['reps']} reps"
    elif step.get("duration_seconds") is not None:
        effort = f"{int(step['duration_seconds'])} sec"
    else:
        effort = "lap button"
    weight_kg = step.get("weight_kg") or step.get("weight_value") or step.get("weight")
    if weight_kg:
        unit = step.get("weight_unit", "kilogram").lower()
        if unit in {"lb", "lbs", "pound", "pounds"}:
            weight_display = f"{float(weight_kg) * 0.453592:.1f} lb"
        else:
            weight_display = f"{float(weight_kg):.1f} kg"
        effort = f"{effort} @ {weight_display}"
    return f"{name} - {effort}"


def _normalize_weight(set_data: dict[str, Any], exercise: dict[str, Any], path: str = "step") -> tuple[float | None, dict[str, Any] | None]:
    """Normalize weight from various aliases to weightValue and weightUnit.
    
    Supported aliases:
    - weight, weight_value, weight_kg → kilogram
    - weight_lb, weight_lbs → pound
    
    Returns: (weight_value, weight_unit) or (None, None)
    
    Raises ValueError with path context if weight value is explicitly set to None.
    """
    weight_value = None
    weight_unit = None
    
    has_explicit_weight_lb = "weight_lb" in set_data or "weight_lbs" in set_data
    has_explicit_weight = "weight" in set_data or "weight_value" in set_data or "weight_kg" in set_data
    has_explicit_exercise_weight = "weight" in exercise or "weight_value" in exercise or "weight_kg" in exercise
    
    if has_explicit_weight_lb:
        weight_lb = set_data.get("weight_lb") if "weight_lb" in set_data else set_data.get("weight_lbs")
        if weight_lb is None:
            raise ValueError(f"{path}: omit weight for bodyweight exercises instead of setting 0/null; otherwise weight must be a number")
        weight_value = float(weight_lb)
        weight_unit = LB_UNIT
    elif has_explicit_weight:
        weight_val = set_data.get("weight") if "weight" in set_data else (set_data.get("weight_value") if "weight_value" in set_data else set_data.get("weight_kg"))
        if weight_val is None:
            raise ValueError(f"{path}: omit weight for bodyweight exercises instead of setting 0/null; otherwise weight must be a number")
        weight_value = float(weight_val)
        unit_str = str(set_data.get("weight_unit", "kilogram")).lower()
        if unit_str in {"lb", "lbs", "pound", "pounds"}:
            weight_unit = LB_UNIT
        else:
            weight_unit = KG_UNIT
    elif has_explicit_exercise_weight:
        weight_val = exercise.get("weight") if "weight" in exercise else (exercise.get("weight_value") if "weight_value" in exercise else exercise.get("weight_kg"))
        if weight_val is None:
            raise ValueError(f"{path}: omit weight for bodyweight exercises instead of setting 0/null; otherwise weight must be a number")
        weight_value = float(weight_val)
        unit_str = str(exercise.get("weight_unit", "kilogram")).lower()
        if unit_str in {"lb", "lbs", "pound", "pounds"}:
            weight_unit = LB_UNIT
        else:
            weight_unit = KG_UNIT
    
    return weight_value, weight_unit


def _exercise_step(order: int, exercise: dict[str, Any], set_data: dict[str, Any], step_path: str = "step") -> dict[str, Any]:
    exercise = dict(exercise)
    friendly_name = exercise.get("exercise") or exercise.get("name") or exercise.get("display_name")
    if friendly_name and not exercise.get("exercise_name"):
        resolved = strength_exercise_catalog.resolve_exercise(str(friendly_name))
        if resolved:
            exercise["exercise_name"] = resolved.get("exercise_name")
            exercise.setdefault("category", resolved.get("category"))
            exercise.setdefault("display_name", resolved.get("display_name"))
    if not exercise.get("exercise_name"):
        raise ValueError(
            f"{step_path}: exercise '{friendly_name or ''}' is not in the local catalog and has no Garmin exercise_name"
        )
    if not exercise.get("category"):
        raise ValueError(f"{step_path}: exercise '{friendly_name or exercise.get('exercise_name')}' has no Garmin category")

    reps = set_data.get("reps", exercise.get("reps"))
    duration_seconds = set_data.get("duration_seconds", exercise.get("duration_seconds"))
    if reps is None and duration_seconds is None:
        raise ValueError(f"{step_path}: each exercise set needs reps or duration_seconds")

    if reps is not None and duration_seconds is not None:
        raise ValueError(f"{step_path}: Garmin strength steps should use reps OR duration_seconds, not both")

    try:
        if reps is not None:
            end_condition = REPS_CONDITION
            end_value = float(reps)
        else:
            end_condition = TIME_CONDITION
            end_value = float(duration_seconds)
    except (TypeError, ValueError) as e:
        raise ValueError(f"{step_path}: invalid reps/duration_seconds value: {e}")

    path_for_weight = f"{step_path}.weight"
    try:
        weight_value, weight_unit = _normalize_weight(set_data, exercise, path_for_weight)
    except ValueError as e:
        raise ValueError(str(e))
    category = str(exercise.get("category") or "").upper()
    exercise_name = str(exercise.get("exercise_name") or "").upper()
    if weight_value == 0 and (category in {"PULL_UP", "PUSH_UP"} or exercise_name in {"PULL_UP", "PUSH_UP"}):
        raise ValueError(f"{path_for_weight}: omit weight for bodyweight exercises instead of setting 0/null")

    step = {
        "type": "ExecutableStepDTO",
        "stepOrder": order,
        "stepType": copy.deepcopy(STEP_TYPES.get(exercise.get("step_type", "interval"), STEP_TYPES["interval"])),
        "description": exercise.get("description") or exercise.get("display_name") or friendly_name,
        "endCondition": copy.deepcopy(end_condition),
        "endConditionValue": end_value,
        "targetType": copy.deepcopy(NO_TARGET),
        "strokeType": {"strokeTypeId": 0, "strokeTypeKey": None, "displayOrder": 0},
        "equipmentType": {"equipmentTypeId": 0, "equipmentTypeKey": None, "displayOrder": 0},
        "category": exercise.get("category"),
        "exerciseName": exercise.get("exercise_name"),
        "weightValue": weight_value,
        "weightUnit": copy.deepcopy(weight_unit) if weight_unit else None,
    }
    return {key: value for key, value in step.items() if value is not None}


def _rest_step(order: int, seconds: float | int | None) -> dict[str, Any] | None:
    if seconds is None:
        return None
    seconds = float(seconds)
    if seconds <= 0:
        return None
    return {
        "type": "ExecutableStepDTO",
        "stepOrder": order,
        "stepType": copy.deepcopy(STEP_TYPES["rest"]),
        "endCondition": copy.deepcopy(TIME_CONDITION),
        "endConditionValue": seconds,
        "targetType": copy.deepcopy(NO_TARGET),
        "strokeType": {"strokeTypeId": 0, "strokeTypeKey": None, "displayOrder": 0},
        "equipmentType": {"equipmentTypeId": 0, "equipmentTypeKey": None, "displayOrder": 0},
        "weightUnit": copy.deepcopy(KG_UNIT),
    }


def _renumber_steps(steps: list[dict[str, Any]], start_order: int = 1) -> int:
    order = start_order
    for step in steps:
        step["stepOrder"] = order
        order += 1
        nested = step.get("workoutSteps")
        if isinstance(nested, list):
            _renumber_steps(nested, 1)
    return order


def _repeat_group(order: int, block: dict[str, Any], nested_steps: list[dict[str, Any]]) -> dict[str, Any]:
    rounds = int(block.get("rounds") or block.get("sets") or 1)
    return {
        "type": "RepeatGroupDTO",
        "stepOrder": order,
        "stepType": copy.deepcopy(STEP_TYPES["repeat"]),
        "description": block.get("name"),
        "endCondition": copy.deepcopy(ITERATIONS_CONDITION),
        "endConditionValue": float(rounds),
        "numberOfIterations": rounds,
        "targetType": copy.deepcopy(NO_TARGET),
        "skipLastRestStep": True,
        "workoutSteps": nested_steps,
    }


def _block_to_steps(
    block: dict[str, Any],
    start_order: int,
    use_repeat_groups: bool = True,
    omit_final_rest: bool = True,
) -> tuple[list[dict[str, Any]], int]:
    if not isinstance(block, dict):
        raise ValueError("each block must be an object")
    block_name = block.get("name") or ""
    
    # Normalize block schema - accept "exercises" as alias for "steps"
    block = _normalize_block_schema(block, f"blocks[{block_name}]")
    steps_in = block.get("steps")
    if not isinstance(steps_in, list) or not steps_in:
        raise ValueError(f"block '{block_name}' needs a non-empty steps list (use 'steps', not 'exercises')")
    rounds = int(block.get("rounds") or 1)
    if rounds < 1:
        raise ValueError("block rounds must be >= 1")

    block_type = str(block.get("type") or "sets").lower()
    if use_repeat_groups and rounds > 1:
        nested = []
        nested_order = 1
        for index, step_in in enumerate(steps_in):
            exercise_data = dict(step_in)
            step_path = f"blocks[{block_name}].steps[{index}]"
            resolved, issues = _resolve_exercise_for_step(exercise_data, step_path)
            errors = [issue["message"] for issue in issues if issue.get("severity") == "error"]
            if errors:
                raise ValueError(f"{step_path}: {'; '.join(errors)}")
            exercise_data.update(
                {
                    "display_name": resolved.get("display_name"),
                    "exercise_name": resolved.get("exercise_name"),
                    "category": resolved.get("category"),
                }
            )
            nested.append(_exercise_step(nested_order, exercise_data, exercise_data, step_path))
            nested_order += 1
            rest = _rest_step(nested_order, step_in.get("rest_seconds"))
            if rest:
                nested.append(rest)
                nested_order += 1
        return [_repeat_group(start_order, block, nested)], start_order + 1

    flattened = []
    order = start_order
    for round_index in range(rounds):
        for index, step_in in enumerate(steps_in):
            exercise_data = dict(step_in)
            step_path = f"blocks[{block_name}].steps[{index}]"
            resolved, issues = _resolve_exercise_for_step(exercise_data, step_path)
            errors = [issue["message"] for issue in issues if issue.get("severity") == "error"]
            if errors:
                raise ValueError(f"{step_path}: {'; '.join(errors)}")
            exercise_data.update(
                {
                    "display_name": resolved.get("display_name"),
                    "exercise_name": resolved.get("exercise_name"),
                    "category": resolved.get("category"),
                }
            )
            if block.get("name"):
                exercise_data["description"] = f"{block['name']} - {exercise_data.get('display_name')}"
            flattened.append(_exercise_step(order, exercise_data, exercise_data, step_path))
            order += 1
            is_last_step = index == len(steps_in) - 1
            is_last_round = round_index == rounds - 1
            if omit_final_rest and is_last_step and is_last_round:
                continue
            rest = _rest_step(order, step_in.get("rest_seconds"))
            if rest:
                flattened.append(rest)
                order += 1
    if block_type in {"superset", "circuit"} and not use_repeat_groups:
        logger.info("strength_block_flattened block=%s type=%s", block.get("name"), block_type)
    return flattened, order


def _simple_exercises_to_blocks(exercises: list[dict[str, Any]]) -> list[dict[str, Any]]:
    blocks = []
    for exercise in exercises:
        sets = exercise.get("sets") or [{}]
        steps = []
        for set_data in sets:
            step = dict(exercise)
            step.pop("sets", None)
            step.update(set_data)
            step.setdefault("exercise", exercise.get("exercise") or exercise.get("name") or exercise.get("display_name") or exercise.get("exercise_name"))
            step.setdefault("rest_seconds", set_data.get("rest_seconds", exercise.get("rest_seconds")))
            steps.append(step)
        blocks.append({"name": exercise.get("name") or exercise.get("display_name") or exercise.get("exercise_name"), "type": "sets", "rounds": 1, "steps": steps})
    return blocks


def build_strength_workout_payload(
    name: str,
    exercises: list[dict[str, Any]] | None = None,
    description: str | None = None,
    estimated_duration_seconds: int | None = None,
    sub_sport_type: str = "GENERIC",
    blocks: list[dict[str, Any]] | None = None,
    use_repeat_groups: bool = True,
) -> dict[str, Any]:
    if not name or not isinstance(name, str):
        raise ValueError("name is required")
    if blocks is None:
        if not isinstance(exercises, list) or not exercises:
            raise ValueError("provide either blocks or a non-empty exercises list")
        blocks = _simple_exercises_to_blocks(exercises)
    if not isinstance(blocks, list) or not blocks:
        raise ValueError("blocks must be a non-empty list")

    validation = validate_strength_workout_definition(name=name, exercises=exercises, blocks=blocks, use_repeat_groups=use_repeat_groups)
    if _has_errors(validation["issues"]):
        messages = "; ".join(issue["message"] for issue in validation["issues"] if issue.get("severity") == "error")
        raise ValueError(f"strength workout validation failed: {messages}")

    steps: list[dict[str, Any]] = []
    step_order = 1
    for block_index, block in enumerate(blocks):
        block_steps, step_order = _block_to_steps(
            block,
            step_order,
            use_repeat_groups=use_repeat_groups,
            omit_final_rest=block_index == len(blocks) - 1,
        )
        steps.extend(block_steps)
    _renumber_steps(steps)

    payload = {
        "workoutName": name[:80],
        "description": description,
        "sportType": copy.deepcopy(STRENGTH_SPORT),
        "subSportType": sub_sport_type,
        "estimatedDurationInSecs": estimated_duration_seconds,
        "estimatedDistanceInMeters": 0.0,
        "workoutSegments": [
            {
                "segmentOrder": 1,
                "sportType": copy.deepcopy(STRENGTH_SPORT),
                "workoutSteps": steps,
            }
        ],
    }
    # Log the exercise step details for debugging
    for step in steps:
        if step.get("exerciseName"):
            logger.info("upload_workout_step category=%s exerciseName=%s", step.get("category"), step.get("exerciseName"))
    return {key: value for key, value in payload.items() if value is not None}


def _normalize_block_schema(block: dict[str, Any], path: str = "block") -> dict[str, Any]:
    """Normalize block schema - accept 'exercises' as alias for 'steps' and handle 'sets'.
    
    Converts:
    - blocks[].exercises → blocks[].steps (alias)
    - steps[].sets → block-level rounds (convert to repeat structure)
    """
    block = dict(block)
    
    # Handle "exercises" as alias for "steps"
    if block.get("exercises") is not None and block.get("steps") is None:
        block["steps"] = block.pop("exercises")
    
    steps = block.get("steps", [])
    if not isinstance(steps, list):
        return block
    
    # Handle per-step "sets" - convert to per-exercise repeats
    new_steps = []
    for step in steps:
        if not isinstance(step, dict):
            new_steps.append(step)
            continue
        
        step_sets = step.get("sets")
        if step_sets is not None and isinstance(step_sets, list) and len(step_sets) > 0:
            # Convert: step with sets -> multiple steps (one per set)
            for set_idx, set_data in enumerate(step_sets):
                new_step = dict(step)
                new_step.pop("sets", None)
                if isinstance(set_data, dict):
                    new_step.update(set_data)
                new_steps.append(new_step)
        else:
            new_steps.append(step)
    
    block["steps"] = new_steps
    return block


def validate_strength_workout_definition(
    name: str,
    exercises: list[dict[str, Any]] | None = None,
    blocks: list[dict[str, Any]] | None = None,
    use_repeat_groups: bool = True,
) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    if not name or not isinstance(name, str):
        issues.append({"severity": "error", "path": "name", "message": "name is required."})
    if blocks is None:
        if exercises:
            blocks = _simple_exercises_to_blocks(exercises)
        else:
            issues.append({"severity": "error", "path": "blocks", "message": "provide either blocks or exercises."})
            blocks = []
    if not isinstance(blocks, list) or not blocks:
        issues.append({"severity": "error", "path": "blocks", "message": "blocks must be a non-empty list."})
        blocks = []

    # Normalize block schema: accept "exercises" as alias for "steps"
    normalized_blocks = []
    for block_index, block in enumerate(blocks):
        if not isinstance(block, dict):
            issues.append({"severity": "error", "path": f"blocks[{block_index}]", "message": "block must be an object."})
            continue
        
        path = f"blocks[{block_index}]"
        
        # Check if using the deprecated "exercises" key
        if block.get("exercises") is not None and block.get("steps") is None:
            issues.append({
                "severity": "error",
                "path": f"{path}.exercises",
                "message": f"{path}.exercises: use blocks[].steps, not blocks[].exercises.",
                "code": "deprecated_key_exercises",
            })
        for step_index, original_step in enumerate(block.get("steps") or []):
            if isinstance(original_step, dict) and "sets" in original_step:
                issues.append(
                    {
                        "severity": "error",
                        "path": f"{path}.steps[{step_index}].sets",
                        "message": f"{path}.steps[{step_index}].sets: use rounds on the block instead of sets on steps.",
                        "code": "step_sets_not_supported",
                    }
                )
        
        normalized_block = _normalize_block_schema(block, path)
        normalized_blocks.append(normalized_block)
    
    blocks = normalized_blocks

    exercise_count = 0
    for block_index, block in enumerate(blocks):
        path = f"blocks[{block_index}]"
        block_name = block.get("name") or ""
        
        if not isinstance(block, dict):
            issues.append({"severity": "error", "path": path, "message": "block must be an object."})
            continue
        block_type = str(block.get("type") or "sets").lower()
        rounds = block.get("rounds", 1)
        try:
            rounds_int = int(rounds)
            if rounds_int < 1:
                raise ValueError
        except Exception:
            issues.append({"severity": "error", "path": f"{path}.rounds", "message": "rounds must be a positive integer."})
            rounds_int = 1
        if block_type in {"superset", "circuit"} and rounds_int > 1 and not use_repeat_groups:
            issues.append({"severity": "warning", "path": path, "message": "Superset/circuit will be flattened because repeat groups are disabled."})
        steps = block.get("steps")
        if not isinstance(steps, list) or not steps:
            # Provide better error message for "exercises" vs "steps"
            original_block = blocks[block_index] if block_index < len(blocks) else {}
            if original_block.get("exercises") is not None:
                issues.append({"severity": "error", "path": f"{path}.steps", "message": "block steps must be a non-empty list. Note: Use 'steps' key inside blocks, not 'exercises'."})
            else:
                issues.append({"severity": "error", "path": f"{path}.steps", "message": "block steps must be a non-empty list."})
            continue
        for step_index, step in enumerate(steps):
            step_path = f"{path}.steps[{step_index}]"
            if not isinstance(step, dict):
                issues.append({"severity": "error", "path": step_path, "message": "step must be an object."})
                continue
            exercise_count += 1
            if step.get("reps") is None and step.get("duration_seconds") is None:
                issues.append({"severity": "error", "path": step_path, "message": f"{step_path}.duration_seconds missing; steps require reps or duration_seconds."})
            if step.get("reps") is not None and step.get("duration_seconds") is not None:
                issues.append({"severity": "error", "path": step_path, "message": "Garmin strength steps should use reps or duration_seconds, not both."})
            explicit_weight_keys = [key for key in ("weight", "weight_value", "weight_kg", "weight_lb", "weight_lbs") if key in step]
            step_weight = next((step.get(key) for key in explicit_weight_keys if step.get(key) is not None), None)
            step_weight_unit = step.get("weight_unit")
            if any(step.get(key) is None for key in explicit_weight_keys):
                bad_key = next(key for key in explicit_weight_keys if step.get(key) is None)
                issues.append(
                    {
                        "severity": "error",
                        "path": f"{step_path}.{bad_key}",
                        "message": f"{step_path}.{bad_key}: omit weight for bodyweight exercises instead of setting 0/null; otherwise provide a number.",
                    }
                )
            if step_weight_unit and step_weight in (None, 0):
                issues.append({"severity": "warning", "path": step_path, "message": "step has weight_unit but no weight value; weight will be ignored."})
            # Only warn about rest step weight if user explicitly provided weight on a rest step
            # We can't know if it's a rest step at validation time, so we'll check in build
            resolved, step_issues = _resolve_exercise_for_step(step, step_path)
            issues.extend(step_issues)
            if resolved and resolved.get("category") and resolved.get("exercise_name"):
                exercise_key = str(resolved["exercise_name"]).upper()
                category = str(resolved["category"]).upper()
                if step_weight == 0 and (category in {"PULL_UP", "PUSH_UP"} or exercise_key in {"PULL_UP", "PUSH_UP"}):
                    issues.append(
                        {
                            "severity": "error",
                            "path": f"{step_path}.weight",
                            "message": f"{step_path}.weight: omit weight for bodyweight exercises instead of setting 0/null",
                        }
                    )
                if category in {"SHOULDER_STABILITY", "PULL_UP", "CORE", "SHOULDER_PRESS"} and not resolved.get("safe_for_exact_tracking"):
                    status = resolved.get("roundtrip_status") or "unknown"
                    if status == "stripped":
                        message = f"{resolved['display_name']} is expected to render as a generic {category} step in Garmin after upload."
                    elif status == "rewritten":
                        target = resolved.get("garmin_rewrite_to") or resolved.get("garmin_actual_exercise_name") or "a Garmin canonical exerciseName"
                        message = f"{resolved['display_name']} is expected to round-trip as {target}."
                    else:
                        message = "Garmin may accept this step but strip the internal exercise code after upload."
                    issues.append(
                        {
                            "severity": "warning",
                            "path": step_path,
                            "message": message,
                            "exercise_name": exercise_key,
                            "category": category,
                            "roundtrip_status": status,
                        }
                    )
    return {
        "status": "error" if _has_errors(issues) else "success",
        "exercise_step_count": exercise_count,
        "repeat_groups": bool(use_repeat_groups),
        "issues": issues,
    }


def preview_strength_workout_definition(
    name: str,
    exercises: list[dict[str, Any]] | None = None,
    blocks: list[dict[str, Any]] | None = None,
    description: str | None = None,
    estimated_duration_seconds: int | None = None,
    use_repeat_groups: bool = True,
    allow_substitutions: bool = False,
) -> dict[str, Any]:
    if blocks is None and exercises:
        blocks = _simple_exercises_to_blocks(exercises)
    blocks = blocks or []
    validation = validate_strength_workout_definition(name, exercises=exercises, blocks=blocks, use_repeat_groups=use_repeat_groups)
    lines = [f"Workout: {name}"]
    if description:
        lines.append(f"Description: {description}")
    duration = _format_duration(estimated_duration_seconds)
    if duration:
        lines.append(f"Estimated duration: {duration}")
    lines.append("Blocks:")
    for block in blocks:
        block_name = block.get("name") or "Block"
        block_type = str(block.get("type") or "sets").title()
        rounds = int(block.get("rounds") or 1)
        suffix = f"{block_type} x{rounds}" if rounds > 1 else block_type
        lines.append(f"{block_name} - {suffix}:")
        for index, step in enumerate(block.get("steps") or [], start=1):
            lines.append(f"  {index}. {_step_summary(step)}")
        rests = [step.get("rest_seconds") for step in block.get("steps") or [] if step.get("rest_seconds")]
        if rests:
            lines.append(f"  Rest {int(rests[-1])} sec")
    warnings = [issue for issue in validation["issues"] if issue.get("severity") == "warning"]
    if warnings:
        lines.append("Warnings:")
        for warning in warnings[:10]:
            lines.append(f"  - {warning['message']}")
    compact_validation = _compact_validation(validation, allow_substitutions=allow_substitutions)
    if compact_validation["upload_ready"]:
        try:
            build_strength_workout_payload(
                name,
                exercises=exercises,
                blocks=blocks,
                description=description,
                estimated_duration_seconds=estimated_duration_seconds,
                use_repeat_groups=use_repeat_groups,
            )
        except ValueError as exc:
            validation["issues"].append(
                {
                    "severity": "error",
                    "path": "payload",
                    "message": f"Local payload construction failed: {exc}",
                    "code": "payload_construction_failed",
                }
            )
            compact_validation = _compact_validation(validation, allow_substitutions=allow_substitutions)
    summary = _preview_summary(name, blocks, validation, allow_substitutions=allow_substitutions)
    return {
        "status": compact_validation["status"],
        "preview": "\n".join(lines),
        "preview_summary": summary,
        "upload_ready": compact_validation["upload_ready"],
        "upload_blocked": not compact_validation["upload_ready"],
        "mapping_failures": compact_validation["mapping_failures"],
        "validation": compact_validation,
    }


def _is_exercise_like_step(step: dict[str, Any]) -> bool:
    step_type = (step.get("stepType") or {}).get("stepTypeKey")
    if step_type in {"rest", "recovery"}:
        return False
    end_condition = (step.get("endCondition") or {}).get("conditionTypeKey")
    return bool(step.get("exerciseName") or step.get("category") or end_condition in {"reps", "time"})


def _allowed_rewrite(expected_name: str) -> str | None:
    expected_name = str(expected_name or "").upper()
    entry = strength_exercise_catalog.find_entry_by_exercise_name(expected_name)
    return (
        GARMIN_EXERCISE_REWRITES.get(expected_name)
        or (entry or {}).get("garmin_rewrite_to")
        or (entry or {}).get("garmin_actual_exercise_name")
        if (entry or {}).get("roundtrip_status") == "rewritten"
        else GARMIN_EXERCISE_REWRITES.get(expected_name)
    )


def _check_inferred_match(expected: dict[str, Any], actual: dict[str, Any]) -> tuple[bool, str | None, str | None]:
    """Check if Garmin stripped exerciseName but inference from description/category still matches.
    
    Returns: (is_inferred, inferred_exercise_name, display_name)
    """
    expected_name = expected.get("exerciseName") or ""
    expected_category = expected.get("category") or ""
    actual_description = actual.get("description") or ""
    actual_category = actual.get("category") or ""
    if not actual_description or " - " not in actual_description:
        return False, None, None
    parts = actual_description.split(" - ", 1)
    if len(parts) != 2:
        return False, None, None
    enum_part = parts[0].strip()
    display_part = parts[1].strip()
    if not enum_part or enum_part.islower() or "_" not in enum_part:
        return False, None, None
    if actual_category and actual_category.upper() == expected_category.upper():
        if enum_part.upper() == expected_name.upper():
            return True, enum_part, display_part
    return False, None, None


def _verification_step_result(index: int, expected: dict[str, Any], actual: dict[str, Any] | None, mode: str) -> dict[str, Any]:
    expected_name = expected.get("exerciseName")
    actual_name = (actual or {}).get("exerciseName") or ""
    rewrite_to = _allowed_rewrite(str(expected_name))
    base = {
        "index": index,
        "step_order": (actual or expected).get("stepOrder"),
        "display_name": expected.get("description"),
        "category": expected.get("category"),
        "expected_exercise_name": expected_name,
        "actual_exercise_name": actual_name,
    }
    if actual is None:
        return {**base, "status": "missing_after_fetch", "fatal": mode in {"strict", "compatible"}}
    if actual_name == expected_name:
        return {**base, "status": "preserved", "fatal": False}
    if actual_name and rewrite_to and actual_name == rewrite_to and mode in {"compatible", "lenient"}:
        return {**base, "status": "rewritten", "rewrite_to": rewrite_to, "fatal": False}
    if not actual_name:
        is_inferred, inferred_name, display = _check_inferred_match(expected, actual or {})
        if is_inferred:
            return {
                **base,
                "status": "inferred",
                "inferred_exercise_name": inferred_name,
                "display_name": display,
                "fatal": False,
                "note": "Garmin stripped raw exerciseName, but description/category indicate app display is correct.",
            }
        return {**base, "status": "garmin_stripped", "fatal": mode in {"strict", "compatible"}}
    return {**base, "status": "mismatch", "rewrite_to": rewrite_to, "fatal": True}


def _actual_matches_expected(actual: dict[str, Any], expected: dict[str, Any], mode: str) -> bool:
    actual_name = actual.get("exerciseName") or ""
    expected_name = expected.get("exerciseName")
    if actual_name == expected_name:
        return True
    rewrite_to = _allowed_rewrite(str(expected_name))
    if actual_name and rewrite_to and actual_name == rewrite_to and mode in {"compatible", "lenient"}:
        return True
    if not actual_name:
        is_inferred, _, _ = _check_inferred_match(expected, actual)
        if is_inferred:
            return True
    return False


def _compare_exercise_steps(expected_exercises: list[dict[str, Any]], actual_exercises: list[dict[str, Any]], mode: str) -> list[dict[str, Any]]:
    comparisons = []
    actual_index = 0
    for expected_index, expected in enumerate(expected_exercises):
        actual = actual_exercises[actual_index] if actual_index < len(actual_exercises) else None
        if actual is not None and not _actual_matches_expected(actual, expected, mode):
            later_expected = expected_exercises[expected_index + 1:]
            if any(_actual_matches_expected(actual, future, mode) for future in later_expected):
                comparisons.append(_verification_step_result(expected_index, expected, None, mode))
                continue
        comparison = _verification_step_result(expected_index, expected, actual, mode)
        comparisons.append(comparison)
        if actual is not None:
            actual_index += 1
    return comparisons


def verify_uploaded_strength_workout(
    workout_id: int,
    expected_payload: dict[str, Any],
    mode: str = "compatible",
) -> dict[str, Any]:
    mode = str(mode or "compatible").lower()
    if mode not in VERIFICATION_MODES:
        return {"status": "error", "message": f"verification_mode must be one of {sorted(VERIFICATION_MODES)}."}
    workout = garmin_client.get_workout_by_id(int(workout_id))
    if not workout:
        return {"status": "error", "message": f"Uploaded workout {workout_id} could not be fetched back."}
    if not _is_strength_workout(workout):
        return {"status": "error", "message": f"Uploaded workout {workout_id} is not strength_training."}

    expected_steps = _walk_steps((expected_payload.get("workoutSegments") or [{}])[0].get("workoutSteps") or [])
    actual_steps = []
    for segment in workout.get("workoutSegments") or []:
        actual_steps.extend(_walk_steps(segment.get("workoutSteps") or []))
    expected_exercises = [step for step in expected_steps if step.get("exerciseName")]
    actual_exercises = [step for step in actual_steps if _is_exercise_like_step(step)]
    expected_names = [step.get("exerciseName") for step in expected_exercises]
    actual_names = [step.get("exerciseName") for step in actual_exercises]

    issues = []
    comparisons = _compare_exercise_steps(expected_exercises, actual_exercises, mode)
    preserved = [item for item in comparisons if item["status"] == "preserved"]
    rewritten = [item for item in comparisons if item["status"] == "rewritten"]
    inferred = [item for item in comparisons if item["status"] == "inferred"]
    degraded = [item for item in comparisons if item["status"] in {"garmin_stripped", "missing_after_fetch", "mismatch"}]
    fatal_degraded = [item for item in degraded if item.get("fatal")]

    if len(actual_exercises) != len(expected_exercises):
        severity = "error" if mode in {"strict", "compatible"} else "warning"
        issues.append(
            {
                "severity": severity,
                "message": f"Expected {len(expected_exercises)} exercise steps after fetch, got {len(actual_exercises)}.",
            }
        )
    missing_names = [item["expected_exercise_name"] for item in degraded if item["status"] in {"garmin_stripped", "missing_after_fetch"}]
    if missing_names:
        severity = "error" if mode in {"strict", "compatible"} else "warning"
        issues.append({"severity": severity, "message": "Fetched workout did not preserve expected exerciseName values.", "missing": missing_names})
    mismatches = [item for item in degraded if item["status"] == "mismatch"]
    if mismatches:
        issues.append({"severity": "error", "message": "Fetched workout has unexpected exerciseName rewrites.", "mismatches": mismatches})
    missing_category = [step.get("stepOrder") for step in actual_exercises if step.get("exerciseName") and not step.get("category")]
    if missing_category:
        issues.append({"severity": "error", "message": "Fetched workout has exercise steps without category.", "step_orders": missing_category})
    blank_exercise = [item.get("step_order") for item in degraded if item["status"] == "garmin_stripped"]
    if blank_exercise:
        severity = "error" if mode in {"strict", "compatible"} else "warning"
        issues.append({"severity": severity, "message": "Garmin fetched exercise steps back with blank exerciseName.", "step_orders": blank_exercise})
    expected_weights = {step.get("stepOrder"): step.get("weightValue") for step in expected_exercises if step.get("weightValue")}
    actual_weights = {step.get("stepOrder"): step.get("weightValue") for step in actual_exercises if step.get("weightValue")}
    if expected_weights and not actual_weights:
        weight_step_orders = list(expected_weights.keys())
        issues.append({"severity": "warning", "message": "Weight values were sent in the upload payload, but Garmin's fetch endpoint did not expose them for verification.", "step_orders": weight_step_orders})
    weight_mismatches = []
    for step_order, expected_w in expected_weights.items():
        actual_w = actual_weights.get(step_order)
        if actual_w is not None and actual_w != expected_w:
            weight_mismatches.append({"step_order": step_order, "expected": expected_w, "actual": actual_w})
    if weight_mismatches:
        issues.append({"severity": "warning", "message": "Weight values differ between upload and fetch.", "mismatches": weight_mismatches})
    expected_groups = [step for step in expected_steps if step.get("type") == "RepeatGroupDTO"]
    actual_groups = [step for step in actual_steps if step.get("type") == "RepeatGroupDTO"]
    if expected_groups and not actual_groups:
        issues.append({"severity": "warning", "message": "Garmin did not preserve RepeatGroupDTO groups; mobile may show a flattened workout."})

    status = "error" if _has_errors(issues) or fatal_degraded else ("degraded" if degraded else "success")
    inferred_warnings = [item.get("note") for item in inferred if item.get("note")]
    if inferred_warnings:
        for warning in inferred_warnings:
            issues.append({"severity": "warning", "message": warning})
    return {
        "status": status,
        "verification_mode": mode,
        "workout": _curate_strength_workout(workout, include_steps=True),
        "expected_exercise_names": expected_names,
        "actual_exercise_names": actual_names,
        "preserved_exercises": preserved,
        "rewritten_exercises": rewritten,
        "inferred_exercises": inferred,
        "degraded_exercises": degraded,
        "issues": issues,
    }


def _roundtrip_result_status(expected_name: str, actual_name: str, verification_status: str) -> str:
    if verification_status == "preserved":
        return "preserved"
    if verification_status == "rewritten":
        return "rewritten"
    if verification_status == "garmin_stripped":
        return "garmin_stripped"
    if verification_status == "missing_after_fetch":
        return "missing_after_fetch"
    if actual_name and actual_name != expected_name:
        return "unexpected_rewrite"
    return verification_status


def register_tools(app: Any) -> Any:
    @app.tool()
    async def list_strength_workout_templates(limit: int = 500) -> str:
        """List Garmin Connect workout templates whose sport is strength_training."""
        try:
            limit = min(max(int(limit), 1), 1000)
            workouts = _fetch_all_workouts(limit)
            strength = [_curate_strength_workout(workout) for workout in workouts if _is_strength_workout(workout)]
            return _json({"status": "success", "count": len(strength), "searched_workouts": len(workouts), "workouts": strength})
        except Exception as exc:
            logger.info("strength_workout_list_failed: %s", type(exc).__name__)
            return _error("list_strength_workout_templates", f"Garmin strength workout templates could not be listed: {exc}")

    @app.tool()
    async def get_strength_workout_template(workout_id: int) -> str:
        """Get strength workout template details, including exercises, reps, weight targets, and rests."""
        try:
            workout = garmin_client.get_workout_by_id(int(workout_id))
            if not workout:
                return _error("get_strength_workout_template", f"No workout found for workout_id {workout_id}", unsupported=True)
            if not _is_strength_workout(workout):
                sport = (workout.get("sportType") or {}).get("sportTypeKey")
                return _error("get_strength_workout_template", f"Workout {workout_id} is sport '{sport}', not strength_training.")
            return _json({"status": "success", "workout": _curate_strength_workout(workout, include_steps=True)})
        except Exception as exc:
            logger.info("strength_workout_get_failed: %s", type(exc).__name__)
            return _error("get_strength_workout_template", f"Garmin strength workout template could not be retrieved: {exc}")

    @app.tool()
    async def build_strength_workout_template(
        name: str,
        exercises: list[dict[str, Any]] | None = None,
        blocks: list[dict[str, Any]] | None = None,
        description: str | None = None,
        estimated_duration_seconds: int | None = None,
        use_repeat_groups: bool = True,
        include_raw_json: bool = True,
        allow_substitutions: bool = False,
    ) -> str:
        """Build and validate Garmin strength workout JSON without writing to Garmin Connect.

        Simplified schema: use blocks[].steps, not blocks[].exercises. Use rounds
        on blocks for repeated sets. Steps use reps or duration_seconds. Weighted
        steps use weight; bodyweight steps omit weight. Rest is rest_seconds.
        Example: {"name":"Pull Day","blocks":[{"name":"Carry","rounds":3,"steps":[{"name":"Farmer Carry","duration_seconds":45,"weight":26,"rest_seconds":60}]}]}
        """
        try:
            preview = preview_strength_workout_definition(name, exercises, blocks, description, estimated_duration_seconds, use_repeat_groups, allow_substitutions)
            if preview["status"] == "error":
                logger.debug("strength_workout_build_validation_failed details=%s", _redact_text(preview))
                return _json({"status": "error", "message": "Strength workout validation failed; no Garmin changes were made.", **preview})
            payload = build_strength_workout_payload(
                name,
                exercises=exercises,
                blocks=blocks,
                description=description,
                estimated_duration_seconds=estimated_duration_seconds,
                use_repeat_groups=use_repeat_groups,
            )
            response = {"status": "success", "message": "Payload built only; no Garmin changes were made.", **preview}
            if include_raw_json:
                response["workout_data"] = payload
            return _json(response)
        except Exception as exc:
            return _error("build_strength_workout_template", str(exc))

    @app.tool()
    async def clone_strength_workout_template(
        source_workout_id: int,
        new_name: str | None = None,
        new_date: str | None = None,
    ) -> str:
        """Clone an existing Garmin strength workout template.
        
        Creates a copy of the source workout with a new name, optionally scheduling it.
        source_workout_id: The workout to clone
        new_name: Name for the cloned workout (default: "Copy of {original_name}")
        new_date: Optional date to schedule the cloned workout (YYYY-MM-DD)
        """
        try:
            # Export the source workout
            source_def = export_strength_workout_definition(int(source_workout_id))
            
            # Create the new workout
            new_workout_name = new_name or f"Copy of {source_def.get('name', 'Workout')}"
            preview = preview_strength_workout_definition(
                new_workout_name,
                blocks=source_def.get("blocks"),
                description=source_def.get("description"),
                estimated_duration_seconds=source_def.get("estimated_duration_seconds"),
            )
            if preview["status"] == "error":
                return _json({"status": "error", "message": "Cloned workout validation failed.", **preview})
            
            payload = build_strength_workout_payload(
                new_workout_name,
                blocks=source_def.get("blocks"),
                description=source_def.get("description"),
                estimated_duration_seconds=source_def.get("estimated_duration_seconds"),
            )
            
            result = garmin_client.upload_workout(payload)
            workout_id = result.get("workoutId") if isinstance(result, dict) else None
            
            if not workout_id:
                return _error("clone_strength_workout_template", "Clone upload did not return workoutId.")
            
            # Schedule if date provided
            schedule_result = None
            if new_date:
                new_date = _validate_date(new_date)
                url = f"{garmin_client.garmin_workouts_schedule_url}/{int(workout_id)}"
                schedule_result = _call_post(url, {"date": new_date})
            
            return _json({
                "status": "success",
                "message": "Strength workout cloned.",
                "source_workout_id": source_workout_id,
                "new_workout_id": workout_id,
                "new_workout_name": new_workout_name,
                "scheduled_date": new_date,
                "schedule_result": _response_payload(schedule_result) if schedule_result else None,
            })
        except Exception as exc:
            logger.info("strength_workout_clone_failed: %s", type(exc).__name__)
            return _error("clone_strength_workout_template", f"Failed to clone workout: {exc}")

    @app.tool()
    async def validate_strength_workout(
        name: str,
        exercises: list[dict[str, Any]] | None = None,
        blocks: list[dict[str, Any]] | None = None,
        use_repeat_groups: bool = True,
    ) -> str:
        """Validate a strength workout definition before upload."""
        try:
            validation = validate_strength_workout_definition(name, exercises=exercises, blocks=blocks, use_repeat_groups=use_repeat_groups)
            return _json(_compact_validation(validation))
        except Exception as exc:
            return _error("validate_strength_workout", str(exc))

    @app.tool()
    async def preview_strength_workout(
        name: str,
        exercises: list[dict[str, Any]] | None = None,
        blocks: list[dict[str, Any]] | None = None,
        description: str | None = None,
        estimated_duration_seconds: int | None = None,
        use_repeat_groups: bool = True,
        allow_substitutions: bool = False,
    ) -> str:
        """Preview and validate a strength workout using the same local payload path as create.

        Use blocks[].steps, block rounds, reps or duration_seconds, weight for
        loaded movements, and omit weight for bodyweight. Examples:
        {"name":"Pull Day","blocks":[{"name":"Pull","rounds":4,"steps":[{"name":"Pull-up","reps":8,"rest_seconds":90}]}]}
        {"name":"Carry","blocks":[{"name":"Carry","rounds":3,"steps":[{"name":"Farmer Carry","duration_seconds":45,"weight":26,"rest_seconds":60}]}]}
        """
        try:
            return _json(preview_strength_workout_definition(name, exercises, blocks, description, estimated_duration_seconds, use_repeat_groups, allow_substitutions))
        except Exception as exc:
            return _error("preview_strength_workout", str(exc))

    @app.tool()
    async def create_strength_workout(
        name: str,
        exercises: list[dict[str, Any]] | None = None,
        blocks: list[dict[str, Any]] | None = None,
        description: str | None = None,
        estimated_duration_seconds: int | None = None,
        use_repeat_groups: bool = True,
        allow_substitutions: bool = False,
        dry_run: bool = False,
        verify_after_upload: bool = True,
        verification_mode: str = "lenient",
        cleanup_on_failure: bool = False,
        cleanup_on_degradation: bool = False,
        verbose: bool = False,
    ) -> str:
        """Create a Garmin strength workout template. verification_mode is strict, compatible, or lenient.
        
        Set verbose=false for compact output (default). Set verbose=true for full
        workout details. Use blocks[].steps, not blocks[].exercises. Use rounds
        on blocks for repeated sets. Timed steps use duration_seconds; rep-based
        steps use reps; weighted steps use weight; bodyweight steps omit weight.
        Rest is supplied as rest_seconds on the exercise step.
        Example: {"name":"Pull Day","verbose":false,"blocks":[{"name":"Pull","rounds":4,"steps":[{"name":"Pull-up","reps":8,"rest_seconds":90},{"name":"Farmer Carry","duration_seconds":45,"weight":26,"rest_seconds":60}]}]}
        """
        try:
            verification_mode = str(verification_mode or "lenient").lower()
            if verification_mode not in VERIFICATION_MODES:
                return _error("create_strength_workout", f"verification_mode must be one of {sorted(VERIFICATION_MODES)}")
            preview = preview_strength_workout_definition(name, exercises, blocks, description, estimated_duration_seconds, use_repeat_groups, allow_substitutions)
            if preview["status"] == "error":
                logger.debug("strength_workout_create_upload_blocked details=%s", _redact_text(preview))
                return _json({"status": "error", "message": "Strength workout validation failed; upload blocked.", **preview})
            
            # Try to build the payload - this will catch validation errors with proper context
            try:
                payload = build_strength_workout_payload(
                    name,
                    exercises=exercises,
                    blocks=blocks,
                    description=description,
                    estimated_duration_seconds=estimated_duration_seconds,
                    use_repeat_groups=use_repeat_groups,
                )
            except ValueError as ve:
                # Provide more actionable error with path context
                logger.info("strength_workout_build_failed: %s", str(ve))
                return _error(
                    "create_strength_workout",
                    f"Workout payload construction failed: {ve}",
                    note="Check that all exercises have valid reps/duration_seconds and weight values are numbers, not None.",
                )
            
            if dry_run:
                return _json({"status": "success", "message": "Dry run only; no Garmin changes were made.", **preview})
            
            result = garmin_client.upload_workout(payload)
            workout_id = result.get("workoutId") if isinstance(result, dict) else None
            verification = None
            if verify_after_upload and workout_id:
                verification = verify_uploaded_strength_workout(int(workout_id), payload, mode=verification_mode)
                verification_status = verification.get("status")
                if verification_status == "error":
                    cleanup_result = None
                    if cleanup_on_failure or verification_mode == "strict":
                        cleanup_result = _response_payload(_delete_workout_by_id(int(workout_id)))
                    response = {
                        "status": "error",
                        "message": "Strength workout uploaded but post-upload verification failed.",
                        "workout_id": workout_id,
                        "verification": verification,
                        "cleanup_result": cleanup_result,
                        "result": result,
                    }
                    if not verbose:
                        return _json(_compact_response(response))
                    return _json(response)
                if verification_status == "degraded":
                    cleanup_result = None
                    if cleanup_on_degradation:
                        cleanup_result = _response_payload(_delete_workout_by_id(int(workout_id)))
                    warnings = [
                        issue.get("message")
                        for issue in verification.get("issues") or []
                        if issue.get("severity") == "warning" and issue.get("message")
                    ]
                    response = {
                        "status": "degraded" if cleanup_on_degradation else "success_with_warnings",
                        "message": "Strength workout created, but Garmin did not preserve every exerciseName after fetch.",
                        "workout_id": workout_id,
                        "name": result.get("workoutName") if isinstance(result, dict) else name,
                        "degraded_exercises": verification.get("degraded_exercises") or [],
                        "preserved_exercises": verification.get("preserved_exercises") or [],
                        "rewritten_exercises": verification.get("rewritten_exercises") or [],
                        "warnings": warnings,
                        "preview": preview["preview"],
                        "validation": preview["validation"],
                        "verification": verification,
                        "cleanup_result": cleanup_result,
                        "result": result,
                    }
                    if not verbose:
                        return _json(_compact_response(response))
                    return _json(response)
            
            response = {
                "status": "success",
                "message": "Strength workout created in Garmin Connect.",
                "workout_id": workout_id,
                "name": result.get("workoutName") if isinstance(result, dict) else name,
                "preview": preview["preview"],
                "validation": preview["validation"],
                "verification": verification,
                "result": result,
            }
            if not verbose:
                return _json(_compact_response(response))
            return _json(response)
        except Exception as exc:
            logger.info("strength_workout_create_failed: %s", type(exc).__name__)
            # Check if it's a ValueError we can provide context for
            if isinstance(exc, ValueError):
                return _error(
                    "create_strength_workout",
                    f"Workout validation failed: {exc}",
                    note="Check exercise names, reps/duration_seconds, and weight values. Use 'steps' inside blocks, not 'exercises'.",
                )
            return _error(
                "create_strength_workout",
                f"Garmin rejected the strength workout payload: {exc}",
                note="Use get_strength_workout_template on a round-trip verified Garmin strength workout to compare exerciseName/category values.",
            )

    @app.tool()
    async def roundtrip_verify_strength_exercises(
        exercises: list[str],
        cleanup: bool = True,
        update_catalog: bool = False,
    ) -> str:
        """Empirically upload/fetch strength exercises and report Garmin round-trip preservation.

        Example: {"exercises":["Pull-up","Face Pull","Farmer Carry"],"cleanup":true}
        """
        if not isinstance(exercises, list) or not exercises:
            return _error("roundtrip_verify_strength_exercises", "exercises must be a non-empty list of display names.")
        try:
            steps = []
            expected_entries = []
            for name in exercises:
                resolved = strength_exercise_catalog.resolve_exercise(str(name))
                if not resolved or not resolved.get("exercise_name") or not resolved.get("category"):
                    return _error("roundtrip_verify_strength_exercises", f"Exercise '{name}' is not locally mapped with exercise_name and category.")
                expected_entries.append(resolved)
                step = {"exercise": resolved["display_name"], "reps": 1, "rest_seconds": 0}
                if str(resolved.get("category") or "").upper() == "CORE":
                    step = {"exercise": resolved["display_name"], "duration_seconds": 5, "rest_seconds": 0}
                steps.append(step)
            name = f"MCP roundtrip verification {dt.datetime.now(dt.timezone.utc).strftime('%Y%m%d%H%M%S')}"
            blocks = [{"name": "Round-trip verification", "type": "sets", "rounds": 1, "steps": steps}]
            payload = build_strength_workout_payload(name, blocks=blocks, use_repeat_groups=False)
            result = garmin_client.upload_workout(payload)
            workout_id = result.get("workoutId") if isinstance(result, dict) else None
            if not workout_id:
                return _error("roundtrip_verify_strength_exercises", "Garmin upload did not return workoutId.", result=result)
            verification = verify_uploaded_strength_workout(int(workout_id), payload, mode="lenient")
            comparisons = (
                verification.get("preserved_exercises", [])
                + verification.get("rewritten_exercises", [])
                + verification.get("degraded_exercises", [])
            )
            comparisons = sorted(comparisons, key=lambda item: item.get("index", 0))
            results = []
            for index, resolved in enumerate(expected_entries):
                comparison = comparisons[index] if index < len(comparisons) else {}
                actual_name = comparison.get("actual_exercise_name") or ""
                status = _roundtrip_result_status(str(resolved.get("exercise_name")), actual_name, comparison.get("status", "missing_after_fetch"))
                rewrite_to = actual_name if status == "rewritten" else None
                results.append(
                    {
                        "display_name": resolved.get("display_name"),
                        "expected_exercise_name": resolved.get("exercise_name"),
                        "actual_exercise_name": actual_name,
                        "status": status,
                        "category": resolved.get("category"),
                    }
                )
                if update_catalog:
                    catalog_status = {
                        "preserved": "preserved",
                        "rewritten": "rewritten",
                        "garmin_stripped": "stripped",
                        "missing_after_fetch": "stripped",
                        "unexpected_rewrite": "rewritten",
                    }.get(status, "unknown")
                    strength_exercise_catalog.update_roundtrip_metadata(
                        str(resolved.get("display_name")),
                        catalog_status,
                        actual_exercise_name=actual_name,
                        rewrite_to=rewrite_to,
                        upload_accepts=True,
                    )
            cleanup_result = None
            if cleanup:
                cleanup_result = _response_payload(_delete_workout_by_id(int(workout_id)))
            return _json(
                {
                    "status": "success",
                    "workout_id": workout_id,
                    "cleanup": bool(cleanup),
                    "cleanup_result": cleanup_result,
                    "updated_catalog": bool(update_catalog),
                    "results": results,
                    "verification": verification,
                }
            )
        except Exception as exc:
            logger.info("strength_roundtrip_verify_failed: %s", type(exc).__name__)
            return _error("roundtrip_verify_strength_exercises", f"Round-trip verification failed: {exc}")

    @app.tool()
    async def create_strength_workout_from_raw_json(workout_data: dict[str, Any]) -> str:
        """Create a Garmin strength workout from raw Garmin workout JSON. Requires sportTypeKey strength_training."""
        try:
            if not isinstance(workout_data, dict):
                return _error("create_strength_workout_from_raw_json", "workout_data must be an object")
            if not _is_strength_workout(workout_data):
                return _error("create_strength_workout_from_raw_json", "Raw workout sportType must be strength_training.")
            result = garmin_client.upload_workout(workout_data)
            return _json({"status": "success", "message": "Raw strength workout created.", "result": result})
        except Exception as exc:
            logger.info("strength_workout_raw_create_failed: %s", type(exc).__name__)
            return _error("create_strength_workout_from_raw_json", f"Garmin rejected the raw strength workout payload: {exc}")

    @app.tool()
    async def schedule_strength_workout(workout_id: int, date: str, verbose: bool = False) -> str:
        """Schedule an existing Garmin strength workout template on the Garmin calendar.
        
        Set verbose=false for compact output (default). Set verbose=true for full workout details.
        Example: {"workout_id":1567063109,"date":"2026-05-14","verbose":false}
        """
        try:
            date = _validate_date(date)
            workout = garmin_client.get_workout_by_id(int(workout_id))
            if not _is_strength_workout(workout):
                sport = (workout.get("sportType") or {}).get("sportTypeKey")
                return _error("schedule_strength_workout", f"Workout {workout_id} is sport '{sport}', not strength_training.")
            url = f"{garmin_client.garmin_workouts_schedule_url}/{int(workout_id)}"
            result = _call_post(url, {"date": date})
            response = {"status": "success", "message": "Strength workout scheduled.", "workout_id": workout_id, "date": date, "result": _response_payload(result)}
            
            if not verbose:
                compact = _compact_response(response)
                return _json(compact)
            return _json(response)
        except Exception as exc:
            logger.info("strength_workout_schedule_failed: %s", type(exc).__name__)
            return _error("schedule_strength_workout", f"Garmin strength workout scheduling failed: {exc}")

    @app.tool()
    async def schedule_strength_workouts(schedule_requests: list[dict[str, Any]], verbose: bool = False) -> str:
        """Schedule multiple strength workouts on the Garmin calendar.
        
        schedule_requests: list of {"workout_id": int, "date": "YYYY-MM-DD"}
        Set verbose=false for compact output (default).
        """
        results = []
        errors = []
        
        for req in schedule_requests:
            wid = req.get("workout_id")
            date = req.get("date")
            if not wid or not date:
                errors.append({"request": req, "error": "Missing workout_id or date"})
                continue
            
            try:
                date = _validate_date(date)
                workout = garmin_client.get_workout_by_id(int(wid))
                if not _is_strength_workout(workout):
                    sport = (workout.get("sportType") or {}).get("sportTypeKey")
                    errors.append({"workout_id": wid, "error": f"sport '{sport}', not strength_training"})
                    continue
                url = f"{garmin_client.garmin_workouts_schedule_url}/{int(wid)}"
                result = _call_post(url, {"date": date})
                results.append({
                    "status": "success",
                    "workout_id": wid,
                    "date": date,
                    "workout_name": workout.get("workoutName"),
                })
            except Exception as exc:
                errors.append({"workout_id": wid, "date": date, "error": str(exc)})
        
        if verbose:
            return _json({
                "status": "success" if not errors else "partial",
                "scheduled_count": len(results),
                "error_count": len(errors),
                "results": results,
                "errors": errors,
            })
        else:
            return _json({
                "status": "success" if not errors else "partial",
                "scheduled_count": len(results),
                "error_count": len(errors),
                "scheduled": [{"id": r["workout_id"], "date": r["date"], "name": r["workout_name"]} for r in results],
                "errors": [{"id": e.get("workout_id"), "error": e.get("error")} for e in errors],
            })

    @app.tool()
    async def replace_scheduled_strength_workout(
        date: str,
        new_workout: dict[str, Any],
        old_workout_id: int | None = None,
        scheduled_workout_id: int | None = None,
        delete_old_template: bool = True,
        verify: bool = True,
        verbose: bool = False,
    ) -> str:
        """Replace one scheduled strength workout in a single workflow.

        If scheduled_workout_id is omitted, the tool finds strength workouts on date.
        Multiple matches return a disambiguation error. new_workout accepts the same
        simplified schema as create_strength_workout: blocks[].steps, block rounds,
        reps or duration_seconds, optional weight, and rest_seconds.
        Example:
        {"date":"2026-05-14","new_workout":{"name":"Pull Heavy v2","blocks":[{"name":"Carry","rounds":3,"steps":[{"name":"Farmer Carry","duration_seconds":45,"weight":26,"rest_seconds":60}]}]}}
        """
        warnings: list[str] = []
        try:
            date = _validate_date(date)
            if not isinstance(new_workout, dict):
                return _error("replace_scheduled_strength_workout", "new_workout must be an object accepted by create_strength_workout.")

            scheduled_items = _fetch_scheduled_workouts(date, date)
            candidates = [_compact_scheduled_workout(item) for item in scheduled_items if _is_scheduled_strength_workout(item)]
            if old_workout_id is not None:
                candidates = [item for item in candidates if int(item.get("workout_id") or 0) == int(old_workout_id)]
            if scheduled_workout_id is not None:
                candidates = [item for item in candidates if str(item.get("scheduled_workout_id")) == str(scheduled_workout_id)]
                if not candidates:
                    candidates = [{"date": date, "scheduled_workout_id": scheduled_workout_id, "workout_id": old_workout_id}]

            if scheduled_workout_id is None and len(candidates) != 1:
                return _json(
                    {
                        "status": "error",
                        "tool": "replace_scheduled_strength_workout",
                        "message": "Could not identify exactly one scheduled strength workout. Re-run with scheduled_workout_id.",
                        "date": date,
                        "candidate_count": len(candidates),
                        "candidates": candidates[:10],
                    }
                )

            old = candidates[0]
            old_schedule_id = old.get("scheduled_workout_id") or scheduled_workout_id
            old_template_id = old.get("workout_id") or old_workout_id
            if old_schedule_id is None:
                return _error("replace_scheduled_strength_workout", "Selected scheduled workout does not include scheduled_workout_id; cannot unschedule safely.", candidates=candidates)

            name = new_workout.get("name")
            blocks = new_workout.get("blocks")
            exercises = new_workout.get("exercises")
            description = new_workout.get("description")
            estimated_duration_seconds = new_workout.get("estimated_duration_seconds")
            use_repeat_groups = bool(new_workout.get("use_repeat_groups", True))
            allow_substitutions = bool(new_workout.get("allow_substitutions", False))

            preview = preview_strength_workout_definition(
                name,
                exercises=exercises,
                blocks=blocks,
                description=description,
                estimated_duration_seconds=estimated_duration_seconds,
                use_repeat_groups=use_repeat_groups,
                allow_substitutions=allow_substitutions,
            )
            if preview["status"] == "error":
                return _json({"status": "error", "message": "New workout validation failed; no Garmin changes were made.", **preview})
            payload = build_strength_workout_payload(
                name,
                exercises=exercises,
                blocks=blocks,
                description=description,
                estimated_duration_seconds=estimated_duration_seconds,
                use_repeat_groups=use_repeat_groups,
            )

            unschedule_result = _call_delete(f"{garmin_client.garmin_workouts_schedule_url}/{int(old_schedule_id)}")
            delete_result = None
            if delete_old_template and old_template_id is not None:
                delete_result = _delete_workout_by_id(int(old_template_id))

            create_result = garmin_client.upload_workout(payload)
            new_workout_id = create_result.get("workoutId") if isinstance(create_result, dict) else None
            if not new_workout_id:
                return _error(
                    "replace_scheduled_strength_workout",
                    "Old scheduled workout was removed, but Garmin did not return workoutId for the replacement.",
                    removed=old,
                    create_result=create_result,
                )
            schedule_result = _call_post(f"{garmin_client.garmin_workouts_schedule_url}/{int(new_workout_id)}", {"date": date})

            verified = False
            verification: dict[str, Any] | None = None
            if verify:
                scheduled_after = [_compact_scheduled_workout(item) for item in _fetch_scheduled_workouts(date, date)]
                template = garmin_client.get_workout_by_id(int(new_workout_id))
                verified = any(str(item.get("workout_id")) == str(new_workout_id) for item in scheduled_after) and bool(template)
                verification = {
                    "scheduled_match": any(str(item.get("workout_id")) == str(new_workout_id) for item in scheduled_after),
                    "template_found": bool(template),
                    "scheduled_workouts": scheduled_after,
                    "workout": _curate_strength_workout(template, include_steps=verbose) if isinstance(template, dict) else None,
                }
                if not verified:
                    warnings.append("Replacement was attempted, but verification did not find both the scheduled entry and template.")

            response = {
                "status": "success",
                "removed": {
                    "scheduled_workout_id": old_schedule_id,
                    "workout_id": old_template_id,
                    "name": old.get("name"),
                },
                "created": {
                    "workout_id": new_workout_id,
                    "name": create_result.get("workoutName") if isinstance(create_result, dict) else name,
                },
                "scheduled": {"date": date},
                "verified": bool(verified) if verify else None,
                "warnings": warnings,
            }
            if verbose:
                response.update(
                    {
                        "unschedule_result": _response_payload(unschedule_result),
                        "delete_result": _response_payload(delete_result) if delete_result is not None else None,
                        "create_result": create_result,
                        "schedule_result": _response_payload(schedule_result),
                        "verification": verification,
                    }
                )
            return _json({key: value for key, value in response.items() if value is not None})
        except Exception as exc:
            logger.info("strength_workout_replace_failed: %s", type(exc).__name__)
            return _error("replace_scheduled_strength_workout", f"Garmin strength workout replacement failed: {exc}")

    @app.tool()
    async def export_strength_workout_definition(workout_id: int) -> str:
        """Export a Garmin strength workout as a simplified definition for recreation.
        
        Returns a dict with: name, description, estimated_duration_seconds, blocks (with steps).
        The output can be passed to create_strength_workout to recreate the workout.
        """
        try:
            result = export_strength_workout_definition(int(workout_id))
            return _json({"status": "success", "workout_id": workout_id, "definition": result})
        except Exception as exc:
            logger.info("strength_workout_export_failed: %s", type(exc).__name__)
            return _error("export_strength_workout_definition", f"Failed to export workout: {exc}")

    # NOTE: The former `unschedule_strength_workout` tool has been removed.
    # Use the generic, sport-agnostic `unschedule_workout` tool exposed by the
    # workouts module instead. It works for running, cycling, strength, cardio,
    # and walking calendar entries and uses confirmation='UNSCHEDULE_WORKOUT'.

    @app.tool()
    async def delete_strength_workout(workout_id: int, confirmation: str = "", verbose: bool = False) -> str:
        """Delete a strength workout template from Garmin Connect. Requires confirmation='DELETE_STRENGTH_WORKOUT'.

        Example: {"workout_id":1567063109,"confirmation":"DELETE_STRENGTH_WORKOUT"}
        """
        if confirmation != "DELETE_STRENGTH_WORKOUT":
            return _error(
                "delete_strength_workout",
                "Explicit confirmation required. Re-run with confirmation='DELETE_STRENGTH_WORKOUT'.",
            )
        try:
            workout = garmin_client.get_workout_by_id(int(workout_id))
            if not _is_strength_workout(workout):
                sport = (workout.get("sportType") or {}).get("sportTypeKey")
                return _error("delete_strength_workout", f"Workout {workout_id} is sport '{sport}', not strength_training.")
            url = f"{garmin_client.garmin_workouts}/workout/{int(workout_id)}"
            result = _call_delete(url)
            response = {"status": "success", "message": "Strength workout template deleted.", "workout_id": workout_id, "result": _response_payload(result)}
            if not verbose:
                response.pop("result", None)
            return _json(response)
        except Exception as exc:
            logger.info("strength_workout_delete_failed: %s", type(exc).__name__)
            return _error("delete_strength_workout", f"Garmin strength workout deletion failed: {exc}")

    return app
