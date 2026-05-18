"""
Workout-related functions for Garmin Connect MCP Server
"""
import copy
import json
import re
import time
import datetime
from typing import Any, Dict, List, Optional, TypedDict, Union

# The garmin_client will be set by the main file
garmin_client = None


def configure(client):
    """Configure the module with the Garmin client instance"""
    global garmin_client
    garmin_client = client


# =============================================================================
# CALENDAR REFRESH — TYPED SCHEMA EXPOSED TO MCP CLIENTS
# =============================================================================

class ScheduleRequest(TypedDict, total=False):
    """One desired calendar slot for refresh_workout_calendar / preview_workout_calendar_refresh.

    Provide either ``workout_id`` (an existing Garmin workout template) OR
    ``workout_data`` (an inline workout body to upload then schedule),
    never both.
    """

    calendar_date: str  # required, "YYYY-MM-DD"
    workout_id: int  # optional, mutually exclusive with workout_data
    workout_data: Dict[str, Any]  # optional, mutually exclusive with workout_id
    label: str  # optional free-form label
    expected_name: str  # optional, used by final verification
    expected_sport: str  # optional, used by final verification


# Retry budget for "did the calendar change yet?" reads after a mutation.
# Kept very small to bound latency. Tests patch ``_RETRY_SLEEP`` to 0 to skip
# the real sleep without changing the retry count.
_POSTCONDITION_RETRY_DELAYS = (0.0, 0.5, 1.0, 2.0)


def _retry_sleep(seconds: float) -> None:
    """Indirection so tests can monkeypatch sleep without changing the
    retry budget. Defaults to ``time.sleep`` and is a no-op for 0 / negative
    durations.
    """
    if seconds and seconds > 0:
        time.sleep(seconds)


# =============================================================================
# RICH ERROR REPORTING
# =============================================================================

# Pattern produced by garminconnect 0.3.2 client._run_request:
#   f"API Error {status_code}" optionally suffixed with " - {msg}" or " - {body_text}"
_API_ERROR_RE = re.compile(r"API Error\s+(\d{3})(?:\s*-\s*(.*))?$", re.DOTALL)


def _extract_response_details(exc: BaseException) -> Dict[str, Any]:
    """Best-effort extraction of HTTP status + response body from a Garmin client exception.

    The garminconnect 0.3.2 client raises GarminConnectConnectionError(string) where the
    string is shaped like "API Error 400 - <message-or-body>". garth-style wrappers expose
    .error.response with .status_code/.text/.url. requests.HTTPError exposes .response.
    We probe all known shapes and return what we can find.
    """
    details: Dict[str, Any] = {}

    # Shape 1: garth-style GarthHTTPError -> .error is requests.HTTPError -> .response
    err = getattr(exc, "error", None)
    resp = getattr(err, "response", None) if err is not None else None
    # Shape 2: direct .response on the exception (requests.HTTPError)
    if resp is None:
        resp = getattr(exc, "response", None)

    if resp is not None:
        status = getattr(resp, "status_code", None)
        if isinstance(status, int):
            details["http_status"] = status
        url = getattr(resp, "url", None)
        if isinstance(url, str) and url:
            details["request_url"] = url
        # Try JSON body first, then fall back to text. Guard against bare mocks
        # whose .json() / .text return non-serializable proxies.
        body_json = None
        try:
            body_json = resp.json()
        except Exception:
            body_json = None
        captured = False
        if body_json is not None:
            try:
                json.dumps(body_json)
                details["response_body_json"] = body_json
                captured = True
            except (TypeError, ValueError):
                captured = False
        if not captured:
            try:
                text = resp.text
            except Exception:
                text = None
            if isinstance(text, str) and text:
                details["response_body_text"] = text[:2000]
        return details

    # Shape 3: parse the GarminConnectConnectionError string
    text = str(exc) or ""
    m = _API_ERROR_RE.search(text)
    if m:
        try:
            details["http_status"] = int(m.group(1))
        except (TypeError, ValueError):
            pass
        body = (m.group(2) or "").strip()
        if body:
            # Try to recognise embedded JSON dict-as-str produced by garminconnect formatter
            stripped = body
            if stripped.startswith("{") or stripped.startswith("["):
                try:
                    details["response_body_json"] = json.loads(stripped)
                except Exception:
                    details["response_body_text"] = stripped[:2000]
            else:
                details["response_body_text"] = stripped[:2000]
    return details


def _summarize_steps(steps: Any, _depth: int = 0) -> List[Dict[str, Any]]:
    """Produce a compact, sanitized summary of workout steps for error reports."""
    if _depth > 4 or not isinstance(steps, list):
        return []
    summary: List[Dict[str, Any]] = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        step_type = step.get("stepType") or {}
        end_cond = step.get("endCondition") or {}
        target = step.get("targetType") or {}
        entry: Dict[str, Any] = {
            "dto": step.get("type"),
            "stepOrder": step.get("stepOrder"),
        }
        if isinstance(step_type, dict) and step_type.get("stepTypeKey"):
            entry["stepTypeKey"] = step_type.get("stepTypeKey")
            entry["stepTypeId"] = step_type.get("stepTypeId")
        if isinstance(end_cond, dict) and end_cond.get("conditionTypeKey"):
            entry["endConditionKey"] = end_cond.get("conditionTypeKey")
            if step.get("endConditionValue") is not None:
                entry["endConditionValue"] = step.get("endConditionValue")
        if isinstance(target, dict) and target.get("workoutTargetTypeKey"):
            entry["targetTypeKey"] = target.get("workoutTargetTypeKey")
        if step.get("zoneNumber") is not None:
            entry["zoneNumber"] = step.get("zoneNumber")
        if step.get("numberOfIterations") is not None:
            entry["numberOfIterations"] = step.get("numberOfIterations")
        if step.get("type") == "RepeatGroupDTO":
            entry["children"] = _summarize_steps(step.get("workoutSteps"), _depth + 1)
        summary.append(entry)
    return summary


def _summarize_workout(workout_data: Any) -> Dict[str, Any]:
    """Sanitized payload summary safe to include in error messages."""
    if not isinstance(workout_data, dict):
        return {"_note": "workout_data was not a JSON object", "type": type(workout_data).__name__}

    sport = workout_data.get("sportType") or {}
    segments = workout_data.get("workoutSegments") or []
    summary: Dict[str, Any] = {
        "workoutName": workout_data.get("workoutName"),
        "sport": sport.get("sportTypeKey") if isinstance(sport, dict) else None,
        "segment_count": len(segments) if isinstance(segments, list) else None,
    }
    if isinstance(segments, list) and segments:
        first = segments[0] if isinstance(segments[0], dict) else {}
        summary["first_segment"] = {
            "segmentOrder": first.get("segmentOrder"),
            "step_count": len(first.get("workoutSteps") or []),
            "steps": _summarize_steps(first.get("workoutSteps")),
        }
    return summary


def _sport_key(workout_data: Any) -> Optional[str]:
    """Return the Garmin sportTypeKey from a workout-like payload."""
    if not isinstance(workout_data, dict):
        return None
    sport = workout_data.get("sportType")
    if isinstance(sport, dict):
        key = sport.get("sportTypeKey")
        return key if isinstance(key, str) and key else None
    return None


def _workout_id_from_payload(workout_data: Any) -> Optional[int]:
    """Extract a numeric workoutId from Garmin's common response shapes."""
    if not isinstance(workout_data, dict):
        return None
    for key in ("workoutId", "workout_id", "id"):
        value = workout_data.get(key)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _step_signature(step: Any) -> Dict[str, Any]:
    """Comparable, sanitized step signature for update verification."""
    if not isinstance(step, dict):
        return {"invalid_step_type": type(step).__name__}
    step_type = step.get("stepType") if isinstance(step.get("stepType"), dict) else {}
    end_condition = step.get("endCondition") if isinstance(step.get("endCondition"), dict) else {}
    target = step.get("targetType") if isinstance(step.get("targetType"), dict) else {}

    signature: Dict[str, Any] = {
        "dto": step.get("type"),
        "order": step.get("stepOrder"),
    }
    if step_type:
        signature["type"] = step_type.get("stepTypeKey")
    if end_condition:
        signature["end_condition"] = end_condition.get("conditionTypeKey")
    if step.get("endConditionValue") is not None:
        signature["end_condition_value"] = step.get("endConditionValue")
    if target:
        signature["target_type"] = target.get("workoutTargetTypeKey")
    for key in ("zoneNumber", "targetValueOne", "targetValueTwo"):
        if step.get(key) is not None:
            signature[key] = step.get(key)
    if step.get("type") == "RepeatGroupDTO":
        signature["repeat_count"] = step.get("numberOfIterations")
        signature["steps"] = [_step_signature(child) for child in step.get("workoutSteps") or []]
    return {k: v for k, v in signature.items() if v is not None}


def _workout_update_summary(workout_data: Any) -> Dict[str, Any]:
    """Comparable workout summary for in-place update verification."""
    if not isinstance(workout_data, dict):
        return {"valid": False, "type": type(workout_data).__name__}

    segments = workout_data.get("workoutSegments") or []
    summary: Dict[str, Any] = {
        "workout_id": _workout_id_from_payload(workout_data),
        "name": workout_data.get("workoutName"),
        "description": workout_data.get("description"),
        "sport": _sport_key(workout_data),
        "segment_count": len(segments) if isinstance(segments, list) else None,
        "segments": [],
    }
    if isinstance(segments, list):
        for segment in segments:
            if not isinstance(segment, dict):
                continue
            steps = segment.get("workoutSteps") or []
            segment_sport = segment.get("sportType") if isinstance(segment.get("sportType"), dict) else {}
            summary["segments"].append({
                "order": segment.get("segmentOrder"),
                "sport": segment_sport.get("sportTypeKey"),
                "step_count": len(steps) if isinstance(steps, list) else None,
                "steps": [_step_signature(step) for step in steps] if isinstance(steps, list) else [],
            })
    return {k: v for k, v in summary.items() if v is not None}


def _response_summary(response: Any) -> Dict[str, Any]:
    """Return a JSON-safe summary of a Garmin update response."""
    if isinstance(response, dict):
        return {
            k: v
            for k, v in {
                "type": "dict",
                "workout_id": _workout_id_from_payload(response),
                "name": response.get("workoutName"),
                "sport": _sport_key(response),
                "keys": sorted(response.keys()),
            }.items()
            if v is not None
        }
    status = getattr(response, "status_code", None)
    if isinstance(status, int):
        summary: Dict[str, Any] = {"type": type(response).__name__, "http_status": status}
        try:
            body = response.json()
        except Exception:
            body = None
        if isinstance(body, dict):
            summary["body_workout_id"] = _workout_id_from_payload(body)
            summary["body_keys"] = sorted(body.keys())
        return {k: v for k, v in summary.items() if v is not None}
    if response is None:
        return {"type": "none"}
    return {"type": type(response).__name__, "repr": repr(response)[:500]}


def _merge_workout_update(existing: Dict[str, Any], requested: Dict[str, Any], workout_id: int) -> Dict[str, Any]:
    """Merge caller-supplied workout fields into Garmin's existing full template.

    Garmin's update endpoint expects a full workout-shaped payload. Builders only
    produce the editable core fields, so preserve any Garmin metadata that came
    back from get_workout_by_id while replacing explicit caller fields.
    """
    merged = copy.deepcopy(existing)
    for key, value in requested.items():
        merged[key] = copy.deepcopy(value)
    merged["workoutId"] = int(workout_id)
    return merged


def _put_workout_template(client: Any, workout_id: int, payload: Dict[str, Any]) -> Any:
    """Update a Garmin workout template in place using the authenticated client."""
    update_method = None
    if "update_workout" in getattr(client, "__dict__", {}) or hasattr(type(client), "update_workout"):
        update_method = getattr(client, "update_workout", None)
    if callable(update_method):
        return update_method(int(workout_id), payload)

    http_client = getattr(client, "client", None)
    put = getattr(http_client, "put", None) if http_client is not None else None
    if not callable(put):
        raise RuntimeError(
            "Garmin client does not expose update_workout or client.put; "
            "template update is not supported by this client."
        )

    base = getattr(client, "garmin_workouts", "workout-service")
    if not isinstance(base, str) or not base:
        base = "workout-service"
    url = f"{base}/workout/{int(workout_id)}"
    return put("connectapi", url, json=payload, api=True)


def update_workout_template_payload(
    client: Any,
    workout_id: int,
    workout_data: Dict[str, Any],
    *,
    verify_after_update: bool = True,
    verbose: bool = False,
    validation_report: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Update an existing Garmin workout template in place.

    This helper intentionally mutates only the workout template endpoint. It
    never touches schedule/calendar endpoints and never deletes templates or
    historical data.
    """
    warnings: List[str] = []
    wid = int(workout_id)
    if not isinstance(workout_data, dict):
        return {
            "status": "error",
            "workout_id": wid,
            "message": "workout_data must be a JSON object",
            "warnings": warnings,
        }

    try:
        existing = client.get_workout_by_id(wid)
    except Exception as exc:
        return build_garmin_api_error(
            exc,
            operation="get_workout_by_id",
            endpoint=f"/workout-service/workout/{wid}",
            method="GET",
            extra={"workout_id": wid, "warnings": warnings},
        )

    if not isinstance(existing, dict) or not existing:
        return {
            "status": "error",
            "workout_id": wid,
            "message": f"No workout template found with ID {wid}.",
            "warnings": warnings,
        }
    existing_id = _workout_id_from_payload(existing)
    if existing_id is not None and existing_id != wid:
        return {
            "status": "error",
            "workout_id": wid,
            "message": f"Fetched workout ID {existing_id} did not match requested workout_id {wid}.",
            "before_summary": _workout_update_summary(existing),
            "warnings": warnings,
        }
    if existing.get("associatedActivityId") is not None:
        return {
            "status": "error",
            "workout_id": wid,
            "message": "Refusing to update a completed activity; update_workout_template only updates templates.",
            "before_summary": _workout_update_summary(existing),
            "warnings": warnings,
        }

    existing_sport = _sport_key(existing)
    requested_sport = _sport_key(workout_data)
    if existing_sport and requested_sport and existing_sport != requested_sport:
        return {
            "status": "error",
            "workout_id": wid,
            "message": (
                f"Refusing sport mismatch: existing workout sport is {existing_sport!r}, "
                f"requested sport is {requested_sport!r}."
            ),
            "before_summary": _workout_update_summary(existing),
            "requested_summary": _workout_update_summary(workout_data),
            "validation_report": validation_report,
            "warnings": warnings,
        }

    _fix_hr_zone_steps(workout_data)
    payload = _merge_workout_update(existing, workout_data, wid)
    before_summary = _workout_update_summary(existing)
    requested_summary = _workout_update_summary(payload)

    try:
        response = _put_workout_template(client, wid, payload)
    except Exception as exc:
        return build_garmin_api_error(
            exc,
            operation="update_workout_template",
            endpoint=f"/workout-service/workout/{wid}",
            method="PUT",
            workout_data=payload,
            extra={
                "workout_id": wid,
                "before_summary": before_summary,
                "validation_report": validation_report,
                "warnings": warnings,
            },
        )

    status_code = getattr(response, "status_code", None)
    if isinstance(status_code, int) and status_code >= 400:
        return {
            "status": "error",
            "workout_id": wid,
            "message": f"Garmin update failed with HTTP {status_code}.",
            "before_summary": before_summary,
            "validation_report": validation_report,
            "garmin_response_summary": _response_summary(response),
            "warnings": warnings,
        }

    after = None
    after_summary = None
    verification_issues: List[str] = []
    if verify_after_update:
        try:
            after = client.get_workout_by_id(wid)
        except Exception as exc:
            return build_garmin_api_error(
                exc,
                operation="verify_update_workout_template",
                endpoint=f"/workout-service/workout/{wid}",
                method="GET",
                extra={
                    "workout_id": wid,
                    "before_summary": before_summary,
                    "requested_summary": requested_summary,
                    "validation_report": validation_report,
                    "garmin_response_summary": _response_summary(response),
                    "warnings": warnings,
                },
            )
        after_summary = _workout_update_summary(after)
        if after_summary.get("workout_id") != wid:
            verification_issues.append("workout_id changed or was missing after update")
        for field in ("name", "sport", "description", "segment_count", "segments"):
            if after_summary.get(field) != requested_summary.get(field):
                verification_issues.append(f"{field} did not match requested payload after update")

    result: Dict[str, Any] = {
        "status": "partial" if verification_issues else "success",
        "workout_id": wid,
        "before_summary": before_summary,
        "after_summary": after_summary or requested_summary,
        "validation_report": validation_report,
        "garmin_response_summary": _response_summary(response),
        "warnings": warnings + verification_issues,
    }
    if verbose:
        result["requested_payload"] = payload
        if after is not None:
            result["after_workout"] = after
    return result


def _exception_chain(exc: BaseException) -> List[Dict[str, str]]:
    """Walk __cause__/__context__ chain to surface nested exceptions."""
    chain: List[Dict[str, str]] = []
    seen: set[int] = set()
    cur: Optional[BaseException] = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        chain.append({"type": type(cur).__name__, "message": str(cur)[:1500]})
        cur = cur.__cause__ or cur.__context__
    return chain


def build_garmin_api_error(
    exc: BaseException,
    *,
    operation: str,
    endpoint: Optional[str] = None,
    method: Optional[str] = None,
    workout_data: Any = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Assemble a rich Garmin API error report.

    Includes (when available):
      - operation label
      - HTTP method and endpoint
      - HTTP status code
      - Garmin response body (JSON if parsable, otherwise text)
      - sanitized payload summary (workout name + step summary)
      - nested exception chain
    """
    report: Dict[str, Any] = {
        "status": "error",
        "operation": operation,
        "error_type": type(exc).__name__,
        "message": str(exc) or repr(exc),
    }
    if method:
        report["request_method"] = method
    if endpoint:
        report["request_endpoint"] = endpoint

    response_details = _extract_response_details(exc)
    report.update(response_details)

    if workout_data is not None:
        report["workout"] = _summarize_workout(workout_data)

    chain = _exception_chain(exc)
    if len(chain) > 1:
        report["exception_chain"] = chain

    if extra:
        report.update(extra)
    return report


# =============================================================================
# RUNNING WORKOUT VALIDATION
# =============================================================================

# Canonical Garmin running workout DTO map (verified against live Garmin Connect API
# 2026-05; see also workout_builders.build_walk_run_json which has been working).
RUNNING_STEP_TYPES: Dict[str, int] = {
    "warmup": 1,
    "cooldown": 2,
    "interval": 3,
    "recovery": 4,
    "rest": 5,
    "repeat": 6,
}

# Bundle of (stepTypeId, stepTypeKey) pairs that are valid for running workouts.
# stepTypeId 5 (rest) and 6 (repeat) are mainly used internally / for strength.
RUNNING_VALID_STEP_KEYS = {"warmup", "cooldown", "interval", "recovery"}

END_CONDITION_TYPES: Dict[str, int] = {
    "lap.button": 1,
    "time": 2,
    "distance": 3,
    "iterations": 7,
    "reps": 10,
    "fixed.rest": 13,
}

TARGET_TYPES: Dict[str, int] = {
    "no.target": 1,
    "power.zone": 2,
    "cadence.zone": 3,
    "heart.rate.zone": 4,
    "speed.zone": 5,
    "pace.zone": 6,
}


def _validate_running_step(
    step: Dict[str, Any],
    issues: List[str],
    location: str,
    inside_repeat: bool = False,
) -> None:
    """Validate a single running workout step. Mutates `issues` in place."""
    if not isinstance(step, dict):
        issues.append(f"{location}: step is not a JSON object")
        return

    dto = step.get("type")
    if dto not in ("ExecutableStepDTO", "RepeatGroupDTO"):
        issues.append(f"{location}: step 'type' must be 'ExecutableStepDTO' or 'RepeatGroupDTO', got {dto!r}")
        return

    if not isinstance(step.get("stepOrder"), int):
        issues.append(f"{location}: 'stepOrder' must be an integer (got {step.get('stepOrder')!r})")

    if dto == "RepeatGroupDTO":
        if inside_repeat:
            issues.append(f"{location}: RepeatGroupDTO nested inside another RepeatGroupDTO is not supported")
        iterations = step.get("numberOfIterations")
        if not isinstance(iterations, int) or iterations < 1:
            issues.append(f"{location}: RepeatGroupDTO requires integer 'numberOfIterations' >= 1 (got {iterations!r})")
        children = step.get("workoutSteps") or []
        if not isinstance(children, list) or not children:
            issues.append(f"{location}: RepeatGroupDTO requires non-empty 'workoutSteps' list")
        else:
            # Children stepOrder must start at 1 and be sequential
            orders = [c.get("stepOrder") for c in children if isinstance(c, dict)]
            if orders != list(range(1, len(orders) + 1)):
                issues.append(
                    f"{location}: RepeatGroupDTO children 'stepOrder' must be 1,2,3,... in order; got {orders}"
                )
            for idx, child in enumerate(children, start=1):
                _validate_running_step(child, issues, f"{location}.workoutSteps[{idx}]", inside_repeat=True)
        return

    # ExecutableStepDTO
    step_type = step.get("stepType")
    if not isinstance(step_type, dict):
        issues.append(f"{location}: 'stepType' object is required (e.g. {{stepTypeId:3, stepTypeKey:'interval'}})")
        return
    key = step_type.get("stepTypeKey")
    if key not in RUNNING_VALID_STEP_KEYS:
        issues.append(
            f"{location}: stepTypeKey '{key}' is not a valid running step "
            f"(use one of {sorted(RUNNING_VALID_STEP_KEYS)})"
        )
    expected_id = RUNNING_STEP_TYPES.get(key)
    if expected_id is not None and step_type.get("stepTypeId") != expected_id:
        issues.append(
            f"{location}: stepTypeId {step_type.get('stepTypeId')!r} does not match stepTypeKey "
            f"'{key}' (expected stepTypeId={expected_id})"
        )

    end_cond = step.get("endCondition")
    if not isinstance(end_cond, dict):
        issues.append(f"{location}: 'endCondition' object is required (e.g. {{conditionTypeId:2, conditionTypeKey:'time'}})")
    else:
        ec_key = end_cond.get("conditionTypeKey")
        if ec_key not in {"time", "distance", "lap.button", "fixed.rest"}:
            issues.append(
                f"{location}: endCondition.conditionTypeKey '{ec_key}' unusual for running; "
                f"expected 'time', 'distance', or 'lap.button'"
            )
        expected_ec = END_CONDITION_TYPES.get(ec_key)
        if expected_ec is not None and end_cond.get("conditionTypeId") != expected_ec:
            issues.append(
                f"{location}: endCondition.conditionTypeId {end_cond.get('conditionTypeId')!r} "
                f"does not match conditionTypeKey '{ec_key}' (expected {expected_ec})"
            )
        if ec_key in {"time", "distance"} and not isinstance(step.get("endConditionValue"), (int, float)):
            issues.append(
                f"{location}: endConditionValue must be a number for endCondition '{ec_key}' "
                f"(got {step.get('endConditionValue')!r})"
            )

    target = step.get("targetType")
    if not isinstance(target, dict):
        issues.append(f"{location}: 'targetType' object is required (use {{workoutTargetTypeId:1, workoutTargetTypeKey:'no.target'}} for no target)")
    else:
        t_key = target.get("workoutTargetTypeKey")
        expected_t = TARGET_TYPES.get(t_key)
        if expected_t is not None and target.get("workoutTargetTypeId") != expected_t:
            issues.append(
                f"{location}: targetType.workoutTargetTypeId {target.get('workoutTargetTypeId')!r} "
                f"does not match workoutTargetTypeKey '{t_key}' (expected {expected_t})"
            )
        if t_key == "heart.rate.zone":
            zone = step.get("zoneNumber")
            v1 = step.get("targetValueOne")
            v2 = step.get("targetValueTwo")
            if zone is None and v1 is None and v2 is None:
                issues.append(
                    f"{location}: heart.rate.zone target requires either 'zoneNumber' (1-5) "
                    f"or 'targetValueOne'/'targetValueTwo' as a custom bpm range"
                )
            elif zone is not None and not (isinstance(zone, int) and 1 <= zone <= 5):
                issues.append(f"{location}: zoneNumber must be an integer between 1 and 5 (got {zone!r})")


def validate_running_workout_data(workout_data: Any) -> Dict[str, Any]:
    """Validate a running workout payload locally and return a structured report.

    Returns:
        {
            "ok": bool,
            "issues": [str, ...],   # human-readable validation problems
            "summary": {...}         # sanitized summary of the workout
        }
    """
    issues: List[str] = []
    if not isinstance(workout_data, dict):
        return {"ok": False, "issues": ["workout_data must be a JSON object"], "summary": {}}

    if not workout_data.get("workoutName"):
        issues.append("'workoutName' is required")

    sport = workout_data.get("sportType")
    if not isinstance(sport, dict):
        issues.append("'sportType' object is required (e.g. {sportTypeId:1, sportTypeKey:'running'})")
    elif sport.get("sportTypeKey") != "running":
        issues.append(
            f"sportTypeKey '{sport.get('sportTypeKey')}' is not 'running'; "
            f"validate_running_workout_data is for running workouts only"
        )
    elif sport.get("sportTypeId") != 1:
        issues.append(f"sportTypeId {sport.get('sportTypeId')!r} should be 1 for running")

    segments = workout_data.get("workoutSegments")
    if not isinstance(segments, list) or not segments:
        issues.append("'workoutSegments' must be a non-empty list")
    else:
        for i, seg in enumerate(segments, start=1):
            if not isinstance(seg, dict):
                issues.append(f"workoutSegments[{i}]: segment is not a JSON object")
                continue
            if seg.get("segmentOrder") != i:
                issues.append(
                    f"workoutSegments[{i}]: 'segmentOrder' should be {i} (got {seg.get('segmentOrder')!r})"
                )
            seg_sport = seg.get("sportType")
            if not isinstance(seg_sport, dict) or seg_sport.get("sportTypeKey") != "running":
                issues.append(f"workoutSegments[{i}]: segment 'sportType' must be running")
            steps = seg.get("workoutSteps")
            if not isinstance(steps, list) or not steps:
                issues.append(f"workoutSegments[{i}]: 'workoutSteps' must be a non-empty list")
            else:
                orders = [s.get("stepOrder") for s in steps if isinstance(s, dict)]
                if orders != list(range(1, len(orders) + 1)):
                    issues.append(
                        f"workoutSegments[{i}]: top-level 'stepOrder' must be 1,2,3,... in order; got {orders}"
                    )
                for j, step in enumerate(steps, start=1):
                    _validate_running_step(step, issues, f"workoutSegments[{i}].workoutSteps[{j}]")

    return {
        "ok": not issues,
        "issues": issues,
        "summary": _summarize_workout(workout_data),
    }


# =============================================================================
# CURATION HELPERS (unchanged below)
# =============================================================================


def _fix_hr_zone_step(step: dict) -> None:
    """Fix a common mistake where HR zone targets use targetValueOne instead of zoneNumber.

    When targetType is heart.rate.zone and a named zone is intended, Garmin expects
    zoneNumber (1-5). If targetValueOne is set to a small integer (1-5) and zoneNumber
    is missing, this is almost certainly a zone number, not an absolute HR value.

    Custom HR bpm ranges (e.g. targetValueOne=105, targetValueTwo=143) are left
    unchanged — these are legitimate custom heart rate targets in Garmin Connect.
    """
    target_type = step.get('targetType', {})
    target_key = target_type.get('workoutTargetTypeKey', '')

    if target_key == 'heart.rate.zone' and 'zoneNumber' not in step:
        zone = step.get('targetValueOne')
        if zone is not None and 1 <= zone <= 5:
            step['zoneNumber'] = int(zone)
            step.pop('targetValueOne', None)
            step.pop('targetValueTwo', None)

    # Recurse into nested steps (RepeatGroupDTO)
    for nested in step.get('workoutSteps', []):
        _fix_hr_zone_step(nested)


def _fix_hr_zone_steps(workout_data: dict) -> None:
    """Walk all workout steps and fix HR zone target mistakes."""
    for segment in workout_data.get('workoutSegments', []):
        for step in segment.get('workoutSteps', []):
            _fix_hr_zone_step(step)


def _curate_workout_summary(workout: dict) -> dict:
    """Extract essential workout metadata for list views"""
    sport_type = workout.get('sportType', {})

    summary = {
        "id": workout.get('workoutId'),
        "name": workout.get('workoutName'),
        "sport": sport_type.get('sportTypeKey'),
        "provider": workout.get('workoutProvider'),
        "created_date": workout.get('createdDate'),
        "updated_date": workout.get('updatedDate'),
    }

    # Add optional fields if present
    if workout.get('description'):
        summary['description'] = workout.get('description')

    if workout.get('estimatedDuration'):
        summary['estimated_duration_seconds'] = workout.get('estimatedDuration')

    if workout.get('estimatedDistance'):
        summary['estimated_distance_meters'] = workout.get('estimatedDistance')

    # Remove None values
    return {k: v for k, v in summary.items() if v is not None}


def _curate_step_target(
    curated: dict,
    step: dict,
    target_field: str,
    value_one_field: str,
    value_two_field: str,
    zone_field: str,
    prefix: str = "",
) -> None:
    """Curate a workout target block, handling Garmin null target payloads safely."""
    target_type = step.get(target_field)
    if not isinstance(target_type, dict):
        target_type = {}
    target_key = target_type.get('workoutTargetTypeKey')

    if not target_key or target_key == 'no.target':
        return

    curated[f'{prefix}target_type'] = target_key

    if step.get(value_one_field) is not None:
        curated[f'{prefix}target_value_low'] = step.get(value_one_field)
    if step.get(value_two_field) is not None:
        curated[f'{prefix}target_value_high'] = step.get(value_two_field)
    if step.get(zone_field) is not None:
        curated[f'{prefix}target_zone'] = step.get(zone_field)


def _curate_workout_step(step: dict) -> dict:
    """Extract essential workout step information"""
    step_type = step.get('stepType') or {}
    end_condition = step.get('endCondition') or {}

    curated = {
        "order": step.get('stepOrder'),
        "type": step_type.get('stepTypeKey'),  # warmup, interval, cooldown, rest, recover
    }

    # Description
    if step.get('description'):
        curated['description'] = step.get('description')

    # End condition (duration/distance/lap press)
    if end_condition.get('conditionTypeKey'):
        curated['end_condition'] = end_condition.get('conditionTypeKey')
    if step.get('endConditionValue'):
        # Value meaning depends on condition type (seconds for time, meters for distance)
        curated['end_condition_value'] = step.get('endConditionValue')

    # Primary target (heart rate, pace, power, etc.)
    _curate_step_target(
        curated,
        step,
        target_field='targetType',
        value_one_field='targetValueOne',
        value_two_field='targetValueTwo',
        zone_field='zoneNumber',
    )

    # Swim workouts often store pace prescriptions as secondary targets.
    _curate_step_target(
        curated,
        step,
        target_field='secondaryTargetType',
        value_one_field='secondaryTargetValueOne',
        value_two_field='secondaryTargetValueTwo',
        zone_field='secondaryZoneNumber',
        prefix='secondary_',
    )

    # Strength training exercise info
    if step.get('category'):
        curated['category'] = step.get('category')
    if step.get('exerciseName'):
        curated['exercise_name'] = step.get('exerciseName')
    if step.get('weightValue') is not None:
        curated['weight_value'] = step.get('weightValue')
        weight_unit = step.get('weightUnit', {})
        if weight_unit and weight_unit.get('unitKey'):
            curated['weight_unit'] = weight_unit.get('unitKey')

    # Repeat info for repeat steps
    if step.get('type') == 'RepeatGroupDTO':
        curated['repeat_count'] = step.get('numberOfIterations')
        nested_steps = step.get('workoutSteps', [])
        if nested_steps:
            curated['steps'] = [_curate_workout_step(s) for s in nested_steps]
            curated['step_count'] = len(nested_steps)

    return {k: v for k, v in curated.items() if v is not None}


def _curate_workout_segment(segment: dict) -> dict:
    """Extract essential segment information including workout steps"""
    sport_type = segment.get('sportType', {})

    curated = {
        "order": segment.get('segmentOrder'),
        "sport": sport_type.get('sportTypeKey'),
    }

    # Estimated metrics
    if segment.get('estimatedDurationInSecs'):
        curated['estimated_duration_seconds'] = segment.get('estimatedDurationInSecs')
    if segment.get('estimatedDistanceInMeters'):
        curated['estimated_distance_meters'] = segment.get('estimatedDistanceInMeters')

    # Workout steps - the actual content of the segment
    steps = segment.get('workoutSteps', [])
    if steps:
        curated['steps'] = [_curate_workout_step(s) for s in steps]
        curated['step_count'] = len(steps)

    return {k: v for k, v in curated.items() if v is not None}


def _curate_workout_details(workout: dict) -> dict:
    """Extract detailed workout information with segments

    Handles both regular workouts (from get_workout_by_id) and training plan workouts
    (from fbt-adaptive endpoint) which use slightly different field names.
    """
    sport_type = workout.get('sportType') or {}

    details = {
        "id": workout.get('workoutId'),
        "uuid": workout.get('workoutUuid'),
        "name": workout.get('workoutName'),
        "sport": sport_type.get('sportTypeKey') if sport_type else None,
        "provider": workout.get('workoutProvider'),
        "created_date": workout.get('createdDate'),
        "updated_date": workout.get('updatedDate'),
    }

    # Optional fields
    if workout.get('description'):
        details['description'] = workout.get('description')

    # Handle both field name variants (regular vs training plan workouts)
    duration = workout.get('estimatedDuration') or workout.get('estimatedDurationInSecs')
    if duration:
        details['estimated_duration_seconds'] = duration

    distance = workout.get('estimatedDistance') or workout.get('estimatedDistanceInMeters')
    if distance:
        details['estimated_distance_meters'] = distance

    if workout.get('avgTrainingSpeed'):
        details['avg_training_speed_mps'] = workout.get('avgTrainingSpeed')

    # Training plan specific fields
    if workout.get('workoutPhrase'):
        details['workout_type'] = workout.get('workoutPhrase')

    if workout.get('trainingEffectLabel'):
        details['training_effect_label'] = workout.get('trainingEffectLabel')

    if workout.get('estimatedTrainingEffect'):
        details['estimated_training_effect'] = workout.get('estimatedTrainingEffect')

    # Curate segments with workout steps
    segments = workout.get('workoutSegments', [])
    if segments:
        details['segments'] = [_curate_workout_segment(seg) for seg in segments]
        details['segment_count'] = len(segments)

    # Remove None values
    return {k: v for k, v in details.items() if v is not None}


def _detect_scheduled_workout_source(scheduled: dict) -> str:
    """Best-effort classification of the source of a scheduled calendar entry.

    Returns one of:
    - "garmin_coach": Garmin Coach generated workout
    - "training_plan": User-enrolled training plan
    - "user_workout": A workout the user uploaded/created and scheduled themselves
    - "unknown": Cannot determine
    """
    # Garmin Coach entries usually expose workoutPhrase (training intent code) and
    # have no user-owned workoutId, but do have a workoutUuid.
    coach_markers = (
        scheduled.get("garminCoach"),
        scheduled.get("coachId"),
        scheduled.get("coachWorkoutId"),
        scheduled.get("coachPlanId"),
    )
    if any(coach_markers):
        return "garmin_coach"

    plan_name = scheduled.get("tpPlanName") or scheduled.get("planName")
    if plan_name:
        return "training_plan"

    phrase = scheduled.get("workoutPhrase")
    workout_id = scheduled.get("workoutId")
    uuid_val = scheduled.get("workoutUuid")

    if workout_id:
        # User-owned templates always have a numeric workoutId.
        return "user_workout"

    if phrase and uuid_val and not workout_id:
        # Garmin Coach entries typically have phrase + uuid but no numeric id.
        return "garmin_coach"

    if uuid_val and not workout_id:
        # UUID-only entries usually come from a training plan or coach.
        return "training_plan"

    return "unknown"


def _curate_scheduled_workout(scheduled: dict) -> dict:
    """Extract essential scheduled workout information from GraphQL response.

    Returns a dict with the fields needed for refresh / unschedule workflows:
    - date
    - scheduled_workout_id (calendar entry id, required for unscheduling)
    - workout_id (template id; may be None for Coach/plan entries)
    - workout_uuid (when applicable)
    - name, sport, completed, estimated_duration_seconds
    - source: one of "user_workout" | "garmin_coach" | "training_plan" | "unknown"
    - raw_identifiers: the unaltered ID fields Garmin returned, so callers can
      pass them straight to unschedule_workout.
    """
    # GraphQL response has workout data at top level (not nested)
    # Completed is determined by presence of associatedActivityId
    is_completed = scheduled.get('associatedActivityId') is not None

    scheduled_workout_id = (
        scheduled.get("scheduledWorkoutId")
        or scheduled.get("workoutScheduleId")
        or scheduled.get("scheduleId")
        or scheduled.get("calendarWorkoutId")
        or scheduled.get("id")
    )

    summary = {
        "date": scheduled.get('scheduleDate'),
        "scheduled_workout_id": scheduled_workout_id,
        "workout_uuid": scheduled.get('workoutUuid'),
        "workout_id": scheduled.get('workoutId'),
        "name": scheduled.get('workoutName'),
        "sport": scheduled.get('workoutType'),
        "completed": is_completed,
        "source": _detect_scheduled_workout_source(scheduled),
    }

    # Training plan info
    if scheduled.get('tpPlanName'):
        summary['training_plan'] = scheduled.get('tpPlanName')

    # Workout type description (e.g., "AEROBIC_LOW_SHORTAGE_BASE", "ANAEROBIC_SPEED", "LONG_WORKOUT")
    # This describes the intent/type of the workout from Garmin Coach
    if scheduled.get('workoutPhrase'):
        summary['workout_type'] = scheduled.get('workoutPhrase')

    # Rest day and race day flags
    if scheduled.get('isRestDay'):
        summary['is_rest_day'] = True
    if scheduled.get('race'):
        summary['is_race_day'] = True

    # Optional fields
    if scheduled.get('estimatedDurationInSecs'):
        summary['estimated_duration_seconds'] = scheduled.get('estimatedDurationInSecs')

    if scheduled.get('estimatedDistanceInMeters'):
        summary['estimated_distance_meters'] = scheduled.get('estimatedDistanceInMeters')

    # If completed, include the activity ID
    if is_completed:
        summary['activity_id'] = scheduled.get('associatedActivityId')

    # Always include raw identifiers needed to safely unschedule the entry,
    # even when some are None, so downstream tools have explicit context.
    raw_identifiers = {
        "scheduledWorkoutId": scheduled.get("scheduledWorkoutId"),
        "workoutScheduleId": scheduled.get("workoutScheduleId"),
        "scheduleId": scheduled.get("scheduleId"),
        "calendarWorkoutId": scheduled.get("calendarWorkoutId"),
        "id": scheduled.get("id"),
        "workoutId": scheduled.get("workoutId"),
        "workoutUuid": scheduled.get("workoutUuid"),
        "associatedActivityId": scheduled.get("associatedActivityId"),
    }
    summary["raw_identifiers"] = {k: v for k, v in raw_identifiers.items() if v is not None}

    # Remove None values from the rest of the summary
    return {k: v for k, v in summary.items() if v is not None}


def register_tools(app):
    """Register all workout-related tools with the MCP server app"""

    @app.tool()
    async def get_workouts() -> str:
        """Get all workouts with curated summary list

        Returns a count and list of workout summaries with essential metadata only.
        For detailed workout information including segments, use get_workout_by_id.
        """
        try:
            workouts = garmin_client.get_workouts()
            if not workouts:
                return "No workouts found."

            # Curate the workout list
            curated = {
                "count": len(workouts),
                "workouts": [_curate_workout_summary(w) for w in workouts]
            }

            return json.dumps(curated, indent=2)
        except Exception as e:
            return f"Error retrieving workouts: {str(e)}"

    @app.tool()
    async def get_workout_by_id(workout_id: Union[int, str]) -> str:
        """Get detailed information for a specific workout

        Returns workout details including segments and step structure.

        Accepts either:
        - Numeric workout ID (from get_workouts or get_scheduled_workouts)
        - Workout UUID (from get_training_plan_workouts for Garmin Coach workouts)

        Args:
            workout_id: Workout ID (numeric) or UUID (for training plan workouts)
        """
        try:
            workout_id_str = str(workout_id)
            # Detect if this is a UUID (contains dashes) or numeric ID
            is_uuid = '-' in workout_id_str

            if is_uuid:
                # Training plan / Garmin Coach workout - use fbt-adaptive endpoint
                url = f"workout-service/fbt-adaptive/{workout_id_str}"
                workout = garmin_client.connectapi(url)
            else:
                # Regular workout - use standard endpoint
                workout = garmin_client.get_workout_by_id(int(workout_id_str))

            if not workout:
                return f"No workout found with ID {workout_id_str}."

            # Return curated details with segments
            curated = _curate_workout_details(workout)
            return json.dumps(curated, indent=2)
        except Exception as e:
            return f"Error retrieving workout: {str(e)}"

    @app.tool()
    async def download_workout(workout_id: int) -> str:
        """Download a workout as a FIT file

        Downloads the workout in FIT format. The binary data cannot be returned
        directly through the MCP interface, but this confirms the workout is available.

        Args:
            workout_id: ID of the workout to download
        """
        try:
            workout_data = garmin_client.download_workout(workout_id)
            if not workout_data:
                return f"No workout data found for workout with ID {workout_id}."

            # Return information about the download
            data_size = len(workout_data) if isinstance(workout_data, (bytes, bytearray)) else 0
            return json.dumps({
                "workout_id": workout_id,
                "format": "FIT",
                "size_bytes": data_size,
                "message": "Workout data is available in FIT format. Use Garmin Connect API to save to file."
            }, indent=2)
        except Exception as e:
            return f"Error downloading workout: {str(e)}"

    @app.tool()
    async def upload_workout(workout_data: dict) -> str:
        """Upload a workout from JSON data

        Creates a new workout in Garmin Connect from structured workout data.

        IMPORTANT: Step types must use Garmin's DTO format:
        - Use "ExecutableStepDTO" for regular steps (warmup, interval, cooldown, recovery)
        - Use "RepeatGroupDTO" for repeat/interval groups with numberOfIterations

        IMPORTANT: Heart rate targets come in two forms:
        - Named zone (e.g. Zone 2): set targetType to "heart.rate.zone" and use "zoneNumber" (1-5).
          Do NOT put the zone number in targetValueOne.
        - Custom HR range (e.g. 105-143 bpm): set targetType to "heart.rate.zone" and use
          "targetValueOne" (low bpm) / "targetValueTwo" (high bpm). Do NOT set "zoneNumber".
          This matches Garmin Connect's "Custom" heart rate target.
        For non-HR targets (pace, power, cadence), use targetValueOne/targetValueTwo directly.

        Note: a safety check converts targetValueOne 1-5 to zoneNumber when zoneNumber is missing,
        to catch the common mistake of putting a zone index in targetValueOne. Typical bpm values
        (e.g. 105, 143) are not affected.

        IMPORTANT: Sport type IDs for workouts (different from activity API!):
        - 1 = running, 2 = cycling, 5 = strength_training, 6 = cardio, 11 = walking

        **Available Templates:**
        Instead of building workout JSON from scratch, you can use these MCP resources as starting points:
        - workout://templates/simple-run - Basic warmup/run/cooldown structure
        - workout://templates/interval-running - Interval training with repeat groups
        - workout://templates/tempo-run - Tempo run with heart rate zone targets
        - workout://templates/progression-run - 15 easy / 15 Z3 / 15 Z4 / 5 cooldown
        - workout://templates/tempo-blocks - 10 wu + 3 x (8 Z4 + 3 Z2) + 5 cd (RepeatGroupDTO)
        - workout://templates/strength-circuit - Strength training with exercises, reps, rest
        - workout://reference/structure - Complete JSON structure reference with all fields

        For structured running workouts, prefer the high-level helpers:
        - validate_running_workout / preview_running_workout - lint a payload offline
        - create_running_workout - build canonical JSON and upload in one call

        On upload failure the response includes http_status, the Garmin response body
        (text or JSON when available), the request endpoint/method, a sanitized step
        summary, and the nested exception chain so you can self-correct without
        guessing.

        Access these resources using your MCP client's resource reading capability, modify the template
        as needed, and pass the resulting JSON as the workout_data parameter.

        **Strength training workouts** require these additional fields on each exercise step:
        - "category": exercise category (e.g. "BENCH_PRESS", "PULL_UP", "CURL", "SHOULDER_PRESS",
          "ROW", "SQUAT", "DEADLIFT", "TRICEPS_EXTENSION", "PLANK", "LUNGE", "CARDIO")
        - "exerciseName": specific exercise (e.g. "BARBELL_BENCH_PRESS", "PULL_UP",
          "DUMBBELL_BICEPS_CURL", "DUMBBELL_SHOULDER_PRESS", "BENT_OVER_ROW_WITH_DUMBELL",
          "BODY_WEIGHT_DIP", "BARBELL_SQUAT", "BARBELL_DEADLIFT")
        - "weightValue" (optional): weight as number (e.g. 24.0)
        - "weightUnit" (optional): {"unitId": 8, "unitKey": "kilogram", "factor": 1000.0}
        Use endCondition reps (conditionTypeId: 10) for exercises, rest (stepTypeId: 5) between sets.

        Example strength exercise step:
        {
            "type": "ExecutableStepDTO",
            "stepOrder": 1,
            "stepType": {"stepTypeId": 3, "stepTypeKey": "interval"},
            "endCondition": {"conditionTypeId": 10, "conditionTypeKey": "reps"},
            "endConditionValue": 10.0,
            "targetType": {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"},
            "category": "BENCH_PRESS",
            "exerciseName": "BARBELL_BENCH_PRESS",
            "weightValue": 60.0,
            "weightUnit": {"unitId": 8, "unitKey": "kilogram", "factor": 1000.0}
        }

        Example running workout with HR zone target:
        {
            "workoutName": "My Workout",
            "sportType": {"sportTypeId": 1, "sportTypeKey": "running"},
            "workoutSegments": [{
                "segmentOrder": 1,
                "sportType": {"sportTypeId": 1, "sportTypeKey": "running"},
                "workoutSteps": [{
                    "type": "ExecutableStepDTO",
                    "stepOrder": 1,
                    "stepType": {"stepTypeId": 3, "stepTypeKey": "interval"},
                    "endCondition": {"conditionTypeId": 2, "conditionTypeKey": "time"},
                    "endConditionValue": 1200.0,
                    "targetType": {"workoutTargetTypeId": 4, "workoutTargetTypeKey": "heart.rate.zone"},
                    "zoneNumber": 3
                }]
            }]
        }

        Args:
            workout_data: Dictionary containing workout structure (name, sport type, segments, etc.)
        """
        try:
            # Fix common mistake: HR zone targets using targetValueOne instead of zoneNumber
            _fix_hr_zone_steps(workout_data)

            # Pass dict directly - library handles conversion
            result = garmin_client.upload_workout(workout_data)

            # Curate the response
            if isinstance(result, dict):
                curated = {
                    "status": "success",
                    "workout_id": result.get('workoutId'),
                    "name": result.get('workoutName'),
                    "message": "Workout uploaded successfully"
                }
                # Remove None values
                curated = {k: v for k, v in curated.items() if v is not None}
                return json.dumps(curated, indent=2)

            return json.dumps(result, indent=2)
        except Exception as e:
            return json.dumps(
                build_garmin_api_error(
                    e,
                    operation="upload_workout",
                    endpoint="/workout-service/workout",
                    method="POST",
                    workout_data=workout_data,
                ),
                indent=2,
            )

    @app.tool()
    async def upload_workouts(workouts: list[dict]) -> str:
        """Upload multiple workouts from JSON data in a single call

        Creates multiple new workouts in Garmin Connect. Each item in the list
        uses the same structure as upload_workout.

        IMPORTANT: Step types must use Garmin's DTO format:
        - Use "ExecutableStepDTO" for regular steps (warmup, interval, cooldown, recovery)
        - Use "RepeatGroupDTO" for repeat/interval groups with numberOfIterations

        IMPORTANT: For heart rate zone targets, use "zoneNumber" (1-5), NOT targetValueOne/targetValueTwo.

        Args:
            workouts: List of workout dictionaries, each containing workout structure
                      (name, sport type, segments, etc.) — same format as upload_workout.
        """
        results = []
        for workout_data in workouts:
            try:
                _fix_hr_zone_steps(workout_data)
                result = garmin_client.upload_workout(workout_data)
                if isinstance(result, dict):
                    entry = {
                        "status": "success",
                        "workout_id": result.get('workoutId'),
                        "name": result.get('workoutName'),
                        "message": "Workout uploaded successfully"
                    }
                    results.append({k: v for k, v in entry.items() if v is not None})
                else:
                    results.append({"status": "success", "message": "Workout uploaded successfully"})
            except Exception as e:
                error_report = build_garmin_api_error(
                    e,
                    operation="upload_workout",
                    endpoint="/workout-service/workout",
                    method="POST",
                    workout_data=workout_data,
                )
                # Reshape into the bulk-results envelope while preserving rich details
                results.append({
                    **error_report,
                    "name": workout_data.get('workoutName'),
                })

        total = len(results)
        succeeded = sum(1 for r in results if r["status"] == "success")
        return json.dumps({
            "total": total,
            "succeeded": succeeded,
            "failed": total - succeeded,
            "results": results
        }, indent=2)

    @app.tool()
    async def update_workout_template(
        workout_id: int,
        workout_data: dict,
        verify_after_update: bool = True,
        verbose: bool = False,
    ) -> str:
        """Update an existing Garmin workout template in place.

        This updates the Garmin Connect workout template identified by
        workout_id using a PUT-style template update. It preserves the same
        workout_id and does not touch scheduled calendar entries, completed
        activities, health data, gear, goals, badges, body composition, or
        nutrition logs.

        Safety behavior:
        - Fetches the existing workout first and refuses missing/non-template data.
        - Refuses sport mismatches between existing and requested payload.
        - Preserves Garmin fields from the existing template that are absent from
          workout_data.
        - Never calls schedule, unschedule, refresh, or delete endpoints.

        Args:
            workout_id: Existing Garmin workout template ID.
            workout_data: Full or partial workout-shaped payload containing the
                          requested template fields/segments.
            verify_after_update: Fetch after update and compare identity/name/
                                 sport/description/segment/step structure.
            verbose: Include requested payload and fetched after-workout details.
        """
        result = update_workout_template_payload(
            garmin_client,
            workout_id,
            workout_data,
            verify_after_update=verify_after_update,
            verbose=verbose,
        )
        return json.dumps(result, indent=2, default=str)

    @app.tool()
    async def validate_running_workout(workout_data: dict) -> str:
        """Validate a running workout payload locally before upload.

        Performs structural checks against the canonical Garmin Connect running schema:
          - sportType must be running (sportTypeId=1, sportTypeKey='running')
          - each step uses ExecutableStepDTO or RepeatGroupDTO
          - stepType/stepTypeKey pairs are consistent (warmup=1, cooldown=2,
            interval=3, recovery=4)
          - endCondition and targetType objects are present and consistent
          - heart.rate.zone targets carry either a zoneNumber (1-5) or a custom
            targetValueOne/targetValueTwo bpm range
          - RepeatGroupDTO has numberOfIterations>=1 and non-empty workoutSteps
          - segmentOrder and stepOrder are 1-based and sequential

        Returns a JSON report with {"ok", "issues", "summary"}. This does NOT call
        Garmin; it is safe to use offline.

        Args:
            workout_data: Workout JSON to validate
        """
        report = validate_running_workout_data(workout_data)
        return json.dumps(report, indent=2)

    @app.tool()
    async def preview_running_workout(workout_data: dict) -> str:
        """Preview a running workout payload without uploading.

        Returns the sanitized step summary, the canonical step counts, and any
        validation issues. Mirrors preview_strength_workout's role for running.
        Useful to verify the structure an LLM is about to upload.

        Args:
            workout_data: Workout JSON to preview
        """
        report = validate_running_workout_data(workout_data)
        preview = {
            "status": "success" if report["ok"] else "invalid",
            "valid": report["ok"],
            "issues": report["issues"],
            "summary": report["summary"],
        }
        # Surface the schema cheat sheet on invalid previews so the next
        # attempt can be self-corrected without re-fetching help.
        if not report["ok"]:
            preview["expected_step_types"] = RUNNING_STEP_TYPES
            preview["expected_target_types"] = TARGET_TYPES
            preview["expected_end_conditions"] = END_CONDITION_TYPES
        return json.dumps(preview, indent=2)

    @app.tool()
    async def delete_workout(workout_id: int) -> str:
        """Delete a workout from Garmin Connect

        Permanently removes a workout from your Garmin Connect workout library.

        Args:
            workout_id: ID of the workout to delete (get IDs from get_workouts)
        """
        try:
            url = f"{garmin_client.garmin_workouts}/workout/{workout_id}"
            response = garmin_client.client.delete("connectapi", url, api=True)

            if response.status_code == 204 or response.status_code == 200:
                return json.dumps({
                    "status": "success",
                    "workout_id": workout_id,
                    "message": f"Workout {workout_id} deleted successfully"
                }, indent=2)
            else:
                return json.dumps({
                    "status": "failed",
                    "workout_id": workout_id,
                    "http_status": response.status_code,
                    "message": f"Failed to delete workout: HTTP {response.status_code}"
                }, indent=2)
        except Exception as e:
            return f"Error deleting workout: {str(e)}"

    @app.tool()
    async def delete_workouts(workout_ids: list[int]) -> str:
        """Delete multiple workouts from Garmin Connect in a single call

        Permanently removes multiple workouts from your Garmin Connect workout library.

        Args:
            workout_ids: List of workout IDs to delete (get IDs from get_workouts)
        """
        results = []
        for workout_id in workout_ids:
            try:
                url = f"{garmin_client.garmin_workouts}/workout/{workout_id}"
                response = garmin_client.client.delete("connectapi", url, api=True)

                if response.status_code in (200, 204):
                    results.append({
                        "status": "success",
                        "workout_id": workout_id,
                        "message": f"Workout {workout_id} deleted successfully"
                    })
                else:
                    results.append({
                        "status": "failed",
                        "workout_id": workout_id,
                        "http_status": response.status_code,
                        "message": f"Failed to delete workout: HTTP {response.status_code}"
                    })
            except Exception as e:
                results.append({
                    "status": "error",
                    "workout_id": workout_id,
                    "message": f"Error deleting workout: {str(e)}"
                })

        total = len(results)
        succeeded = sum(1 for r in results if r["status"] == "success")
        return json.dumps({
            "total": total,
            "succeeded": succeeded,
            "failed": total - succeeded,
            "results": results
        }, indent=2)

    @app.tool()
    async def get_scheduled_workouts(start_date: str, end_date: str, verbose: bool = False) -> str:
        """Get scheduled workouts between two dates with curated summary list.

        Returns workouts that have been scheduled on the Garmin Connect calendar
        across all sports (running, cycling, strength, cardio, walking, etc.),
        including:
            - date
            - scheduled_workout_id  (calendar entry id; required for unscheduling)
            - workout_id            (template id; may be None for Coach/plan items)
            - workout_uuid          (when applicable)
            - name, sport, completed, estimated_duration_seconds
            - source                ("user_workout" | "garmin_coach" |
                                     "training_plan" | "unknown")
            - raw_identifiers       (every Garmin id field for downstream safety)

        Pass scheduled_workout_id to unschedule_workout to remove just the calendar
        entry. For a full date-range rebuild use refresh_workout_calendar.

        Args:
            start_date: Start date in YYYY-MM-DD format
            end_date: End date in YYYY-MM-DD format
            verbose: Include full curated optional fields when true; compact JSON by default.
        """
        try:
            # Query for scheduled workouts using GraphQL
            query = {
                "query": f'query{{workoutScheduleSummariesScalar(startDate:"{start_date}", endDate:"{end_date}")}}'
            }
            result = garmin_client.query_garmin_graphql(query)

            if not result or "data" not in result:
                return json.dumps({"status": "error", "message": "No scheduled workouts found or error querying data.", "scheduled_workouts": []}, indent=2)

            scheduled = result.get("data", {}).get("workoutScheduleSummariesScalar", [])

            if not scheduled:
                return json.dumps({"status": "success", "count": 0, "date_range": {"start": start_date, "end": end_date}, "scheduled_workouts": []}, indent=2)

            # Curate the scheduled workout list
            curated = {
                "status": "success",
                "count": len(scheduled),
                "date_range": {"start": start_date, "end": end_date},
                "scheduled_workouts": [_curate_scheduled_workout(s) for s in scheduled]
            }

            return json.dumps(curated, indent=2)
        except Exception as e:
            return f"Error retrieving scheduled workouts: {str(e)}"

    @app.tool()
    async def get_training_plan_workouts(calendar_date: str) -> str:
        """Get training plan workouts for the week containing the given date

        Returns workouts from your active training plan for the week containing
        the specified date. The API returns approximately 7 days of scheduled
        workouts anchored around the given date.

        Training plan workouts have workout_uuid (not workout_id). Use the
        workout_uuid with get_workout_by_id to get detailed step information.

        Args:
            calendar_date: Reference date in YYYY-MM-DD format (returns week's workouts)
        """
        try:
            # Query for training plan workouts using GraphQL
            query = {
                "query": f'query{{trainingPlanScalar(calendarDate:"{calendar_date}", lang:"en-US", firstDayOfWeek:"monday")}}'
            }
            result = garmin_client.query_garmin_graphql(query)

            if not result or "data" not in result:
                return "No training plan data found or error querying data."

            plan_data = result.get("data", {}).get("trainingPlanScalar", {})
            training_plans = plan_data.get("trainingPlanWorkoutScheduleDTOS", [])

            if not training_plans:
                return f"No training plan workouts scheduled for {calendar_date}."

            # Collect all workouts from all training plans
            all_workouts = []
            plan_names = []

            for plan in training_plans:
                plan_name = plan.get('planName')
                if plan_name and plan_name not in plan_names:
                    plan_names.append(plan_name)

                # workoutScheduleSummaries has same structure as scheduled workouts
                workout_summaries = plan.get('workoutScheduleSummaries', [])
                for workout in workout_summaries:
                    # Reuse the scheduled workout curation since structure is identical
                    all_workouts.append(_curate_scheduled_workout(workout))

            # Curate training plan data
            curated = {
                "date": calendar_date,
                "training_plans": plan_names if plan_names else None,
                "count": len(all_workouts),
                "workouts": all_workouts
            }

            # Remove None values from top level
            curated = {k: v for k, v in curated.items() if v is not None}

            return json.dumps(curated, indent=2)
        except Exception as e:
            return f"Error retrieving training plan workouts: {str(e)}"

    @app.tool()
    async def schedule_workout(workout_id: int, calendar_date: str) -> str:
        """Schedule a workout to a specific calendar date

        This adds an existing workout from your Garmin workout library
        to your Garmin Connect calendar on the specified date.

        Args:
            workout_id: ID of the workout to schedule (get IDs from get_workouts)
            calendar_date: Date to schedule the workout in YYYY-MM-DD format
        """
        try:
            url = f"workout-service/schedule/{workout_id}"
            response = garmin_client.client.post("connectapi", url, json={"date": calendar_date})

            if response.status_code == 200:
                return json.dumps({
                    "status": "success",
                    "workout_id": workout_id,
                    "scheduled_date": calendar_date,
                    "message": f"Successfully scheduled workout {workout_id} for {calendar_date}"
                }, indent=2)
            else:
                return json.dumps({
                    "status": "failed",
                    "workout_id": workout_id,
                    "scheduled_date": calendar_date,
                    "http_status": response.status_code,
                    "message": f"Failed to schedule workout: HTTP {response.status_code}"
                }, indent=2)
        except Exception as e:
            return f"Error scheduling workout: {str(e)}"

    @app.tool()
    async def schedule_workouts(schedules: list[dict]) -> str:
        """Schedule multiple workouts to specific calendar dates

        This adds workouts to your Garmin Connect calendar in a single call.
        Each item can either reference an existing workout by ID, or provide
        inline workout_data to upload-and-schedule in one step.

        Args:
            schedules: List of workout schedules, each with:
                - calendar_date (str): Date to schedule the workout in YYYY-MM-DD format (required)
                - workout_id (int): ID of an existing workout to schedule (required unless workout_data is provided)
                - workout_data (dict): Inline workout JSON to upload first, then schedule (optional).
                  When provided, workout_id is not required. Uses the same structure as upload_workout.

        Examples:
            Schedule existing workouts by ID:
            [{"workout_id": 123456, "calendar_date": "2024-01-15"},
             {"workout_id": 789012, "calendar_date": "2024-01-17"}]

            Upload and schedule inline:
            [{"calendar_date": "2024-01-15", "workout_data": {"workoutName": "Easy Run", ...}},
             {"workout_id": 789012, "calendar_date": "2024-01-17"}]
        """
        results = []
        for item in schedules:
            workout_id = item.get("workout_id")
            calendar_date = item.get("calendar_date")
            workout_data = item.get("workout_data")

            if calendar_date is None:
                results.append({
                    "status": "failed",
                    "workout_id": workout_id,
                    "scheduled_date": calendar_date,
                    "message": "Missing required field: calendar_date"
                })
                continue

            if workout_id is None and workout_data is None:
                results.append({
                    "status": "failed",
                    "workout_id": None,
                    "scheduled_date": calendar_date,
                    "message": "Missing required fields: provide either workout_id or workout_data"
                })
                continue

            workout_name = None

            if workout_data is not None:
                # Upload the workout first, then use the returned ID to schedule
                try:
                    _fix_hr_zone_steps(workout_data)
                    upload_result = garmin_client.upload_workout(workout_data)
                except Exception as upload_exc:
                    upload_error = build_garmin_api_error(
                        upload_exc,
                        operation="upload_workout",
                        endpoint="/workout-service/workout",
                        method="POST",
                        workout_data=workout_data,
                    )
                    # Preserve every field of the upload error while annotating the
                    # scheduling context so callers can see exactly which inline
                    # workout failed and why Garmin rejected it.
                    results.append({
                        **upload_error,
                        "name": workout_data.get("workoutName"),
                        "scheduled_date": calendar_date,
                        "stage": "upload",
                        "message": f"Inline workout upload failed: {upload_error.get('message')}",
                    })
                    continue

                if not isinstance(upload_result, dict) or upload_result.get("workoutId") is None:
                    results.append({
                        "status": "failed",
                        "name": workout_data.get("workoutName"),
                        "scheduled_date": calendar_date,
                        "stage": "upload",
                        "message": "Upload succeeded but no workout_id returned",
                        "upload_result": upload_result if isinstance(upload_result, dict) else None,
                    })
                    continue
                workout_id = upload_result["workoutId"]
                workout_name = upload_result.get("workoutName")

            try:
                url = f"workout-service/schedule/{workout_id}"
                response = garmin_client.client.post("connectapi", url, json={"date": calendar_date})

                if response.status_code == 200:
                    entry = {
                        "status": "success",
                        "workout_id": workout_id,
                        "scheduled_date": calendar_date,
                        "message": f"Successfully scheduled workout {workout_id} for {calendar_date}",
                    }
                    if workout_name:
                        entry["workout_name"] = workout_name
                    results.append(entry)
                else:
                    failure = {
                        "status": "failed",
                        "workout_id": workout_id,
                        "scheduled_date": calendar_date,
                        "stage": "schedule",
                        "http_status": response.status_code,
                        "request_method": "POST",
                        "request_endpoint": f"/{url}",
                        "message": f"Failed to schedule workout: HTTP {response.status_code}",
                    }
                    # Try to surface a response body if available (helps debug 4xx schedule errors).
                    # Guard against bare mocks / non-serializable bodies so this never
                    # turns into a TypeError inside json.dumps.
                    body_captured = False
                    try:
                        body_json = response.json()
                    except Exception:
                        body_json = None
                    if body_json is not None:
                        try:
                            json.dumps(body_json)
                            failure["response_body_json"] = body_json
                            body_captured = True
                        except (TypeError, ValueError):
                            body_captured = False
                    if not body_captured:
                        body_text = getattr(response, "text", "") or ""
                        if isinstance(body_text, str) and body_text:
                            failure["response_body_text"] = body_text[:2000]
                    if workout_name:
                        failure["workout_name"] = workout_name
                    results.append(failure)
            except Exception as e:
                results.append({
                    **build_garmin_api_error(
                        e,
                        operation="schedule_workout",
                        endpoint=f"/workout-service/schedule/{workout_id}",
                        method="POST",
                    ),
                    "workout_id": workout_id,
                    "scheduled_date": calendar_date,
                    "stage": "schedule",
                })

        total = len(results)
        succeeded = sum(1 for r in results if r["status"] == "success")
        return json.dumps({
            "total": total,
            "succeeded": succeeded,
            "failed": total - succeeded,
            "results": results
        }, indent=2)

    # =========================================================================
    # CALENDAR REFRESH WORKFLOW
    #
    # These tools provide a safe, generic, multi-sport way to rebuild the future
    # workout calendar. Calendar entries are removed, but workout templates,
    # completed activities, and historical training/health data are NEVER
    # touched by these tools.
    # =========================================================================

    @app.tool()
    async def unschedule_workout(
        scheduled_workout_id: int,
        confirmation: str = "",
        verbose: bool = False,
        confirm_unschedule_completed: str = "",
    ) -> str:
        """Remove a single scheduled workout from the Garmin Connect calendar.

        This is the generic, sport-agnostic unschedule tool. It works for
        running, cycling, strength, cardio, and walking calendar entries.

        Safety contract:
        - Removes only the scheduled calendar item (the calendar row).
        - Does NOT delete the underlying workout template.
        - Does NOT delete completed activities or historical training data.
        - Does NOT delete health data, gear, body composition, nutrition,
          personal records, or goals.
        - Refuses to unschedule a completed calendar entry unless the caller
          also passes confirm_unschedule_completed="UNSCHEDULE_COMPLETED".

        Args:
            scheduled_workout_id: Calendar entry id from get_scheduled_workouts.
            confirmation: Must equal "UNSCHEDULE_WORKOUT".
            verbose: When true, include the raw Garmin response payload.
            confirm_unschedule_completed: Optional secondary confirmation
                required only when the calendar entry is already completed.
        """
        if confirmation != "UNSCHEDULE_WORKOUT":
            return json.dumps({
                "status": "error",
                "tool": "unschedule_workout",
                "message": (
                    "Explicit confirmation required. Re-run with "
                    "confirmation='UNSCHEDULE_WORKOUT'."
                ),
            }, indent=2)

        try:
            sid = int(scheduled_workout_id)
        except (TypeError, ValueError):
            return json.dumps({
                "status": "error",
                "tool": "unschedule_workout",
                "message": "scheduled_workout_id must be an integer.",
                "scheduled_workout_id": scheduled_workout_id,
            }, indent=2)

        # Look up the entry first so we can refuse to nuke completed items and
        # so we can return useful name/sport/date metadata in the response.
        entry = _find_scheduled_workout_by_id(sid)
        if entry is not None and entry.get("completed") and confirm_unschedule_completed != "UNSCHEDULE_COMPLETED":
            return json.dumps({
                "status": "error",
                "tool": "unschedule_workout",
                "message": (
                    "Refusing to unschedule a completed calendar entry. "
                    "If you really want to remove this calendar row (the "
                    "underlying activity will NOT be deleted), re-run with "
                    "confirm_unschedule_completed='UNSCHEDULE_COMPLETED'."
                ),
                "scheduled_workout_id": sid,
                "workout_id": entry.get("workout_id"),
                "name": entry.get("name"),
                "date": entry.get("date"),
                "sport": entry.get("sport"),
                "completed": True,
                "activity_id": entry.get("activity_id"),
            }, indent=2)

        # Issue the DELETE. We treat the HTTP response as a hint, not as the
        # source of truth — Garmin's calendar endpoint frequently returns
        # an empty body or a missing/None status code on successful 204s,
        # so the only reliable signal is to refetch the calendar afterwards.
        url = f"workout-service/schedule/{sid}"
        delete_error_payload: Optional[dict] = None
        response = None
        status_code: Any = None
        response_body_json: Any = None
        response_body_text: Optional[str] = None
        try:
            response = garmin_client.client.delete("connectapi", url, api=True)
            status_code = getattr(response, "status_code", None)
            try:
                response_body_json = response.json()
            except Exception:
                response_body_json = None
                text = getattr(response, "text", None)
                if isinstance(text, str) and text:
                    response_body_text = text[:2000]
        except Exception as exc:
            delete_error_payload = {
                **build_garmin_api_error(
                    exc,
                    operation="unschedule_workout",
                    endpoint=f"/workout-service/schedule/{sid}",
                    method="DELETE",
                ),
                "tool": "unschedule_workout",
                "scheduled_workout_id": sid,
            }

        # Refetch and check the postcondition regardless of how the DELETE went.
        # An empty / None response is no longer treated as failure on its own:
        # if the SID has disappeared from the calendar, the unschedule
        # succeeded; if it is still there, it failed.
        hint_date = entry.get("date") if entry else None
        absent = _verify_scheduled_workout_absent(sid, hint_date=hint_date)
        # absent: True  -> SID gone, success.
        # absent: False -> SID still present, failure.
        # absent: None  -> we could not refetch, status is "unknown".

        garmin_response_empty = (
            response is not None
            and response_body_json in (None, {}, [])
            and not response_body_text
        )

        debug: dict = {
            "garmin_response_empty": bool(garmin_response_empty),
            "postcondition_checked": absent is not None,
            "postcondition_result": (
                "removed" if absent is True
                else "still_present" if absent is False
                else "unknown"
            ),
            "http_status": status_code,
        }

        if absent is True:
            payload = {
                "status": "success",
                "scheduled_workout_id": sid,
                "workout_id": entry.get("workout_id") if entry else None,
                "name": entry.get("name") if entry else None,
                "date": entry.get("date") if entry else None,
                "sport": entry.get("sport") if entry else None,
                "removed_item": {
                    "scheduled_workout_id": sid,
                    "workout_id": entry.get("workout_id") if entry else None,
                    "name": entry.get("name") if entry else None,
                    "date": entry.get("date") if entry else None,
                    "sport": entry.get("sport") if entry else None,
                } if entry else {"scheduled_workout_id": sid},
                "message": "Scheduled workout removed from calendar. Workout template, activities, and historical data were not touched.",
                "garmin_response_summary": debug,
            }
            if verbose:
                if response_body_json is not None:
                    payload["response_body_json"] = response_body_json
                elif response_body_text:
                    payload["response_body_text"] = response_body_text
                if delete_error_payload is not None:
                    payload["delete_error"] = delete_error_payload
            return json.dumps({k: v for k, v in payload.items() if v is not None}, indent=2)

        if absent is False:
            failure = {
                "status": "failed",
                "tool": "unschedule_workout",
                "scheduled_workout_id": sid,
                "http_status": status_code,
                "message": (
                    "Garmin refused to unschedule calendar entry: the item is "
                    "still on the calendar after the DELETE request and retry "
                    "window."
                ),
                "garmin_response_summary": debug,
            }
            if response_body_json is not None:
                failure["response_body_json"] = response_body_json
            elif response_body_text:
                failure["response_body_text"] = response_body_text
            if delete_error_payload is not None:
                failure["delete_error"] = delete_error_payload
            return json.dumps(failure, indent=2)

        # absent is None -> we could not verify.
        unknown: dict = {
            "status": "unknown",
            "tool": "unschedule_workout",
            "scheduled_workout_id": sid,
            "http_status": status_code,
            "message": (
                "Unschedule request was sent but the calendar could not be "
                "refetched to verify the result. The item may or may not have "
                "been removed."
            ),
            "garmin_response_summary": debug,
        }
        if response_body_json is not None:
            unknown["response_body_json"] = response_body_json
        elif response_body_text:
            unknown["response_body_text"] = response_body_text
        if delete_error_payload is not None:
            unknown["delete_error"] = delete_error_payload
        return json.dumps(unknown, indent=2)

    @app.tool()
    async def preview_workout_calendar_refresh(
        start_date: str,
        end_date: str,
        schedules: list[ScheduleRequest],
        delete_unused_templates: bool = False,
        verbose: bool = False,
    ) -> str:
        """Dry-run preview of refresh_workout_calendar. Performs NO mutations.

        Fetches current scheduled workouts in [start_date, end_date], compares
        them against the desired schedule, and returns exactly what would be
        unscheduled, uploaded, and scheduled if you ran refresh_workout_calendar
        with the same arguments. Completed workouts are flagged as preserved.

        Idempotency: items already present on the calendar (matching date +
        workout_id) are reported separately under ``already_present_items``
        and are NOT counted in ``would_schedule_count``; running refresh
        twice with the same desired schedule will not create duplicates.

        Args:
            start_date: Start of window in YYYY-MM-DD format (inclusive).
            end_date: End of window in YYYY-MM-DD format (inclusive).
            schedules: Desired schedule (list of ScheduleRequest). Each item:
                - calendar_date: "YYYY-MM-DD" (required)
                - exactly one of:
                    * workout_id: int (existing template)
                    * workout_data: dict (inline template body)
                - label / expected_name / expected_sport (optional)
            delete_unused_templates: If true, also list template candidates that
                would be considered for deletion. The preview never deletes.
            verbose: Include raw scheduled entries in the response.
        """
        try:
            _validate_yyyy_mm_dd(start_date, "start_date")
            _validate_yyyy_mm_dd(end_date, "end_date")
        except ValueError as exc:
            return json.dumps({
                "status": "error",
                "tool": "preview_workout_calendar_refresh",
                "message": str(exc),
            }, indent=2)

        if not isinstance(schedules, list):
            return json.dumps({
                "status": "error",
                "tool": "preview_workout_calendar_refresh",
                "message": "schedules must be a list of schedule objects.",
            }, indent=2)

        try:
            existing_raw = _fetch_scheduled_workouts_range(start_date, end_date)
        except Exception as exc:
            return json.dumps({
                "status": "error",
                "tool": "preview_workout_calendar_refresh",
                "message": f"Failed to fetch existing scheduled workouts: {exc}",
            }, indent=2)

        existing = [_curate_scheduled_workout(item) for item in existing_raw]
        incomplete_existing = [e for e in existing if not e.get("completed")]
        completed_existing = [e for e in existing if e.get("completed")]

        # Build a quick lookup of incomplete existing entries by (date, workout_id)
        # so we can recognize already-present desired items.
        incomplete_by_pair: dict[tuple, list[dict]] = {}
        for e in incomplete_existing:
            key = (e.get("date"), str(e.get("workout_id")) if e.get("workout_id") is not None else None)
            incomplete_by_pair.setdefault(key, []).append(e)

        validation_issues: list[dict] = []
        would_upload = 0
        would_schedule_items: list[dict] = []
        already_present_items: list[dict] = []
        preview_items: list[dict] = []
        warnings: list[str] = []
        # Track which existing SIDs are "claimed" as already-present so they
        # don't also appear in would_remove.
        claimed_existing_sids: set = set()

        for index, item in enumerate(schedules):
            issues, normalized = _validate_schedule_item(item, index)
            if issues:
                validation_issues.extend(issues)
                preview_items.append({
                    "index": index,
                    "status": "invalid",
                    "calendar_date": item.get("calendar_date") if isinstance(item, dict) else None,
                    "issues": issues,
                })
                continue

            entry: dict = {
                "index": index,
                "calendar_date": normalized["calendar_date"],
                "label": normalized.get("label"),
                "expected_name": normalized.get("expected_name"),
                "expected_sport": normalized.get("expected_sport"),
            }
            already_present = False
            if "workout_id" in normalized:
                wid = normalized["workout_id"]
                entry["workout_id"] = wid
                pair_key = (normalized["calendar_date"], str(wid))
                pool = incomplete_by_pair.get(pair_key) or []
                # Claim one existing entry per requested item (so same date+wid
                # twice counts as one already_present + one to schedule).
                claimable = next(
                    (e for e in pool if e.get("scheduled_workout_id") not in claimed_existing_sids),
                    None,
                )
                if claimable is not None:
                    already_present = True
                    claimed_existing_sids.add(claimable.get("scheduled_workout_id"))
                    entry["action"] = "already_present"
                    entry["scheduled_workout_id"] = claimable.get("scheduled_workout_id")
                    entry["name"] = claimable.get("name")
                    entry["sport"] = claimable.get("sport")
                    already_present_items.append(entry)
                else:
                    entry["action"] = "schedule_existing_template"
                    would_schedule_items.append(entry)
            else:
                entry["action"] = "upload_and_schedule"
                wd = normalized.get("workout_data") or {}
                entry["workout_name"] = wd.get("workoutName") or wd.get("name")
                entry["sport"] = (
                    (wd.get("sportType") or {}).get("sportTypeKey")
                    if isinstance(wd.get("sportType"), dict)
                    else wd.get("sport")
                )
                would_upload += 1
                would_schedule_items.append(entry)
            preview_items.append(entry)

        # would_remove is incomplete existing items that were NOT claimed
        # as already-present.
        would_remove = [
            e for e in incomplete_existing
            if e.get("scheduled_workout_id") not in claimed_existing_sids
        ]

        # Duplicate template detection.
        seen_pairs: dict[tuple, int] = {}
        for entry in preview_items:
            if entry.get("action") not in ("schedule_existing_template", "already_present"):
                continue
            key = (entry.get("calendar_date"), entry.get("workout_id"))
            seen_pairs[key] = seen_pairs.get(key, 0) + 1
        for (cdate, wid), count in seen_pairs.items():
            if count > 1:
                warnings.append(
                    f"Duplicate schedule: workout_id={wid} on {cdate} appears {count} times."
                )

        unused_template_candidates: list[dict] = []
        if delete_unused_templates:
            try:
                unused_template_candidates = _temporary_template_candidates(existing)
            except Exception as exc:
                warnings.append(f"Failed to inspect templates for deletion candidates: {exc}")

        already_satisfied = (
            not would_remove
            and not would_schedule_items
            and not validation_issues
        )

        result: dict = {
            "status": "success" if not validation_issues else "error",
            "tool": "preview_workout_calendar_refresh",
            "date_range": {"start": start_date, "end": end_date},
            "current_scheduled_count": len(existing),
            "would_remove_count": len(would_remove),
            "would_upload_count": would_upload,
            "would_schedule_count": len(would_schedule_items),
            "already_present_count": len(already_present_items),
            "preserved_completed_count": len(completed_existing),
            "already_satisfied": already_satisfied,
            "warnings": warnings,
            "preview_items": preview_items,
            "would_remove": would_remove,
            "would_schedule_items": would_schedule_items,
            "already_present_items": already_present_items,
            "preserved_completed": completed_existing,
        }
        if validation_issues:
            result["validation_issues"] = validation_issues
            result["message"] = "Schedule validation failed. No mutations would be performed."
        if delete_unused_templates:
            result["unused_template_candidates"] = unused_template_candidates
            result["would_delete_template_count"] = len(unused_template_candidates)
        if verbose:
            result["existing_scheduled_raw"] = existing_raw
        return json.dumps(result, indent=2, default=str)

    @app.tool()
    async def refresh_workout_calendar(
        start_date: str,
        end_date: str,
        schedules: list[ScheduleRequest],
        confirmation: str = "",
        delete_unused_templates: bool = False,
        verify: bool = True,
        verbose: bool = False,
    ) -> str:
        """Safely rebuild the workout calendar between start_date and end_date.

        This is the safe, sport-agnostic full-refresh workflow. In one call it:
          A. Preflights the date range (no mutations until validation passes).
          B. Unschedules every INCOMPLETE planned workout in the range that is
             NOT already a desired item (idempotency).
          C. Uploads any inline workout_data and schedules every requested item
             that is not already present.
          D. Verifies the resulting calendar state by re-fetching and matching
             against the desired schedule.

        Idempotency:
            Calling this tool twice with the same ``schedules`` is safe and
            will not create duplicates. Items where the existing calendar
            already has the same (date, workout_id) are recognized in
            preflight and counted as ``already_present`` rather than being
            unscheduled and re-scheduled.

        Eventual consistency / empty responses:
            Garmin's calendar mutation endpoints sometimes return an empty
            body or a missing/None status code on success. Refresh treats
            those as indeterminate intermediate signals and uses the FINAL
            re-fetched calendar as the source of truth. An unschedule
            counts as ``removed`` iff its SID is absent from the final
            calendar; a schedule counts as ``scheduled`` iff its (date,
            workout_id) is present in the final calendar.

        Safety contract (these are guaranteed by this tool):
        - Completed calendar entries are NEVER unscheduled.
        - Completed activities and historical training data are NEVER deleted.
        - Health data, gear, body composition, nutrition, personal records,
          goals, badges, and device settings are NEVER touched.
        - Workout templates are NEVER deleted unless delete_unused_templates
          is true, and even then only clearly temporary/test/draft/GPT-generated
          templates that are not currently scheduled.
        - If preflight validation fails, NO mutations are performed at all.

        Args:
            start_date: Inclusive window start in YYYY-MM-DD.
            end_date:   Inclusive window end   in YYYY-MM-DD.
            schedules:  Desired schedule (list of ScheduleRequest). Each item:
                - calendar_date: "YYYY-MM-DD"          (required)
                - exactly one of:
                    * workout_id: int                   (existing template)
                    * workout_data: dict                (inline template body)
                - label: str                            (optional, free-form)
                - expected_name: str                    (optional)
                - expected_sport: str                   (optional)
                Multiple items on the same calendar_date are allowed and are
                NEVER collapsed into a single workout.
            confirmation: Must equal "REFRESH_WORKOUT_CALENDAR".
            delete_unused_templates: If true, also remove clearly temporary or
                test/draft/GPT-generated templates that are not currently
                scheduled anywhere. Default false.
            verify: When true (default), re-fetch the calendar after mutations
                and confirm every requested item is present. Verification is
                authoritative; it can promote ``success`` over empty Garmin
                responses, or demote to ``partial`` when the final state is
                materially wrong.
            verbose: Include raw Garmin responses in the result.
        """
        if confirmation != "REFRESH_WORKOUT_CALENDAR":
            return json.dumps({
                "status": "error",
                "tool": "refresh_workout_calendar",
                "message": (
                    "Explicit confirmation required. Re-run with "
                    "confirmation='REFRESH_WORKOUT_CALENDAR'."
                ),
            }, indent=2)

        try:
            _validate_yyyy_mm_dd(start_date, "start_date")
            _validate_yyyy_mm_dd(end_date, "end_date")
        except ValueError as exc:
            return json.dumps({
                "status": "error",
                "tool": "refresh_workout_calendar",
                "message": str(exc),
            }, indent=2)

        if not isinstance(schedules, list) or not schedules:
            return json.dumps({
                "status": "error",
                "tool": "refresh_workout_calendar",
                "message": "schedules must be a non-empty list of schedule objects.",
            }, indent=2)

        # ----- Step A: preflight ------------------------------------------------
        validation_issues: list[dict] = []
        normalized: list[dict] = []
        for index, item in enumerate(schedules):
            issues, norm = _validate_schedule_item(item, index)
            normalized.append(norm)
            validation_issues.extend(issues)

        # Also confirm every calendar_date is inside the window.
        try:
            start = datetime.date.fromisoformat(start_date)
            end = datetime.date.fromisoformat(end_date)
        except Exception:
            start = end = None
        if start and end:
            for index, norm in enumerate(normalized):
                cdate = norm.get("calendar_date")
                if not cdate:
                    continue
                try:
                    parsed = datetime.date.fromisoformat(cdate)
                except Exception:
                    validation_issues.append({
                        "index": index,
                        "field": "calendar_date",
                        "message": f"calendar_date '{cdate}' is not a valid date.",
                    })
                    continue
                if parsed < start or parsed > end:
                    validation_issues.append({
                        "index": index,
                        "field": "calendar_date",
                        "message": (
                            f"calendar_date '{cdate}' is outside the requested "
                            f"window [{start_date}, {end_date}]."
                        ),
                    })

        try:
            existing_raw = _fetch_scheduled_workouts_range(start_date, end_date)
        except Exception as exc:
            return json.dumps({
                "status": "error",
                "tool": "refresh_workout_calendar",
                "message": f"Failed to fetch current calendar before mutating: {exc}",
            }, indent=2)
        existing = [_curate_scheduled_workout(item) for item in existing_raw]

        if validation_issues:
            return json.dumps({
                "status": "error",
                "tool": "refresh_workout_calendar",
                "message": "Schedule validation failed. No Garmin changes were made.",
                "validation_issues": validation_issues,
                "current_scheduled_count": len(existing),
                "removed_count": 0,
                "uploaded_count": 0,
                "scheduled_count": 0,
                "already_present_count": 0,
                "failed_count": 0,
            }, indent=2)

        incomplete_existing = [e for e in existing if not e.get("completed")]
        completed_existing = [e for e in existing if e.get("completed")]

        # ----- Idempotency preflight: match desired items against current ------
        # incomplete entries by (date, workout_id). Each existing entry can be
        # claimed by at most one desired item; remaining desired items will be
        # POSTed and remaining incomplete entries will be unscheduled.
        incomplete_by_pair: dict[tuple, list[dict]] = {}
        for e in incomplete_existing:
            pair = (
                e.get("date"),
                str(e.get("workout_id")) if e.get("workout_id") is not None else None,
            )
            incomplete_by_pair.setdefault(pair, []).append(e)

        # Also build a (date, workout_id) view of completed entries so we can
        # warn the caller if they tried to schedule a workout that conflicts
        # with a completed one.
        completed_by_pair: dict[tuple, list[dict]] = {}
        for e in completed_existing:
            pair = (
                e.get("date"),
                str(e.get("workout_id")) if e.get("workout_id") is not None else None,
            )
            completed_by_pair.setdefault(pair, []).append(e)

        already_present_items: list[dict] = []
        claimed_sids: set = set()
        to_schedule_indexes: list[int] = []  # indexes into ``normalized``
        warnings: list[str] = []

        for index, norm in enumerate(normalized):
            if "workout_id" in norm:
                pair_key = (norm["calendar_date"], str(norm["workout_id"]))
                pool = incomplete_by_pair.get(pair_key) or []
                claimable = next(
                    (e for e in pool if e.get("scheduled_workout_id") not in claimed_sids),
                    None,
                )
                if claimable is not None:
                    claimed_sids.add(claimable.get("scheduled_workout_id"))
                    already_present_items.append({
                        "index": index,
                        "calendar_date": norm["calendar_date"],
                        "workout_id": norm["workout_id"],
                        "scheduled_workout_id": claimable.get("scheduled_workout_id"),
                        "name": claimable.get("name"),
                        "sport": claimable.get("sport"),
                        "label": norm.get("label"),
                    })
                    # Completed-on-same-date conflict warning (informational).
                    if completed_by_pair.get(pair_key):
                        warnings.append(
                            f"Requested workout_id={norm['workout_id']} on "
                            f"{norm['calendar_date']} already exists as a "
                            f"completed activity; keeping completed entry intact."
                        )
                    continue
            to_schedule_indexes.append(index)

        # Entries to unschedule = incomplete that were NOT claimed.
        to_unschedule = [
            e for e in incomplete_existing
            if e.get("scheduled_workout_id") not in claimed_sids
        ]

        # ----- Step B: clear calendar (only entries we intend to remove) -------
        removed_items: list[dict] = []
        failed_items: list[dict] = []
        unschedule_attempts: list[dict] = []  # one record per DELETE attempt

        for entry in to_unschedule:
            sid = entry.get("scheduled_workout_id")
            if sid is None:
                failed_items.append({
                    "stage": "unschedule",
                    "reason": "Scheduled entry had no scheduled_workout_id; refusing to touch.",
                    "entry": entry,
                })
                continue
            attempt: dict = {
                "scheduled_workout_id": sid,
                "workout_id": entry.get("workout_id"),
                "name": entry.get("name"),
                "date": entry.get("date"),
                "sport": entry.get("sport"),
                "source": entry.get("source"),
            }
            try:
                url = f"workout-service/schedule/{int(sid)}"
                response = garmin_client.client.delete("connectapi", url, api=True)
                attempt["http_status"] = getattr(response, "status_code", None)
                # Capture body only for verbose / failed reporting later.
                try:
                    body = response.json()
                except Exception:
                    body = None
                attempt["response_body_json"] = body if body not in (None, {}, []) else None
                attempt["garmin_response_empty"] = (
                    attempt["response_body_json"] is None
                    and not getattr(response, "text", None)
                )
            except Exception as exc:
                attempt["exception"] = build_garmin_api_error(
                    exc,
                    operation="unschedule_workout",
                    endpoint=f"/workout-service/schedule/{sid}",
                    method="DELETE",
                )
            unschedule_attempts.append(attempt)

        # ----- Step C: build new schedule --------------------------------------
        uploaded_count = 0
        scheduled_attempts: list[dict] = []  # one record per POST attempt
        scheduled_items: list[dict] = []     # confirmed by postcondition below

        for index in to_schedule_indexes:
            norm = normalized[index]
            calendar_date = norm["calendar_date"]
            label = norm.get("label")
            workout_name: Optional[str] = None
            sport_key: Optional[str] = None
            workout_id: Optional[int] = None

            from_upload = False
            if "workout_id" in norm:
                workout_id = norm["workout_id"]
            else:
                from_upload = True
                workout_data = dict(norm.get("workout_data") or {})
                try:
                    _fix_hr_zone_steps(workout_data)
                    upload_result = garmin_client.upload_workout(workout_data)
                except Exception as exc:
                    failed_items.append({
                        "stage": "upload",
                        "index": index,
                        "calendar_date": calendar_date,
                        "label": label,
                        **build_garmin_api_error(
                            exc,
                            operation="upload_workout",
                            endpoint="/workout-service/workout",
                            method="POST",
                            workout_data=workout_data,
                        ),
                    })
                    continue
                if not isinstance(upload_result, dict) or upload_result.get("workoutId") is None:
                    failed_items.append({
                        "stage": "upload",
                        "index": index,
                        "calendar_date": calendar_date,
                        "label": label,
                        "message": "Upload succeeded but no workout_id was returned.",
                        "upload_result": upload_result if isinstance(upload_result, dict) else None,
                    })
                    continue
                workout_id = upload_result["workoutId"]
                workout_name = upload_result.get("workoutName") or workout_data.get("workoutName")
                if isinstance(workout_data.get("sportType"), dict):
                    sport_key = workout_data["sportType"].get("sportTypeKey")
                uploaded_count += 1

            attempt: dict = {
                "index": index,
                "calendar_date": calendar_date,
                "workout_id": workout_id,
                "name": workout_name or norm.get("expected_name"),
                "sport": sport_key or norm.get("expected_sport"),
                "label": label,
                "from_upload": from_upload,
            }
            try:
                url = f"workout-service/schedule/{int(workout_id)}"
                response = garmin_client.client.post(
                    "connectapi", url, json={"date": calendar_date}
                )
                attempt["http_status"] = getattr(response, "status_code", None)
                try:
                    body = response.json()
                except Exception:
                    body = None
                attempt["response_body_json"] = body if body not in (None, {}, []) else None
                attempt["garmin_response_empty"] = (
                    attempt["response_body_json"] is None
                    and not getattr(response, "text", None)
                )
            except Exception as exc:
                attempt["exception"] = build_garmin_api_error(
                    exc,
                    operation="schedule_workout",
                    endpoint=f"/workout-service/schedule/{workout_id}",
                    method="POST",
                )
            scheduled_attempts.append(attempt)

        # ----- Step D: final verification (the source of truth) ---------------
        verification_status: str = "skipped"
        verification_payload: Optional[dict] = None
        final_calendar: list[dict] = []
        final_fetch_error: Optional[str] = None
        if verify:
            try:
                final_raw = _fetch_scheduled_workouts_range(start_date, end_date)
                final_calendar = [_curate_scheduled_workout(item) for item in final_raw]
            except Exception as exc:
                final_fetch_error = f"Could not re-fetch calendar to verify: {exc}"
                verification_status = "failed"
                verification_payload = {
                    "message": final_fetch_error,
                    "matched_requested_items": [],
                    "missing_requested_items": [],
                    "lingering_old_items": [],
                    "extra_items": [],
                }
            else:
                # Index the final calendar by date + (date, workout_id).
                final_by_pair: dict[tuple, list[dict]] = {}
                final_by_sid: dict[str, dict] = {}
                for item in final_calendar:
                    pair = (
                        item.get("date"),
                        str(item.get("workout_id")) if item.get("workout_id") is not None else None,
                    )
                    final_by_pair.setdefault(pair, []).append(item)
                    sid = item.get("scheduled_workout_id")
                    if sid is not None:
                        final_by_sid[str(sid)] = item

                # 1) Postcondition for unschedule attempts: did the SID
                #    actually disappear?
                claimed_final_sids: set = set()
                for attempt in unschedule_attempts:
                    sid = attempt.get("scheduled_workout_id")
                    if sid is None:
                        continue
                    still_present = str(sid) in final_by_sid
                    attempt["postcondition_checked"] = True
                    attempt["postcondition_result"] = (
                        "still_present" if still_present else "removed"
                    )
                    if not still_present:
                        removed_items.append({
                            k: attempt.get(k) for k in (
                                "scheduled_workout_id", "workout_id", "name",
                                "date", "sport", "source",
                            )
                            if attempt.get(k) is not None
                        })
                    else:
                        # Empty response AND item still present -> real failure.
                        failed_items.append({
                            "stage": "unschedule",
                            "scheduled_workout_id": sid,
                            "http_status": attempt.get("http_status"),
                            "garmin_response_empty": attempt.get("garmin_response_empty"),
                            "postcondition_result": "still_present",
                            "entry": {
                                k: attempt.get(k) for k in (
                                    "scheduled_workout_id", "workout_id",
                                    "name", "date", "sport",
                                )
                            },
                            **(attempt.get("exception") or {}),
                        })

                # 2) Postcondition for schedule attempts: did a matching
                #    (date, workout_id) appear?
                matched_requested_items: list[dict] = []
                for attempt in scheduled_attempts:
                    pair = (
                        attempt.get("calendar_date"),
                        str(attempt.get("workout_id")) if attempt.get("workout_id") is not None else None,
                    )
                    pool = final_by_pair.get(pair) or []
                    claimable = next(
                        (m for m in pool if m.get("scheduled_workout_id") not in claimed_final_sids),
                        None,
                    )
                    attempt["postcondition_checked"] = True
                    if claimable is not None:
                        claimed_final_sids.add(claimable.get("scheduled_workout_id"))
                        attempt["postcondition_result"] = "scheduled"
                        attempt["scheduled_workout_id"] = claimable.get("scheduled_workout_id")
                        scheduled_items.append({
                            "index": attempt.get("index"),
                            "calendar_date": attempt.get("calendar_date"),
                            "workout_id": attempt.get("workout_id"),
                            "scheduled_workout_id": claimable.get("scheduled_workout_id"),
                            "name": attempt.get("name") or claimable.get("name"),
                            "sport": attempt.get("sport") or claimable.get("sport"),
                            "label": attempt.get("label"),
                        })
                        matched_requested_items.append({
                            "calendar_date": attempt.get("calendar_date"),
                            "workout_id": attempt.get("workout_id"),
                            "scheduled_workout_id": claimable.get("scheduled_workout_id"),
                            "name": claimable.get("name"),
                            "sport": claimable.get("sport"),
                        })
                    else:
                        attempt["postcondition_result"] = "missing"
                        failed_items.append({
                            "stage": "schedule",
                            "index": attempt.get("index"),
                            "calendar_date": attempt.get("calendar_date"),
                            "workout_id": attempt.get("workout_id"),
                            "http_status": attempt.get("http_status"),
                            "garmin_response_empty": attempt.get("garmin_response_empty"),
                            "postcondition_result": "missing",
                            "label": attempt.get("label"),
                            **(attempt.get("exception") or {}),
                        })

                # 3) Account for items that were already present in preflight:
                #    they should still be in the final calendar. If they are
                #    not, the user lost a desired item between preflight and
                #    final fetch.
                missing_already_present: list[dict] = []
                for ap in already_present_items:
                    ap_sid = str(ap.get("scheduled_workout_id"))
                    final = final_by_sid.get(ap_sid)
                    if final is not None:
                        claimed_final_sids.add(final.get("scheduled_workout_id"))
                        matched_requested_items.append({
                            "calendar_date": ap.get("calendar_date"),
                            "workout_id": ap.get("workout_id"),
                            "scheduled_workout_id": final.get("scheduled_workout_id"),
                            "name": final.get("name"),
                            "sport": final.get("sport"),
                            "already_present": True,
                        })
                    else:
                        # Pair-based fallback: maybe Garmin replaced the SID but
                        # kept the (date, workout_id) pair.
                        pair = (ap.get("calendar_date"), str(ap.get("workout_id")))
                        pool = final_by_pair.get(pair) or []
                        claimable = next(
                            (m for m in pool if m.get("scheduled_workout_id") not in claimed_final_sids),
                            None,
                        )
                        if claimable is not None:
                            claimed_final_sids.add(claimable.get("scheduled_workout_id"))
                            matched_requested_items.append({
                                "calendar_date": ap.get("calendar_date"),
                                "workout_id": ap.get("workout_id"),
                                "scheduled_workout_id": claimable.get("scheduled_workout_id"),
                                "name": claimable.get("name"),
                                "sport": claimable.get("sport"),
                                "already_present": True,
                            })
                        else:
                            missing_already_present.append(ap)

                # 4) Compute missing_requested_items (desired entries that the
                #    final calendar does not contain).
                missing_requested_items: list[dict] = []
                for attempt in scheduled_attempts:
                    if attempt.get("postcondition_result") == "missing":
                        missing_requested_items.append({
                            "calendar_date": attempt.get("calendar_date"),
                            "workout_id": attempt.get("workout_id"),
                            "label": attempt.get("label"),
                        })
                for ap in missing_already_present:
                    missing_requested_items.append({
                        "calendar_date": ap.get("calendar_date"),
                        "workout_id": ap.get("workout_id"),
                        "label": ap.get("label"),
                        "was_already_present_in_preflight": True,
                    })

                # 5) Lingering old incomplete items: any incomplete entry in
                #    the final calendar whose SID is not in
                #    ``claimed_final_sids`` AND that we intended to remove.
                intended_removed_sids = {
                    str(a.get("scheduled_workout_id")) for a in unschedule_attempts
                    if a.get("scheduled_workout_id") is not None
                }
                lingering: list[dict] = []
                extra_items: list[dict] = []
                for item in final_calendar:
                    if item.get("completed"):
                        continue
                    sid = str(item.get("scheduled_workout_id"))
                    if sid in claimed_final_sids:
                        continue
                    if sid in intended_removed_sids:
                        lingering.append(item)
                    else:
                        extra_items.append(item)

                if not missing_requested_items and not lingering:
                    verification_status = "success"
                else:
                    verification_status = "degraded"

                verification_payload = {
                    "matched_requested_items": matched_requested_items,
                    "missing_requested_items": missing_requested_items,
                    "lingering_old_items": lingering,
                    "extra_items": extra_items,
                }

        # ----- Build warnings from postcondition outcomes ----------------------
        empty_responses_with_success = 0
        for attempt in unschedule_attempts + scheduled_attempts:
            if attempt.get("garmin_response_empty") and attempt.get("postcondition_result") in ("removed", "scheduled"):
                empty_responses_with_success += 1
        if empty_responses_with_success:
            warnings.append(
                f"{empty_responses_with_success} Garmin mutation response(s) "
                "had an empty body or missing status code, but the final "
                "calendar postcondition confirmed success."
            )

        # ----- Optional template cleanup ----------------------------------------
        deleted_templates: list[dict] = []
        template_deletion_skipped: list[dict] = []
        if delete_unused_templates:
            try:
                # Re-fetch so templates that we just scheduled are correctly
                # marked as "currently scheduled" and therefore preserved.
                fresh_calendar = final_calendar or [
                    _curate_scheduled_workout(item)
                    for item in _fetch_scheduled_workouts_range(start_date, end_date)
                ]
                candidates = _temporary_template_candidates(fresh_calendar)
                for cand in candidates:
                    wid = cand.get("workout_id")
                    if wid is None:
                        template_deletion_skipped.append({
                            **cand,
                            "reason": "No workout_id available.",
                        })
                        continue
                    try:
                        del_url = f"workout-service/workout/{int(wid)}"
                        del_resp = garmin_client.client.delete("connectapi", del_url, api=True)
                        del_code = getattr(del_resp, "status_code", None)
                        if del_code in (200, 204):
                            deleted_templates.append(cand)
                        else:
                            template_deletion_skipped.append({
                                **cand,
                                "reason": f"HTTP {del_code}",
                            })
                    except Exception as exc:
                        template_deletion_skipped.append({
                            **cand,
                            "reason": f"Exception: {exc}",
                        })
            except Exception as exc:
                template_deletion_skipped.append({"reason": f"Template scan failed: {exc}"})

        # ----- Top-level status: verification is authoritative ----------------
        # Rules:
        #   - If verify and final state matches desired (verification_status
        #     == "success") and no preflight blocked, status="success".
        #     Failed intermediate operations whose postcondition still passed
        #     are NOT counted toward failed_count and do NOT downgrade status.
        #   - If verification is "degraded", status="partial".
        #   - If verification is "failed" (final fetch error), status="failed"
        #     unless we have evidence the mutation succeeded (we have none
        #     without a final fetch).
        #   - If verify=False, fall back to attempt-level success: status is
        #     success unless we have explicit failed_items, in which case
        #     partial (if anything succeeded) or failed (if nothing).
        if verify:
            if verification_status == "success":
                overall_status = "success"
            elif verification_status == "failed":
                overall_status = "failed"
            else:  # degraded
                overall_status = "partial"
        else:
            if not failed_items:
                overall_status = "success"
            elif removed_items or scheduled_items or already_present_items:
                overall_status = "partial"
            else:
                overall_status = "failed"

        result: dict = {
            "status": overall_status,
            "tool": "refresh_workout_calendar",
            "date_range": {"start": start_date, "end": end_date},
            "removed_count": len(removed_items),
            "uploaded_count": uploaded_count,
            "scheduled_count": len(scheduled_items),
            "already_present_count": len(already_present_items),
            "failed_count": len(failed_items),
            "final_calendar_count": len(final_calendar) if verify else None,
            "removed_items": removed_items,
            "uploaded_items": [
                {
                    "index": a.get("index"),
                    "calendar_date": a.get("calendar_date"),
                    "workout_id": a.get("workout_id"),
                    "name": a.get("name"),
                    "sport": a.get("sport"),
                    "label": a.get("label"),
                }
                for a in scheduled_attempts
                if a.get("from_upload") and a.get("postcondition_result") == "scheduled"
            ],
            "scheduled_items": scheduled_items,
            "already_present_items": already_present_items,
            "preserved_completed": completed_existing,
            "verification_status": verification_status,
            "warnings": warnings,
        }
        if failed_items:
            result["failed_items"] = failed_items
        if verify and verification_payload is not None:
            result["verification"] = verification_payload
        if delete_unused_templates:
            result["deleted_templates"] = deleted_templates
            result["template_deletion_skipped"] = template_deletion_skipped
        if verbose:
            result["existing_scheduled_raw"] = existing_raw
            result["unschedule_attempts"] = unschedule_attempts
            result["scheduled_attempts"] = scheduled_attempts
            if verify:
                result["final_calendar"] = final_calendar
        # Drop None values for cleanliness
        result = {k: v for k, v in result.items() if v is not None}
        return json.dumps(result, indent=2, default=str)

    return app


# =============================================================================
# CALENDAR REFRESH SHARED HELPERS
# =============================================================================

def _validate_yyyy_mm_dd(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string in YYYY-MM-DD format.")
    try:
        datetime.date.fromisoformat(value)
    except Exception as exc:
        raise ValueError(f"{field} must be a real date in YYYY-MM-DD format.") from exc
    return value


def _fetch_scheduled_workouts_range(start_date: str, end_date: str) -> List[dict]:
    """Fetch scheduled workouts in [start_date, end_date] via GraphQL.

    Returns an empty list if the API returns nothing.
    """
    query = {
        "query": (
            'query{workoutScheduleSummariesScalar('
            f'startDate:"{start_date}", endDate:"{end_date}")}}'
        )
    }
    result = garmin_client.query_garmin_graphql(query)
    if not isinstance(result, dict):
        return []
    return (result.get("data") or {}).get("workoutScheduleSummariesScalar") or []


def _hint_window_for_date(hint_date: Optional[str], pad_days: int = 2) -> tuple[str, str]:
    """Build a small +/- ``pad_days`` window around ``hint_date`` for cheap
    postcondition refetches. Falls back to a ~year window around today when no
    hint is available."""
    today = datetime.date.today()
    if isinstance(hint_date, str):
        try:
            parsed = datetime.date.fromisoformat(hint_date)
        except Exception:
            parsed = today
    else:
        parsed = today
    start = (parsed - datetime.timedelta(days=pad_days)).isoformat()
    end = (parsed + datetime.timedelta(days=pad_days)).isoformat()
    return start, end


def _verify_scheduled_workout_absent(
    scheduled_workout_id: Any,
    *,
    hint_date: Optional[str] = None,
    retry_delays: tuple = _POSTCONDITION_RETRY_DELAYS,
) -> Optional[bool]:
    """Return True if ``scheduled_workout_id`` is no longer on the calendar,
    False if it is still present, or None when verification could not be
    performed.

    The function refetches a narrow window around ``hint_date`` and walks the
    list looking for the SID. It retries with the supplied backoff sequence to
    accommodate Garmin's eventual consistency. The first attempt skips the
    sleep (delays[0] is treated as the pre-attempt pause)."""
    target = str(scheduled_workout_id)
    start, end = _hint_window_for_date(hint_date)
    last_error: Optional[Exception] = None
    for delay in retry_delays:
        try:
            _retry_sleep(delay)
            raw = _fetch_scheduled_workouts_range(start, end)
        except Exception as exc:
            last_error = exc
            continue
        present = False
        for item in raw:
            curated = _curate_scheduled_workout(item)
            if str(curated.get("scheduled_workout_id")) == target:
                present = True
                break
        if not present:
            return True
        # Item still present — try again until the budget is exhausted.
        last_error = None
    if last_error is not None:
        return None
    # All retries saw the item — postcondition definitively failed.
    return False


def _verify_scheduled_workout_present(
    workout_id: Any,
    calendar_date: str,
    *,
    retry_delays: tuple = _POSTCONDITION_RETRY_DELAYS,
) -> tuple[Optional[bool], Optional[dict]]:
    """Return (True, entry) if a calendar entry with the given ``workout_id``
    exists on ``calendar_date`` after retries, (False, None) if it is missing
    after all retries, or (None, None) when verification could not be
    performed.
    """
    target_wid = str(workout_id)
    start, end = _hint_window_for_date(calendar_date)
    last_error: Optional[Exception] = None
    last_found: Optional[dict] = None
    for delay in retry_delays:
        try:
            _retry_sleep(delay)
            raw = _fetch_scheduled_workouts_range(start, end)
        except Exception as exc:
            last_error = exc
            continue
        last_error = None
        for item in raw:
            curated = _curate_scheduled_workout(item)
            if (
                str(curated.get("workout_id")) == target_wid
                and curated.get("date") == calendar_date
            ):
                last_found = curated
                return True, curated
    if last_error is not None:
        return None, None
    return False, last_found


def _find_scheduled_workout_by_id(scheduled_workout_id: int) -> Optional[dict]:
    """Look up a single calendar entry by scheduled_workout_id.

    Best-effort: searches a 365-day window centered around today. Returns the
    curated dict or None when not found / lookup fails. Failures are silent
    because the caller may still legitimately want to attempt the delete.
    """
    try:
        today = datetime.date.today()
        start = (today - datetime.timedelta(days=180)).isoformat()
        end = (today + datetime.timedelta(days=180)).isoformat()
        raw = _fetch_scheduled_workouts_range(start, end)
    except Exception:
        return None
    target = str(scheduled_workout_id)
    for item in raw:
        curated = _curate_scheduled_workout(item)
        if str(curated.get("scheduled_workout_id")) == target:
            return curated
    return None


def _validate_schedule_item(item: Any, index: int) -> tuple[list[dict], dict]:
    """Validate one schedule object for refresh_workout_calendar.

    Returns (issues, normalized). When issues is non-empty the item is unsafe
    to mutate. The normalized dict is best-effort and always returned so that
    preview tools can echo back what the caller passed in.
    """
    issues: list[dict] = []
    normalized: dict = {}

    if not isinstance(item, dict):
        issues.append({
            "index": index,
            "field": "<root>",
            "message": f"Schedule item at index {index} must be an object.",
        })
        return issues, {"calendar_date": None}

    calendar_date = item.get("calendar_date")
    if not isinstance(calendar_date, str):
        issues.append({
            "index": index,
            "field": "calendar_date",
            "message": "calendar_date is required and must be a YYYY-MM-DD string.",
        })
    else:
        try:
            datetime.date.fromisoformat(calendar_date)
            normalized["calendar_date"] = calendar_date
        except Exception:
            issues.append({
                "index": index,
                "field": "calendar_date",
                "message": f"calendar_date '{calendar_date}' is not a real date.",
            })

    workout_id = item.get("workout_id")
    workout_data = item.get("workout_data")

    if workout_id is None and workout_data is None:
        issues.append({
            "index": index,
            "field": "workout_id|workout_data",
            "message": "Provide exactly one of workout_id or workout_data.",
        })
    elif workout_id is not None and workout_data is not None:
        issues.append({
            "index": index,
            "field": "workout_id|workout_data",
            "message": "Provide exactly one of workout_id or workout_data, not both.",
        })
    elif workout_id is not None:
        try:
            normalized["workout_id"] = int(workout_id)
        except (TypeError, ValueError):
            issues.append({
                "index": index,
                "field": "workout_id",
                "message": "workout_id must be an integer.",
            })
    else:
        if not isinstance(workout_data, dict) or not workout_data:
            issues.append({
                "index": index,
                "field": "workout_data",
                "message": "workout_data must be a non-empty dict.",
            })
        else:
            normalized["workout_data"] = workout_data
            # Light structural sanity check; we don't run full validation here
            # because Garmin will reject malformed payloads at upload time and
            # the failure will be reported per-item without rolling back already
            # committed steps.
            if "workoutName" not in workout_data and "name" not in workout_data:
                issues.append({
                    "index": index,
                    "field": "workout_data.workoutName",
                    "message": "workout_data should include a workoutName.",
                })
            if "workoutSegments" not in workout_data and "segments" not in workout_data:
                issues.append({
                    "index": index,
                    "field": "workout_data.workoutSegments",
                    "message": "workout_data should include workoutSegments.",
                })

    for opt in ("label", "expected_name", "expected_sport"):
        if opt in item:
            normalized[opt] = item.get(opt)

    return issues, normalized


_TEMP_TEMPLATE_TOKENS = (
    "temp", "tmp", "test", "draft", "scratch", "delete me", "deleteme",
    "gpt", "chatgpt", "gpt-generated", "gpt generated", "ai-generated",
    "ai generated", "[draft]", "[test]", "[temp]",
)


def _temporary_template_candidates(currently_scheduled: List[dict]) -> List[dict]:
    """List workout templates that look temporary/test/draft and are not scheduled.

    Conservative: we only flag templates whose name clearly indicates it is a
    throwaway, and only when their workout_id is not currently used by any
    scheduled calendar entry in the inspected range.
    """
    try:
        templates = garmin_client.get_workouts() or []
    except Exception:
        return []

    scheduled_ids = {
        str(item.get("workout_id")) for item in currently_scheduled
        if item.get("workout_id") is not None
    }

    candidates: list[dict] = []
    for tpl in templates:
        if not isinstance(tpl, dict):
            continue
        name = tpl.get("workoutName") or tpl.get("name") or ""
        wid = tpl.get("workoutId") or tpl.get("workout_id")
        if wid is None:
            continue
        if str(wid) in scheduled_ids:
            continue
        lname = str(name).lower()
        if any(tok in lname for tok in _TEMP_TEMPLATE_TOKENS):
            candidates.append({
                "workout_id": wid,
                "name": name,
                "sport": (
                    (tpl.get("sportType") or {}).get("sportTypeKey")
                    if isinstance(tpl.get("sportType"), dict)
                    else tpl.get("sport")
                ),
                "reason": "Name contains temporary/test/draft/GPT marker and template is not currently scheduled.",
            })
    return candidates
