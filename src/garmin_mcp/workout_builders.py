"""
High-level workout builders for Garmin Connect MCP Server.

These tools construct the internal Garmin Connect JSON internally and delegate
to the existing upload_workout / schedule_workout endpoints.
"""
import json
from typing import Any, Dict, List, Optional

from garmin_mcp.workouts import (
    build_garmin_api_error,
    update_workout_template_payload,
    validate_running_workout_data,
)

# The garmin_client will be set by the main file
garmin_client = None


def configure(client):
    """Configure the module with the Garmin client instance"""
    global garmin_client
    garmin_client = client


# =============================================================================
# JSON BUILDERS
# =============================================================================

HR_ZONE_MAP = {
    "Z1": 1,
    "Z2": 2,
    "Z3": 3,
    "Z4": 4,
    "Z5": 5,
}


def _zone_number(zone: str) -> int:
    """Resolve a human-friendly zone string like 'Z3' to Garmin's zoneNumber."""
    zone_upper = zone.strip().upper()
    if zone_upper in HR_ZONE_MAP:
        return HR_ZONE_MAP[zone_upper]
    # Fallback: if user passed a digit directly
    try:
        z = int(zone_upper)
        if 1 <= z <= 5:
            return z
    except ValueError:
        pass
    raise ValueError(f"Invalid hr_zone '{zone}'. Use Z1-Z5 or 1-5.")


def build_walk_run_json(
    name: str,
    run_seconds: int,
    walk_seconds: int,
    repeats: int,
    warmup_min: int,
    cooldown_min: int,
    hr_zone: str = "Z3",
) -> dict:
    """Build the Garmin Connect JSON for a walk/run interval workout.

    Parameters match create_walk_run_workout exactly.
    """
    zone = _zone_number(hr_zone)
    return {
        "workoutName": name,
        "description": (
            f"{warmup_min}m warmup + {repeats}x({run_seconds}s run / {walk_seconds}s walk) Z{zone} + "
            f"{cooldown_min}m cooldown"
        ),
        "sportType": {"sportTypeId": 1, "sportTypeKey": "running"},
        "workoutSegments": [{
            "segmentOrder": 1,
            "sportType": {"sportTypeId": 1, "sportTypeKey": "running"},
            "workoutSteps": [
                {
                    "type": "ExecutableStepDTO",
                    "stepOrder": 1,
                    "stepType": {"stepTypeId": 1, "stepTypeKey": "warmup"},
                    "description": f"Warmup {warmup_min} min",
                    "endCondition": {"conditionTypeId": 2, "conditionTypeKey": "time"},
                    "endConditionValue": float(warmup_min * 60),
                    "targetType": {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"},
                },
                {
                    "type": "RepeatGroupDTO",
                    "stepOrder": 2,
                    "numberOfIterations": repeats,
                    "workoutSteps": [
                        {
                            "type": "ExecutableStepDTO",
                            "stepOrder": 1,
                            "stepType": {"stepTypeId": 3, "stepTypeKey": "interval"},
                            "description": f"Run {run_seconds}s Z{zone}",
                            "endCondition": {"conditionTypeId": 2, "conditionTypeKey": "time"},
                            "endConditionValue": float(run_seconds),
                            "targetType": {"workoutTargetTypeId": 4, "workoutTargetTypeKey": "heart.rate.zone"},
                            "zoneNumber": zone,
                        },
                        {
                            "type": "ExecutableStepDTO",
                            "stepOrder": 2,
                            "stepType": {"stepTypeId": 4, "stepTypeKey": "recovery"},
                            "description": f"Walk {walk_seconds}s Z{zone}",
                            "endCondition": {"conditionTypeId": 2, "conditionTypeKey": "time"},
                            "endConditionValue": float(walk_seconds),
                            "targetType": {"workoutTargetTypeId": 4, "workoutTargetTypeKey": "heart.rate.zone"},
                            "zoneNumber": zone,
                        },
                    ],
                },
                {
                    "type": "ExecutableStepDTO",
                    "stepOrder": 3,
                    "stepType": {"stepTypeId": 2, "stepTypeKey": "cooldown"},
                    "description": f"Cooldown {cooldown_min} min",
                    "endCondition": {"conditionTypeId": 2, "conditionTypeKey": "time"},
                    "endConditionValue": float(cooldown_min * 60),
                    "targetType": {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"},
                },
            ],
        }],
    }


# =============================================================================
# RUNNING WORKOUT BUILDERS
#
# These produce canonical Garmin Connect running workout JSON. The DTO map is:
#   stepTypeId=1, stepTypeKey="warmup"
#   stepTypeId=2, stepTypeKey="cooldown"
#   stepTypeId=3, stepTypeKey="interval"     (work block; ALSO used for steady runs)
#   stepTypeId=4, stepTypeKey="recovery"     (active recovery between intervals)
#   RepeatGroupDTO carries numberOfIterations and a non-empty workoutSteps list
#   whose children use their own internal stepOrder starting at 1.
# All values verified against live Garmin Connect API 2026-05.
# =============================================================================

RUNNING_SPORT_TYPE = {"sportTypeId": 1, "sportTypeKey": "running"}
NO_TARGET = {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"}
HR_ZONE_TARGET = {"workoutTargetTypeId": 4, "workoutTargetTypeKey": "heart.rate.zone"}
PACE_ZONE_TARGET = {"workoutTargetTypeId": 6, "workoutTargetTypeKey": "pace.zone"}
END_TIME = {"conditionTypeId": 2, "conditionTypeKey": "time"}
END_DISTANCE = {"conditionTypeId": 3, "conditionTypeKey": "distance"}

_RUNNING_STEP_TYPE_DTO = {
    "warmup": {"stepTypeId": 1, "stepTypeKey": "warmup"},
    "cooldown": {"stepTypeId": 2, "stepTypeKey": "cooldown"},
    "interval": {"stepTypeId": 3, "stepTypeKey": "interval"},
    "recovery": {"stepTypeId": 4, "stepTypeKey": "recovery"},
}


def _hr_target_from_spec(hr_zone: Optional[Any]) -> Dict[str, Any]:
    """Resolve an HR zone spec into target+zoneNumber fields.

    `hr_zone` accepts:
        None / "" / "no.target"  -> no target
        "Z1".."Z5" or 1..5       -> heart.rate.zone with zoneNumber
        {"min": int, "max": int} -> heart.rate.zone with custom bpm range
        [min, max] (2-tuple)     -> heart.rate.zone with custom bpm range
    """
    if hr_zone is None or hr_zone == "" or hr_zone == "no.target":
        return {"targetType": NO_TARGET}

    if isinstance(hr_zone, dict) and {"min", "max"}.issubset(hr_zone):
        lo, hi = int(hr_zone["min"]), int(hr_zone["max"])
        return {
            "targetType": HR_ZONE_TARGET,
            "targetValueOne": lo,
            "targetValueTwo": hi,
        }
    if isinstance(hr_zone, (list, tuple)) and len(hr_zone) == 2:
        lo, hi = int(hr_zone[0]), int(hr_zone[1])
        return {
            "targetType": HR_ZONE_TARGET,
            "targetValueOne": lo,
            "targetValueTwo": hi,
        }

    zone = _zone_number(str(hr_zone))
    return {
        "targetType": HR_ZONE_TARGET,
        "zoneNumber": zone,
    }


def _seconds_per_km_to_mps(seconds_per_km: float) -> float:
    seconds = float(seconds_per_km)
    if seconds <= 0:
        raise ValueError("pace_seconds_per_km values must be greater than 0")
    return 1000.0 / seconds


def _pace_range_to_target(*, fast_mps: float, slow_mps: float) -> Dict[str, Any]:
    fast = float(fast_mps)
    slow = float(slow_mps)
    if fast <= 0 or slow <= 0:
        raise ValueError("pace/speed target values must be greater than 0")
    if fast < slow:
        raise ValueError(
            "pace target range must be ordered fast-to-slow after conversion to m/s"
        )
    return {
        "targetType": PACE_ZONE_TARGET,
        "targetValueOne": fast,
        "targetValueTwo": slow,
    }


def _value_range(value: Any, field_name: str) -> tuple[float, float]:
    """Return an explicit fast-to-slow range from a number, pair, or dict."""
    if isinstance(value, dict):
        if {"fast", "slow"}.issubset(value):
            return float(value["fast"]), float(value["slow"])
        raise ValueError(f"{field_name} dict must use explicit 'fast' and 'slow' keys")
    if isinstance(value, (list, tuple)):
        if len(value) != 2:
            raise ValueError(f"{field_name} list must be [fast, slow]")
        return float(value[0]), float(value[1])
    number = float(value)
    return number, number


def _pace_target_from_spec(
    *,
    pace_seconds_per_km: Optional[Any] = None,
    pace_min_per_km: Optional[Any] = None,
    speed_meters_per_second: Optional[Any] = None,
) -> Optional[Dict[str, Any]]:
    """Build a Garmin pace.zone target from explicitly-unitized inputs.

    Garmin stores run pace targets as speed in meters/second, where
    targetValueOne is the faster bound and targetValueTwo is the slower bound.
    The public builder intentionally accepts only explicit unit-bearing fields
    so callers do not accidentally mix min/km pace with m/s speed.
    """
    provided = [
        name
        for name, value in (
            ("pace_seconds_per_km", pace_seconds_per_km),
            ("pace_min_per_km", pace_min_per_km),
            ("speed_meters_per_second", speed_meters_per_second),
        )
        if value is not None
    ]
    if not provided:
        return None
    if len(provided) > 1:
        raise ValueError(
            "Use only one pace/speed field per step: pace_seconds_per_km, "
            "pace_min_per_km, or speed_meters_per_second"
        )

    field = provided[0]
    value = {
        "pace_seconds_per_km": pace_seconds_per_km,
        "pace_min_per_km": pace_min_per_km,
        "speed_meters_per_second": speed_meters_per_second,
    }[field]
    fast, slow = _value_range(value, field)

    if field == "pace_seconds_per_km":
        if fast > slow:
            raise ValueError("pace_seconds_per_km must be ordered fast-to-slow, e.g. [300, 330]")
        return _pace_range_to_target(
            fast_mps=_seconds_per_km_to_mps(fast),
            slow_mps=_seconds_per_km_to_mps(slow),
        )
    if field == "pace_min_per_km":
        if fast > slow:
            raise ValueError("pace_min_per_km must be ordered fast-to-slow, e.g. [5.0, 5.5]")
        return _pace_range_to_target(
            fast_mps=_seconds_per_km_to_mps(fast * 60.0),
            slow_mps=_seconds_per_km_to_mps(slow * 60.0),
        )

    return _pace_range_to_target(fast_mps=fast, slow_mps=slow)


def _reject_ambiguous_target_fields(spec: Dict[str, Any], path: str) -> None:
    ambiguous = [key for key in ("pace", "speed", "targetValueOne", "targetValueTwo") if key in spec]
    if ambiguous:
        raise ValueError(
            f"{path}: ambiguous target field(s) {ambiguous}. Use explicit unit fields: "
            "pace_seconds_per_km, pace_min_per_km, or speed_meters_per_second."
        )


def _make_running_step(
    step_order: int,
    *,
    kind: str,
    duration_seconds: Optional[float] = None,
    distance_meters: Optional[float] = None,
    hr_zone: Optional[Any] = None,
    pace_seconds_per_km: Optional[Any] = None,
    pace_min_per_km: Optional[Any] = None,
    speed_meters_per_second: Optional[Any] = None,
    description: Optional[str] = None,
) -> Dict[str, Any]:
    """Construct a canonical Garmin running ExecutableStepDTO.

    `kind` is one of: warmup, cooldown, interval, recovery.
    Exactly one of duration_seconds / distance_meters must be provided.
    """
    if kind not in _RUNNING_STEP_TYPE_DTO:
        raise ValueError(
            f"Invalid running step kind '{kind}'. Use warmup, cooldown, interval, or recovery."
        )
    if (duration_seconds is None) == (distance_meters is None):
        raise ValueError(
            f"Step '{kind}' requires exactly one of duration_seconds or distance_meters"
        )

    if duration_seconds is not None:
        end_condition = END_TIME
        end_value = float(duration_seconds)
    else:
        end_condition = END_DISTANCE
        end_value = float(distance_meters)

    step: Dict[str, Any] = {
        "type": "ExecutableStepDTO",
        "stepOrder": int(step_order),
        "stepType": _RUNNING_STEP_TYPE_DTO[kind],
        "endCondition": end_condition,
        "endConditionValue": end_value,
    }
    pace_target = _pace_target_from_spec(
        pace_seconds_per_km=pace_seconds_per_km,
        pace_min_per_km=pace_min_per_km,
        speed_meters_per_second=speed_meters_per_second,
    )
    if hr_zone not in (None, "", "no.target") and pace_target is not None:
        raise ValueError("Use either hr_zone or an explicit pace/speed target on a step, not both")

    step.update(pace_target or _hr_target_from_spec(hr_zone))
    if description:
        step["description"] = description
    return step


def _make_repeat_group(
    step_order: int,
    *,
    iterations: int,
    children: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Construct a canonical Garmin RepeatGroupDTO with renumbered children."""
    if not isinstance(iterations, int) or iterations < 1:
        raise ValueError(f"RepeatGroupDTO iterations must be >= 1 (got {iterations!r})")
    if not children:
        raise ValueError("RepeatGroupDTO requires a non-empty list of children")
    renumbered = []
    for i, child in enumerate(children, start=1):
        c = dict(child)
        c["stepOrder"] = i
        renumbered.append(c)
    return {
        "type": "RepeatGroupDTO",
        "stepOrder": int(step_order),
        "numberOfIterations": int(iterations),
        "workoutSteps": renumbered,
    }


def build_running_workout_json(
    name: str,
    steps: List[Dict[str, Any]],
    *,
    description: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a canonical Garmin Connect running workout from a high-level step list.

    `steps` is a list of step specifications. Two shapes are accepted:

    1. ExecutableStep:
        {
            "kind": "warmup|cooldown|interval|recovery",
            "duration_seconds": int (optional, mutually exclusive with distance_meters),
            "distance_meters": int (optional),
            "hr_zone": "Z1".."Z5" / 1..5 / {"min": bpm, "max": bpm} / None,
            "description": "..."  (optional)
        }

    2. RepeatGroup:
        {
            "repeat": {
                "iterations": int,
                "steps": [<ExecutableStep>, ...]
            }
        }

    The function renumbers segmentOrder and stepOrder so callers do not have to.
    """
    workout_steps: List[Dict[str, Any]] = []
    for idx, spec in enumerate(steps, start=1):
        if not isinstance(spec, dict):
            raise ValueError(f"Step #{idx}: expected dict, got {type(spec).__name__}")
        if "repeat" in spec:
            rg = spec["repeat"]
            if not isinstance(rg, dict):
                raise ValueError(f"Step #{idx}: 'repeat' must be an object")
            children_specs = rg.get("steps") or []
            iterations = rg.get("iterations") or rg.get("repeats") or rg.get("numberOfIterations")
            children = []
            for j, child_spec in enumerate(children_specs, start=1):
                if not isinstance(child_spec, dict):
                    raise ValueError(f"Step #{idx} repeat child #{j}: expected dict")
                _reject_ambiguous_target_fields(child_spec, f"Step #{idx} repeat child #{j}")
                children.append(
                    _make_running_step(
                        j,
                        kind=child_spec.get("kind"),
                        duration_seconds=child_spec.get("duration_seconds"),
                        distance_meters=child_spec.get("distance_meters"),
                        hr_zone=child_spec.get("hr_zone"),
                        pace_seconds_per_km=child_spec.get("pace_seconds_per_km"),
                        pace_min_per_km=child_spec.get("pace_min_per_km"),
                        speed_meters_per_second=child_spec.get("speed_meters_per_second"),
                        description=child_spec.get("description"),
                    )
                )
            workout_steps.append(
                _make_repeat_group(idx, iterations=int(iterations or 0), children=children)
            )
        else:
            _reject_ambiguous_target_fields(spec, f"Step #{idx}")
            workout_steps.append(
                _make_running_step(
                    idx,
                    kind=spec.get("kind"),
                    duration_seconds=spec.get("duration_seconds"),
                    distance_meters=spec.get("distance_meters"),
                    hr_zone=spec.get("hr_zone"),
                    pace_seconds_per_km=spec.get("pace_seconds_per_km"),
                    pace_min_per_km=spec.get("pace_min_per_km"),
                    speed_meters_per_second=spec.get("speed_meters_per_second"),
                    description=spec.get("description"),
                )
            )

    workout: Dict[str, Any] = {
        "workoutName": name,
        "sportType": dict(RUNNING_SPORT_TYPE),
        "workoutSegments": [
            {
                "segmentOrder": 1,
                "sportType": dict(RUNNING_SPORT_TYPE),
                "workoutSteps": workout_steps,
            }
        ],
    }
    if description:
        workout["description"] = description
    return workout


def build_progression_run_json(
    name: str,
    *,
    warmup_min: int,
    blocks: List[Dict[str, Any]],
    cooldown_min: int,
    description: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a progression-run workout: warmup, N work blocks with rising intensity, cooldown.

    `blocks` is a list of {"duration_min": int, "hr_zone": "Z2".."Z5" | None}.
    Each block becomes an interval step.
    """
    steps: List[Dict[str, Any]] = [
        {
            "kind": "warmup",
            "duration_seconds": int(warmup_min) * 60,
            "description": f"Warmup {warmup_min} min",
        }
    ]
    for blk in blocks:
        steps.append(
            {
                "kind": "interval",
                "duration_seconds": int(blk["duration_min"]) * 60,
                "hr_zone": blk.get("hr_zone"),
                "description": (
                    f"{int(blk['duration_min'])} min "
                    f"{blk.get('hr_zone') or 'no target'}"
                ),
            }
        )
    steps.append(
        {
            "kind": "cooldown",
            "duration_seconds": int(cooldown_min) * 60,
            "description": f"Cooldown {cooldown_min} min",
        }
    )
    return build_running_workout_json(name, steps, description=description)


def build_tempo_blocks_json(
    name: str,
    *,
    warmup_min: int,
    repeats: int,
    work_min: int,
    work_hr_zone: Optional[Any],
    recovery_min: int,
    recovery_hr_zone: Optional[Any],
    cooldown_min: int,
    description: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a tempo-blocks workout: warmup + N * (work + recovery) + cooldown.

    Uses a RepeatGroupDTO around the work+recovery pair.
    """
    steps: List[Dict[str, Any]] = [
        {
            "kind": "warmup",
            "duration_seconds": int(warmup_min) * 60,
            "description": f"Warmup {warmup_min} min",
        },
        {
            "repeat": {
                "iterations": int(repeats),
                "steps": [
                    {
                        "kind": "interval",
                        "duration_seconds": int(work_min) * 60,
                        "hr_zone": work_hr_zone,
                        "description": f"Work {work_min} min {work_hr_zone or 'no target'}",
                    },
                    {
                        "kind": "recovery",
                        "duration_seconds": int(recovery_min) * 60,
                        "hr_zone": recovery_hr_zone,
                        "description": f"Recovery {recovery_min} min {recovery_hr_zone or 'no target'}",
                    },
                ],
            }
        },
        {
            "kind": "cooldown",
            "duration_seconds": int(cooldown_min) * 60,
            "description": f"Cooldown {cooldown_min} min",
        },
    ]
    return build_running_workout_json(name, steps, description=description)


def build_z2_walk_json(
    name: str,
    duration_min: int,
    hr_min: int,
    hr_max: int,
) -> dict:
    """Build the Garmin Connect JSON for a steady Z2 walking workout with absolute HR range."""
    return {
        "workoutName": name,
        "description": f"Walk {duration_min} min at Z2 ({hr_min}-{hr_max} bpm)",
        "sportType": {"sportTypeId": 12, "sportTypeKey": "walking"},
        "workoutSegments": [{
            "segmentOrder": 1,
            "sportType": {"sportTypeId": 12, "sportTypeKey": "walking"},
            "workoutSteps": [
                {
                    "type": "ExecutableStepDTO",
                    "stepOrder": 1,
                    "stepType": {"stepTypeId": 1, "stepTypeKey": "warmup"},
                    "description": "Warmup 5 min",
                    "endCondition": {"conditionTypeId": 2, "conditionTypeKey": "time"},
                    "endConditionValue": 300.0,
                    "targetType": {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"},
                },
                {
                    "type": "ExecutableStepDTO",
                    "stepOrder": 2,
                    "stepType": {"stepTypeId": 3, "stepTypeKey": "interval"},
                    "description": f"Walk {duration_min} min Z2",
                    "endCondition": {"conditionTypeId": 2, "conditionTypeKey": "time"},
                    "endConditionValue": float(duration_min * 60),
                    "targetType": {"workoutTargetTypeId": 4, "workoutTargetTypeKey": "heart.rate.zone"},
                    "zoneNumber": 2,
                },
                {
                    "type": "ExecutableStepDTO",
                    "stepOrder": 3,
                    "stepType": {"stepTypeId": 2, "stepTypeKey": "cooldown"},
                    "description": "Cooldown 5 min",
                    "endCondition": {"conditionTypeId": 2, "conditionTypeKey": "time"},
                    "endConditionValue": 300.0,
                    "targetType": {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"},
                },
            ],
        }],
    }


# Simplified internal exercise catalog (English → Garmin exerciseName key or fallback)
# Garmin strength workouts use exerciseName as a free-text label when the exercise
# is not in their catalog. For structured strength, we use "Other" (generic) and
# put the user name in description / exerciseName.

def build_strength_json(
    name: str,
    exercises: List[Dict[str, Any]],
) -> dict:
    """Build the Garmin Connect JSON for a strength workout.

    Each exercise maps to a generic step; if the name is not recognised in the
    Garmin catalog we use 'Other' and put the original name in exerciseName.
    """
    steps: List[dict] = []
    step_order = 1

    for ex in exercises:
        ex_name = ex.get("name", "Exercise")
        sets = int(ex.get("sets", 1))
        reps = int(ex.get("reps", 1))
        rest_seconds = int(ex.get("rest_seconds", 60))

        # Work step
        steps.append({
            "type": "ExecutableStepDTO",
            "stepOrder": step_order,
            "stepType": {"stepTypeId": 3, "stepTypeKey": "interval"},
            "description": f"{ex_name}: {sets} sets x {reps} reps",
            "endCondition": {"conditionTypeId": 2, "conditionTypeKey": "time"},
            "endConditionValue": float(sets * 45),  # rough estimate: 45s per set
            "targetType": {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"},
            "exerciseName": ex_name,
        })
        step_order += 1

        # Rest step (skip after last exercise)
        if rest_seconds > 0 and ex != exercises[-1]:
            steps.append({
                "type": "ExecutableStepDTO",
                "stepOrder": step_order,
                "stepType": {"stepTypeId": 4, "stepTypeKey": "recovery"},
                "description": f"Rest {rest_seconds}s",
                "endCondition": {"conditionTypeId": 2, "conditionTypeKey": "time"},
                "endConditionValue": float(rest_seconds),
                "targetType": {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"},
            })
            step_order += 1

    return {
        "workoutName": name,
        "description": f"Strength: {len(exercises)} exercises",
        "sportType": {"sportTypeId": 5, "sportTypeKey": "strength_training"},
        "workoutSegments": [{
            "segmentOrder": 1,
            "sportType": {"sportTypeId": 5, "sportTypeKey": "strength_training"},
            "workoutSteps": steps,
        }],
    }


# =============================================================================
# MCP TOOLS
# =============================================================================

def register_tools(app):
    """Register all high-level workout builder tools with the MCP server app"""

    @app.tool()
    async def create_running_workout(
        name: str,
        steps: List[Dict[str, Any]],
        description: Optional[str] = None,
        dry_run: bool = False,
    ) -> str:
        """Create a structured running workout from a high-level step list and upload it.

        This builder produces the canonical Garmin Connect running JSON
        (warmup=stepTypeId 1, cooldown=2, interval=3, recovery=4, RepeatGroupDTO
        for repeats) and runs the local validator before contacting Garmin.

        Args:
            name: Workout name.
            steps: List of step specs. Two shapes are accepted:

                ExecutableStep:
                  {"kind": "warmup|cooldown|interval|recovery",
                   "duration_seconds": int OR "distance_meters": float,
                   "hr_zone": "Z1".."Z5" | 1..5 | {"min": bpm, "max": bpm} | None,
                   "pace_seconds_per_km": [fast_seconds, slow_seconds] OR
                   "pace_min_per_km": [fast_minutes, slow_minutes] OR
                   "speed_meters_per_second": [fast_mps, slow_mps],
                   "description": optional}

                Pace/speed targets must use one of the explicit unit fields
                above. Garmin receives pace.zone values as meters/second, with
                targetValueOne=faster and targetValueTwo=slower.

                RepeatGroup:
                  {"repeat": {"iterations": int,
                              "steps": [<ExecutableStep>, ...]}}
            description: Optional workout description.
            dry_run: If true, build + validate the JSON locally but do not upload.
                     Returns the proposed JSON and validation report.

        Examples:
            Simple run with warmup + Z2 main + cooldown:
                steps = [
                    {"kind": "warmup",   "duration_seconds": 600},
                    {"kind": "interval", "duration_seconds": 1200, "hr_zone": "Z2"},
                    {"kind": "cooldown", "duration_seconds": 300},
                ]

            Tempo blocks (3 x (8 min Z4 + 3 min Z2)):
                steps = [
                    {"kind": "warmup", "duration_seconds": 600},
                    {"repeat": {"iterations": 3, "steps": [
                        {"kind": "interval", "duration_seconds": 480, "hr_zone": "Z4"},
                        {"kind": "recovery", "duration_seconds": 180, "hr_zone": "Z2"},
                    ]}},
                    {"kind": "cooldown", "duration_seconds": 300},
                ]
        """
        try:
            workout_json = build_running_workout_json(name, steps, description=description)
        except Exception as build_exc:
            return json.dumps(
                {
                    "status": "error",
                    "operation": "build_running_workout_json",
                    "error_type": type(build_exc).__name__,
                    "message": str(build_exc),
                },
                indent=2,
            )

        report = validate_running_workout_data(workout_json)
        if not report["ok"]:
            return json.dumps(
                {
                    "status": "invalid",
                    "operation": "validate_running_workout",
                    "message": "Built workout failed local validation; not uploading.",
                    "issues": report["issues"],
                    "workout_json": workout_json,
                    "summary": report["summary"],
                },
                indent=2,
            )

        if dry_run:
            return json.dumps(
                {
                    "status": "dry_run",
                    "valid": True,
                    "summary": report["summary"],
                    "workout_json": workout_json,
                },
                indent=2,
            )

        try:
            result = garmin_client.upload_workout(workout_json)
        except Exception as upload_exc:
            return json.dumps(
                build_garmin_api_error(
                    upload_exc,
                    operation="upload_workout",
                    endpoint="/workout-service/workout",
                    method="POST",
                    workout_data=workout_json,
                ),
                indent=2,
            )

        if isinstance(result, dict):
            curated = {
                "status": "success",
                "workout_id": result.get("workoutId"),
                "name": result.get("workoutName"),
                "summary": report["summary"],
                "message": "Running workout uploaded successfully",
            }
            return json.dumps({k: v for k, v in curated.items() if v is not None}, indent=2)
        return json.dumps(result, indent=2)

    @app.tool()
    async def update_running_workout(
        workout_id: int,
        steps: List[Dict[str, Any]],
        name: Optional[str] = None,
        description: Optional[str] = None,
        verify_after_update: bool = True,
        dry_run: bool = False,
        verbose: bool = False,
    ) -> str:
        """Update an existing Garmin running workout template in place.

        Builds the same canonical Garmin running JSON as create_running_workout,
        validates it locally, then updates the existing template without
        deleting/recreating the template and without touching calendar entries.

        Args:
            workout_id: Existing Garmin workout template ID to update.
            name: Optional replacement name. If omitted for a real update, the
                  existing template name is preserved. Dry runs use a placeholder
                  name when omitted because they do not contact Garmin.
            description: Optional replacement description.
            steps: High-level running step list accepted by create_running_workout.
            verify_after_update: Fetch after update and verify the template.
            dry_run: Build and validate only; do not contact Garmin.
            verbose: Include requested payload and fetched after-workout details.
        """
        effective_name = name
        if effective_name is None and not dry_run:
            try:
                existing = garmin_client.get_workout_by_id(int(workout_id))
            except Exception as fetch_exc:
                return json.dumps(
                    build_garmin_api_error(
                        fetch_exc,
                        operation="get_workout_by_id",
                        endpoint=f"/workout-service/workout/{int(workout_id)}",
                        method="GET",
                        extra={"workout_id": int(workout_id)},
                    ),
                    indent=2,
                )
            if not isinstance(existing, dict) or not existing:
                return json.dumps(
                    {
                        "status": "error",
                        "workout_id": int(workout_id),
                        "message": f"No workout template found with ID {int(workout_id)}.",
                    },
                    indent=2,
                )
            effective_name = existing.get("workoutName")
        if effective_name is None:
            effective_name = f"Workout {int(workout_id)}"

        try:
            workout_json = build_running_workout_json(
                effective_name,
                steps,
                description=description,
            )
        except Exception as build_exc:
            return json.dumps(
                {
                    "status": "error",
                    "operation": "build_running_workout_json",
                    "workout_id": int(workout_id),
                    "error_type": type(build_exc).__name__,
                    "message": str(build_exc),
                },
                indent=2,
            )

        report = validate_running_workout_data(workout_json)
        if not report["ok"]:
            return json.dumps(
                {
                    "status": "invalid",
                    "operation": "validate_running_workout",
                    "workout_id": int(workout_id),
                    "message": "Built workout failed local validation; not updating.",
                    "issues": report["issues"],
                    "validation_report": report,
                    "workout_json": workout_json,
                    "summary": report["summary"],
                },
                indent=2,
            )

        if dry_run:
            return json.dumps(
                {
                    "status": "dry_run",
                    "workout_id": int(workout_id),
                    "valid": True,
                    "validation_report": report,
                    "summary": report["summary"],
                    "workout_json": workout_json,
                },
                indent=2,
            )

        result = update_workout_template_payload(
            garmin_client,
            int(workout_id),
            workout_json,
            verify_after_update=verify_after_update,
            verbose=verbose,
            validation_report=report,
        )
        return json.dumps(result, indent=2, default=str)

    @app.tool()
    async def create_walk_run_workout(
        name: str,
        run_seconds: int,
        walk_seconds: int,
        repeats: int,
        warmup_min: int,
        cooldown_min: int,
        hr_zone: str = "Z3",
    ) -> str:
        """Create a walk/run interval workout and upload it to Garmin Connect.

        Builds the internal Garmin JSON automatically and returns the new workout ID.

        Args:
            name: Workout name (e.g. "W3 Mié 2:2")
            run_seconds: Duration of each run interval in seconds
            walk_seconds: Duration of each walk/recovery interval in seconds
            repeats: Number of run/walk repetitions
            warmup_min: Warmup duration in minutes
            cooldown_min: Cooldown duration in minutes
            hr_zone: Target heart-rate zone (Z1-Z5, default Z3)
        """
        try:
            workout_json = build_walk_run_json(
                name=name,
                run_seconds=run_seconds,
                walk_seconds=walk_seconds,
                repeats=repeats,
                warmup_min=warmup_min,
                cooldown_min=cooldown_min,
                hr_zone=hr_zone,
            )
            result = garmin_client.upload_workout(workout_json)

            if isinstance(result, dict):
                curated = {
                    "status": "success",
                    "workout_id": result.get("workoutId"),
                    "name": result.get("workoutName"),
                    "message": "Workout uploaded successfully",
                }
                curated = {k: v for k, v in curated.items() if v is not None}
                return json.dumps(curated, indent=2)
            return json.dumps(result, indent=2)
        except Exception as e:
            return f"Error creating walk/run workout: {str(e)}"

    @app.tool()
    async def create_z2_walk_workout(
        name: str,
        duration_min: int,
        hr_min: int,
        hr_max: int,
    ) -> str:
        """Create a steady Z2 walking workout and upload it to Garmin Connect.

        Args:
            name: Workout name
            duration_min: Main walking block duration in minutes
            hr_min: Minimum heart rate in bpm (used for description; target is Z2)
            hr_max: Maximum heart rate in bpm (used for description; target is Z2)
        """
        try:
            workout_json = build_z2_walk_json(
                name=name,
                duration_min=duration_min,
                hr_min=hr_min,
                hr_max=hr_max,
            )
            result = garmin_client.upload_workout(workout_json)

            if isinstance(result, dict):
                curated = {
                    "status": "success",
                    "workout_id": result.get("workoutId"),
                    "name": result.get("workoutName"),
                    "message": "Workout uploaded successfully",
                }
                curated = {k: v for k, v in curated.items() if v is not None}
                return json.dumps(curated, indent=2)
            return json.dumps(result, indent=2)
        except Exception as e:
            return f"Error creating Z2 walk workout: {str(e)}"

    @app.tool()
    async def create_strength_workout(
        name: str,
        exercises: List[Dict[str, Any]],
    ) -> str:
        """Create a strength workout and upload it to Garmin Connect.

        Each exercise is mapped to a generic step; unsupported names fallback to
        "Other" with the original name stored in exerciseName.

        Args:
            name: Workout name
            exercises: List of dicts with keys: name, sets, reps, rest_seconds
        """
        try:
            workout_json = build_strength_json(name=name, exercises=exercises)
            result = garmin_client.upload_workout(workout_json)

            if isinstance(result, dict):
                curated = {
                    "status": "success",
                    "workout_id": result.get("workoutId"),
                    "name": result.get("workoutName"),
                    "message": "Workout uploaded successfully",
                }
                curated = {k: v for k, v in curated.items() if v is not None}
                return json.dumps(curated, indent=2)
            return json.dumps(result, indent=2)
        except Exception as e:
            return f"Error creating strength workout: {str(e)}"

    @app.tool()
    async def schedule_week(week: List[Dict[str, Any]]) -> str:
        """Schedule a list of workouts for the week in a single call.

        Args:
            week: List of dicts with keys: date (YYYY-MM-DD), workout_id (int)
        """
        try:
            results = []
            for item in week:
                calendar_date = item["date"]
                workout_id = int(item["workout_id"])
                url = f"workout-service/schedule/{workout_id}"
                response = garmin_client.garth.post(
                    "connectapi", url, json={"date": calendar_date}
                )
                if response.status_code == 200:
                    results.append({
                        "date": calendar_date,
                        "workout_id": workout_id,
                        "status": "scheduled",
                    })
                else:
                    results.append({
                        "date": calendar_date,
                        "workout_id": workout_id,
                        "status": "failed",
                        "http_status": response.status_code,
                    })
            return json.dumps({
                "status": "complete",
                "scheduled": results,
            }, indent=2)
        except Exception as e:
            return f"Error scheduling week: {str(e)}"

    return app
