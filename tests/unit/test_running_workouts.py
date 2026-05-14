"""
Unit tests for running workout builders, validator, and the rich Garmin API
error helper. These verify the canonical Garmin Connect running DTOs that
build_running_workout_json produces and the structural checks performed by
validate_running_workout_data.
"""
import json
import re
from unittest.mock import MagicMock

import pytest

from garmin_mcp.workouts import (
    build_garmin_api_error,
    validate_running_workout_data,
)
from garmin_mcp.workout_builders import (
    build_running_workout_json,
    build_progression_run_json,
    build_tempo_blocks_json,
)


# ---------------------------------------------------------------------------
# build_running_workout_json — canonical DTO production
# ---------------------------------------------------------------------------

def _running_sport():
    return {"sportTypeId": 1, "sportTypeKey": "running"}


def test_simple_run_warmup_z2_cooldown_produces_canonical_dtos():
    """10 min warmup + 20 min Z2 + 5 min cooldown."""
    workout = build_running_workout_json(
        "Simple Run",
        [
            {"kind": "warmup", "duration_seconds": 600},
            {"kind": "interval", "duration_seconds": 1200, "hr_zone": "Z2"},
            {"kind": "cooldown", "duration_seconds": 300},
        ],
    )

    assert workout["workoutName"] == "Simple Run"
    assert workout["sportType"] == _running_sport()

    segment = workout["workoutSegments"][0]
    assert segment["segmentOrder"] == 1
    assert segment["sportType"] == _running_sport()

    steps = segment["workoutSteps"]
    assert len(steps) == 3

    # Warmup
    assert steps[0]["type"] == "ExecutableStepDTO"
    assert steps[0]["stepOrder"] == 1
    assert steps[0]["stepType"] == {"stepTypeId": 1, "stepTypeKey": "warmup"}
    assert steps[0]["endCondition"] == {"conditionTypeId": 2, "conditionTypeKey": "time"}
    assert steps[0]["endConditionValue"] == 600.0
    assert steps[0]["targetType"] == {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"}

    # Z2 interval
    assert steps[1]["stepType"] == {"stepTypeId": 3, "stepTypeKey": "interval"}
    assert steps[1]["endConditionValue"] == 1200.0
    assert steps[1]["targetType"] == {"workoutTargetTypeId": 4, "workoutTargetTypeKey": "heart.rate.zone"}
    assert steps[1]["zoneNumber"] == 2

    # Cooldown
    assert steps[2]["stepType"] == {"stepTypeId": 2, "stepTypeKey": "cooldown"}
    assert steps[2]["endConditionValue"] == 300.0


def test_progression_run_emits_three_interval_blocks_with_rising_zones():
    """Progression: 15 min easy / 15 min Z3 / 15 min Z4 / 5 min cooldown."""
    workout = build_progression_run_json(
        "Progression Run",
        warmup_min=15,
        blocks=[
            {"duration_min": 15, "hr_zone": "Z3"},
            {"duration_min": 15, "hr_zone": "Z4"},
        ],
        cooldown_min=5,
    )

    steps = workout["workoutSegments"][0]["workoutSteps"]
    # warmup + 2 intervals + cooldown
    assert [s["stepType"]["stepTypeKey"] for s in steps] == [
        "warmup", "interval", "interval", "cooldown",
    ]
    assert [s["stepOrder"] for s in steps] == [1, 2, 3, 4]
    # Durations in seconds.
    assert [s["endConditionValue"] for s in steps] == [900.0, 900.0, 900.0, 300.0]
    # Zone numbers on the interval blocks.
    assert steps[1]["zoneNumber"] == 3
    assert steps[2]["zoneNumber"] == 4
    # Warmup/cooldown carry no target.
    assert steps[0]["targetType"]["workoutTargetTypeKey"] == "no.target"
    assert steps[3]["targetType"]["workoutTargetTypeKey"] == "no.target"


def test_tempo_blocks_emits_repeat_group_with_correct_children():
    """Tempo: 10 min warmup + 3 x (8 min Z4 + 3 min Z2) + 5 min cooldown."""
    workout = build_tempo_blocks_json(
        "Tempo Blocks",
        warmup_min=10,
        repeats=3,
        work_min=8,
        work_hr_zone="Z4",
        recovery_min=3,
        recovery_hr_zone="Z2",
        cooldown_min=5,
    )

    steps = workout["workoutSegments"][0]["workoutSteps"]
    assert [s["stepOrder"] for s in steps] == [1, 2, 3]
    assert steps[0]["stepType"]["stepTypeKey"] == "warmup"
    assert steps[2]["stepType"]["stepTypeKey"] == "cooldown"

    rg = steps[1]
    assert rg["type"] == "RepeatGroupDTO"
    assert rg["numberOfIterations"] == 3
    assert len(rg["workoutSteps"]) == 2

    work, recovery = rg["workoutSteps"]
    # Children stepOrder must restart at 1.
    assert work["stepOrder"] == 1
    assert recovery["stepOrder"] == 2
    # Canonical DTOs.
    assert work["stepType"] == {"stepTypeId": 3, "stepTypeKey": "interval"}
    assert work["endConditionValue"] == 480.0
    assert work["zoneNumber"] == 4
    assert recovery["stepType"] == {"stepTypeId": 4, "stepTypeKey": "recovery"}
    assert recovery["endConditionValue"] == 180.0
    assert recovery["zoneNumber"] == 2


def test_custom_hr_bpm_range_uses_target_values_not_zone_number():
    """When hr_zone is {'min': lo, 'max': hi}, the step uses targetValueOne/Two."""
    workout = build_running_workout_json(
        "Custom HR Easy",
        [
            {"kind": "interval", "duration_seconds": 1800, "hr_zone": {"min": 110, "max": 140}},
        ],
    )
    step = workout["workoutSegments"][0]["workoutSteps"][0]
    assert step["targetType"]["workoutTargetTypeKey"] == "heart.rate.zone"
    assert "zoneNumber" not in step
    assert step["targetValueOne"] == 110
    assert step["targetValueTwo"] == 140


def test_distance_based_step_uses_distance_end_condition():
    workout = build_running_workout_json(
        "1 km",
        [{"kind": "interval", "distance_meters": 1000}],
    )
    step = workout["workoutSegments"][0]["workoutSteps"][0]
    assert step["endCondition"] == {"conditionTypeId": 3, "conditionTypeKey": "distance"}
    assert step["endConditionValue"] == 1000.0


def test_pace_seconds_per_km_target_is_explicit_and_converted_to_mps():
    workout = build_running_workout_json(
        "Pace Range",
        [{"kind": "interval", "duration_seconds": 1200, "pace_seconds_per_km": [300, 330]}],
    )
    step = workout["workoutSegments"][0]["workoutSteps"][0]
    assert step["targetType"] == {"workoutTargetTypeId": 6, "workoutTargetTypeKey": "pace.zone"}
    assert step["targetValueOne"] == pytest.approx(1000 / 300)
    assert step["targetValueTwo"] == pytest.approx(1000 / 330)


def test_pace_min_per_km_target_is_explicit_and_converted_to_mps():
    workout = build_running_workout_json(
        "Pace Range",
        [{"kind": "interval", "duration_seconds": 1200, "pace_min_per_km": {"fast": 5.0, "slow": 5.5}}],
    )
    step = workout["workoutSegments"][0]["workoutSteps"][0]
    assert step["targetType"]["workoutTargetTypeKey"] == "pace.zone"
    assert step["targetValueOne"] == pytest.approx(1000 / 300)
    assert step["targetValueTwo"] == pytest.approx(1000 / 330)


def test_ambiguous_pace_field_is_rejected():
    with pytest.raises(ValueError, match="ambiguous target"):
        build_running_workout_json(
            "bad",
            [{"kind": "interval", "duration_seconds": 60, "pace": [5.0, 5.5]}],
        )


def test_hr_and_pace_targets_are_mutually_exclusive():
    with pytest.raises(ValueError, match="either hr_zone or an explicit pace"):
        build_running_workout_json(
            "bad",
            [
                {
                    "kind": "interval",
                    "duration_seconds": 60,
                    "hr_zone": "Z3",
                    "pace_seconds_per_km": [300, 330],
                }
            ],
        )


def test_building_repeat_group_without_iterations_raises():
    with pytest.raises(ValueError, match="iterations"):
        build_running_workout_json(
            "bad",
            [{"repeat": {"steps": [{"kind": "interval", "duration_seconds": 60}]}}],
        )


def test_building_repeat_group_without_children_raises():
    with pytest.raises(ValueError, match="non-empty"):
        build_running_workout_json(
            "bad",
            [{"repeat": {"iterations": 3, "steps": []}}],
        )


def test_step_requires_either_duration_or_distance_not_both():
    with pytest.raises(ValueError, match="exactly one"):
        build_running_workout_json(
            "bad",
            [{"kind": "interval", "duration_seconds": 60, "distance_meters": 400}],
        )


# ---------------------------------------------------------------------------
# validate_running_workout_data — local linter
# ---------------------------------------------------------------------------

def test_validator_accepts_canonical_progression_run():
    workout = build_progression_run_json(
        "P", warmup_min=15,
        blocks=[{"duration_min": 15, "hr_zone": "Z3"}, {"duration_min": 15, "hr_zone": "Z4"}],
        cooldown_min=5,
    )
    report = validate_running_workout_data(workout)
    assert report["ok"], report["issues"]
    assert report["issues"] == []
    assert report["summary"]["workoutName"] == "P"
    assert report["summary"]["sport"] == "running"


def test_validator_accepts_canonical_tempo_blocks_with_repeat_group():
    workout = build_tempo_blocks_json(
        "T", warmup_min=10, repeats=3,
        work_min=8, work_hr_zone="Z4",
        recovery_min=3, recovery_hr_zone="Z2",
        cooldown_min=5,
    )
    report = validate_running_workout_data(workout)
    assert report["ok"], report["issues"]


def test_validator_flags_mismatched_step_type_id_and_key():
    workout = build_running_workout_json(
        "bad",
        [{"kind": "interval", "duration_seconds": 60}],
    )
    # Manually break the DTO: keep the warmup key but with the interval id.
    workout["workoutSegments"][0]["workoutSteps"][0]["stepType"] = {
        "stepTypeId": 3, "stepTypeKey": "warmup",
    }
    report = validate_running_workout_data(workout)
    assert not report["ok"]
    assert any("stepTypeId" in issue and "warmup" in issue for issue in report["issues"])


def test_validator_flags_repeat_group_missing_iterations():
    workout = build_running_workout_json("simple", [{"kind": "interval", "duration_seconds": 60}])
    # Inject a malformed RepeatGroupDTO.
    workout["workoutSegments"][0]["workoutSteps"] = [
        {
            "type": "RepeatGroupDTO",
            "stepOrder": 1,
            "workoutSteps": [
                {
                    "type": "ExecutableStepDTO",
                    "stepOrder": 1,
                    "stepType": {"stepTypeId": 3, "stepTypeKey": "interval"},
                    "endCondition": {"conditionTypeId": 2, "conditionTypeKey": "time"},
                    "endConditionValue": 60.0,
                    "targetType": {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"},
                }
            ],
        }
    ]
    report = validate_running_workout_data(workout)
    assert not report["ok"]
    assert any("numberOfIterations" in issue for issue in report["issues"])


def test_validator_flags_repeat_group_with_empty_children():
    workout = {
        "workoutName": "x",
        "sportType": {"sportTypeId": 1, "sportTypeKey": "running"},
        "workoutSegments": [{
            "segmentOrder": 1,
            "sportType": {"sportTypeId": 1, "sportTypeKey": "running"},
            "workoutSteps": [{
                "type": "RepeatGroupDTO",
                "stepOrder": 1,
                "numberOfIterations": 3,
                "workoutSteps": [],
            }],
        }],
    }
    report = validate_running_workout_data(workout)
    assert not report["ok"]
    assert any("non-empty" in issue for issue in report["issues"])


def test_validator_flags_hr_zone_target_without_zone_number_or_range():
    workout = build_running_workout_json("simple", [{"kind": "interval", "duration_seconds": 60}])
    step = workout["workoutSegments"][0]["workoutSteps"][0]
    step["targetType"] = {"workoutTargetTypeId": 4, "workoutTargetTypeKey": "heart.rate.zone"}
    step.pop("zoneNumber", None)
    step.pop("targetValueOne", None)
    step.pop("targetValueTwo", None)
    report = validate_running_workout_data(workout)
    assert not report["ok"]
    assert any("heart.rate.zone" in issue for issue in report["issues"])


def test_validator_flags_non_running_sport():
    workout = {
        "workoutName": "bike",
        "sportType": {"sportTypeId": 2, "sportTypeKey": "cycling"},
        "workoutSegments": [{
            "segmentOrder": 1,
            "sportType": {"sportTypeId": 2, "sportTypeKey": "cycling"},
            "workoutSteps": [{
                "type": "ExecutableStepDTO",
                "stepOrder": 1,
                "stepType": {"stepTypeId": 3, "stepTypeKey": "interval"},
                "endCondition": {"conditionTypeId": 2, "conditionTypeKey": "time"},
                "endConditionValue": 60.0,
                "targetType": {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"},
            }],
        }],
    }
    report = validate_running_workout_data(workout)
    assert not report["ok"]
    assert any("not 'running'" in issue or "running" in issue for issue in report["issues"])


def test_validator_flags_step_order_gap():
    workout = build_running_workout_json("simple", [
        {"kind": "warmup", "duration_seconds": 60},
        {"kind": "interval", "duration_seconds": 60},
    ])
    # Break sequence
    workout["workoutSegments"][0]["workoutSteps"][1]["stepOrder"] = 3
    report = validate_running_workout_data(workout)
    assert not report["ok"]
    assert any("stepOrder" in issue for issue in report["issues"])


# ---------------------------------------------------------------------------
# build_garmin_api_error — rich error reporting
# ---------------------------------------------------------------------------

def _exc_with_response(status_code, *, text=None, json_body=None, url=None):
    """Build an Exception with a .response attribute mimicking requests.HTTPError."""
    exc = RuntimeError("boom")
    resp = MagicMock()
    resp.status_code = status_code
    resp.url = url or "https://example.invalid/whatever"
    if json_body is not None:
        resp.json.return_value = json_body
    else:
        resp.json.side_effect = ValueError("no json")
    resp.text = text or ""
    exc.response = resp
    return exc


def test_error_helper_extracts_status_url_and_text_body():
    exc = _exc_with_response(400, text="bad payload: missing stepType")
    report = build_garmin_api_error(
        exc,
        operation="upload_workout",
        endpoint="/workout-service/workout",
        method="POST",
        workout_data={"workoutName": "X", "sportType": {"sportTypeKey": "running"}},
    )
    assert report["status"] == "error"
    assert report["operation"] == "upload_workout"
    assert report["request_method"] == "POST"
    assert report["request_endpoint"] == "/workout-service/workout"
    assert report["http_status"] == 400
    assert report["response_body_text"].startswith("bad payload")
    assert report["request_url"].endswith("whatever")
    assert report["workout"]["sport"] == "running"
    assert report["workout"]["workoutName"] == "X"


def test_error_helper_extracts_json_body_when_available():
    exc = _exc_with_response(400, json_body={"message": "RepeatGroup: numberOfIterations is required."})
    report = build_garmin_api_error(exc, operation="upload_workout")
    assert report["http_status"] == 400
    assert report["response_body_json"] == {"message": "RepeatGroup: numberOfIterations is required."}


def test_error_helper_parses_garmin_connect_connection_error_string():
    """Without a .response, the helper falls back to parsing the exception string,
    which is the shape produced by garminconnect 0.3.2 client._run_request."""
    exc = RuntimeError("API Error 400 - RepeatGroup: numberOfIterations is required.")
    report = build_garmin_api_error(
        exc, operation="upload_workout", endpoint="/workout-service/workout", method="POST"
    )
    assert report["http_status"] == 400
    assert "numberOfIterations" in report["response_body_text"]


def test_error_helper_parses_embedded_json_dict_in_message():
    """garminconnect formatter sometimes serialises the response dict via str()."""
    exc = RuntimeError("API Error 400 - {'errorMessage': 'bad'}")
    report = build_garmin_api_error(exc, operation="upload_workout")
    assert report["http_status"] == 400
    # The dict literal is not valid JSON, so it falls back to text — that's fine,
    # we just need the body content to survive.
    body = report.get("response_body_text") or json.dumps(report.get("response_body_json"))
    assert "errorMessage" in body


def test_error_helper_includes_exception_chain():
    try:
        try:
            raise ValueError("root cause: bad zoneNumber 7")
        except ValueError:
            raise RuntimeError("API Error 400 - upstream rejected")
    except RuntimeError as exc:
        report = build_garmin_api_error(exc, operation="upload_workout")
    assert report.get("exception_chain"), "expected exception chain when __context__ is set"
    types = [entry["type"] for entry in report["exception_chain"]]
    assert types[0] == "RuntimeError"
    assert "ValueError" in types


def test_error_helper_sanitized_step_summary_has_no_pii_no_raw_body():
    workout = build_tempo_blocks_json(
        "T", warmup_min=10, repeats=3,
        work_min=8, work_hr_zone="Z4",
        recovery_min=3, recovery_hr_zone="Z2",
        cooldown_min=5,
    )
    exc = _exc_with_response(400, text="bad")
    report = build_garmin_api_error(exc, operation="upload_workout", workout_data=workout)
    summary = report["workout"]
    assert summary["workoutName"] == "T"
    assert summary["sport"] == "running"
    assert summary["segment_count"] == 1
    steps = summary["first_segment"]["steps"]
    # Should expose dto types / stepTypeKey / numberOfIterations / children but not
    # raw description fields.
    assert any(s["dto"] == "RepeatGroupDTO" for s in steps)
    rg = next(s for s in steps if s["dto"] == "RepeatGroupDTO")
    assert rg["numberOfIterations"] == 3
    assert len(rg["children"]) == 2
    assert rg["children"][0]["stepTypeKey"] == "interval"
    assert rg["children"][1]["stepTypeKey"] == "recovery"
    # Confirm no description payload leaked into the sanitized summary.
    assert "description" not in rg["children"][0]
