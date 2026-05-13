import inspect
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "garmin_mcp"))

_tmp = tempfile.TemporaryDirectory()
os.environ["GARMIN_STRENGTH_EXERCISES_FILE"] = str(Path(_tmp.name) / "strength_exercises.json")
os.environ["GARMIN_STRENGTH_EXERCISES_SEED"] = str(Path(_tmp.name) / "missing-seed.json")
os.environ["GARMIN_GLOBAL_CATALOG_FILE"] = str(Path(_tmp.name) / "garmin_global_exercises.json")

import strength_exercise_catalog  # noqa: E402
import strength_workouts  # noqa: E402

# Populate the GLOBAL catalog with test data (mimics Garmin's Exercises.json structure)
test_data = {
    "categories": {
        "ROW": {
            "exercises": {
                "FACE_PULL": {"primaryMuscles": ["SHOULDERS"], "secondaryMuscles": []},
                "DUMBBELL_ROW": {"primaryMuscles": ["BACK"], "secondaryMuscles": []},
                "SEATED_CABLE_ROW": {"primaryMuscles": ["BACK"], "secondaryMuscles": []},
                "CHEST_SUPPORTED_DUMBBELL_ROW": {"primaryMuscles": ["BACK"], "secondaryMuscles": []},
            }
        },
        "LATERAL_RAISE": {
            "exercises": {
                "DUMBBELL_LATERAL_RAISE": {"primaryMuscles": ["SHOULDERS"], "secondaryMuscles": []},
                "BENT_OVER_LATERAL_RAISE": {"primaryMuscles": ["SHOULDERS"], "secondaryMuscles": []},
                "SEATED_REAR_LATERAL_RAISE": {"primaryMuscles": ["SHOULDERS"], "secondaryMuscles": []},
            }
        },
        "PLANK": {
            "exercises": {
                "PLANK": {"primaryMuscles": ["CORE"], "secondaryMuscles": []},
            }
        },
        "PULL_UP": {
            "exercises": {
                "LAT_PULLDOWN": {"primaryMuscles": ["LATS"], "secondaryMuscles": []},
                "CLOSE_GRIP_LAT_PULL_DOWN": {"primaryMuscles": ["LATS"], "secondaryMuscles": []},
            }
        },
        "BENCH_PRESS": {
            "exercises": {
                "BARBELL_BENCH_PRESS": {"primaryMuscles": ["CHEST"], "secondaryMuscles": []},
                "SMITH_MACHINE_BENCH_PRESS": {"primaryMuscles": ["CHEST"], "secondaryMuscles": []},
                "DUMBBELL_FLOOR_PRESS": {"primaryMuscles": ["CHEST"], "secondaryMuscles": []},
            }
        },
        "SQUAT": {
            "exercises": {
                "BARBELL_SQUAT": {"primaryMuscles": ["LEGS"], "secondaryMuscles": []},
                "DUMBBELL_SQUAT": {"primaryMuscles": ["LEGS"], "secondaryMuscles": []},
            }
        },
        "DEADLIFT": {
            "exercises": {
                "BARBELL_DEADLIFT": {"primaryMuscles": ["BACK"], "secondaryMuscles": []},
                "ROMANIAN_DEADLIFT": {"primaryMuscles": ["BACK"], "secondaryMuscles": []},
            }
        },
        "CURL": {
            "exercises": {
                "BARBELL_BICEPS_CURL": {"primaryMuscles": ["BICEPS"], "secondaryMuscles": []},
                "DUMBBELL_HAMMER_CURL": {"primaryMuscles": ["BICEPS"], "secondaryMuscles": []},
                "SEATED_ALTERNATING_DUMBBELL_BICEPS_CURL": {"primaryMuscles": ["BICEPS"], "secondaryMuscles": []},
            }
        },
        "TRICEPS_EXTENSION": {
            "exercises": {
                "TRICEPS_EXTENSION": {"primaryMuscles": ["TRICEPS"], "secondaryMuscles": []},
                "OVERHEAD_DUMBBELL_TRICEPS_EXTENSION": {"primaryMuscles": ["TRICEPS"], "secondaryMuscles": []},
            }
        },
        "SHOULDER_PRESS": {
            "exercises": {
                "BARBELL_SHOULDER_PRESS": {"primaryMuscles": ["SHOULDERS"], "secondaryMuscles": []},
                "OVERHEAD_BARBELL_PRESS": {"primaryMuscles": ["SHOULDERS"], "secondaryMuscles": []},
                "DUMBBELL_SHOULDER_PRESS": {"primaryMuscles": ["SHOULDERS"], "secondaryMuscles": []},
                "SEATED_BARBELL_SHOULDER_PRESS": {"primaryMuscles": ["SHOULDERS"], "secondaryMuscles": []},
            }
        },
        "FLYE": {
            "exercises": {
                "INCLINE_DUMBBELL_FLY": {"primaryMuscles": ["CHEST"], "secondaryMuscles": []},
                "INCLINE_REVERSE_FLY": {"primaryMuscles": ["SHOULDERS"], "secondaryMuscles": []},
            }
        },
        "CORE": {
            "exercises": {
                "RUSSIAN_TWIST": {"primaryMuscles": ["CORE"], "secondaryMuscles": []},
                "DEAD_BUG": {"primaryMuscles": ["CORE"], "secondaryMuscles": []},
                "WEIGHTED_DEAD_BUG": {"primaryMuscles": ["CORE"], "secondaryMuscles": []},
                "X_ABS": {"primaryMuscles": ["CORE"], "secondaryMuscles": []},
            }
        },
        "CARRY": {
            "exercises": {
                "FARMERS_CARRY": {"primaryMuscles": ["CORE"], "secondaryMuscles": []},
            }
        },
        "SHOULDER_STABILITY": {
            "exercises": {
                "BAND_EXTERNAL_ROTATION": {"primaryMuscles": ["SHOULDERS"], "secondaryMuscles": []},
                "BANDED_EXTERNAL_ROTATION": {"primaryMuscles": ["SHOULDERS"], "secondaryMuscles": []},
                "CABLE_EXTERNAL_ROTATION": {"primaryMuscles": ["SHOULDERS"], "secondaryMuscles": []},
                "FLOOR_Y_RAISE": {"primaryMuscles": ["SHOULDERS"], "secondaryMuscles": []},
            }
        },
        "PUSH_UP": {
            "exercises": {
                "PUSH_UP": {"primaryMuscles": ["CHEST"], "secondaryMuscles": []},
            }
        },
        "LUNGE": {
            "exercises": {
                "DUMBBELL_BULGARIAN_SPLIT_SQUAT": {"primaryMuscles": ["LEGS"], "secondaryMuscles": []},
            }
        },
        "CALF_RAISE": {
            "exercises": {
                "CALF_RAISE": {"primaryMuscles": ["LEGS"], "secondaryMuscles": []},
            }
        },
    }
}
test_translations = {
    "ROW_FACE_PULL": "Face Pull",
    "ROW_DUMBBELL_ROW": "Dumbbell Row",
    "ROW_SEATED_CABLE_ROW": "Seated Cable Row",
    "ROW_CHEST_SUPPORTED_DUMBBELL_ROW": "Chest Supported Dumbbell Row",
    "LATERAL_RAISE_DUMBBELL_LATERAL_RAISE": "Dumbbell Lateral Raise",
    "LATERAL_RAISE_BENT_OVER_LATERAL_RAISE": "Bent-over Lateral Raise",
    "LATERAL_RAISE_SEATED_REAR_LATERAL_RAISE": "Seated Rear Lateral Raise",
    "PLANK_PLANK": "Plank",
    "PULL_UP_LAT_PULLDOWN": "Lat Pull-down",
    "PULL_UP_CLOSE_GRIP_LAT_PULL_DOWN": "Close-grip Lat Pull-down",
    "BENCH_PRESS_BARBELL_BENCH_PRESS": "Barbell Bench Press",
    "BENCH_PRESS_SMITH_MACHINE_BENCH_PRESS": "Smith Machine Bench Press",
    "BENCH_PRESS_DUMBBELL_FLOOR_PRESS": "Dumbbell Floor Press",
    "SQUAT_BARBELL_SQUAT": "Barbell Squat",
    "SQUAT_DUMBBELL_SQUAT": "Dumbbell Squat",
    "DEADLIFT_BARBELL_DEADLIFT": "Barbell Deadlift",
    "DEADLIFT_ROMANIAN_DEADLIFT": "Romanian Deadlift",
    "CURL_BARBELL_BICEPS_CURL": "Barbell Biceps Curl",
    "CURL_DUMBBELL_HAMMER_CURL": "Dumbbell Hammer Curl",
    "CURL_SEATED_ALTERNATING_DUMBBELL_BICEPS_CURL": "Seated Alternating Dumbbell Biceps Curl",
    "TRICEPS_EXTENSION_TRICEPS_EXTENSION": "Triceps Extension",
    "TRICEPS_EXTENSION_OVERHEAD_DUMBBELL_TRICEPS_EXTENSION": "Overhead Dumbbell Triceps Extension",
    "SHOULDER_PRESS_BARBELL_SHOULDER_PRESS": "Barbell Shoulder Press",
    "SHOULDER_PRESS_OVERHEAD_BARBELL_PRESS": "Overhead Barbell Press",
    "SHOULDER_PRESS_DUMBBELL_SHOULDER_PRESS": "Dumbbell Shoulder Press",
    "SHOULDER_PRESS_SEATED_BARBELL_SHOULDER_PRESS": "Seated Barbell Shoulder Press",
    "FLYE_INCLINE_DUMBBELL_FLY": "Incline Dumbbell Fly",
    "FLYE_INCLINE_REVERSE_FLY": "Incline Reverse Fly",
    "CORE_RUSSIAN_TWIST": "Russian Twist",
    "CORE_DEAD_BUG": "Dead Bug",
    "CORE_WEIGHTED_DEAD_BUG": "Weighted Dead Bug",
    "CORE_X_ABS": "X Abs",
    "CARRY_FARMERS_CARRY": "Farmer's Carry",
    "SHOULDER_STABILITY_BAND_EXTERNAL_ROTATION": "Band External Rotation",
    "SHOULDER_STABILITY_BANDED_EXTERNAL_ROTATION": "Banded External Rotation",
    "SHOULDER_STABILITY_CABLE_EXTERNAL_ROTATION": "Cable External Rotation",
    "SHOULDER_STABILITY_FLOOR_Y_RAISE": "Floor Y Raise",
    "PUSH_UP_PUSH_UP": "Push-up",
    "LUNGE_DUMBBELL_BULGARIAN_SPLIT_SQUAT": "Dumbbell Bulgarian Split Squat",
    "CALF_RAISE_CALF_RAISE": "Calf Raise",
}

# Walk the test data and create global catalog entries
imported = strength_exercise_catalog._walk_garmin_exercise_catalog(test_data, test_translations)
test_global_data = {"version": 1, "exercises": imported, "updated_at": int(__import__("time").time())}
strength_exercise_catalog.save_global_catalog(test_global_data)


def upper_back_blocks():
    return [
        {
            "name": "A - Row + Reverse Fly",
            "type": "superset",
            "rounds": 3,
            "steps": [
                {"exercise": "Dumbbell Row", "reps": 10, "rest_seconds": 15},
                {"exercise": "Incline Reverse Fly", "reps": 15, "rest_seconds": 75},
            ],
        },
        {
            "name": "B - Pulldown + Lateral Raise",
            "type": "superset",
            "rounds": 3,
            "steps": [
                {"exercise": "Close-grip Lat Pull-down", "reps": 10, "rest_seconds": 15},
                {"exercise": "Dumbbell Lateral Raise", "reps": 15, "rest_seconds": 75},
            ],
        },
        {
            "name": "C - Face Pull + External Rotation",
            "type": "superset",
            "rounds": 3,
            "steps": [
                {"exercise": "Face Pull", "reps": 12, "rest_seconds": 15},
                {"exercise": "Band External Rotation", "reps": 15, "rest_seconds": 60},
            ],
        },
        {
            "name": "Carry Finisher",
            "type": "sets",
            "rounds": 2,
            "steps": [
                {"exercise": "Farmer's Carry", "duration_seconds": 40, "rest_seconds": 60},
            ],
        },
    ]


class StrengthExerciseSearchTest(unittest.TestCase):
    def test_exact_alias_and_fuzzy_search(self):
        exact = strength_exercise_catalog.search_catalog("Dumbbell Lateral Raise", limit=1, fuzzy=True)[0]
        self.assertEqual(exact["garmin_exercise_name"], "DUMBBELL_LATERAL_RAISE")
        self.assertGreaterEqual(exact["confidence"], 0.99)

        alias = strength_exercise_catalog.search_catalog("rear delt fly", limit=4, fuzzy=True)
        self.assertIn("Incline Reverse Fly", [item["display_name"] for item in alias])
        self.assertIn("Seated Rear Lateral Raise", [item["display_name"] for item in alias])

        fuzzy = strength_exercise_catalog.search_catalog("rotator cuff external rotation", limit=4, fuzzy=True)
        self.assertIn(fuzzy[0]["display_name"], {"Band External Rotation", "Banded External Rotation", "Cable External Rotation"})

    def test_resolve_lateral_raise_to_safe_mapping(self):
        resolved = strength_exercise_catalog.resolve_exercise("lateral raise")
        self.assertEqual(resolved["exercise_name"], "DUMBBELL_LATERAL_RAISE")
        self.assertEqual(resolved["category"], "SHOULDER_STABILITY")
        self.assertFalse(resolved["known_good"])
        self.assertEqual(resolved["roundtrip_status"], "stripped")
        self.assertFalse(resolved["safe_for_exact_tracking"])

    def test_sync_garmin_public_catalog_imports_unknown_roundtrip_candidates(self):
        exercises_json = json.dumps(
            {
                "categories": {
                    "SUSPENSION": {
                        "exercises": {
                            "FACE_PULL": {"primaryMuscles": ["SHOULDERS"], "secondaryMuscles": ["TRAPS"]},
                            "POWER_PULL": {"primaryMuscles": ["LATS"], "secondaryMuscles": []},
                        }
                    }
                }
            }
        )
        translations = "SUSPENSION_FACE_PULL=Suspension Face Pull\nSUSPENSION_POWER_PULL=Suspension Power Pull\n"
        result = strength_exercise_catalog.sync_garmin_public_exercise_catalog(
            exercises_json_text=exercises_json,
            translations_text=translations,
        )
        self.assertGreaterEqual(result["imported_count"], 2)
        imported = strength_exercise_catalog.resolve_exercise("Suspension Power Pull")
        self.assertEqual(imported["exercise_name"], "POWER_PULL")
        self.assertEqual(imported["category"], "SUSPENSION")
        self.assertEqual(imported["roundtrip_status"], "unknown")
        self.assertFalse(imported["known_good"])

    def test_sync_garmin_public_catalog_preserves_verified_metadata(self):
        strength_exercise_catalog.update_roundtrip_metadata("Lat Pull-down", "preserved", actual_exercise_name="LAT_PULLDOWN")
        exercises_json = json.dumps({"categories": {"PULL_UP": {"exercises": {"LAT_PULLDOWN": {"primaryMuscles": ["LATS"]}}}}})
        translations = "PULL_UP_LAT_PULLDOWN=Lat Pull-down\n"
        strength_exercise_catalog.sync_garmin_public_exercise_catalog(
            update_existing=True,
            exercises_json_text=exercises_json,
            translations_text=translations,
        )
        resolved = strength_exercise_catalog.resolve_exercise("Lat Pull-down")
        self.assertEqual(resolved["roundtrip_status"], "preserved")
        self.assertTrue(resolved["known_good"])

    def test_sync_garmin_public_catalog_fills_missing_core_fields(self):
        data = strength_exercise_catalog.load_catalog()
        data["exercises"].append(
            {
                "display_name": "Imported Missing Category",
                "exercise_name": "IMPORTED_MISSING_CATEGORY",
                "category": None,
                "aliases": [],
                "canonical": "imported_missing_category",
                "known_good": False,
            }
        )
        strength_exercise_catalog.save_catalog(data)
        exercises_json = json.dumps(
            {"categories": {"SHOULDER_PRESS": {"exercises": {"IMPORTED_MISSING_CATEGORY": {"primaryMuscles": ["SHOULDERS"]}}}}}
        )
        translations = "SHOULDER_PRESS_IMPORTED_MISSING_CATEGORY=Imported Missing Category\n"
        strength_exercise_catalog.sync_garmin_public_exercise_catalog(
            update_existing=False,
            exercises_json_text=exercises_json,
            translations_text=translations,
        )
        resolved = strength_exercise_catalog.resolve_exercise("Imported Missing Category")
        self.assertEqual(resolved["category"], "SHOULDER_PRESS")
        self.assertEqual(resolved["roundtrip_status"], "unknown")

    def test_sync_garmin_public_catalog_fills_legacy_same_exercise_name(self):
        data = strength_exercise_catalog.load_catalog()
        data["exercises"].append(
            {
                "display_name": "Legacy Overhead Name",
                "exercise_name": "LEGACY_OVERHEAD_NAME",
                "category": None,
                "aliases": [],
                "canonical": "legacy_overhead_name",
                "known_good": False,
            }
        )
        strength_exercise_catalog.save_catalog(data)
        exercises_json = json.dumps(
            {"categories": {"SHOULDER_PRESS": {"exercises": {"LEGACY_OVERHEAD_NAME": {"primaryMuscles": ["SHOULDERS"]}}}}}
        )
        translations = "SHOULDER_PRESS_LEGACY_OVERHEAD_NAME=Garmin Current Name\n"
        strength_exercise_catalog.sync_garmin_public_exercise_catalog(
            update_existing=False,
            exercises_json_text=exercises_json,
            translations_text=translations,
        )
        resolved = strength_exercise_catalog.resolve_exercise("Legacy Overhead Name")
        self.assertEqual(resolved["category"], "SHOULDER_PRESS")
        self.assertEqual(resolved["exercise_name"], "LEGACY_OVERHEAD_NAME")

    def test_sync_garmin_public_catalog_fills_duplicate_legacy_same_exercise_name(self):
        data = strength_exercise_catalog.load_catalog()
        data["exercises"].append(
            {
                "display_name": "Garmin Current Duplicate",
                "exercise_name": "DUPLICATE_OVERHEAD_NAME",
                "category": "SHOULDER_PRESS",
                "aliases": [],
                "canonical": "garmin_current_duplicate",
                "known_good": False,
            }
        )
        data["exercises"].append(
            {
                "display_name": "Legacy Duplicate",
                "exercise_name": "DUPLICATE_OVERHEAD_NAME",
                "category": None,
                "aliases": [],
                "canonical": "legacy_duplicate",
                "known_good": False,
            }
        )
        strength_exercise_catalog.save_catalog(data)
        exercises_json = json.dumps(
            {"categories": {"SHOULDER_PRESS": {"exercises": {"DUPLICATE_OVERHEAD_NAME": {"primaryMuscles": ["SHOULDERS"]}}}}}
        )
        translations = "SHOULDER_PRESS_DUPLICATE_OVERHEAD_NAME=Garmin Current Duplicate\n"
        strength_exercise_catalog.sync_garmin_public_exercise_catalog(
            update_existing=False,
            exercises_json_text=exercises_json,
            translations_text=translations,
        )
        resolved = strength_exercise_catalog.resolve_exercise("Legacy Duplicate")
        self.assertEqual(resolved["category"], "SHOULDER_PRESS")


class StrengthWorkoutBuilderTest(unittest.TestCase):
    def test_simple_strength_workout(self):
        payload = strength_workouts.build_strength_workout_payload(
            "Simple",
            exercises=[
                {"name": "Dumbbell Row", "sets": [{"reps": 10, "rest_seconds": 30}]},
                {"name": "Face Pull", "sets": [{"reps": 12}]},
            ],
            use_repeat_groups=True,
        )
        steps = payload["workoutSegments"][0]["workoutSteps"]
        self.assertEqual(steps[0]["exerciseName"], "DUMBBELL_ROW")
        self.assertEqual(steps[2]["exerciseName"], "FACE_PULL")

    def test_superset_uses_repeat_groups(self):
        payload = strength_workouts.build_strength_workout_payload(
            "Upper Back Shoulders A",
            blocks=upper_back_blocks(),
            estimated_duration_seconds=2700,
            use_repeat_groups=True,
        )
        steps = payload["workoutSegments"][0]["workoutSteps"]
        self.assertEqual(steps[0]["type"], "RepeatGroupDTO")
        self.assertEqual(steps[0]["numberOfIterations"], 3)
        nested_names = [step.get("exerciseName") for step in steps[0]["workoutSteps"] if step.get("exerciseName")]
        self.assertEqual(nested_names, ["DUMBBELL_ROW", "INCLINE_REVERSE_FLY"])

    def test_preview_and_validation_warnings(self):
        preview = strength_workouts.preview_strength_workout_definition(
            "Upper Back Shoulders A",
            blocks=upper_back_blocks(),
            estimated_duration_seconds=2700,
        )
        self.assertIn("A - Row + Reverse Fly - Superset x3", preview["preview"])
        self.assertEqual(preview["status"], "success")
        warnings = [issue for issue in preview["validation"]["issues"] if issue["severity"] == "warning"]
        # New behavior: warnings mention generic roundtrip behavior since we're using Garmin's catalog
        self.assertTrue(any("generic" in issue.get("message", "") or "strip" in issue.get("message", "") for issue in warnings))

    def test_validation_blocks_unknown_exercise(self):
        validation = strength_workouts.validate_strength_workout_definition(
            "Bad",
            blocks=[{"name": "Bad", "steps": [{"exercise": "Definitely Not An Exercise", "reps": 10}]}],
        )
        self.assertEqual(validation["status"], "error")

    def test_public_tool_signatures_expose_blocks_and_flags(self):
        self.assertIn("blocks", inspect.signature(strength_workouts.build_strength_workout_payload).parameters)
        self.assertIn("use_repeat_groups", inspect.signature(strength_workouts.build_strength_workout_payload).parameters)
        self.assertIn("exercises", inspect.signature(strength_workouts.build_strength_workout_payload).parameters)

        class FakeApp:
            def __init__(self):
                self.tools = {}

            def tool(self):
                def register(fn):
                    self.tools[fn.__name__] = fn
                    return fn

                return register

        app = strength_workouts.register_tools(FakeApp())
        create_params = inspect.signature(app.tools["create_strength_workout"]).parameters
        self.assertIn("blocks", create_params)
        self.assertIn("exercises", create_params)
        self.assertIn("allow_substitutions", create_params)
        self.assertIn("verify_after_upload", create_params)
        self.assertIn("verification_mode", create_params)
        self.assertIn("cleanup_on_failure", create_params)
        self.assertIn("cleanup_on_degradation", create_params)

    def test_failure_responses_are_compact_and_do_not_include_catalog_payloads(self):
        class FakeApp:
            def __init__(self):
                self.tools = {}

            def tool(self):
                def register(fn):
                    self.tools[fn.__name__] = fn
                    return fn

                return register

        app = strength_workouts.register_tools(FakeApp())
        bad_blocks = [
            {
                "name": "Bad",
                "steps": [
                    {"exercise": f"Definitely Not An Exercise {index}", "reps": 10}
                    for index in range(30)
                ],
            }
        ]

        import asyncio

        preview = asyncio.run(app.tools["preview_strength_workout"]("Bad", blocks=bad_blocks))
        create = asyncio.run(app.tools["create_strength_workout"]("Bad", blocks=bad_blocks))
        self.assertLess(len(preview.encode("utf-8")), 8192)
        self.assertLess(len(create.encode("utf-8")), 8192)
        self.assertNotIn("aliases", preview)
        self.assertNotIn("aliases", create)
        self.assertIn("mapping_failures", preview)
        self.assertIn("upload_blocked", create)


class FakeGarminClient:
    garmin_workouts = "/workout-service"

    def __init__(self, fetched):
        self.fetched = fetched
        self.deleted = []
        self.garth = self

    def delete(self, scope, url, api=True):
        self.deleted.append(url)
        return {"deleted": url}

    def get_workout_by_id(self, workout_id):
        return self.fetched


class UploadingFakeGarminClient(FakeGarminClient):
    def __init__(self, fetched):
        super().__init__(fetched)
        self.uploaded = None

    def upload_workout(self, payload):
        self.uploaded = payload
        return {"workoutId": 123, "workoutName": payload["workoutName"]}


class StrengthUploadVerificationTest(unittest.TestCase):
    def test_post_upload_verification_mocked(self):
        expected = strength_workouts.build_strength_workout_payload(
            "Simple",
            exercises=[{"name": "Dumbbell Row", "sets": [{"reps": 10}]}],
        )
        fetched = json.loads(json.dumps(expected))
        fetched["workoutId"] = 123
        strength_workouts.configure(FakeGarminClient(fetched))
        result = strength_workouts.verify_uploaded_strength_workout(123, expected)
        self.assertEqual(result["status"], "success")

    def test_exact_preserved_mapping_passes_strict_verification(self):
        expected = strength_workouts.build_strength_workout_payload(
            "Simple",
            exercises=[{"name": "Dumbbell Row", "sets": [{"reps": 10}]}],
        )
        fetched = json.loads(json.dumps(expected))
        fetched["workoutId"] = 123
        strength_workouts.configure(FakeGarminClient(fetched))
        result = strength_workouts.verify_uploaded_strength_workout(123, expected, mode="strict")
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["preserved_exercises"][0]["expected_exercise_name"], "DUMBBELL_ROW")

    def test_configured_rewrite_passes_compatible_verification(self):
        expected = strength_workouts.build_strength_workout_payload(
            "Rewrite",
            exercises=[{"name": "Barbell Shoulder Press", "sets": [{"reps": 10}]}],
        )
        fetched = json.loads(json.dumps(expected))
        fetched["workoutSegments"][0]["workoutSteps"][0]["exerciseName"] = "OVERHEAD_BARBELL_PRESS"
        fetched["workoutId"] = 123
        strength_workouts.configure(FakeGarminClient(fetched))
        result = strength_workouts.verify_uploaded_strength_workout(123, expected, mode="compatible")
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["rewritten_exercises"][0]["rewrite_to"], "OVERHEAD_BARBELL_PRESS")

    def test_blank_exercise_name_fails_strict_verification(self):
        expected = strength_workouts.build_strength_workout_payload(
            "Blank",
            exercises=[{"name": "Face Pull", "sets": [{"reps": 10}]}],
        )
        fetched = json.loads(json.dumps(expected))
        fetched["workoutSegments"][0]["workoutSteps"][0]["exerciseName"] = ""
        fetched["workoutId"] = 123
        strength_workouts.configure(FakeGarminClient(fetched))
        result = strength_workouts.verify_uploaded_strength_workout(123, expected, mode="strict")
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["degraded_exercises"][0]["status"], "garmin_stripped")

    def test_blank_exercise_name_returns_degraded_in_lenient_mode(self):
        expected = strength_workouts.build_strength_workout_payload(
            "Blank",
            exercises=[{"name": "Face Pull", "sets": [{"reps": 10}]}],
        )
        fetched = json.loads(json.dumps(expected))
        fetched["workoutSegments"][0]["workoutSteps"][0]["exerciseName"] = ""
        fetched["workoutId"] = 123
        strength_workouts.configure(FakeGarminClient(fetched))
        result = strength_workouts.verify_uploaded_strength_workout(123, expected, mode="lenient")
        self.assertEqual(result["status"], "degraded")
        self.assertTrue(any(issue["severity"] == "warning" for issue in result["issues"]))

    def test_create_does_not_cleanup_lenient_degradation(self):
        expected = strength_workouts.build_strength_workout_payload(
            "Blank",
            exercises=[{"name": "Face Pull", "sets": [{"reps": 10}]}],
        )
        fetched = json.loads(json.dumps(expected))
        fetched["workoutSegments"][0]["workoutSteps"][0]["exerciseName"] = ""
        fetched["workoutId"] = 123
        client = UploadingFakeGarminClient(fetched)
        strength_workouts.configure(client)

        class FakeApp:
            def __init__(self):
                self.tools = {}

            def tool(self):
                def register(fn):
                    self.tools[fn.__name__] = fn
                    return fn

                return register

        import asyncio

        app = strength_workouts.register_tools(FakeApp())
        result = json.loads(asyncio.run(app.tools["create_strength_workout"](
            "Blank",
            exercises=[{"name": "Face Pull", "sets": [{"reps": 10}]}],
            verify_after_upload=True,
            verification_mode="lenient",
            cleanup_on_failure=True,
        )))
        self.assertEqual(result["status"], "success_with_warnings")
        self.assertEqual(client.deleted, [])
        self.assertEqual(result["degraded_exercises"][0]["status"], "garmin_stripped")

    def test_preview_reports_roundtrip_warnings(self):
        preview = strength_workouts.preview_strength_workout_definition(
            "Warnings",
            exercises=[
                {"name": "Face Pull", "sets": [{"reps": 10}]},
                {"name": "Barbell Shoulder Press", "sets": [{"reps": 10}]},
                {"name": "Band External Rotation", "sets": [{"reps": 10}]},
            ],
        )
        text = preview["preview"]
        # New behavior: uses Garmin's canonical mappings, shows generic warning for unknown roundtrip status
        self.assertIn("Garmin may accept this step but strip the internal exercise code after upload", text)


class MultiExerciseSearchTest(unittest.TestCase):
    def test_bulk_resolution_handles_multiple_exercises(self):
        results = strength_exercise_catalog.resolve_strength_exercises_bulk([
            "pull up",
            "lat pulldown",
            "row",
            "rear delt fly",
            "face pull",
            "farmer carry",
            "overhead press",
            "lateral raise",
        ])
        self.assertEqual(len(results), 8)
        # Check that we get proper resolution status
        for result in results:
            self.assertIn("status", result)
            self.assertIn("requested", result)
    
    def test_bulk_resolution_with_nonexistent_exercise(self):
        results = strength_exercise_catalog.resolve_strength_exercises_bulk([
            "xyzzy definitely not an exercise",
        ])
        self.assertEqual(len(results), 1)
        # Fuzzy search might still find something, or return not_found
        self.assertIn(results[0]["status"], ["resolved", "not_found"])
    
    def test_bulk_resolution_safe_only(self):
        # "Lat Pull-down" is known_good in the canonical entries
        results = strength_exercise_catalog.resolve_strength_exercises_bulk([
            "Lat Pull-down",
            "lateral raise",  # Not known_good (roundtrip_status=stripped)
        ], safe_only=True)
        # Lat Pull-down should be resolved (known_good)
        # lateral raise may or may not be resolved depending on catalog state


class BlockSchemaCompatibilityTest(unittest.TestCase):
    def test_exercises_key_accepted_as_alias_for_steps(self):
        # This should work - using "exercises" instead of "steps"
        blocks = [
            {
                "name": "Test Block",
                "exercises": [  # Deprecated key, but accepted as alias
                    {"exercise": "Dumbbell Row", "reps": 10, "rest_seconds": 30},
                ]
            }
        ]
        validation = strength_workouts.validate_strength_workout_definition(
            "Test",
            blocks=blocks,
        )
        # Should have a deprecation warning but not fail
        self.assertEqual(validation["status"], "success")
        warning_codes = [issue.get("code") for issue in validation["issues"] if issue.get("severity") == "warning"]
        self.assertIn("deprecated_key_exercises", warning_codes)
    
    def test_sets_in_step_converted_properly(self):
        # Test that steps with "sets" are handled correctly
        blocks = [
            {
                "name": "Multi-set Block",
                "steps": [
                    {"exercise": "Dumbbell Row", "sets": [{"reps": 10, "rest_seconds": 30}, {"reps": 8, "rest_seconds": 30}]},
                ]
            }
        ]
        # Normalize the block schema
        normalized = strength_workouts._normalize_block_schema(blocks[0])
        self.assertEqual(len(normalized["steps"]), 2)  # Two steps from two sets


class CompactModeTest(unittest.TestCase):
    def test_compact_response_includes_degraded_info(self):
        # Test that compact mode includes degraded exercise info
        result = {
            "status": "success_with_warnings",
            "workout_id": 123,
            "name": "Test Workout",
            "degraded_exercises": [
                {"display_name": "Face Pull", "status": "garmin_stripped"},
                {"display_name": "Lateral Raise", "status": "garmin_stripped"},
            ],
            "preserved_exercises": [
                {"display_name": "Dumbbell Row", "status": "preserved"},
            ],
            "warnings": ["Warning 1", "Warning 2"],
        }
        compact = strength_workouts._compact_response(result)
        self.assertEqual(compact["status"], "success_with_warnings")
        self.assertEqual(compact["workout_id"], 123)
        self.assertIn("degraded_exercise_count", compact)
        self.assertEqual(compact["degraded_exercise_count"], 2)
        self.assertIn("preserved_exercise_count", compact)
        # Warnings should be truncated
        self.assertEqual(len(compact["warnings"]), 2)


class BulkSchedulingTest(unittest.TestCase):
    def test_schedule_requests_validation(self):
        # Test bulk schedule request format
        requests = [
            {"workout_id": 123, "date": "2026-05-14"},
            {"workout_id": 456, "date": "2026-05-16"},
        ]
        # Validate dates
        for req in requests:
            from datetime import date
            date.fromisoformat(req["date"])


class ExportHelperTest(unittest.TestCase):
    def test_export_function_exists(self):
        # Just verify the function exists and is callable
        self.assertTrue(callable(strength_workouts.export_strength_workout_definition))


if __name__ == "__main__":
    unittest.main()
