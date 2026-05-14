"""Self-documenting Garmin MCP help and discovery tools."""

from __future__ import annotations

import json
from typing import Any


STRENGTH_CREATE_EXAMPLE = {
    "name": "Back & Shoulders - Pull Heavy",
    "description": "Complete pull-heavy day.",
    "estimated_duration_seconds": 3300,
    "use_repeat_groups": True,
    "allow_substitutions": True,
    "verify_after_upload": True,
    "verification_mode": "lenient",
    "verbose": False,
    "blocks": [
        {
            "name": "Vertical Pull",
            "rounds": 4,
            "steps": [
                {"name": "Pull-up", "reps": 8, "rest_seconds": 90},
            ],
        },
        {
            "name": "Heavy Row",
            "rounds": 4,
            "steps": [
                {"name": "Chest Supported Dumbbell Row", "reps": 8, "weight": 20, "rest_seconds": 90},
            ],
        },
        {
            "name": "Carry Finisher",
            "rounds": 3,
            "steps": [
                {"name": "Farmer Carry", "duration_seconds": 45, "weight": 26, "rest_seconds": 60},
            ],
        },
    ],
}

STRENGTH_SCHEMA = {
    "block": {
        "name": "string",
        "rounds": "integer; use this for repeated sets",
        "steps": "list of exercise steps; use blocks[].steps, not blocks[].exercises",
    },
    "step": {
        "name": "exercise display name",
        "reps": "rep-based target",
        "duration_seconds": "timed target",
        "weight": "kg by default; omit for bodyweight",
        "rest_seconds": "rest after this step",
    },
    "examples": {
        "bodyweight_pull_up": {"name": "Pull-up", "reps": 8, "rest_seconds": 90},
        "timed_carry": {"name": "Farmer Carry", "duration_seconds": 45, "weight": 26, "rest_seconds": 60},
    },
}


# Canonical Garmin Connect running workout schema, verified against the live
# API. The DTOs below match what build_running_workout_json produces and what
# create_running_workout uploads.
RUNNING_WORKOUT_SCHEMA = {
    "sportType": {"sportTypeId": 1, "sportTypeKey": "running"},
    "step_dto_types": {
        "ExecutableStepDTO": "Single executable step (warmup / interval / recovery / cooldown).",
        "RepeatGroupDTO": "Container that repeats its workoutSteps numberOfIterations times.",
    },
    "executable_step_required_fields": {
        "type": "'ExecutableStepDTO'",
        "stepOrder": "1-based integer, sequential at each nesting level",
        "stepType": "{stepTypeId, stepTypeKey} — running uses warmup=1, cooldown=2, interval=3, recovery=4",
        "endCondition": "{conditionTypeId, conditionTypeKey} — time=2 or distance=3",
        "endConditionValue": "seconds (for time) or meters (for distance), float",
        "targetType": "{workoutTargetTypeId, workoutTargetTypeKey} — no.target=1, heart.rate.zone=4, pace.zone=6",
    },
    "executable_step_optional_fields": {
        "zoneNumber": "1-5 when targetType is heart.rate.zone and a named zone is desired",
        "targetValueOne": "low bpm when heart.rate.zone is used as a custom range",
        "targetValueTwo": "high bpm when heart.rate.zone is used as a custom range",
        "pace_target_values": "For pace.zone Garmin stores speeds in m/s: targetValueOne=faster, targetValueTwo=slower. Prefer create_running_workout explicit unit inputs.",
        "description": "human-readable label",
    },
    "repeat_group_required_fields": {
        "type": "'RepeatGroupDTO'",
        "stepOrder": "1-based integer at the parent level",
        "numberOfIterations": "integer >= 1",
        "workoutSteps": "non-empty list of ExecutableStepDTO; children stepOrder must restart at 1",
    },
    "segment_required_fields": {
        "segmentOrder": "1-based integer (currently always 1 for running workouts)",
        "sportType": "{sportTypeId: 1, sportTypeKey: 'running'}",
        "workoutSteps": "non-empty list",
    },
}

RUNNING_PROGRESSION_EXAMPLE = {
    "workoutName": "Progression Run",
    "sportType": {"sportTypeId": 1, "sportTypeKey": "running"},
    "workoutSegments": [{
        "segmentOrder": 1,
        "sportType": {"sportTypeId": 1, "sportTypeKey": "running"},
        "workoutSteps": [
            {"type": "ExecutableStepDTO", "stepOrder": 1,
             "stepType": {"stepTypeId": 1, "stepTypeKey": "warmup"},
             "endCondition": {"conditionTypeId": 2, "conditionTypeKey": "time"},
             "endConditionValue": 900.0,
             "targetType": {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"}},
            {"type": "ExecutableStepDTO", "stepOrder": 2,
             "stepType": {"stepTypeId": 3, "stepTypeKey": "interval"},
             "endCondition": {"conditionTypeId": 2, "conditionTypeKey": "time"},
             "endConditionValue": 900.0,
             "targetType": {"workoutTargetTypeId": 4, "workoutTargetTypeKey": "heart.rate.zone"},
             "zoneNumber": 3},
            {"type": "ExecutableStepDTO", "stepOrder": 3,
             "stepType": {"stepTypeId": 3, "stepTypeKey": "interval"},
             "endCondition": {"conditionTypeId": 2, "conditionTypeKey": "time"},
             "endConditionValue": 900.0,
             "targetType": {"workoutTargetTypeId": 4, "workoutTargetTypeKey": "heart.rate.zone"},
             "zoneNumber": 4},
            {"type": "ExecutableStepDTO", "stepOrder": 4,
             "stepType": {"stepTypeId": 2, "stepTypeKey": "cooldown"},
             "endCondition": {"conditionTypeId": 2, "conditionTypeKey": "time"},
             "endConditionValue": 300.0,
             "targetType": {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"}},
        ],
    }],
}

RUNNING_TEMPO_BLOCKS_EXAMPLE = {
    "workoutName": "Tempo Blocks",
    "sportType": {"sportTypeId": 1, "sportTypeKey": "running"},
    "workoutSegments": [{
        "segmentOrder": 1,
        "sportType": {"sportTypeId": 1, "sportTypeKey": "running"},
        "workoutSteps": [
            {"type": "ExecutableStepDTO", "stepOrder": 1,
             "stepType": {"stepTypeId": 1, "stepTypeKey": "warmup"},
             "endCondition": {"conditionTypeId": 2, "conditionTypeKey": "time"},
             "endConditionValue": 600.0,
             "targetType": {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"}},
            {"type": "RepeatGroupDTO", "stepOrder": 2, "numberOfIterations": 3,
             "workoutSteps": [
                 {"type": "ExecutableStepDTO", "stepOrder": 1,
                  "stepType": {"stepTypeId": 3, "stepTypeKey": "interval"},
                  "endCondition": {"conditionTypeId": 2, "conditionTypeKey": "time"},
                  "endConditionValue": 480.0,
                  "targetType": {"workoutTargetTypeId": 4, "workoutTargetTypeKey": "heart.rate.zone"},
                  "zoneNumber": 4},
                 {"type": "ExecutableStepDTO", "stepOrder": 2,
                  "stepType": {"stepTypeId": 4, "stepTypeKey": "recovery"},
                  "endCondition": {"conditionTypeId": 2, "conditionTypeKey": "time"},
                  "endConditionValue": 180.0,
                  "targetType": {"workoutTargetTypeId": 4, "workoutTargetTypeKey": "heart.rate.zone"},
                  "zoneNumber": 2},
             ]},
            {"type": "ExecutableStepDTO", "stepOrder": 3,
             "stepType": {"stepTypeId": 2, "stepTypeKey": "cooldown"},
             "endCondition": {"conditionTypeId": 2, "conditionTypeKey": "time"},
             "endConditionValue": 300.0,
             "targetType": {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"}},
        ],
    }],
}

RUNNING_GOTCHAS = [
    "Use stepTypeKey='warmup' (id=1), 'cooldown' (id=2), 'interval' (id=3), 'recovery' (id=4) for running. Do not invent other ids.",
    "stepTypeId and stepTypeKey must match. validate_running_workout will flag mismatches before they hit Garmin.",
    "RepeatGroupDTO requires numberOfIterations>=1 and a non-empty workoutSteps list. Children stepOrder restarts at 1.",
    "Top-level workoutSteps stepOrder must be 1,2,3,... in sequence; same for RepeatGroupDTO children.",
    "For HR zone targets, set targetType={workoutTargetTypeId:4, workoutTargetTypeKey:'heart.rate.zone'} AND a zoneNumber 1-5. Do NOT put the zone number in targetValueOne.",
    "For custom HR bpm ranges, omit zoneNumber and set targetValueOne (low) + targetValueTwo (high).",
    "For create_running_workout pace targets, use exactly one explicit unit field: pace_seconds_per_km, pace_min_per_km, or speed_meters_per_second. Do not use ambiguous keys like pace or speed.",
    "Garmin raw pace.zone target values are speeds in m/s, with targetValueOne=faster and targetValueTwo=slower. Example 5:00-5:30/km becomes 3.3333-3.0303 m/s.",
    "If you do not want any target on a step, send targetType={workoutTargetTypeId:1, workoutTargetTypeKey:'no.target'}; do not omit targetType.",
    "On upload errors, the connector returns http_status, the Garmin response body (json or text), the endpoint/method, a sanitized step summary, and the exception chain. Use that to self-correct instead of guessing.",
]


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, default=str)


def _workflow(name: str, sequence: list[str], notes: list[str], example: dict[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"name": name, "recommended_sequence": sequence, "notes": notes}
    if example is not None:
        payload["example"] = example
    return payload


def _workouts_topic_payload(verbose: bool = False) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "summary": "Upload, schedule, validate, and inspect Garmin running/walking/strength workouts.",
        "recommended_tools": [
            "garmin_mcp_help",
            "validate_running_workout",
            "preview_running_workout",
            "create_running_workout",
            "upload_workout",
            "upload_workouts",
            "schedule_workouts",
            "get_workouts",
            "get_workout_by_id",
            "get_scheduled_workouts",
        ],
        "common_workflows": [
            _workflow(
                "Create and upload a structured running workout",
                [
                    "preview_running_workout (or validate_running_workout)",
                    "create_running_workout",
                    "schedule_workouts (or schedule_workout)",
                    "get_scheduled_workouts",
                ],
                [
                    "create_running_workout builds the canonical DTOs for you and uploads.",
                    "If you must build JSON by hand, preview/validate first to catch DTO mismatches.",
                    "Set dry_run=true on create_running_workout to inspect the payload without uploading.",
                ],
            ),
            _workflow(
                "Inline upload and schedule in one shot",
                ["schedule_workouts"],
                [
                    "Pass schedules=[{calendar_date, workout_data}]; on upload failure the result entry still contains http_status, response_body_*, endpoint, and a sanitized step summary.",
                ],
            ),
            _workflow(
                "Debug a 400 from Garmin",
                ["upload_workout", "validate_running_workout"],
                [
                    "The upload error report includes Garmin's raw response body and the failing step summary.",
                    "Re-run validate_running_workout on the payload to discover stepTypeId/stepTypeKey mismatches, missing targets, or RepeatGroup misconfigurations.",
                ],
            ),
        ],
        "schemas": {"running_workout": RUNNING_WORKOUT_SCHEMA},
        "examples": [RUNNING_PROGRESSION_EXAMPLE, RUNNING_TEMPO_BLOCKS_EXAMPLE],
        "gotchas": RUNNING_GOTCHAS,
        "related_topics": ["strength_workouts", "scheduling", "troubleshooting"],
        "templates": {
            "simple-run": "workout://templates/simple-run",
            "progression-run": "workout://templates/progression-run",
            "tempo-blocks": "workout://templates/tempo-blocks",
            "interval-running": "workout://templates/interval-running",
            "tempo-run": "workout://templates/tempo-run",
        },
    }
    if not verbose:
        # Compact mode: keep only the progression example to reduce token cost.
        payload["examples"] = [RUNNING_PROGRESSION_EXAMPLE]
    return payload


def _topic_payload(topic: str, verbose: bool = False) -> dict[str, Any]:
    topic = (topic or "overview").lower()
    base: dict[str, Any] = {
        "status": "success",
        "topic": topic,
        "summary": "Garmin MCP exposes Garmin Connect activity, health, training, workout, nutrition, and gear tools.",
        "recommended_tools": ["garmin_mcp_help", "recommend_garmin_tools"],
        "common_workflows": [],
        "schemas": {},
        "examples": [],
        "gotchas": [
            "If only setup tools appear, reconnect the MCP connector so the client refreshes its tool list.",
            "Use verbose=false for compact LLM-friendly output unless raw Garmin payloads are needed.",
        ],
        "related_topics": ["activities", "health", "strength_workouts", "workouts", "scheduling", "troubleshooting"],
    }

    if topic == "strength_workouts":
        base.update(
            {
                "summary": "Create, validate, upload, schedule, clone, export, replace, and verify Garmin strength workout templates.",
                "recommended_tools": [
                    "recommend_garmin_tools",
                    "preview_strength_workout",
                    "create_strength_workout",
                    "schedule_strength_workout",
                    "replace_scheduled_strength_workout",
                    "get_scheduled_workouts",
                    "get_strength_workout_template",
                    "resolve_strength_exercises_bulk",
                    "roundtrip_verify_strength_exercises",
                ],
                "common_workflows": [
                    _workflow(
                        "Create and schedule a strength workout",
                        [
                            "preview_strength_workout",
                            "create_strength_workout",
                            "schedule_strength_workout",
                            "get_scheduled_workouts",
                            "get_strength_workout_template",
                        ],
                        [
                            "Use blocks[].steps; repeated sets are block rounds.",
                            "Omit weight for bodyweight exercises; do not send weight=0/null.",
                        ],
                        STRENGTH_CREATE_EXAMPLE,
                    ),
                    _workflow(
                        "Replace a scheduled strength workout",
                        [
                            "get_scheduled_workouts",
                            "replace_scheduled_strength_workout",
                            "get_scheduled_workouts",
                            "get_strength_workout_template",
                        ],
                        [
                            "scheduled_workout_id identifies the calendar entry.",
                            "workout_id identifies the reusable template.",
                        ],
                        {
                            "date": "2026-05-14",
                            "new_workout": {
                                "name": "Back & Shoulders - Pull Heavy v2",
                                "blocks": STRENGTH_CREATE_EXAMPLE["blocks"],
                            },
                            "delete_old_template": True,
                            "verify": True,
                        },
                    ),
                    _workflow("Clone a workout", ["clone_strength_workout_template"], ["Pass new_date to schedule the clone."]),
                    _workflow("Export and recreate", ["export_strength_workout_definition", "create_strength_workout"], ["Modify the returned definition before create."]),
                    _workflow(
                        "Verify exercise mappings",
                        ["search_strength_exercises", "get_strength_exercise", "resolve_strength_exercises_bulk", "roundtrip_verify_strength_exercises"],
                        ["Use roundtrip verification when exact Garmin rendering matters."],
                    ),
                ],
                "schemas": {"strength_workout": STRENGTH_SCHEMA},
                "examples": [STRENGTH_CREATE_EXAMPLE],
                "gotchas": [
                    "Use blocks[].steps, not blocks[].exercises.",
                    "Use rounds on blocks; do not put sets on steps.",
                    "Timed steps use duration_seconds; rep steps use reps.",
                    "Bodyweight steps should omit weight entirely.",
                    "Rest is rest_seconds on the exercise step.",
                    "verbose=false gives compact output and should be the default for LLM use.",
                ],
                "related_topics": ["workouts", "scheduling", "troubleshooting"],
            }
        )
    elif topic == "scheduling":
        base.update(
            {
                "summary": "Scheduling uses calendar entries. Unscheduling needs scheduled_workout_id; deleting templates needs workout_id. All destructive calendar tools require an explicit confirmation string and verify success by re-fetching the calendar (the final state is the source of truth, not the HTTP body).",
                "recommended_tools": [
                    "get_scheduled_workouts",
                    "schedule_strength_workout",
                    "schedule_workouts",
                    "unschedule_workout",
                    "preview_workout_calendar_refresh",
                    "refresh_workout_calendar",
                    "replace_scheduled_strength_workout",
                ],
                "confirmation_tokens": {
                    "unschedule_workout": "UNSCHEDULE_WORKOUT",
                    "unschedule_workout_completed": "UNSCHEDULE_COMPLETED (extra, only when entry already completed)",
                    "refresh_workout_calendar": "REFRESH_WORKOUT_CALENDAR",
                },
                "gotchas": [
                    "Garmin sometimes returns an empty body / missing status code for successful unschedules. unschedule_workout and refresh_workout_calendar treat the FINAL re-fetched calendar as the source of truth.",
                    "refresh_workout_calendar is idempotent: items whose (date, workout_id) already match the desired schedule are kept in place (counted as already_present) and never re-scheduled.",
                    "preview_workout_calendar_refresh is a strict dry-run and surfaces already_present_count, would_schedule_items, and already_satisfied.",
                ],
                "schemas": {
                    "ids": {"workout_id": "template/library workout ID", "scheduled_workout_id": "calendar entry ID", "activity_id": "completed activity ID"},
                    "ScheduleRequest": {
                        "calendar_date": "YYYY-MM-DD (required)",
                        "workout_id": "int (existing template) — exactly one of workout_id or workout_data",
                        "workout_data": "dict (inline template body)",
                        "label": "optional free-form label",
                        "expected_name": "optional, used by final verification",
                        "expected_sport": "optional, used by final verification",
                    },
                },
                "related_topics": ["strength_workouts", "workouts"],
            }
        )
    elif topic == "workouts":
        base.update(_workouts_topic_payload(verbose=verbose))
    elif topic in {"activities", "health", "training_readiness", "nutrition", "gear", "troubleshooting", "overview"}:
        topic_tools = {
            "activities": ["get_activities", "get_activity", "get_activity_splits", "get_activity_exercise_sets"],
            "health": ["get_user_summary", "get_stats", "get_sleep_data", "get_body_battery", "get_hrv_data"],
            "training_readiness": ["get_training_readiness", "get_morning_training_readiness", "get_training_status"],
            "nutrition": ["get_nutrition_daily_food_log", "get_nutrition_daily_meals", "search_foods"],
            "gear": ["get_gear", "get_gear_stats", "add_gear_to_activity", "remove_gear_from_activity"],
            "troubleshooting": ["garmin_setup_status", "garmin_mcp_help"],
            "overview": ["garmin_mcp_help", "recommend_garmin_tools"],
        }
        base["recommended_tools"] = topic_tools[topic]
    else:
        base.update({"status": "error", "message": f"Unknown help topic '{topic}'."})

    if not verbose:
        for workflow in base.get("common_workflows", []):
            if "example" in workflow and workflow["name"] != "Create and schedule a strength workout":
                workflow["example"] = {k: workflow["example"][k] for k in list(workflow["example"])[:2]}
    return base


def recommend_tools_for_intent(intent: str) -> dict[str, Any]:
    normalized = " ".join(str(intent or "").lower().split())
    if "replace" in normalized and "strength" in normalized:
        return {
            "intent": "replace scheduled strength workout",
            "recommended_sequence": [
                "get_scheduled_workouts",
                "unschedule_workout",
                "delete_strength_workout",
                "create_strength_workout",
                "schedule_strength_workout",
                "get_strength_workout_template",
            ],
            "single_tool_shortcut": "replace_scheduled_strength_workout",
            "notes": [
                "Use scheduled_workout_id for unscheduling, workout_id for deleting templates.",
                "Use confirmation strings for destructive actions.",
            ],
        }
    if "create" in normalized and "strength" in normalized:
        return {
            "intent": "create strength workout",
            "recommended_sequence": [
                "preview_strength_workout",
                "create_strength_workout",
                "schedule_strength_workout",
                "get_scheduled_workouts",
                "get_strength_workout_template",
            ],
            "single_tool_shortcut": None,
            "notes": ["Use blocks[].steps, block rounds, duration_seconds for timed steps, and omit weight for bodyweight."],
        }
    if ("create" in normalized or "build" in normalized) and ("run" in normalized or "running" in normalized):
        return {
            "intent": "create running workout",
            "recommended_sequence": [
                "preview_running_workout",
                "create_running_workout",
                "schedule_workouts",
                "get_scheduled_workouts",
            ],
            "single_tool_shortcut": "create_running_workout",
            "notes": [
                "create_running_workout builds canonical Garmin DTOs and uploads in one call.",
                "Use dry_run=true to inspect the payload first.",
                "For pace targets, use explicit unit keys such as pace_min_per_km=[5.0, 5.5] or pace_seconds_per_km=[300, 330]; raw Garmin pace.zone values are m/s.",
            ],
        }
    if "refresh" in normalized or "rebuild" in normalized or "calendar" in normalized:
        return {
            "intent": "refresh / rebuild workout calendar",
            "recommended_sequence": [
                "get_scheduled_workouts",
                "preview_workout_calendar_refresh",
                "refresh_workout_calendar",
            ],
            "single_tool_shortcut": "refresh_workout_calendar",
            "notes": [
                "Always preview first; refresh requires confirmation='REFRESH_WORKOUT_CALENDAR'.",
                "Refresh is idempotent: re-running with the same desired schedule will NOT duplicate calendar entries.",
                "Completed activities and history are NEVER touched; only incomplete planned calendar entries are unscheduled.",
            ],
        }
    if "unschedule" in normalized or "remove" in normalized:
        return {
            "intent": "unschedule a single calendar entry",
            "recommended_sequence": ["get_scheduled_workouts", "unschedule_workout"],
            "single_tool_shortcut": "unschedule_workout",
            "notes": [
                "unschedule_workout needs scheduled_workout_id (not workout_id) and confirmation='UNSCHEDULE_WORKOUT'.",
                "Garmin's empty/None response on success is handled by a postcondition refetch; status will be 'success' iff the SID is gone from the calendar.",
            ],
        }
    if "schedule" in normalized:
        return {
            "intent": "schedule workout",
            "recommended_sequence": ["get_strength_workout_template", "schedule_strength_workout", "get_scheduled_workouts"],
            "single_tool_shortcut": None,
            "notes": ["schedule_strength_workout uses workout_id, not scheduled_workout_id."],
        }
    return {
        "intent": normalized or "unknown",
        "recommended_sequence": ["garmin_mcp_help"],
        "single_tool_shortcut": None,
        "notes": ["Call garmin_mcp_help(topic='overview') or provide a more specific intent."],
    }


def register_tools(app: Any) -> Any:
    @app.tool()
    async def garmin_mcp_help(topic: str | None = None, intent: str | None = None, verbose: bool = False) -> str:
        """Start here for Garmin MCP capabilities, workflows, schemas, examples, gotchas, and related tools."""
        if intent and not topic:
            payload = _topic_payload("overview", verbose=verbose)
            payload["intent_recommendation"] = recommend_tools_for_intent(intent)
            return _json(payload)
        return _json(_topic_payload(topic or "overview", verbose=verbose))

    @app.tool()
    async def recommend_garmin_tools(intent: str) -> str:
        """Recommend the first Garmin tools to call for a natural-language intent."""
        return _json(recommend_tools_for_intent(intent))

    return app
