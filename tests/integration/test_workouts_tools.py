"""
Integration tests for workouts module MCP tools

Tests workout tools using FastMCP integration with mocked Garmin API responses.
"""
import pytest
from unittest.mock import Mock
from mcp.server.fastmcp import FastMCP

from garmin_mcp import workouts
from tests.fixtures.garmin_responses import (
    MOCK_WORKOUTS,
    MOCK_WORKOUT_DETAILS,
    MOCK_SWIM_WORKOUT_DETAILS,
)


@pytest.fixture
def app_with_workouts(mock_garmin_client):
    """Create FastMCP app with workouts tools registered"""
    workouts.configure(mock_garmin_client)
    app = FastMCP("Test Workouts")
    app = workouts.register_tools(app)
    return app


@pytest.mark.asyncio
async def test_get_workouts_tool(app_with_workouts, mock_garmin_client):
    """Test get_workouts tool returns all workouts"""
    # Setup mock
    mock_garmin_client.get_workouts.return_value = MOCK_WORKOUTS

    # Call tool
    result = await app_with_workouts.call_tool(
        "get_workouts",
        {}
    )

    # Verify
    assert result is not None
    mock_garmin_client.get_workouts.assert_called_once()


@pytest.mark.asyncio
async def test_get_workout_by_id_tool(app_with_workouts, mock_garmin_client):
    """Test get_workout_by_id tool returns specific workout with step details (numeric ID)"""
    import json as json_module

    # Setup mock
    mock_garmin_client.get_workout_by_id.return_value = MOCK_WORKOUT_DETAILS

    # Call tool with numeric ID (FastMCP passes numeric strings as int)
    workout_id = 123456
    result = await app_with_workouts.call_tool(
        "get_workout_by_id",
        {"workout_id": workout_id}
    )

    # Verify - tool converts to int for numeric IDs
    assert result is not None
    mock_garmin_client.get_workout_by_id.assert_called_once_with(123456)

    # Parse the result and verify curation includes steps
    result_data = json_module.loads(result[0][0].text)
    assert result_data["id"] == 123456
    assert result_data["name"] == "5K Tempo Run"
    assert result_data["sport"] == "running"

    # Verify segments include steps
    assert "segments" in result_data
    segment = result_data["segments"][0]
    assert "steps" in segment
    assert segment["step_count"] == 3

    # Verify step details are curated correctly
    warmup_step = segment["steps"][0]
    assert warmup_step["type"] == "warmup"
    assert warmup_step["end_condition"] == "time"
    assert warmup_step["end_condition_value"] == 600.0

    # Verify interval step with target zone
    interval_step = segment["steps"][1]
    assert interval_step["type"] == "interval"
    assert interval_step["target_type"] == "pace.zone"
    assert interval_step["target_zone"] == 4


@pytest.mark.asyncio
async def test_get_workout_by_id_tool_handles_swim_secondary_targets(
    app_with_workouts, mock_garmin_client
):
    """Test swim workouts with null primary targetType still expose secondary pace targets."""
    import json as json_module

    mock_garmin_client.get_workout_by_id.return_value = MOCK_SWIM_WORKOUT_DETAILS

    result = await app_with_workouts.call_tool(
        "get_workout_by_id",
        {"workout_id": 1528077786}
    )

    result_data = json_module.loads(result[0][0].text)
    assert result_data["id"] == 1528077786
    assert result_data["sport"] == "swimming"
    assert result_data["estimated_distance_meters"] == 3000.0

    segment = result_data["segments"][0]
    assert segment["step_count"] == 2

    warmup_step = segment["steps"][0]
    assert warmup_step["type"] == "warmup"
    assert warmup_step["secondary_target_type"] == "pace.zone"
    assert warmup_step["secondary_target_value_low"] == 0.45
    assert warmup_step["secondary_target_value_high"] == 0.6916667
    assert "target_type" not in warmup_step

    repeat_step = segment["steps"][1]
    assert repeat_step["type"] == "repeat"
    assert repeat_step["repeat_count"] == 2
    assert repeat_step["step_count"] == 2

    interval_step = repeat_step["steps"][0]
    assert interval_step["type"] == "interval"
    assert interval_step["secondary_target_type"] == "pace.zone"
    assert interval_step["secondary_target_value_low"] == 0.7751938
    assert interval_step["secondary_target_value_high"] == 0.8583333

    rest_step = repeat_step["steps"][1]
    assert rest_step["type"] == "rest"
    assert rest_step["end_condition"] == "fixed.rest"
    assert rest_step["end_condition_value"] == 60.0


@pytest.mark.asyncio
async def test_get_workout_by_id_tool_ignores_malformed_target_blocks(
    app_with_workouts, mock_garmin_client
):
    """Test malformed Garmin target blocks do not crash workout curation."""
    import json as json_module

    malformed_workout = {
        "workoutId": 123457,
        "workoutName": "Malformed Swim Workout",
        "sportType": {"sportTypeId": 4, "sportTypeKey": "swimming"},
        "workoutSegments": [{
            "segmentOrder": 1,
            "sportType": {"sportTypeId": 4, "sportTypeKey": "swimming"},
            "workoutSteps": [{
                "type": "ExecutableStepDTO",
                "stepOrder": 1,
                "stepType": {"stepTypeId": 1, "stepTypeKey": "warmup"},
                "endCondition": {"conditionTypeId": 3, "conditionTypeKey": "distance"},
                "endConditionValue": 100.0,
                "targetType": "pace.zone",
                "secondaryTargetType": [],
            }]
        }],
    }
    mock_garmin_client.get_workout_by_id.return_value = malformed_workout

    result = await app_with_workouts.call_tool(
        "get_workout_by_id",
        {"workout_id": 123457}
    )

    result_data = json_module.loads(result[0][0].text)
    step = result_data["segments"][0]["steps"][0]
    assert step["type"] == "warmup"
    assert step["end_condition"] == "distance"
    assert step["end_condition_value"] == 100.0
    assert "target_type" not in step
    assert "secondary_target_type" not in step


@pytest.mark.asyncio
async def test_get_workout_by_uuid_tool(app_with_workouts, mock_garmin_client):
    """Test get_workout_by_id tool with UUID (training plan workout)"""
    import json as json_module

    # Setup mock for connectapi call (fbt-adaptive endpoint)
    mock_garmin_client.connectapi.return_value = {
        "workoutId": None,
        "workoutUuid": "d7a5491b-42a5-4d2d-ba38-4e414fc03caf",
        "workoutName": "Base",
        "description": "6:20/km",
        "sportType": {"sportTypeId": 1, "sportTypeKey": "running"},
        "estimatedDurationInSecs": 2160,
        "workoutPhrase": "AEROBIC_LOW_SHORTAGE_BASE",
        "trainingEffectLabel": "AEROBIC_BASE",
        "estimatedTrainingEffect": 2.3,
        "workoutSegments": [{
            "segmentOrder": 1,
            "sportType": {"sportTypeId": 1, "sportTypeKey": "running"},
            "workoutSteps": [{
                "type": "ExecutableStepDTO",
                "stepOrder": 1,
                "stepType": {"stepTypeId": 3, "stepTypeKey": "interval"},
                "endCondition": {"conditionTypeId": 2, "conditionTypeKey": "time"},
                "endConditionValue": 2160.0,
                "targetType": {"workoutTargetTypeId": 6, "workoutTargetTypeKey": "pace.zone"},
                "targetValueOne": 2.777,
                "targetValueTwo": 2.472
            }]
        }]
    }

    # Call tool with UUID (contains dashes)
    workout_uuid = "d7a5491b-42a5-4d2d-ba38-4e414fc03caf"
    result = await app_with_workouts.call_tool(
        "get_workout_by_id",
        {"workout_id": workout_uuid}
    )

    # Verify fbt-adaptive endpoint was called
    assert result is not None
    mock_garmin_client.connectapi.assert_called_once_with(
        f"workout-service/fbt-adaptive/{workout_uuid}"
    )

    # Parse the result and verify training plan workout fields
    result_data = json_module.loads(result[0][0].text)
    assert result_data["uuid"] == workout_uuid
    assert result_data["name"] == "Base"
    assert result_data["sport"] == "running"
    assert result_data["workout_type"] == "AEROBIC_LOW_SHORTAGE_BASE"
    assert result_data["training_effect_label"] == "AEROBIC_BASE"
    assert result_data["estimated_training_effect"] == 2.3
    assert result_data["estimated_duration_seconds"] == 2160

    # Verify segments include steps
    assert "segments" in result_data
    segment = result_data["segments"][0]
    assert "steps" in segment
    assert segment["step_count"] == 1


@pytest.mark.asyncio
async def test_download_workout_tool(app_with_workouts, mock_garmin_client):
    """Test download_workout tool downloads workout data"""
    # Setup mock
    workout_data = {
        "workoutId": 123456,
        "workoutName": "5K Tempo Run",
        "data": "...workout file content..."
    }
    mock_garmin_client.download_workout.return_value = workout_data

    # Call tool
    workout_id = 123456
    result = await app_with_workouts.call_tool(
        "download_workout",
        {"workout_id": workout_id}
    )

    # Verify
    assert result is not None
    mock_garmin_client.download_workout.assert_called_once_with(workout_id)


@pytest.mark.asyncio
async def test_upload_workout_tool(app_with_workouts, mock_garmin_client):
    """Test upload_workout tool uploads new workout"""
    # Setup mock
    upload_response = {
        "workoutId": 123457,
        "workoutName": "New Workout"
    }
    mock_garmin_client.upload_workout.return_value = upload_response

    # Call tool - pass dict which is passed directly to API
    workout_data = {"workoutName": "New Workout", "sportType": {"sportTypeId": 1}}
    result = await app_with_workouts.call_tool(
        "upload_workout",
        {"workout_data": workout_data}
    )

    # Verify - dict is passed directly to the API
    assert result is not None
    mock_garmin_client.upload_workout.assert_called_once_with(workout_data)


@pytest.mark.asyncio
async def test_upload_workout_fixes_hr_zone_target(app_with_workouts, mock_garmin_client):
    """Test upload_workout converts targetValueOne to zoneNumber for HR zone targets"""
    import json as json_module

    upload_response = {"workoutId": 123458, "workoutName": "HR Zone Workout"}
    mock_garmin_client.upload_workout.return_value = upload_response

    # Simulate the common LLM mistake: using targetValueOne instead of zoneNumber
    workout_data = {
        "workoutName": "HR Zone Workout",
        "sportType": {"sportTypeId": 1, "sportTypeKey": "running"},
        "workoutSegments": [{
            "segmentOrder": 1,
            "sportType": {"sportTypeId": 1, "sportTypeKey": "running"},
            "workoutSteps": [{
                "type": "ExecutableStepDTO",
                "stepOrder": 1,
                "stepType": {"stepTypeId": 3, "stepTypeKey": "interval"},
                "endCondition": {"conditionTypeId": 2, "conditionTypeKey": "time"},
                "endConditionValue": 600,
                "targetType": {"workoutTargetTypeId": 4, "workoutTargetTypeKey": "heart.rate.zone"},
                "targetValueOne": 3,
            }]
        }]
    }

    result = await app_with_workouts.call_tool(
        "upload_workout",
        {"workout_data": workout_data}
    )

    # Verify the data sent to Garmin API was fixed
    called_data = mock_garmin_client.upload_workout.call_args[0][0]
    step = called_data["workoutSegments"][0]["workoutSteps"][0]
    assert step["zoneNumber"] == 3
    assert "targetValueOne" not in step
    assert "targetValueTwo" not in step

    result_data = json_module.loads(result[0][0].text)
    assert result_data["status"] == "success"


@pytest.mark.asyncio
async def test_upload_workout_fixes_hr_zone_in_repeat_group(app_with_workouts, mock_garmin_client):
    """Test upload_workout fixes HR zone targets inside RepeatGroupDTO"""
    import json as json_module

    upload_response = {"workoutId": 123459, "workoutName": "Repeat HR Zone"}
    mock_garmin_client.upload_workout.return_value = upload_response

    workout_data = {
        "workoutName": "Repeat HR Zone",
        "sportType": {"sportTypeId": 1, "sportTypeKey": "running"},
        "workoutSegments": [{
            "segmentOrder": 1,
            "sportType": {"sportTypeId": 1, "sportTypeKey": "running"},
            "workoutSteps": [{
                "type": "RepeatGroupDTO",
                "stepOrder": 1,
                "numberOfIterations": 2,
                "workoutSteps": [
                    {
                        "type": "ExecutableStepDTO",
                        "stepOrder": 1,
                        "stepType": {"stepTypeId": 3, "stepTypeKey": "interval"},
                        "endCondition": {"conditionTypeId": 2, "conditionTypeKey": "time"},
                        "endConditionValue": 600,
                        "targetType": {"workoutTargetTypeId": 4, "workoutTargetTypeKey": "heart.rate.zone"},
                        "targetValueOne": 3,
                        "targetValueTwo": 3,
                    },
                    {
                        "type": "ExecutableStepDTO",
                        "stepOrder": 2,
                        "stepType": {"stepTypeId": 4, "stepTypeKey": "recovery"},
                        "endCondition": {"conditionTypeId": 2, "conditionTypeKey": "time"},
                        "endConditionValue": 240,
                        "targetType": {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"},
                    }
                ]
            }]
        }]
    }

    result = await app_with_workouts.call_tool(
        "upload_workout",
        {"workout_data": workout_data}
    )

    # Verify nested step was fixed
    called_data = mock_garmin_client.upload_workout.call_args[0][0]
    interval_step = called_data["workoutSegments"][0]["workoutSteps"][0]["workoutSteps"][0]
    assert interval_step["zoneNumber"] == 3
    assert "targetValueOne" not in interval_step
    assert "targetValueTwo" not in interval_step

    result_data = json_module.loads(result[0][0].text)
    assert result_data["status"] == "success"


@pytest.mark.asyncio
async def test_get_scheduled_workouts_tool(app_with_workouts, mock_garmin_client):
    """Test get_scheduled_workouts tool - uses GraphQL query"""
    import json as json_module

    # Setup mock for GraphQL query - matches actual API response structure
    graphql_response = {
        "data": {
            "workoutScheduleSummariesScalar": [
                {
                    "workoutUuid": "abc-123-def",
                    "scheduledWorkoutId": 1648061908,
                    "workoutId": 123456,
                    "workoutName": "5K Tempo Run",
                    "workoutType": "running",
                    "scheduleDate": "2024-01-15",
                    "tpPlanName": "5K Training Plan",
                    "associatedActivityId": None,
                    "estimatedDurationInSecs": 1800,
                    "estimatedDistanceInMeters": 5000.0
                }
            ]
        }
    }
    mock_garmin_client.query_garmin_graphql.return_value = graphql_response

    # Call tool
    result = await app_with_workouts.call_tool(
        "get_scheduled_workouts",
        {"start_date": "2024-01-08", "end_date": "2024-01-15"}
    )

    # Verify curation extracts correct fields
    result_data = json_module.loads(result[0][0].text)
    assert result_data["count"] == 1
    workout = result_data["scheduled_workouts"][0]
    assert workout["name"] == "5K Tempo Run"
    assert workout["scheduled_workout_id"] == 1648061908
    assert workout["sport"] == "running"
    assert workout["completed"] is False
    assert workout["training_plan"] == "5K Training Plan"
    assert workout["estimated_duration_seconds"] == 1800

    # Verify
    assert result is not None
    mock_garmin_client.query_garmin_graphql.assert_called_once()


@pytest.mark.asyncio
async def test_get_training_plan_workouts_tool(app_with_workouts, mock_garmin_client):
    """Test get_training_plan_workouts tool - uses GraphQL query"""
    import json as json_module

    # Setup mock for GraphQL query - matches actual API response structure
    graphql_response = {
        "data": {
            "trainingPlanScalar": {
                "trainingPlanWorkoutScheduleDTOS": [
                    {
                        "planName": "5K Training Plan",
                        "trainingPlanDetailsDTO": {
                            "athletePlanId": 12345,
                            "workoutsPerWeek": 4
                        },
                        "workoutScheduleSummaries": [
                            {
                                "workoutUuid": "abc-123-def",
                                "workoutId": None,
                                "workoutName": "Base Run",
                                "workoutType": "running",
                                "scheduleDate": "2024-01-15",
                                "tpPlanName": "5K Training Plan",
                                "associatedActivityId": None,
                                "estimatedDurationInSecs": 1800
                            },
                            {
                                "workoutUuid": "xyz-456-ghi",
                                "workoutId": None,
                                "workoutName": "Strength",
                                "workoutType": "strength_training",
                                "scheduleDate": "2024-01-15",
                                "tpPlanName": "5K Training Plan",
                                "associatedActivityId": 987654,
                                "estimatedDurationInSecs": 1200
                            }
                        ]
                    }
                ]
            }
        }
    }
    mock_garmin_client.query_garmin_graphql.return_value = graphql_response

    # Call tool
    result = await app_with_workouts.call_tool(
        "get_training_plan_workouts",
        {"calendar_date": "2024-01-15"}
    )

    # Verify
    assert result is not None
    mock_garmin_client.query_garmin_graphql.assert_called_once()

    # Verify curation extracts correct fields
    result_data = json_module.loads(result[0][0].text)
    assert result_data["date"] == "2024-01-15"
    assert result_data["training_plans"] == ["5K Training Plan"]
    assert result_data["count"] == 2

    # Verify workouts are curated correctly
    workouts = result_data["workouts"]
    assert workouts[0]["name"] == "Base Run"
    assert workouts[0]["sport"] == "running"
    assert workouts[0]["completed"] is False

    # Verify completed workout has activity_id
    assert workouts[1]["name"] == "Strength"
    assert workouts[1]["completed"] is True
    assert workouts[1]["activity_id"] == 987654


# Delete workout tests
@pytest.mark.asyncio
async def test_delete_workout_success_204(app_with_workouts, mock_garmin_client):
    """Test delete_workout tool with 204 response"""
    import json as json_module
    from unittest.mock import MagicMock

    # Setup mock for client.delete call
    mock_response = MagicMock()
    mock_response.status_code = 204
    mock_garmin_client.client.delete.return_value = mock_response

    # Call tool
    workout_id = 123456
    result = await app_with_workouts.call_tool(
        "delete_workout",
        {"workout_id": workout_id}
    )

    # Verify
    assert result is not None
    result_data = json_module.loads(result[0][0].text)
    assert result_data["status"] == "success"
    assert result_data["workout_id"] == 123456
    assert "deleted successfully" in result_data["message"]


@pytest.mark.asyncio
async def test_delete_workout_success_200(app_with_workouts, mock_garmin_client):
    """Test delete_workout tool with 200 response"""
    import json as json_module
    from unittest.mock import MagicMock

    # Setup mock for client.delete call
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_garmin_client.client.delete.return_value = mock_response

    # Call tool
    workout_id = 789012
    result = await app_with_workouts.call_tool(
        "delete_workout",
        {"workout_id": workout_id}
    )

    # Verify
    assert result is not None
    result_data = json_module.loads(result[0][0].text)
    assert result_data["status"] == "success"
    assert result_data["workout_id"] == 789012


@pytest.mark.asyncio
async def test_delete_workout_failure(app_with_workouts, mock_garmin_client):
    """Test delete_workout tool when deletion fails (non-200/204 status)"""
    import json as json_module
    from unittest.mock import MagicMock

    # Setup mock for client.delete call with error status
    mock_response = MagicMock()
    mock_response.status_code = 404
    mock_garmin_client.client.delete.return_value = mock_response

    # Call tool
    workout_id = 999999
    result = await app_with_workouts.call_tool(
        "delete_workout",
        {"workout_id": workout_id}
    )

    # Verify
    assert result is not None
    result_data = json_module.loads(result[0][0].text)
    assert result_data["status"] == "failed"
    assert result_data["workout_id"] == 999999
    assert result_data["http_status"] == 404


@pytest.mark.asyncio
async def test_delete_workout_exception(app_with_workouts, mock_garmin_client):
    """Test delete_workout tool when an exception is raised"""
    # Setup mock to raise exception
    mock_garmin_client.client.delete.side_effect = Exception("Network error")

    # Call tool
    result = await app_with_workouts.call_tool(
        "delete_workout",
        {"workout_id": 123456}
    )

    # Verify error is handled gracefully
    assert result is not None
    assert "Error deleting workout" in result[0][0].text


# Error handling tests
@pytest.mark.asyncio
async def test_get_workouts_no_data(app_with_workouts, mock_garmin_client):
    """Test get_workouts tool when no workouts found"""
    # Setup mock to return None
    mock_garmin_client.get_workouts.return_value = None

    # Call tool
    result = await app_with_workouts.call_tool(
        "get_workouts",
        {}
    )

    # Verify error message is returned
    assert result is not None


@pytest.mark.asyncio
async def test_upload_workout_exception(app_with_workouts, mock_garmin_client):
    """Test upload_workout tool when upload fails"""
    # Setup mock to raise exception
    mock_garmin_client.upload_workout.side_effect = Exception("Upload failed")

    # Call tool with valid workout data
    result = await app_with_workouts.call_tool(
        "upload_workout",
        {"workout_data": {}}
    )

    # Verify error is handled gracefully
    assert result is not None


# delete_workouts tests
@pytest.mark.asyncio
async def test_delete_workouts_single(app_with_workouts, mock_garmin_client):
    """Test delete_workouts with a single workout ID"""
    import json as json_module
    from unittest.mock import MagicMock

    mock_response = MagicMock()
    mock_response.status_code = 204
    mock_garmin_client.client.delete.return_value = mock_response

    result = await app_with_workouts.call_tool(
        "delete_workouts",
        {"workout_ids": [123456]}
    )

    assert result is not None
    result_data = json_module.loads(result[0][0].text)
    assert result_data["total"] == 1
    assert result_data["succeeded"] == 1
    assert result_data["failed"] == 0
    assert result_data["results"][0]["status"] == "success"
    assert result_data["results"][0]["workout_id"] == 123456


@pytest.mark.asyncio
async def test_delete_workouts_multiple(app_with_workouts, mock_garmin_client):
    """Test delete_workouts with multiple workout IDs"""
    import json as json_module
    from unittest.mock import MagicMock

    mock_response = MagicMock()
    mock_response.status_code = 204
    mock_garmin_client.client.delete.return_value = mock_response

    result = await app_with_workouts.call_tool(
        "delete_workouts",
        {"workout_ids": [111, 222, 333]}
    )

    assert result is not None
    result_data = json_module.loads(result[0][0].text)
    assert result_data["total"] == 3
    assert result_data["succeeded"] == 3
    assert result_data["failed"] == 0
    assert mock_garmin_client.client.delete.call_count == 3


@pytest.mark.asyncio
async def test_delete_workouts_partial_failure(app_with_workouts, mock_garmin_client):
    """Test delete_workouts when some deletions fail"""
    import json as json_module
    from unittest.mock import MagicMock

    ok_response = MagicMock()
    ok_response.status_code = 204
    err_response = MagicMock()
    err_response.status_code = 404

    mock_garmin_client.client.delete.side_effect = [ok_response, err_response]

    result = await app_with_workouts.call_tool(
        "delete_workouts",
        {"workout_ids": [111, 999]}
    )

    assert result is not None
    result_data = json_module.loads(result[0][0].text)
    assert result_data["total"] == 2
    assert result_data["succeeded"] == 1
    assert result_data["failed"] == 1
    assert result_data["results"][0]["status"] == "success"
    assert result_data["results"][1]["status"] == "failed"
    assert result_data["results"][1]["http_status"] == 404


@pytest.mark.asyncio
async def test_delete_workouts_exception(app_with_workouts, mock_garmin_client):
    """Test delete_workouts when an exception is raised"""
    import json as json_module

    mock_garmin_client.client.delete.side_effect = Exception("Network error")

    result = await app_with_workouts.call_tool(
        "delete_workouts",
        {"workout_ids": [123456]}
    )

    assert result is not None
    result_data = json_module.loads(result[0][0].text)
    assert result_data["total"] == 1
    assert result_data["succeeded"] == 0
    assert result_data["failed"] == 1
    assert result_data["results"][0]["status"] == "error"
    assert "Network error" in result_data["results"][0]["message"]


# upload_workouts tests
@pytest.mark.asyncio
async def test_upload_workouts_single(app_with_workouts, mock_garmin_client):
    """Test upload_workouts with a single workout"""
    import json as json_module

    mock_garmin_client.upload_workout.return_value = {"workoutId": 111, "workoutName": "Easy Run"}

    result = await app_with_workouts.call_tool(
        "upload_workouts",
        {"workouts": [{"workoutName": "Easy Run", "sportType": {"sportTypeId": 1, "sportTypeKey": "running"}}]}
    )

    assert result is not None
    result_data = json_module.loads(result[0][0].text)
    assert result_data["total"] == 1
    assert result_data["succeeded"] == 1
    assert result_data["failed"] == 0
    assert result_data["results"][0]["status"] == "success"
    assert result_data["results"][0]["workout_id"] == 111
    assert result_data["results"][0]["name"] == "Easy Run"
    mock_garmin_client.upload_workout.assert_called_once()


@pytest.mark.asyncio
async def test_upload_workouts_multiple(app_with_workouts, mock_garmin_client):
    """Test upload_workouts with multiple workouts"""
    import json as json_module

    mock_garmin_client.upload_workout.side_effect = [
        {"workoutId": 111, "workoutName": "Easy Run"},
        {"workoutId": 222, "workoutName": "Tempo Run"},
        {"workoutId": 333, "workoutName": "Long Run"},
    ]

    workouts = [
        {"workoutName": "Easy Run"},
        {"workoutName": "Tempo Run"},
        {"workoutName": "Long Run"},
    ]
    result = await app_with_workouts.call_tool("upload_workouts", {"workouts": workouts})

    assert result is not None
    result_data = json_module.loads(result[0][0].text)
    assert result_data["total"] == 3
    assert result_data["succeeded"] == 3
    assert result_data["failed"] == 0
    assert mock_garmin_client.upload_workout.call_count == 3


@pytest.mark.asyncio
async def test_upload_workouts_partial_failure(app_with_workouts, mock_garmin_client):
    """Test upload_workouts when some uploads fail"""
    import json as json_module

    mock_garmin_client.upload_workout.side_effect = [
        {"workoutId": 111, "workoutName": "Easy Run"},
        Exception("API error"),
    ]

    workouts = [
        {"workoutName": "Easy Run"},
        {"workoutName": "Bad Workout"},
    ]
    result = await app_with_workouts.call_tool("upload_workouts", {"workouts": workouts})

    assert result is not None
    result_data = json_module.loads(result[0][0].text)
    assert result_data["total"] == 2
    assert result_data["succeeded"] == 1
    assert result_data["failed"] == 1
    assert result_data["results"][0]["status"] == "success"
    assert result_data["results"][1]["status"] == "error"
    assert "API error" in result_data["results"][1]["message"]
    assert result_data["results"][1]["name"] == "Bad Workout"


# schedule_workouts tests
@pytest.mark.asyncio
async def test_schedule_workouts_single(app_with_workouts, mock_garmin_client):
    """Test schedule_workouts with a single workout"""
    import json as json_module
    from unittest.mock import MagicMock

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_garmin_client.client.post.return_value = mock_response

    result = await app_with_workouts.call_tool(
        "schedule_workouts",
        {"schedules": [{"workout_id": 123456, "calendar_date": "2024-01-15"}]}
    )

    assert result is not None
    result_data = json_module.loads(result[0][0].text)
    assert result_data["total"] == 1
    assert result_data["succeeded"] == 1
    assert result_data["failed"] == 0
    assert result_data["results"][0]["status"] == "success"
    assert result_data["results"][0]["workout_id"] == 123456
    assert result_data["results"][0]["scheduled_date"] == "2024-01-15"
    mock_garmin_client.client.post.assert_called_once_with(
        "connectapi", "workout-service/schedule/123456", json={"date": "2024-01-15"}
    )


@pytest.mark.asyncio
async def test_schedule_workouts_multiple(app_with_workouts, mock_garmin_client):
    """Test schedule_workouts with multiple workouts"""
    import json as json_module
    from unittest.mock import MagicMock

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_garmin_client.client.post.return_value = mock_response

    schedules = [
        {"workout_id": 111, "calendar_date": "2024-01-15"},
        {"workout_id": 222, "calendar_date": "2024-01-17"},
        {"workout_id": 333, "calendar_date": "2024-01-19"},
    ]
    result = await app_with_workouts.call_tool(
        "schedule_workouts",
        {"schedules": schedules}
    )

    assert result is not None
    result_data = json_module.loads(result[0][0].text)
    assert result_data["total"] == 3
    assert result_data["succeeded"] == 3
    assert result_data["failed"] == 0
    assert mock_garmin_client.client.post.call_count == 3


@pytest.mark.asyncio
async def test_schedule_workouts_partial_failure(app_with_workouts, mock_garmin_client):
    """Test schedule_workouts when some workouts fail"""
    import json as json_module
    from unittest.mock import MagicMock

    ok_response = MagicMock()
    ok_response.status_code = 200
    err_response = MagicMock()
    err_response.status_code = 404

    mock_garmin_client.client.post.side_effect = [ok_response, err_response]

    schedules = [
        {"workout_id": 111, "calendar_date": "2024-01-15"},
        {"workout_id": 999, "calendar_date": "2024-01-17"},
    ]
    result = await app_with_workouts.call_tool(
        "schedule_workouts",
        {"schedules": schedules}
    )

    assert result is not None
    result_data = json_module.loads(result[0][0].text)
    assert result_data["total"] == 2
    assert result_data["succeeded"] == 1
    assert result_data["failed"] == 1
    assert result_data["results"][0]["status"] == "success"
    assert result_data["results"][1]["status"] == "failed"
    assert result_data["results"][1]["http_status"] == 404


@pytest.mark.asyncio
async def test_schedule_workouts_missing_fields(app_with_workouts, mock_garmin_client):
    """Test schedule_workouts with missing required fields"""
    import json as json_module

    result = await app_with_workouts.call_tool(
        "schedule_workouts",
        {"schedules": [{"workout_id": 123456}]}  # missing calendar_date
    )

    assert result is not None
    result_data = json_module.loads(result[0][0].text)
    assert result_data["total"] == 1
    assert result_data["succeeded"] == 0
    assert result_data["failed"] == 1
    assert result_data["results"][0]["status"] == "failed"
    assert "Missing required field" in result_data["results"][0]["message"]
    mock_garmin_client.client.post.assert_not_called()


@pytest.mark.asyncio
async def test_schedule_workouts_exception(app_with_workouts, mock_garmin_client):
    """Test schedule_workouts when an exception is raised"""
    import json as json_module

    mock_garmin_client.client.post.side_effect = Exception("Network error")

    result = await app_with_workouts.call_tool(
        "schedule_workouts",
        {"schedules": [{"workout_id": 123456, "calendar_date": "2024-01-15"}]}
    )

    assert result is not None
    result_data = json_module.loads(result[0][0].text)
    assert result_data["total"] == 1
    assert result_data["succeeded"] == 0
    assert result_data["failed"] == 1
    assert result_data["results"][0]["status"] == "error"
    assert "Network error" in result_data["results"][0]["message"]


@pytest.mark.asyncio
async def test_schedule_workouts_inline_upload(app_with_workouts, mock_garmin_client):
    """Test schedule_workouts with inline workout_data uploads-and-schedules in one call"""
    import json as json_module
    from unittest.mock import MagicMock

    upload_result = {"workoutId": 999001, "workoutName": "Easy Run"}
    mock_garmin_client.upload_workout.return_value = upload_result

    schedule_response = MagicMock()
    schedule_response.status_code = 200
    mock_garmin_client.client.post.return_value = schedule_response

    inline_data = {"workoutName": "Easy Run", "sportType": {"sportTypeId": 1, "sportTypeKey": "running"}}
    result = await app_with_workouts.call_tool(
        "schedule_workouts",
        {"schedules": [{"workout_data": inline_data, "calendar_date": "2024-02-01"}]}
    )

    assert result is not None
    result_data = json_module.loads(result[0][0].text)
    assert result_data["total"] == 1
    assert result_data["succeeded"] == 1
    assert result_data["failed"] == 0
    entry = result_data["results"][0]
    assert entry["status"] == "success"
    assert entry["workout_id"] == 999001
    assert entry["scheduled_date"] == "2024-02-01"
    assert entry["workout_name"] == "Easy Run"
    mock_garmin_client.upload_workout.assert_called_once_with(inline_data)
    mock_garmin_client.client.post.assert_called_once_with(
        "connectapi", "workout-service/schedule/999001", json={"date": "2024-02-01"}
    )


@pytest.mark.asyncio
async def test_schedule_workouts_mixed_inline_and_id(app_with_workouts, mock_garmin_client):
    """Test schedule_workouts mixing inline workout_data and existing workout_id"""
    import json as json_module
    from unittest.mock import MagicMock

    upload_result = {"workoutId": 999002, "workoutName": "Tempo Run"}
    mock_garmin_client.upload_workout.return_value = upload_result

    schedule_response = MagicMock()
    schedule_response.status_code = 200
    mock_garmin_client.client.post.return_value = schedule_response

    inline_data = {"workoutName": "Tempo Run", "sportType": {"sportTypeId": 1, "sportTypeKey": "running"}}
    schedules = [
        {"workout_id": 111, "calendar_date": "2024-02-05"},
        {"workout_data": inline_data, "calendar_date": "2024-02-07"},
    ]
    result = await app_with_workouts.call_tool("schedule_workouts", {"schedules": schedules})

    assert result is not None
    result_data = json_module.loads(result[0][0].text)
    assert result_data["total"] == 2
    assert result_data["succeeded"] == 2
    assert result_data["failed"] == 0
    assert result_data["results"][0]["workout_id"] == 111
    assert result_data["results"][1]["workout_id"] == 999002


@pytest.mark.asyncio
async def test_schedule_workouts_missing_both_id_and_data(app_with_workouts, mock_garmin_client):
    """Test schedule_workouts fails when neither workout_id nor workout_data is provided"""
    import json as json_module

    result = await app_with_workouts.call_tool(
        "schedule_workouts",
        {"schedules": [{"calendar_date": "2024-02-01"}]}
    )

    assert result is not None
    result_data = json_module.loads(result[0][0].text)
    assert result_data["total"] == 1
    assert result_data["succeeded"] == 0
    assert result_data["failed"] == 1
    assert "workout_id" in result_data["results"][0]["message"] or "workout_data" in result_data["results"][0]["message"]
    mock_garmin_client.client.post.assert_not_called()


@pytest.mark.asyncio
async def test_schedule_workouts_inline_upload_no_id_returned(app_with_workouts, mock_garmin_client):
    """Test schedule_workouts fails gracefully when upload returns no workout_id"""
    import json as json_module

    mock_garmin_client.upload_workout.return_value = {"workoutName": "Bad Response"}

    inline_data = {"workoutName": "Bad Response"}
    result = await app_with_workouts.call_tool(
        "schedule_workouts",
        {"schedules": [{"workout_data": inline_data, "calendar_date": "2024-02-01"}]}
    )

    assert result is not None
    result_data = json_module.loads(result[0][0].text)
    assert result_data["total"] == 1
    assert result_data["succeeded"] == 0
    assert result_data["failed"] == 1
    assert result_data["results"][0]["status"] == "failed"
    mock_garmin_client.client.post.assert_not_called()


# =============================================================================
# Rich Garmin API error reporting (upload_workout / upload_workouts /
# schedule_workouts) and the new running-workout MCP tools.
# =============================================================================

def _structured_run_payload():
    """Mirrors the failing-then-fixed progression run from the bug report."""
    return {
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


def _tempo_blocks_payload():
    """Mirrors the failing-then-fixed tempo-blocks workout with a RepeatGroupDTO."""
    return {
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
                      "zoneNumber": 2}]},
                {"type": "ExecutableStepDTO", "stepOrder": 3,
                 "stepType": {"stepTypeId": 2, "stepTypeKey": "cooldown"},
                 "endCondition": {"conditionTypeId": 2, "conditionTypeKey": "time"},
                 "endConditionValue": 300.0,
                 "targetType": {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"}},
            ],
        }],
    }


@pytest.mark.asyncio
async def test_upload_workout_progression_run_uploads(app_with_workouts, mock_garmin_client):
    """Acceptance: a progression run with warmup/cooldown uploads successfully."""
    import json as json_module
    mock_garmin_client.upload_workout.return_value = {
        "workoutId": 4242, "workoutName": "Progression Run",
    }

    payload = _structured_run_payload()
    result = await app_with_workouts.call_tool("upload_workout", {"workout_data": payload})
    data = json_module.loads(result[0][0].text)

    assert data["status"] == "success"
    assert data["workout_id"] == 4242
    # The exact payload (with all warmup/interval/cooldown steps) was forwarded.
    forwarded = mock_garmin_client.upload_workout.call_args[0][0]
    keys = [s["stepType"]["stepTypeKey"] for s in forwarded["workoutSegments"][0]["workoutSteps"]]
    assert keys == ["warmup", "interval", "interval", "cooldown"]


@pytest.mark.asyncio
async def test_upload_workout_tempo_blocks_with_repeat_group_uploads(app_with_workouts, mock_garmin_client):
    """Acceptance: a tempo-blocks workout with RepeatGroupDTO uploads successfully."""
    import json as json_module
    mock_garmin_client.upload_workout.return_value = {
        "workoutId": 4343, "workoutName": "Tempo Blocks",
    }

    payload = _tempo_blocks_payload()
    result = await app_with_workouts.call_tool("upload_workout", {"workout_data": payload})
    data = json_module.loads(result[0][0].text)

    assert data["status"] == "success"
    forwarded = mock_garmin_client.upload_workout.call_args[0][0]
    rg = forwarded["workoutSegments"][0]["workoutSteps"][1]
    assert rg["type"] == "RepeatGroupDTO"
    assert rg["numberOfIterations"] == 3
    assert len(rg["workoutSteps"]) == 2


@pytest.mark.asyncio
async def test_upload_workout_surfaces_garmin_response_body_on_400(app_with_workouts, mock_garmin_client):
    """When Garmin rejects with HTTP 400, the response now includes the body, endpoint, method, and step summary."""
    import json as json_module
    from unittest.mock import MagicMock

    # Build an exception whose .response mimics a real requests.HTTPError response.
    resp = MagicMock()
    resp.status_code = 400
    resp.url = "https://connectapi.garmin.com/workout-service/workout"
    resp.json.side_effect = ValueError("not json")
    resp.text = "RepeatGroup: numberOfIterations is required."
    boom = RuntimeError("API Error 400 - HTTP 400 Bad Request")
    boom.response = resp
    mock_garmin_client.upload_workout.side_effect = boom

    payload = _tempo_blocks_payload()
    # Strip the iterations to simulate the failing payload.
    payload["workoutSegments"][0]["workoutSteps"][1].pop("numberOfIterations", None)

    result = await app_with_workouts.call_tool("upload_workout", {"workout_data": payload})
    data = json_module.loads(result[0][0].text)

    assert data["status"] == "error"
    assert data["operation"] == "upload_workout"
    assert data["http_status"] == 400
    assert data["request_method"] == "POST"
    assert data["request_endpoint"] == "/workout-service/workout"
    assert "numberOfIterations" in data["response_body_text"]
    # Sanitized payload summary should expose the failing step layout.
    assert data["workout"]["workoutName"] == "Tempo Blocks"
    assert data["workout"]["sport"] == "running"
    summary_steps = data["workout"]["first_segment"]["steps"]
    assert any(step["dto"] == "RepeatGroupDTO" for step in summary_steps)


@pytest.mark.asyncio
async def test_upload_workout_parses_garmin_connect_string_when_no_response(app_with_workouts, mock_garmin_client):
    """garminconnect 0.3.2 raises GarminConnectConnectionError(string) — we must still
    extract status + body fragment from the string."""
    import json as json_module

    mock_garmin_client.upload_workout.side_effect = RuntimeError(
        "API Error 400 - RepeatGroup: numberOfIterations is required."
    )

    result = await app_with_workouts.call_tool(
        "upload_workout", {"workout_data": _tempo_blocks_payload()}
    )
    data = json_module.loads(result[0][0].text)
    assert data["status"] == "error"
    assert data["http_status"] == 400
    assert "numberOfIterations" in data["response_body_text"]


@pytest.mark.asyncio
async def test_upload_workouts_partial_failure_preserves_rich_error(app_with_workouts, mock_garmin_client):
    """upload_workouts bulk failures keep http_status + body + step summary per entry."""
    import json as json_module

    mock_garmin_client.upload_workout.side_effect = [
        {"workoutId": 1, "workoutName": "OK Run"},
        RuntimeError("API Error 400 - missing stepType somewhere"),
    ]

    good = _structured_run_payload()
    good["workoutName"] = "OK Run"
    bad = _tempo_blocks_payload()
    bad["workoutName"] = "Bad Run"
    result = await app_with_workouts.call_tool(
        "upload_workouts", {"workouts": [good, bad]}
    )
    data = json_module.loads(result[0][0].text)
    assert data["succeeded"] == 1 and data["failed"] == 1
    bad_entry = data["results"][1]
    assert bad_entry["status"] == "error"
    assert bad_entry["http_status"] == 400
    assert "missing stepType" in bad_entry["response_body_text"]
    assert bad_entry["workout"]["workoutName"] == "Bad Run"


@pytest.mark.asyncio
async def test_schedule_workouts_inline_upload_failure_preserves_full_error(app_with_workouts, mock_garmin_client):
    """When inline workout_data upload fails inside schedule_workouts, the entry must
    carry the full upload error (status/body/endpoint/summary) and never reach the
    schedule HTTP call."""
    import json as json_module
    from unittest.mock import MagicMock

    resp = MagicMock()
    resp.status_code = 400
    resp.url = "https://connectapi.garmin.com/workout-service/workout"
    resp.json.side_effect = ValueError("nope")
    resp.text = "RepeatGroup: numberOfIterations is required."
    upload_err = RuntimeError("API Error 400 - HTTP 400 Bad Request")
    upload_err.response = resp
    mock_garmin_client.upload_workout.side_effect = upload_err

    inline = _tempo_blocks_payload()
    inline["workoutName"] = "Bad Inline Upload"

    result = await app_with_workouts.call_tool(
        "schedule_workouts",
        {"schedules": [{"workout_data": inline, "calendar_date": "2026-05-20"}]},
    )
    data = json_module.loads(result[0][0].text)
    assert data["total"] == 1
    assert data["succeeded"] == 0
    assert data["failed"] == 1
    entry = data["results"][0]
    assert entry["status"] == "error"
    assert entry["stage"] == "upload"
    assert entry["http_status"] == 400
    assert "numberOfIterations" in entry["response_body_text"]
    assert entry["name"] == "Bad Inline Upload"
    assert entry["scheduled_date"] == "2026-05-20"
    # Critically, we never attempted to schedule.
    mock_garmin_client.client.post.assert_not_called()


@pytest.mark.asyncio
async def test_schedule_workouts_schedule_failure_includes_body(app_with_workouts, mock_garmin_client):
    """schedule step failure (HTTP 4xx) should include response body when available."""
    import json as json_module
    from unittest.mock import MagicMock

    schedule_resp = MagicMock()
    schedule_resp.status_code = 409
    schedule_resp.json.side_effect = ValueError("no json")
    schedule_resp.text = "Workout already scheduled on that date"
    mock_garmin_client.client.post.return_value = schedule_resp

    result = await app_with_workouts.call_tool(
        "schedule_workouts",
        {"schedules": [{"workout_id": 999, "calendar_date": "2026-05-20"}]},
    )
    data = json_module.loads(result[0][0].text)
    entry = data["results"][0]
    assert entry["status"] == "failed"
    assert entry["http_status"] == 409
    assert entry["stage"] == "schedule"
    assert "already scheduled" in entry["response_body_text"]
    assert entry["request_endpoint"] == "/workout-service/schedule/999"


@pytest.mark.asyncio
async def test_validate_running_workout_tool_returns_ok_for_canonical_payload(app_with_workouts, mock_garmin_client):
    import json as json_module

    result = await app_with_workouts.call_tool(
        "validate_running_workout", {"workout_data": _structured_run_payload()}
    )
    data = json_module.loads(result[0][0].text)
    assert data["ok"] is True
    assert data["issues"] == []
    assert data["summary"]["sport"] == "running"
    # validator must not touch Garmin.
    mock_garmin_client.upload_workout.assert_not_called()


@pytest.mark.asyncio
async def test_validate_running_workout_tool_flags_broken_repeat_group(app_with_workouts):
    import json as json_module

    broken = _tempo_blocks_payload()
    broken["workoutSegments"][0]["workoutSteps"][1].pop("numberOfIterations", None)

    result = await app_with_workouts.call_tool(
        "validate_running_workout", {"workout_data": broken}
    )
    data = json_module.loads(result[0][0].text)
    assert data["ok"] is False
    assert any("numberOfIterations" in issue for issue in data["issues"])


@pytest.mark.asyncio
async def test_preview_running_workout_tool_includes_schema_on_invalid(app_with_workouts):
    import json as json_module

    broken = _structured_run_payload()
    # Break a stepType id/key pair.
    broken["workoutSegments"][0]["workoutSteps"][1]["stepType"] = {
        "stepTypeId": 3, "stepTypeKey": "warmup",
    }

    result = await app_with_workouts.call_tool(
        "preview_running_workout", {"workout_data": broken}
    )
    data = json_module.loads(result[0][0].text)
    assert data["status"] == "invalid"
    assert data["valid"] is False
    assert any("stepTypeId" in i and "warmup" in i for i in data["issues"])
    assert "expected_step_types" in data
    assert data["expected_step_types"]["interval"] == 3
    assert data["expected_step_types"]["recovery"] == 4
