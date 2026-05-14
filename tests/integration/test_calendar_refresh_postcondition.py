"""
Postcondition-driven calendar refresh tests.

These tests cover the behavior of:
  - unschedule_workout
  - preview_workout_calendar_refresh
  - refresh_workout_calendar

after the V16+ fix that makes the final calendar state (not the HTTP status
code or response body) the source of truth.

Scenarios mirror the live-production failure mode described in the patch
brief: Garmin returns an empty / None body for a successful unschedule, the
final calendar nonetheless matches the desired state, and the tool must
report ``status="success"`` rather than ``"failed"`` / ``"partial"`` /
``"degraded"``.
"""

import json

import pytest
from mcp.server.fastmcp import FastMCP
from unittest.mock import MagicMock

from garmin_mcp import workouts


# ---------------------------------------------------------------------------
# Helpers and local fixtures
# ---------------------------------------------------------------------------


def _result_text(result) -> dict:
    """Decode the JSON body of a FastMCP tool response."""
    return json.loads(result[0][0].text)


def _make_scheduled(
    scheduled_workout_id: int,
    workout_id,
    name: str,
    sport: str,
    date: str,
    completed: bool = False,
    activity_id=None,
) -> dict:
    return {
        "scheduledWorkoutId": scheduled_workout_id,
        "workoutId": workout_id,
        "workoutName": name,
        "workoutType": sport,
        "scheduleDate": date,
        "associatedActivityId": activity_id if completed else None,
    }


def _inline_workout_data(name: str, sport: str = "running") -> dict:
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
    workouts.configure(mock_garmin_client)
    app = FastMCP("Test Calendar Refresh Postcondition")
    app = workouts.register_tools(app)
    return app


@pytest.fixture
def http_empty_response():
    """A response with no usable status code and no body — what Garmin
    occasionally returns on a successful unschedule.

    This is the *bad* signal the user reported: the lib used to treat this
    as ``http_status: None`` -> failure. After the fix it must be treated
    as indeterminate and resolved by the postcondition check.
    """

    class _Resp:
        status_code = None

        def json(self):
            raise ValueError("no body")

        text = ""

    return _Resp()


@pytest.fixture
def http_ok_response():
    response = MagicMock()
    response.status_code = 200
    response.json = MagicMock(return_value={"ok": True})
    response.text = '{"ok": true}'
    return response


# ===========================================================================
# A. unschedule_workout — postcondition-driven status
# ===========================================================================


@pytest.mark.asyncio
async def test_unschedule_success_on_empty_response_when_item_disappears(
    app_with_workouts, mock_garmin_client, http_empty_response, no_retry_sleep
):
    """Garmin returns None / empty body, but the post-mutation calendar shows
    the item is gone. Status must be ``success`` and we must record the
    debug fields ``garmin_response_empty=True`` and
    ``postcondition_result="removed"``."""
    sid = 1648741350
    entry = _make_scheduled(sid, 12345, "Hard Run", "running", "2026-05-14")
    mock_garmin_client.query_garmin_graphql.side_effect = [
        # preflight lookup -> item present
        {"data": {"workoutScheduleSummariesScalar": [entry]}},
        # postcondition refetch -> item gone
        {"data": {"workoutScheduleSummariesScalar": []}},
    ]
    mock_garmin_client.client.delete.return_value = http_empty_response

    result = await app_with_workouts.call_tool(
        "unschedule_workout",
        {"scheduled_workout_id": sid, "confirmation": "UNSCHEDULE_WORKOUT"},
    )
    data = _result_text(result)
    assert data["status"] == "success"
    assert data["scheduled_workout_id"] == sid
    assert data["garmin_response_summary"]["garmin_response_empty"] is True
    assert data["garmin_response_summary"]["postcondition_checked"] is True
    assert data["garmin_response_summary"]["postcondition_result"] == "removed"
    assert data["garmin_response_summary"]["http_status"] is None
    # The DELETE must have been issued exactly once to the calendar endpoint.
    mock_garmin_client.client.delete.assert_called_once_with(
        "connectapi", f"workout-service/schedule/{sid}", api=True
    )


@pytest.mark.asyncio
async def test_unschedule_failed_when_empty_response_and_item_still_present(
    app_with_workouts, mock_garmin_client, http_empty_response, no_retry_sleep
):
    """Garmin returns None body and the item STAYS on the calendar even after
    the retry budget — the unschedule really did fail. Status must be
    ``failed`` with ``postcondition_result="still_present"``."""
    sid = 999
    entry = _make_scheduled(sid, 11, "Stuck Run", "running", "2026-05-14")
    # All postcondition refetches keep seeing the item.
    mock_garmin_client.query_garmin_graphql.return_value = {
        "data": {"workoutScheduleSummariesScalar": [entry]}
    }
    mock_garmin_client.client.delete.return_value = http_empty_response

    result = await app_with_workouts.call_tool(
        "unschedule_workout",
        {"scheduled_workout_id": sid, "confirmation": "UNSCHEDULE_WORKOUT"},
    )
    data = _result_text(result)
    assert data["status"] == "failed"
    assert data["garmin_response_summary"]["postcondition_result"] == "still_present"
    assert "still on the calendar" in data["message"]


@pytest.mark.asyncio
async def test_unschedule_unknown_when_postcondition_fetch_fails(
    app_with_workouts, mock_garmin_client, http_empty_response, no_retry_sleep
):
    """If we cannot refetch the calendar, status must be ``unknown`` (NOT
    ``failed``) so the caller knows the result is indeterminate."""
    sid = 42
    entry = _make_scheduled(sid, 7, "Mystery", "running", "2026-05-14")

    # Preflight lookup succeeds; every subsequent refetch raises.
    def _graphql_side_effect(_query):
        _graphql_side_effect.calls += 1
        if _graphql_side_effect.calls == 1:
            return {"data": {"workoutScheduleSummariesScalar": [entry]}}
        raise RuntimeError("network down")

    _graphql_side_effect.calls = 0
    mock_garmin_client.query_garmin_graphql.side_effect = _graphql_side_effect
    mock_garmin_client.client.delete.return_value = http_empty_response

    result = await app_with_workouts.call_tool(
        "unschedule_workout",
        {"scheduled_workout_id": sid, "confirmation": "UNSCHEDULE_WORKOUT"},
    )
    data = _result_text(result)
    assert data["status"] == "unknown"
    assert data["garmin_response_summary"]["postcondition_result"] == "unknown"


@pytest.mark.asyncio
async def test_unschedule_refuses_completed_without_extra_token(
    app_with_workouts, mock_garmin_client, http_204_response, no_retry_sleep
):
    """Safety: completed calendar entries cannot be unscheduled without
    ``confirm_unschedule_completed=UNSCHEDULE_COMPLETED``. Even a "success"
    HTTP status must not bypass this."""
    sid = 5555
    completed = _make_scheduled(
        sid, 100, "Done Hard Run", "running", "2026-05-12",
        completed=True, activity_id=8888,
    )
    mock_garmin_client.query_garmin_graphql.return_value = {
        "data": {"workoutScheduleSummariesScalar": [completed]}
    }
    mock_garmin_client.client.delete.return_value = http_204_response

    result = await app_with_workouts.call_tool(
        "unschedule_workout",
        {"scheduled_workout_id": sid, "confirmation": "UNSCHEDULE_WORKOUT"},
    )
    data = _result_text(result)
    assert data["status"] == "error"
    assert data["completed"] is True
    # No DELETE may have been issued.
    mock_garmin_client.client.delete.assert_not_called()


@pytest.mark.asyncio
async def test_unschedule_never_deletes_workout_template(
    app_with_workouts, mock_garmin_client, http_empty_response, no_retry_sleep
):
    """Safety: even on success, only the calendar entry is deleted, never the
    underlying workout template URL (workout-service/workout/<wid>)."""
    sid = 12345
    entry = _make_scheduled(sid, 99, "Easy Run", "running", "2026-05-14")
    mock_garmin_client.query_garmin_graphql.side_effect = [
        {"data": {"workoutScheduleSummariesScalar": [entry]}},
        {"data": {"workoutScheduleSummariesScalar": []}},
    ]
    mock_garmin_client.client.delete.return_value = http_empty_response

    await app_with_workouts.call_tool(
        "unschedule_workout",
        {"scheduled_workout_id": sid, "confirmation": "UNSCHEDULE_WORKOUT"},
    )

    delete_urls = [
        call.args[1] for call in mock_garmin_client.client.delete.call_args_list
    ]
    assert delete_urls == [f"workout-service/schedule/{sid}"]
    assert all("workout-service/workout/" not in u for u in delete_urls)
    assert all("activity-service" not in u for u in delete_urls)


# ===========================================================================
# B. refresh_workout_calendar — postcondition / verification is authoritative
# ===========================================================================


@pytest.fixture
def http_204_response():
    """Inline copy so this test file does not depend on the fixture from
    ``test_calendar_refresh.py``."""

    class _Resp:
        status_code = 204

        def json(self):
            raise ValueError("no body")

        text = ""

    return _Resp()


@pytest.mark.asyncio
async def test_refresh_live_scenario_empty_responses_but_final_state_correct(
    app_with_workouts, mock_garmin_client, http_empty_response, no_retry_sleep
):
    """The exact V16 failure scenario from the field report.

    11 old workouts to unschedule, 11 new desired workouts to schedule. The
    DELETE calls all return ``status_code=None`` + empty body (the live
    Garmin "successful 204 returning None" behaviour). The POST calls also
    return None. The post-mutation calendar contains exactly the 11 new
    items and none of the old. The tool must report ``status="success"``.
    """
    raw_existing = [
        _make_scheduled(1000 + i, 100 + i, f"Old HR Run {i}", "running",
                        f"2026-05-{13 + (i % 7):02d}")
        for i in range(11)
    ]
    desired = [
        {"calendar_date": f"2026-05-{13 + (i % 7):02d}",
         "workout_id": 500 + i,
         "label": f"new pace {i}"}
        for i in range(11)
    ]
    final_calendar = [
        _make_scheduled(2000 + i, 500 + i, f"New Pace Run {i}", "running",
                        f"2026-05-{13 + (i % 7):02d}")
        for i in range(11)
    ]
    mock_garmin_client.query_garmin_graphql.side_effect = [
        {"data": {"workoutScheduleSummariesScalar": raw_existing}},
        {"data": {"workoutScheduleSummariesScalar": final_calendar}},
    ]
    mock_garmin_client.client.delete.return_value = http_empty_response
    mock_garmin_client.client.post.return_value = http_empty_response

    result = await app_with_workouts.call_tool(
        "refresh_workout_calendar",
        {
            "start_date": "2026-05-13",
            "end_date": "2026-05-20",
            "schedules": desired,
            "confirmation": "REFRESH_WORKOUT_CALENDAR",
        },
    )
    data = _result_text(result)

    assert data["status"] == "success", json.dumps(data, indent=2)
    assert data["verification_status"] == "success"
    assert data["removed_count"] == 11
    assert data["scheduled_count"] == 11
    assert data["failed_count"] == 0
    assert data["already_present_count"] == 0
    assert data["final_calendar_count"] == 11
    # The empty-response warning is informational, not a failure.
    assert any(
        "empty body or missing status code" in w for w in data["warnings"]
    )


@pytest.mark.asyncio
async def test_refresh_idempotent_when_desired_already_present(
    app_with_workouts, mock_garmin_client, http_empty_response, no_retry_sleep
):
    """Running refresh twice with the same desired schedule must not create
    duplicates. The second run should detect the desired items in preflight
    and count them as ``already_present`` — never POST again."""
    desired = [
        {"calendar_date": "2026-05-13", "workout_id": 555},
        {"calendar_date": "2026-05-14", "workout_id": 666},
    ]
    already_there = [
        _make_scheduled(9001, 555, "Easy Run", "running", "2026-05-13"),
        _make_scheduled(9002, 666, "Bike", "cycling", "2026-05-14"),
    ]
    # Preflight already shows the desired items; final fetch is identical.
    mock_garmin_client.query_garmin_graphql.side_effect = [
        {"data": {"workoutScheduleSummariesScalar": already_there}},
        {"data": {"workoutScheduleSummariesScalar": already_there}},
    ]
    mock_garmin_client.client.delete.return_value = http_empty_response
    mock_garmin_client.client.post.return_value = http_empty_response

    result = await app_with_workouts.call_tool(
        "refresh_workout_calendar",
        {
            "start_date": "2026-05-11",
            "end_date": "2026-05-17",
            "schedules": desired,
            "confirmation": "REFRESH_WORKOUT_CALENDAR",
        },
    )
    data = _result_text(result)

    assert data["status"] == "success", json.dumps(data, indent=2)
    assert data["already_present_count"] == 2
    assert data["scheduled_count"] == 0
    assert data["removed_count"] == 0
    assert data["uploaded_count"] == 0
    assert data["failed_count"] == 0
    # No POST/DELETE issued at all.
    mock_garmin_client.client.post.assert_not_called()
    mock_garmin_client.client.delete.assert_not_called()


@pytest.mark.asyncio
async def test_refresh_partial_keeps_already_present_and_replaces_others(
    app_with_workouts, mock_garmin_client, http_empty_response, no_retry_sleep
):
    """A mixed run: one item is already present, one needs to replace an old
    entry. Verify that only the conflicting old entry is unscheduled and
    only the truly-new entry is scheduled."""
    raw_existing = [
        _make_scheduled(9001, 555, "Easy Run", "running", "2026-05-13"),
        _make_scheduled(9002, 111, "Old Bike", "cycling", "2026-05-14"),
    ]
    desired = [
        {"calendar_date": "2026-05-13", "workout_id": 555},  # already present
        {"calendar_date": "2026-05-14", "workout_id": 222},  # replaces old bike
    ]
    final_calendar = [
        _make_scheduled(9001, 555, "Easy Run", "running", "2026-05-13"),
        _make_scheduled(9003, 222, "New Bike", "cycling", "2026-05-14"),
    ]
    mock_garmin_client.query_garmin_graphql.side_effect = [
        {"data": {"workoutScheduleSummariesScalar": raw_existing}},
        {"data": {"workoutScheduleSummariesScalar": final_calendar}},
    ]
    mock_garmin_client.client.delete.return_value = http_empty_response
    mock_garmin_client.client.post.return_value = http_empty_response

    result = await app_with_workouts.call_tool(
        "refresh_workout_calendar",
        {
            "start_date": "2026-05-11",
            "end_date": "2026-05-17",
            "schedules": desired,
            "confirmation": "REFRESH_WORKOUT_CALENDAR",
        },
    )
    data = _result_text(result)

    assert data["status"] == "success", json.dumps(data, indent=2)
    assert data["already_present_count"] == 1
    assert data["scheduled_count"] == 1
    assert data["removed_count"] == 1

    # Only the old bike was deleted, the already-present run was preserved.
    delete_urls = [
        call.args[1] for call in mock_garmin_client.client.delete.call_args_list
    ]
    assert delete_urls == ["workout-service/schedule/9002"]
    # Only the new bike was POSTed; the already-present run was not.
    post_calls = mock_garmin_client.client.post.call_args_list
    assert len(post_calls) == 1
    assert post_calls[0].args[1] == "workout-service/schedule/222"


@pytest.mark.asyncio
async def test_refresh_same_workout_id_on_two_different_dates_allowed(
    app_with_workouts, mock_garmin_client, http_empty_response, no_retry_sleep
):
    """Same template scheduled on two distinct dates is a normal pattern
    (e.g. easy run twice a week)."""
    desired = [
        {"calendar_date": "2026-05-13", "workout_id": 555},
        {"calendar_date": "2026-05-15", "workout_id": 555},
    ]
    final_calendar = [
        _make_scheduled(2001, 555, "Easy Run", "running", "2026-05-13"),
        _make_scheduled(2002, 555, "Easy Run", "running", "2026-05-15"),
    ]
    mock_garmin_client.query_garmin_graphql.side_effect = [
        {"data": {"workoutScheduleSummariesScalar": []}},
        {"data": {"workoutScheduleSummariesScalar": final_calendar}},
    ]
    mock_garmin_client.client.delete.return_value = http_empty_response
    mock_garmin_client.client.post.return_value = http_empty_response

    result = await app_with_workouts.call_tool(
        "refresh_workout_calendar",
        {
            "start_date": "2026-05-11",
            "end_date": "2026-05-17",
            "schedules": desired,
            "confirmation": "REFRESH_WORKOUT_CALENDAR",
        },
    )
    data = _result_text(result)

    assert data["status"] == "success"
    assert data["scheduled_count"] == 2


@pytest.mark.asyncio
async def test_refresh_multiple_workouts_on_same_date_allowed(
    app_with_workouts, mock_garmin_client, http_empty_response, no_retry_sleep
):
    """Same day run + strength + cycling — three rows, one date."""
    desired = [
        {"calendar_date": "2026-05-13", "workout_id": 555},
        {"calendar_date": "2026-05-13", "workout_id": 666},
        {"calendar_date": "2026-05-13", "workout_id": 777},
    ]
    final_calendar = [
        _make_scheduled(2001, 555, "Run", "running", "2026-05-13"),
        _make_scheduled(2002, 666, "Strength", "strength_training", "2026-05-13"),
        _make_scheduled(2003, 777, "Bike", "cycling", "2026-05-13"),
    ]
    mock_garmin_client.query_garmin_graphql.side_effect = [
        {"data": {"workoutScheduleSummariesScalar": []}},
        {"data": {"workoutScheduleSummariesScalar": final_calendar}},
    ]
    mock_garmin_client.client.delete.return_value = http_empty_response
    mock_garmin_client.client.post.return_value = http_empty_response

    result = await app_with_workouts.call_tool(
        "refresh_workout_calendar",
        {
            "start_date": "2026-05-11",
            "end_date": "2026-05-17",
            "schedules": desired,
            "confirmation": "REFRESH_WORKOUT_CALENDAR",
        },
    )
    data = _result_text(result)

    assert data["status"] == "success"
    assert data["scheduled_count"] == 3


@pytest.mark.asyncio
async def test_refresh_completed_items_preserved_and_not_in_failed(
    app_with_workouts, mock_garmin_client, http_empty_response, no_retry_sleep
):
    """Completed calendar entries must be preserved (separate field) and
    never touched."""
    raw_existing = [
        _make_scheduled(9001, 11, "Old Run", "running", "2026-05-13"),
        _make_scheduled(9002, 12, "Done Strength", "strength_training",
                        "2026-05-12", completed=True, activity_id=777),
    ]
    final_calendar = [
        _make_scheduled(9002, 12, "Done Strength", "strength_training",
                        "2026-05-12", completed=True, activity_id=777),
        _make_scheduled(2001, 555, "Easy Run", "running", "2026-05-13"),
    ]
    mock_garmin_client.query_garmin_graphql.side_effect = [
        {"data": {"workoutScheduleSummariesScalar": raw_existing}},
        {"data": {"workoutScheduleSummariesScalar": final_calendar}},
    ]
    mock_garmin_client.client.delete.return_value = http_empty_response
    mock_garmin_client.client.post.return_value = http_empty_response

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
    assert data["status"] == "success"
    assert data["preserved_completed"][0]["scheduled_workout_id"] == 9002
    delete_urls = [
        c.args[1] for c in mock_garmin_client.client.delete.call_args_list
    ]
    assert "workout-service/schedule/9002" not in delete_urls


@pytest.mark.asyncio
async def test_refresh_partial_when_a_desired_item_is_missing_after_mutation(
    app_with_workouts, mock_garmin_client, http_empty_response, no_retry_sleep
):
    """Final calendar is missing a desired item -> verification_status
    "degraded", top-level status "partial"."""
    raw_existing = []
    final_calendar = [
        _make_scheduled(2001, 555, "Easy Run", "running", "2026-05-13"),
        # workout_id=666 never made it.
    ]
    mock_garmin_client.query_garmin_graphql.side_effect = [
        {"data": {"workoutScheduleSummariesScalar": raw_existing}},
        {"data": {"workoutScheduleSummariesScalar": final_calendar}},
    ]
    mock_garmin_client.client.delete.return_value = http_empty_response
    mock_garmin_client.client.post.return_value = http_empty_response

    result = await app_with_workouts.call_tool(
        "refresh_workout_calendar",
        {
            "start_date": "2026-05-11",
            "end_date": "2026-05-17",
            "schedules": [
                {"calendar_date": "2026-05-13", "workout_id": 555},
                {"calendar_date": "2026-05-13", "workout_id": 666},
            ],
            "confirmation": "REFRESH_WORKOUT_CALENDAR",
        },
    )
    data = _result_text(result)
    assert data["status"] == "partial"
    assert data["verification_status"] == "degraded"
    missing = data["verification"]["missing_requested_items"]
    assert any(m["workout_id"] == 666 for m in missing)


@pytest.mark.asyncio
async def test_refresh_partial_when_lingering_old_item_remains(
    app_with_workouts, mock_garmin_client, http_empty_response, no_retry_sleep
):
    """Final calendar still has an old incomplete entry that we tried to
    unschedule -> degraded / partial."""
    raw_existing = [
        _make_scheduled(9001, 11, "Old Run", "running", "2026-05-13"),
    ]
    final_calendar = [
        # old run still present!
        _make_scheduled(9001, 11, "Old Run", "running", "2026-05-13"),
        _make_scheduled(2001, 555, "Easy Run", "running", "2026-05-13"),
    ]
    mock_garmin_client.query_garmin_graphql.side_effect = [
        {"data": {"workoutScheduleSummariesScalar": raw_existing}},
        {"data": {"workoutScheduleSummariesScalar": final_calendar}},
    ]
    mock_garmin_client.client.delete.return_value = http_empty_response
    mock_garmin_client.client.post.return_value = http_empty_response

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
    assert data["status"] == "partial"
    assert data["verification_status"] == "degraded"
    lingering_sids = {
        item["scheduled_workout_id"]
        for item in data["verification"]["lingering_old_items"]
    }
    assert 9001 in lingering_sids


@pytest.mark.asyncio
async def test_refresh_invalid_schedule_item_aborts_before_mutation(
    app_with_workouts, mock_garmin_client, http_empty_response, no_retry_sleep
):
    """An invalid desired item (no workout_id and no workout_data) aborts
    everything BEFORE any Garmin mutation, regardless of how many other
    items are valid."""
    mock_garmin_client.query_garmin_graphql.return_value = {
        "data": {"workoutScheduleSummariesScalar": []}
    }
    result = await app_with_workouts.call_tool(
        "refresh_workout_calendar",
        {
            "start_date": "2026-05-11",
            "end_date": "2026-05-17",
            "schedules": [
                {"calendar_date": "2026-05-13", "workout_id": 555},
                {"calendar_date": "2026-05-14"},  # invalid
            ],
            "confirmation": "REFRESH_WORKOUT_CALENDAR",
        },
    )
    data = _result_text(result)
    assert data["status"] == "error"
    mock_garmin_client.client.delete.assert_not_called()
    mock_garmin_client.client.post.assert_not_called()
    mock_garmin_client.upload_workout.assert_not_called()


# ===========================================================================
# C. preview_workout_calendar_refresh — already_present / would_schedule
# ===========================================================================


@pytest.mark.asyncio
async def test_preview_reports_already_present_when_desired_match_existing(
    app_with_workouts, mock_garmin_client
):
    """Preview must distinguish already-present items from new ones."""
    mock_garmin_client.query_garmin_graphql.return_value = {
        "data": {
            "workoutScheduleSummariesScalar": [
                _make_scheduled(9001, 555, "Easy Run", "running", "2026-05-13"),
                _make_scheduled(9002, 11, "Old Bike", "cycling", "2026-05-14"),
            ]
        }
    }
    schedules = [
        {"calendar_date": "2026-05-13", "workout_id": 555},  # already present
        {"calendar_date": "2026-05-15", "workout_id": 777},  # new
    ]
    result = await app_with_workouts.call_tool(
        "preview_workout_calendar_refresh",
        {"start_date": "2026-05-11", "end_date": "2026-05-17",
         "schedules": schedules},
    )
    data = _result_text(result)
    assert data["status"] == "success"
    assert data["already_present_count"] == 1
    assert data["already_present_items"][0]["workout_id"] == 555
    assert data["would_schedule_count"] == 1
    assert data["would_schedule_items"][0]["workout_id"] == 777
    # would_remove only the unrelated old bike (NOT the already-present run).
    assert data["would_remove_count"] == 1
    assert data["would_remove"][0]["scheduled_workout_id"] == 9002
    # No mutations.
    mock_garmin_client.client.delete.assert_not_called()
    mock_garmin_client.client.post.assert_not_called()
    mock_garmin_client.upload_workout.assert_not_called()


@pytest.mark.asyncio
async def test_preview_already_satisfied_flag(
    app_with_workouts, mock_garmin_client
):
    """When every desired item is already on the calendar and there are no
    extra incomplete entries, preview reports ``already_satisfied=True`` and
    zero counts for would_remove / would_schedule."""
    mock_garmin_client.query_garmin_graphql.return_value = {
        "data": {
            "workoutScheduleSummariesScalar": [
                _make_scheduled(9001, 555, "Easy Run", "running", "2026-05-13"),
            ]
        }
    }
    result = await app_with_workouts.call_tool(
        "preview_workout_calendar_refresh",
        {
            "start_date": "2026-05-11",
            "end_date": "2026-05-17",
            "schedules": [{"calendar_date": "2026-05-13", "workout_id": 555}],
        },
    )
    data = _result_text(result)
    assert data["status"] == "success"
    assert data["already_satisfied"] is True
    assert data["already_present_count"] == 1
    assert data["would_schedule_count"] == 0
    assert data["would_remove_count"] == 0


@pytest.mark.asyncio
async def test_preview_preserves_completed_items_separately(
    app_with_workouts, mock_garmin_client
):
    mock_garmin_client.query_garmin_graphql.return_value = {
        "data": {
            "workoutScheduleSummariesScalar": [
                _make_scheduled(9001, 11, "Old", "running", "2026-05-13"),
                _make_scheduled(9002, 12, "Done", "running", "2026-05-12",
                                completed=True, activity_id=888),
            ]
        }
    }
    result = await app_with_workouts.call_tool(
        "preview_workout_calendar_refresh",
        {
            "start_date": "2026-05-11",
            "end_date": "2026-05-17",
            "schedules": [
                {"calendar_date": "2026-05-13", "workout_id": 555},
            ],
        },
    )
    data = _result_text(result)
    assert data["preserved_completed_count"] == 1
    assert data["preserved_completed"][0]["scheduled_workout_id"] == 9002
    # The completed entry is NOT in would_remove.
    assert all(e["scheduled_workout_id"] != 9002 for e in data["would_remove"])


# ===========================================================================
# Schema exposure — schedules must be a typed array
# ===========================================================================


@pytest.mark.asyncio
async def test_schema_exposes_schedules_for_preview_and_refresh(
    app_with_workouts
):
    """``schedules`` must be a required, typed array in the JSON schema of
    both tools. We check for the field name and ``type: array``."""
    tools = await app_with_workouts.list_tools()
    by_name = {t.name: t for t in tools}

    preview_schema = by_name["preview_workout_calendar_refresh"].inputSchema
    refresh_schema = by_name["refresh_workout_calendar"].inputSchema

    for label, schema in (
        ("preview", preview_schema),
        ("refresh", refresh_schema),
    ):
        assert "schedules" in schema["properties"], (
            f"{label}: schedules missing from schema"
        )
        s = schema["properties"]["schedules"]
        assert s.get("type") == "array", f"{label}: schedules is not an array"
        assert "schedules" in schema.get("required", []), (
            f"{label}: schedules must be required"
        )
        # Inner items reference a ScheduleRequest-like object.
        items = s.get("items", {})
        assert items, f"{label}: schedules.items is empty"


# ===========================================================================
# Eventual consistency — retry budget surfaces success
# ===========================================================================


@pytest.mark.asyncio
async def test_unschedule_retries_until_postcondition_clears(
    app_with_workouts, mock_garmin_client, http_empty_response, no_retry_sleep
):
    """First few postcondition refetches still see the item; later ones
    don't. The unschedule should succeed once the retry budget catches the
    eventual-consistency update."""
    sid = 71
    entry = _make_scheduled(sid, 11, "Slow Sync Run", "running", "2026-05-14")
    mock_garmin_client.query_garmin_graphql.side_effect = [
        # preflight lookup
        {"data": {"workoutScheduleSummariesScalar": [entry]}},
        # postcondition refetches: still present, still present, gone
        {"data": {"workoutScheduleSummariesScalar": [entry]}},
        {"data": {"workoutScheduleSummariesScalar": [entry]}},
        {"data": {"workoutScheduleSummariesScalar": []}},
    ]
    mock_garmin_client.client.delete.return_value = http_empty_response

    result = await app_with_workouts.call_tool(
        "unschedule_workout",
        {"scheduled_workout_id": sid, "confirmation": "UNSCHEDULE_WORKOUT"},
    )
    data = _result_text(result)
    assert data["status"] == "success"
    assert data["garmin_response_summary"]["postcondition_result"] == "removed"
