"""
Integration tests for the refresh_workout_calendar / preview_workout_calendar_refresh
/ unschedule_workout workflow.

These tests use the FastMCP integration with a mocked Garmin client. They verify
the safety contract of the calendar-refresh tools:

- preview never mutates anything.
- refresh refuses to run without confirmation="REFRESH_WORKOUT_CALENDAR".
- refresh aborts BEFORE mutation when a schedule item is invalid.
- refresh unschedules only incomplete future planned workouts.
- refresh preserves completed workouts.
- refresh preserves activities and historical data (it never calls activity
  deletion APIs).
- refresh supports multiple workouts on one date.
- refresh supports running, cycling, and strength workouts in one batch.
- refresh uploads inline workout_data and schedules the uploaded workout.
- refresh schedules existing workout_id items.
- refresh verification catches missing final scheduled items.
- delete_unused_templates=false never deletes templates.
- delete_unused_templates=true only deletes clearly temporary/test/draft
  templates and never deletes currently scheduled templates.
"""

import json
import pytest
from unittest.mock import MagicMock
from mcp.server.fastmcp import FastMCP

from garmin_mcp import workouts


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _result_text(result) -> dict:
    """Decode the JSON body of a FastMCP tool response."""
    return json.loads(result[0][0].text)


def _make_scheduled(
    scheduled_workout_id: int,
    workout_id: int | None,
    name: str,
    sport: str,
    date: str,
    completed: bool = False,
    activity_id: int | None = None,
    workout_uuid: str | None = None,
    workout_phrase: str | None = None,
) -> dict:
    """Build a raw GraphQL scheduled-workout entry as Garmin returns it."""
    item = {
        "scheduledWorkoutId": scheduled_workout_id,
        "workoutId": workout_id,
        "workoutName": name,
        "workoutType": sport,
        "scheduleDate": date,
        "associatedActivityId": activity_id if completed else None,
    }
    if workout_uuid is not None:
        item["workoutUuid"] = workout_uuid
    if workout_phrase is not None:
        item["workoutPhrase"] = workout_phrase
    return item


def _inline_workout_data(name: str, sport: str = "running") -> dict:
    """Minimal valid-looking inline workout body for upload-and-schedule."""
    return {
        "workoutName": name,
        "sportType": {"sportTypeId": 1, "sportTypeKey": sport},
        "workoutSegments": [
            {
                "segmentOrder": 1,
                "sportType": {"sportTypeId": 1, "sportTypeKey": sport},
                "workoutSteps": [],
            }
        ],
    }


@pytest.fixture
def app_with_workouts(mock_garmin_client):
    """Create FastMCP app with workouts tools registered."""
    workouts.configure(mock_garmin_client)
    app = FastMCP("Test Calendar Refresh")
    app = workouts.register_tools(app)
    return app


@pytest.fixture
def http_ok_response():
    response = MagicMock()
    response.status_code = 200
    response.json = MagicMock(return_value={"ok": True})
    response.text = '{"ok": true}'
    return response


@pytest.fixture
def http_204_response():
    response = MagicMock()
    response.status_code = 204
    response.json = MagicMock(side_effect=Exception("no body"))
    response.text = ""
    return response


# ---------------------------------------------------------------------------
# unschedule_workout
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unschedule_workout_requires_confirmation(app_with_workouts, mock_garmin_client):
    result = await app_with_workouts.call_tool(
        "unschedule_workout",
        {"scheduled_workout_id": 12345},
    )
    data = _result_text(result)
    assert data["status"] == "error"
    assert "UNSCHEDULE_WORKOUT" in data["message"]
    # No HTTP calls allowed.
    mock_garmin_client.client.delete.assert_not_called()


@pytest.mark.asyncio
async def test_unschedule_workout_refuses_completed_without_extra_confirmation(
    app_with_workouts, mock_garmin_client, http_204_response
):
    # Looking up the entry reveals it is completed.
    mock_garmin_client.query_garmin_graphql.return_value = {
        "data": {
            "workoutScheduleSummariesScalar": [
                _make_scheduled(
                    scheduled_workout_id=12345,
                    workout_id=999,
                    name="Old Easy Run",
                    sport="running",
                    date="2026-05-01",
                    completed=True,
                    activity_id=777,
                ),
            ]
        }
    }
    mock_garmin_client.client.delete.return_value = http_204_response

    result = await app_with_workouts.call_tool(
        "unschedule_workout",
        {"scheduled_workout_id": 12345, "confirmation": "UNSCHEDULE_WORKOUT"},
    )
    data = _result_text(result)
    assert data["status"] == "error"
    assert "completed" in data["message"].lower()
    assert data["completed"] is True
    # Critically: no HTTP delete was issued.
    mock_garmin_client.client.delete.assert_not_called()


@pytest.mark.asyncio
async def test_unschedule_workout_removes_only_calendar_entry(
    app_with_workouts, mock_garmin_client, http_204_response, no_retry_sleep
):
    # Lookup returns an incomplete entry, then the post-mutation refetch shows
    # the entry is gone — postcondition: removed.
    entry = _make_scheduled(
        scheduled_workout_id=12345,
        workout_id=999,
        name="Easy Run",
        sport="running",
        date="2026-05-20",
    )
    mock_garmin_client.query_garmin_graphql.side_effect = [
        {"data": {"workoutScheduleSummariesScalar": [entry]}},  # preflight lookup
        {"data": {"workoutScheduleSummariesScalar": []}},        # postcondition refetch
    ]
    mock_garmin_client.client.delete.return_value = http_204_response

    result = await app_with_workouts.call_tool(
        "unschedule_workout",
        {"scheduled_workout_id": 12345, "confirmation": "UNSCHEDULE_WORKOUT"},
    )
    data = _result_text(result)
    assert data["status"] == "success"
    assert data["scheduled_workout_id"] == 12345
    assert data["workout_id"] == 999
    assert data["name"] == "Easy Run"
    assert data["sport"] == "running"
    assert data["date"] == "2026-05-20"
    assert data["garmin_response_summary"]["postcondition_result"] == "removed"
    # Exactly one DELETE to the calendar endpoint, NO calls to delete
    # workout templates or activities.
    mock_garmin_client.client.delete.assert_called_once_with(
        "connectapi", "workout-service/schedule/12345", api=True
    )


# ---------------------------------------------------------------------------
# preview_workout_calendar_refresh -- never mutates
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_preview_does_not_mutate(app_with_workouts, mock_garmin_client):
    mock_garmin_client.query_garmin_graphql.return_value = {
        "data": {
            "workoutScheduleSummariesScalar": [
                _make_scheduled(1001, 11, "Old Easy Run", "running", "2026-05-13"),
                _make_scheduled(
                    1002, 12, "Done Bike", "cycling", "2026-05-12",
                    completed=True, activity_id=888,
                ),
            ]
        }
    }

    schedules = [
        {"calendar_date": "2026-05-13", "workout_id": 555, "label": "Easy"},
        {"calendar_date": "2026-05-14", "workout_data": _inline_workout_data("New Run")},
    ]
    result = await app_with_workouts.call_tool(
        "preview_workout_calendar_refresh",
        {
            "start_date": "2026-05-11",
            "end_date": "2026-05-17",
            "schedules": schedules,
        },
    )
    data = _result_text(result)
    assert data["status"] == "success"
    assert data["current_scheduled_count"] == 2
    assert data["would_remove_count"] == 1
    assert data["preserved_completed_count"] == 1
    assert data["would_upload_count"] == 1
    assert data["would_schedule_count"] == 2

    # NO mutation methods were called.
    mock_garmin_client.upload_workout.assert_not_called()
    mock_garmin_client.client.post.assert_not_called()
    mock_garmin_client.client.delete.assert_not_called()


# ---------------------------------------------------------------------------
# refresh_workout_calendar -- confirmation + abort-before-mutation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_refresh_requires_confirmation(app_with_workouts, mock_garmin_client):
    result = await app_with_workouts.call_tool(
        "refresh_workout_calendar",
        {
            "start_date": "2026-05-13",
            "end_date": "2026-05-17",
            "schedules": [{"calendar_date": "2026-05-14", "workout_id": 1}],
        },
    )
    data = _result_text(result)
    assert data["status"] == "error"
    assert "REFRESH_WORKOUT_CALENDAR" in data["message"]
    mock_garmin_client.client.delete.assert_not_called()
    mock_garmin_client.client.post.assert_not_called()
    mock_garmin_client.upload_workout.assert_not_called()
    mock_garmin_client.query_garmin_graphql.assert_not_called()


@pytest.mark.asyncio
async def test_refresh_aborts_before_mutation_on_invalid_schedule(
    app_with_workouts, mock_garmin_client
):
    mock_garmin_client.query_garmin_graphql.return_value = {
        "data": {
            "workoutScheduleSummariesScalar": [
                _make_scheduled(1001, 11, "Old Run", "running", "2026-05-13"),
            ]
        }
    }

    # Invalid: provides neither workout_id nor workout_data on the second item.
    schedules = [
        {"calendar_date": "2026-05-13", "workout_id": 555},
        {"calendar_date": "2026-05-15"},
    ]
    result = await app_with_workouts.call_tool(
        "refresh_workout_calendar",
        {
            "start_date": "2026-05-11",
            "end_date": "2026-05-17",
            "schedules": schedules,
            "confirmation": "REFRESH_WORKOUT_CALENDAR",
        },
    )
    data = _result_text(result)
    assert data["status"] == "error"
    assert data["removed_count"] == 0
    assert data["scheduled_count"] == 0
    assert data["uploaded_count"] == 0
    # Critical: even though preflight read the calendar, no DELETE/POST/upload
    # was performed.
    mock_garmin_client.client.delete.assert_not_called()
    mock_garmin_client.client.post.assert_not_called()
    mock_garmin_client.upload_workout.assert_not_called()


@pytest.mark.asyncio
async def test_refresh_aborts_when_calendar_date_outside_window(
    app_with_workouts, mock_garmin_client
):
    mock_garmin_client.query_garmin_graphql.return_value = {
        "data": {"workoutScheduleSummariesScalar": []}
    }

    schedules = [
        {"calendar_date": "2026-06-01", "workout_id": 555},  # outside window
    ]
    result = await app_with_workouts.call_tool(
        "refresh_workout_calendar",
        {
            "start_date": "2026-05-11",
            "end_date": "2026-05-17",
            "schedules": schedules,
            "confirmation": "REFRESH_WORKOUT_CALENDAR",
        },
    )
    data = _result_text(result)
    assert data["status"] == "error"
    assert any(
        issue.get("field") == "calendar_date" for issue in data["validation_issues"]
    )
    mock_garmin_client.client.delete.assert_not_called()
    mock_garmin_client.client.post.assert_not_called()


# ---------------------------------------------------------------------------
# refresh_workout_calendar -- mutation behavior
# ---------------------------------------------------------------------------

def _wire_schedule_apis(mock_garmin_client, http_ok_response, http_204_response):
    """Configure delete/post/upload mocks for a successful run."""
    mock_garmin_client.client.delete.return_value = http_204_response
    mock_garmin_client.client.post.return_value = http_ok_response


@pytest.mark.asyncio
async def test_refresh_unschedules_only_incomplete_future_and_preserves_completed(
    app_with_workouts, mock_garmin_client, http_ok_response, http_204_response
):
    raw_existing = [
        _make_scheduled(1001, 11, "Old Easy Run", "running", "2026-05-13"),
        _make_scheduled(1002, 12, "Done Strength", "strength_training", "2026-05-12",
                        completed=True, activity_id=777),
        _make_scheduled(1003, 13, "Old Bike", "cycling", "2026-05-14"),
    ]
    final_calendar = [
        # The completed entry remains.
        _make_scheduled(1002, 12, "Done Strength", "strength_training", "2026-05-12",
                        completed=True, activity_id=777),
        # New scheduled entries.
        _make_scheduled(2001, 555, "Easy Run", "running", "2026-05-13"),
    ]
    mock_garmin_client.query_garmin_graphql.side_effect = [
        {"data": {"workoutScheduleSummariesScalar": raw_existing}},
        {"data": {"workoutScheduleSummariesScalar": final_calendar}},
    ]
    _wire_schedule_apis(mock_garmin_client, http_ok_response, http_204_response)

    result = await app_with_workouts.call_tool(
        "refresh_workout_calendar",
        {
            "start_date": "2026-05-11",
            "end_date": "2026-05-17",
            "schedules": [{"calendar_date": "2026-05-13", "workout_id": 555}],
            "confirmation": "REFRESH_WORKOUT_CALENDAR",
        },
    )
    data = _result_text(result)

    # Two incomplete future items removed; the completed item is preserved.
    assert data["removed_count"] == 2
    assert data["preserved_completed"][0]["scheduled_workout_id"] == 1002
    assert data["preserved_completed"][0]["completed"] is True

    # Only delete the two incomplete calendar entries; the completed one is
    # NEVER deleted.
    delete_urls = [
        call.args[1] for call in mock_garmin_client.client.delete.call_args_list
    ]
    assert "workout-service/schedule/1001" in delete_urls
    assert "workout-service/schedule/1003" in delete_urls
    assert "workout-service/schedule/1002" not in delete_urls
    assert all("activity-service" not in u for u in delete_urls)
    assert all("workout-service/workout/" not in u for u in delete_urls)


@pytest.mark.asyncio
async def test_refresh_preserves_activities_and_history(
    app_with_workouts, mock_garmin_client, http_ok_response, http_204_response
):
    """The tool must NEVER call any activity-deletion / history-deletion APIs."""
    raw_existing = [
        _make_scheduled(1001, 11, "Old Run", "running", "2026-05-13"),
    ]
    final_calendar = [
        _make_scheduled(2001, 555, "Easy Run", "running", "2026-05-13"),
    ]
    mock_garmin_client.query_garmin_graphql.side_effect = [
        {"data": {"workoutScheduleSummariesScalar": raw_existing}},
        {"data": {"workoutScheduleSummariesScalar": final_calendar}},
    ]
    _wire_schedule_apis(mock_garmin_client, http_ok_response, http_204_response)

    await app_with_workouts.call_tool(
        "refresh_workout_calendar",
        {
            "start_date": "2026-05-11",
            "end_date": "2026-05-17",
            "schedules": [{"calendar_date": "2026-05-13", "workout_id": 555}],
            "confirmation": "REFRESH_WORKOUT_CALENDAR",
        },
    )

    # Ensure no historical / health / gear / nutrition / PR / goals tools were
    # called. The mocked client never grew those attributes so this is a
    # belt-and-braces assertion via .called on the bare delete URL set.
    delete_urls = [
        call.args[1] for call in mock_garmin_client.client.delete.call_args_list
    ]
    forbidden_substrings = (
        "activity-service",
        "wellness-service",
        "userprofile-service",
        "gear-service",
        "biometric-service",
        "personalrecord-service",
        "goal-service",
        "badge-service",
        "device-service",
        "weight-service",
        "nutrition-service",
    )
    for url in delete_urls:
        for forbidden in forbidden_substrings:
            assert forbidden not in url, f"refresh wrote to forbidden endpoint: {url}"


@pytest.mark.asyncio
async def test_refresh_supports_multiple_workouts_on_one_date(
    app_with_workouts, mock_garmin_client, http_ok_response, http_204_response
):
    raw_existing = []
    final_calendar = [
        _make_scheduled(2001, 555, "Easy Run", "running", "2026-05-13"),
        _make_scheduled(2002, 666, "Sneaky Shoulders", "strength_training", "2026-05-13"),
        _make_scheduled(2003, 777, "Bike", "cycling", "2026-05-13"),
    ]
    mock_garmin_client.query_garmin_graphql.side_effect = [
        {"data": {"workoutScheduleSummariesScalar": raw_existing}},
        {"data": {"workoutScheduleSummariesScalar": final_calendar}},
    ]
    _wire_schedule_apis(mock_garmin_client, http_ok_response, http_204_response)

    schedules = [
        {"calendar_date": "2026-05-13", "workout_id": 555, "label": "Easy Run"},
        {"calendar_date": "2026-05-13", "workout_id": 666, "label": "Sneaky Shoulders"},
        {"calendar_date": "2026-05-13", "workout_id": 777, "label": "Bike"},
    ]
    result = await app_with_workouts.call_tool(
        "refresh_workout_calendar",
        {
            "start_date": "2026-05-11",
            "end_date": "2026-05-17",
            "schedules": schedules,
            "confirmation": "REFRESH_WORKOUT_CALENDAR",
        },
    )
    data = _result_text(result)
    assert data["scheduled_count"] == 3
    # All three POSTs should have happened, each on the same date.
    post_payloads = [c.kwargs.get("json") for c in mock_garmin_client.client.post.call_args_list]
    assert post_payloads == [{"date": "2026-05-13"}] * 3
    assert data["verification_status"] == "success"


@pytest.mark.asyncio
async def test_refresh_supports_mixed_sports_in_one_batch(
    app_with_workouts, mock_garmin_client, http_ok_response, http_204_response
):
    raw_existing = []
    final_calendar = [
        _make_scheduled(2001, 555, "Easy Run", "running", "2026-05-13"),
        _make_scheduled(2002, 666, "Bike", "cycling", "2026-05-14"),
        _make_scheduled(2003, 777, "Sneaky Shoulders", "strength_training", "2026-05-15"),
    ]
    mock_garmin_client.query_garmin_graphql.side_effect = [
        {"data": {"workoutScheduleSummariesScalar": raw_existing}},
        {"data": {"workoutScheduleSummariesScalar": final_calendar}},
    ]
    _wire_schedule_apis(mock_garmin_client, http_ok_response, http_204_response)

    schedules = [
        {"calendar_date": "2026-05-13", "workout_id": 555, "expected_sport": "running"},
        {"calendar_date": "2026-05-14", "workout_id": 666, "expected_sport": "cycling"},
        {"calendar_date": "2026-05-15", "workout_id": 777, "expected_sport": "strength_training"},
    ]
    result = await app_with_workouts.call_tool(
        "refresh_workout_calendar",
        {
            "start_date": "2026-05-11",
            "end_date": "2026-05-17",
            "schedules": schedules,
            "confirmation": "REFRESH_WORKOUT_CALENDAR",
        },
    )
    data = _result_text(result)
    assert data["scheduled_count"] == 3
    sports = {item["sport"] for item in data["scheduled_items"]}
    assert sports == {"running", "cycling", "strength_training"}


@pytest.mark.asyncio
async def test_refresh_uploads_inline_workout_data_and_schedules(
    app_with_workouts, mock_garmin_client, http_ok_response, http_204_response
):
    raw_existing = []
    final_calendar = [
        _make_scheduled(2001, 999, "Sprint Primer", "running", "2026-05-14"),
    ]
    mock_garmin_client.query_garmin_graphql.side_effect = [
        {"data": {"workoutScheduleSummariesScalar": raw_existing}},
        {"data": {"workoutScheduleSummariesScalar": final_calendar}},
    ]
    _wire_schedule_apis(mock_garmin_client, http_ok_response, http_204_response)
    mock_garmin_client.upload_workout.return_value = {
        "workoutId": 999,
        "workoutName": "Sprint Primer",
    }

    schedules = [
        {
            "calendar_date": "2026-05-14",
            "workout_data": _inline_workout_data("Sprint Primer"),
        }
    ]
    result = await app_with_workouts.call_tool(
        "refresh_workout_calendar",
        {
            "start_date": "2026-05-11",
            "end_date": "2026-05-17",
            "schedules": schedules,
            "confirmation": "REFRESH_WORKOUT_CALENDAR",
        },
    )
    data = _result_text(result)
    assert data["uploaded_count"] == 1
    assert data["scheduled_count"] == 1
    assert data["scheduled_items"][0]["workout_id"] == 999
    mock_garmin_client.upload_workout.assert_called_once()
    mock_garmin_client.client.post.assert_called_with(
        "connectapi", "workout-service/schedule/999", json={"date": "2026-05-14"}
    )


@pytest.mark.asyncio
async def test_refresh_schedules_existing_workout_id_items(
    app_with_workouts, mock_garmin_client, http_ok_response, http_204_response
):
    raw_existing = []
    final_calendar = [
        _make_scheduled(2001, 555, "Easy Run", "running", "2026-05-13"),
    ]
    mock_garmin_client.query_garmin_graphql.side_effect = [
        {"data": {"workoutScheduleSummariesScalar": raw_existing}},
        {"data": {"workoutScheduleSummariesScalar": final_calendar}},
    ]
    _wire_schedule_apis(mock_garmin_client, http_ok_response, http_204_response)

    schedules = [{"calendar_date": "2026-05-13", "workout_id": 555}]
    result = await app_with_workouts.call_tool(
        "refresh_workout_calendar",
        {
            "start_date": "2026-05-11",
            "end_date": "2026-05-17",
            "schedules": schedules,
            "confirmation": "REFRESH_WORKOUT_CALENDAR",
        },
    )
    data = _result_text(result)
    assert data["uploaded_count"] == 0
    assert data["scheduled_count"] == 1
    mock_garmin_client.upload_workout.assert_not_called()
    mock_garmin_client.client.post.assert_called_with(
        "connectapi", "workout-service/schedule/555", json={"date": "2026-05-13"}
    )


@pytest.mark.asyncio
async def test_refresh_verification_catches_missing_final_scheduled_items(
    app_with_workouts, mock_garmin_client, http_ok_response, http_204_response
):
    raw_existing = []
    # The final calendar fetch does NOT contain workout 666 even though the
    # tool tried to schedule it. Verification should flag this.
    final_calendar = [
        _make_scheduled(2001, 555, "Easy Run", "running", "2026-05-13"),
    ]
    mock_garmin_client.query_garmin_graphql.side_effect = [
        {"data": {"workoutScheduleSummariesScalar": raw_existing}},
        {"data": {"workoutScheduleSummariesScalar": final_calendar}},
    ]
    _wire_schedule_apis(mock_garmin_client, http_ok_response, http_204_response)

    schedules = [
        {"calendar_date": "2026-05-13", "workout_id": 555},
        {"calendar_date": "2026-05-13", "workout_id": 666},
    ]
    result = await app_with_workouts.call_tool(
        "refresh_workout_calendar",
        {
            "start_date": "2026-05-11",
            "end_date": "2026-05-17",
            "schedules": schedules,
            "confirmation": "REFRESH_WORKOUT_CALENDAR",
        },
    )
    data = _result_text(result)
    assert data["verification_status"] == "degraded"
    missing = data["verification"]["missing_requested_items"]
    assert len(missing) == 1
    assert missing[0]["workout_id"] == 666


# ---------------------------------------------------------------------------
# delete_unused_templates
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_refresh_does_not_delete_templates_by_default(
    app_with_workouts, mock_garmin_client, http_ok_response, http_204_response
):
    raw_existing = [
        _make_scheduled(1001, 11, "Old Run", "running", "2026-05-13"),
    ]
    final_calendar = [
        _make_scheduled(2001, 555, "Easy Run", "running", "2026-05-13"),
    ]
    mock_garmin_client.query_garmin_graphql.side_effect = [
        {"data": {"workoutScheduleSummariesScalar": raw_existing}},
        {"data": {"workoutScheduleSummariesScalar": final_calendar}},
    ]
    _wire_schedule_apis(mock_garmin_client, http_ok_response, http_204_response)
    # Make sure templates exist that LOOK temporary; they must still be
    # preserved when delete_unused_templates is false.
    mock_garmin_client.get_workouts.return_value = [
        {"workoutId": 1234, "workoutName": "DRAFT - test workout",
         "sportType": {"sportTypeKey": "running"}},
    ]

    await app_with_workouts.call_tool(
        "refresh_workout_calendar",
        {
            "start_date": "2026-05-11",
            "end_date": "2026-05-17",
            "schedules": [{"calendar_date": "2026-05-13", "workout_id": 555}],
            "confirmation": "REFRESH_WORKOUT_CALENDAR",
        },
    )

    delete_urls = [
        call.args[1] for call in mock_garmin_client.client.delete.call_args_list
    ]
    # NO workout-template deletions: only schedule deletions.
    assert not any("workout-service/workout/" in u for u in delete_urls)


@pytest.mark.asyncio
async def test_refresh_delete_unused_templates_only_removes_temp_ones(
    app_with_workouts, mock_garmin_client, http_ok_response, http_204_response
):
    raw_existing = [
        _make_scheduled(1001, 11, "Old Run", "running", "2026-05-13"),
    ]
    final_calendar = [
        _make_scheduled(2001, 555, "Easy Run", "running", "2026-05-13"),
    ]
    # Provide enough query results: existing + final + the optional fresh
    # fetch inside the delete_unused_templates branch.
    mock_garmin_client.query_garmin_graphql.side_effect = [
        {"data": {"workoutScheduleSummariesScalar": raw_existing}},
        {"data": {"workoutScheduleSummariesScalar": final_calendar}},
        {"data": {"workoutScheduleSummariesScalar": final_calendar}},
    ]
    _wire_schedule_apis(mock_garmin_client, http_ok_response, http_204_response)
    mock_garmin_client.get_workouts.return_value = [
        # Currently scheduled template -- must NEVER be deleted.
        {"workoutId": 555, "workoutName": "Easy Run",
         "sportType": {"sportTypeKey": "running"}},
        # Production-sounding name -- must NEVER be deleted.
        {"workoutId": 100, "workoutName": "Marathon Pace Long Run",
         "sportType": {"sportTypeKey": "running"}},
        # Clearly temporary -- safe to delete.
        {"workoutId": 200, "workoutName": "DRAFT - test interval",
         "sportType": {"sportTypeKey": "running"}},
        # GPT-generated -- safe to delete.
        {"workoutId": 201, "workoutName": "GPT generated bike v2",
         "sportType": {"sportTypeKey": "cycling"}},
    ]

    result = await app_with_workouts.call_tool(
        "refresh_workout_calendar",
        {
            "start_date": "2026-05-11",
            "end_date": "2026-05-17",
            "schedules": [{"calendar_date": "2026-05-13", "workout_id": 555}],
            "confirmation": "REFRESH_WORKOUT_CALENDAR",
            "delete_unused_templates": True,
        },
    )
    data = _result_text(result)

    deleted_ids = sorted([t["workout_id"] for t in data.get("deleted_templates", [])])
    assert deleted_ids == [200, 201]
    # Make absolutely sure neither the currently scheduled template (555) nor
    # the production template (100) was ever hit with a DELETE.
    delete_urls = [
        call.args[1] for call in mock_garmin_client.client.delete.call_args_list
    ]
    assert "workout-service/workout/555" not in delete_urls
    assert "workout-service/workout/100" not in delete_urls
    assert "workout-service/workout/200" in delete_urls
    assert "workout-service/workout/201" in delete_urls


# ---------------------------------------------------------------------------
# Source detection
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_scheduled_workouts_classifies_source(app_with_workouts, mock_garmin_client):
    mock_garmin_client.query_garmin_graphql.return_value = {
        "data": {
            "workoutScheduleSummariesScalar": [
                # User workout: has a numeric workoutId, no plan.
                _make_scheduled(1, 111, "My Run", "running", "2026-05-13"),
                # Training plan: has tpPlanName.
                {
                    **_make_scheduled(2, None, "Plan Run", "running", "2026-05-14",
                                      workout_uuid="uuid-2"),
                    "tpPlanName": "5K Training Plan",
                },
                # Garmin Coach: phrase + uuid + no numeric id.
                _make_scheduled(3, None, "Coach Tempo", "running", "2026-05-15",
                                workout_uuid="uuid-3", workout_phrase="ANAEROBIC_SPEED"),
            ]
        }
    }
    result = await app_with_workouts.call_tool(
        "get_scheduled_workouts",
        {"start_date": "2026-05-11", "end_date": "2026-05-17"},
    )
    data = _result_text(result)
    sources = {w["scheduled_workout_id"]: w["source"] for w in data["scheduled_workouts"]}
    assert sources[1] == "user_workout"
    assert sources[2] == "training_plan"
    assert sources[3] == "garmin_coach"
    # raw_identifiers is exposed so refresh tools can chain safely.
    assert all("raw_identifiers" in w for w in data["scheduled_workouts"])
