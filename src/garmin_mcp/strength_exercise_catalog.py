"""Persisted Garmin strength exercise catalog for MCP tools."""

import json
import logging
import os
import re
import tempfile
import time
from urllib.request import Request, urlopen
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any


logger = logging.getLogger("garmin_mcp_strength_exercise_catalog")
DATA_DIR = Path(os.environ.get("MCP_DATA_DIR", "/app/data"))
CATALOG_PATH = Path(os.environ.get("GARMIN_STRENGTH_EXERCISES_FILE", DATA_DIR / "strength_exercises.json"))
GLOBAL_CATALOG_PATH = Path(os.environ.get("GARMIN_GLOBAL_CATALOG_FILE", "/app/global_data/garmin_global_exercises.json"))
SEED_PATH = Path(os.environ.get("GARMIN_STRENGTH_EXERCISES_SEED", "/app/strength_exercises_seed.json"))
ROUNDTRIP_STATUSES = {"unknown", "preserved", "rewritten", "stripped", "rejected"}
GARMIN_EXERCISES_URL = "https://connect.garmin.com/web-data/exercises/Exercises.json"
GARMIN_EXERCISE_TRANSLATIONS_URL = "https://connect.garmin.com/web-translations/exercise_types/exercise_types.properties"


def normalize_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def tokenize(value: str) -> set[str]:
    return {token for token in normalize_name(value).split() if token}


def garmin_key_from_display_name(value: str) -> str:
    cleaned = value.replace("\N{REGISTERED SIGN}", "").replace("'", "")
    cleaned = cleaned.replace("30-degree", "30 degree").replace("45-degree", "45 degree").replace("90-degree", "90 degree")
    return re.sub(r"[^A-Za-z0-9]+", "_", cleaned).strip("_").upper()


def display_name_from_garmin_key(value: str) -> str:
    words = []
    for token in str(value or "").strip("_").split("_"):
        if not token:
            continue
        if token.isdigit():
            words.append(token)
        elif token in {"3D", "90"}:
            words.append(token)
        else:
            words.append(token[:1].upper() + token[1:].lower())
    return " ".join(words)


def category_from_display_name(value: str) -> str | None:
    text = normalize_name(value)
    mapping = [
        ("curl", "CURL"),
        ("biceps", "CURL"),
        ("bench press", "BENCH_PRESS"),
        ("floor press", "BENCH_PRESS"),
        ("chest press", "BENCH_PRESS"),
        ("fly", "FLYE"),
        ("row", "ROW"),
        ("pull down", "PULL_UP"),
        ("lat pull", "PULL_UP"),
        ("face pull", "SHOULDER_STABILITY"),
        ("shoulder", "SHOULDER_PRESS"),
        ("front raise", "SHOULDER_STABILITY"),
        ("lateral raise", "SHOULDER_STABILITY"),
        ("y raise", "SHOULDER_STABILITY"),
        ("dead bug", "HIP_STABILITY"),
        ("russian twist", "CORE"),
        ("abs", "CORE"),
        ("plank", "CORE"),
        ("squat", "SQUAT"),
        ("lunge", "LUNGE"),
        ("deadlift", "DEADLIFT"),
        ("push up", "PUSH_UP"),
        ("push-up", "PUSH_UP"),
        ("triceps", "TRICEPS_EXTENSION"),
        ("calf", "CALF_RAISE"),
    ]
    for needle, category in mapping:
        if needle in text:
            return category
    return None


def make_entry(display_name: str, exercise_name: str | None = None, category: str | None = None) -> dict[str, Any]:
    display_name = display_name.strip()
    exercise_name = exercise_name or garmin_key_from_display_name(display_name)
    category = category or category_from_display_name(display_name)
    return {
        "display_name": display_name,
        "exercise_name": exercise_name,
        "category": category,
        "aliases": [],
        "canonical": normalize_name(display_name).replace(" ", "_"),
        "local_valid": bool(exercise_name and category),
        "garmin_upload_accepts": None,
        "garmin_roundtrip_preserves": False,
        "roundtrip_status": "unknown",
        "garmin_actual_exercise_name": None,
        "garmin_rewrite_to": None,
        "last_roundtrip_verified_at": None,
        "safe_for_exact_tracking": False,
        "known_good": False,
        "source": "local",
        "updated_at": int(time.time()),
    }


CANONICAL_STRENGTH_ENTRIES: list[dict[str, Any]] = [
    {
        "display_name": "Dumbbell Row",
        "exercise_name": "DUMBBELL_ROW",
        "category": "ROW",
        "canonical": "dumbbell_row",
        "aliases": ["db row", "one arm dumbbell row", "single arm dumbbell row"],
        "known_good": True,
        "roundtrip_status": "preserved",
        "safe_for_exact_tracking": True,
        "garmin_roundtrip_preserves": True,
        "garmin_upload_accepts": True,
    },
    {
        "display_name": "Chest Supported Dumbbell Row",
        "exercise_name": "DUMBBELL_ROW",
        "category": "ROW",
        "canonical": "chest_supported_dumbbell_row",
        "aliases": ["chest supported row", "incline dumbbell row", "supported dumbbell row"],
        "known_good": True,
        "roundtrip_status": "preserved",
        "safe_for_exact_tracking": True,
        "garmin_roundtrip_preserves": True,
        "garmin_upload_accepts": True,
        "notes": "Garmin may display this as Dumbbell Row; use the workout step description for the chest-supported variant.",
    },
    {
        "display_name": "Close-grip Lat Pull-down",
        "exercise_name": "CLOSE_GRIP_LAT_PULL_DOWN",
        "category": "PULL_UP",
        "canonical": "close_grip_lat_pull_down",
        "aliases": ["close grip lat pulldown", "close grip pulldown", "narrow grip lat pulldown"],
        "known_good": False,
        "notes": "If Garmin renders this generically, fall back to Lat Pull-down.",
    },
    {
        "display_name": "Lat Pull-down",
        "exercise_name": "LAT_PULLDOWN",
        "category": "PULL_UP",
        "canonical": "lat_pull_down",
        "aliases": ["lat pulldown", "pull down", "pulldown"],
        "known_good": True,
        "roundtrip_status": "preserved",
        "safe_for_exact_tracking": True,
        "garmin_roundtrip_preserves": True,
        "garmin_upload_accepts": True,
    },
    {
        "display_name": "Dumbbell Lateral Raise",
        "exercise_name": "DUMBBELL_LATERAL_RAISE",
        "category": "SHOULDER_STABILITY",
        "canonical": "lateral_raise",
        "aliases": ["lateral raise", "side lateral raise", "db lateral raise", "dumbbell side raise"],
        "known_good": False,
        "roundtrip_status": "stripped",
        "garmin_actual_exercise_name": "",
        "safe_for_exact_tracking": False,
        "garmin_roundtrip_preserves": False,
        "garmin_upload_accepts": True,
        "notes": "Garmin accepts upload but has been observed fetching this back with blank exerciseName.",
    },
    {
        "display_name": "Lateral Raise",
        "exercise_name": "DUMBBELL_LATERAL_RAISE",
        "category": "SHOULDER_STABILITY",
        "canonical": "lateral_raise",
        "aliases": ["side raise", "side lateral"],
        "known_good": False,
        "roundtrip_status": "stripped",
        "garmin_actual_exercise_name": "",
        "safe_for_exact_tracking": False,
        "garmin_roundtrip_preserves": False,
        "garmin_upload_accepts": True,
        "notes": "Garmin accepts upload but has been observed fetching DUMBBELL_LATERAL_RAISE back with blank exerciseName.",
    },
    {
        "display_name": "Incline Reverse Fly",
        "exercise_name": "INCLINE_REVERSE_FLY",
        "category": "FLYE",
        "canonical": "rear_delt_fly",
        "aliases": ["rear delt fly", "incline rear delt fly", "chest supported reverse fly", "reverse fly"],
        "known_good": False,
        "notes": "Verify on your account; use Seated Rear Lateral Raise if Garmin displays only the category.",
    },
    {
        "display_name": "Seated Rear Lateral Raise",
        "exercise_name": "SEATED_REAR_LATERAL_RAISE",
        "category": "SHOULDER_STABILITY",
        "canonical": "rear_delt_fly",
        "aliases": ["rear lateral raise", "seated rear delt raise", "seated rear delt fly"],
        "known_good": False,
        "roundtrip_status": "stripped",
        "garmin_actual_exercise_name": "",
        "garmin_upload_accepts": True,
        "notes": "Garmin accepts upload but has been observed fetching this back with blank exerciseName.",
    },
    {
        "display_name": "Bent-over Lateral Raise",
        "exercise_name": "BENT_OVER_LATERAL_RAISE",
        "category": "SHOULDER_STABILITY",
        "canonical": "rear_delt_fly",
        "aliases": ["bent over rear delt fly", "bentover lateral raise"],
        "known_good": False,
    },
    {
        "display_name": "Kneeling Rear Fly",
        "exercise_name": "KNEELING_REAR_FLY",
        "category": "FLYE",
        "canonical": "rear_delt_fly",
        "aliases": ["kneeling rear delt fly"],
        "known_good": False,
    },
    {
        "display_name": "Face Pull",
        "exercise_name": "FACE_PULL",
        "category": "SHOULDER_STABILITY",
        "canonical": "face_pull",
        "aliases": ["cable face pull", "rope face pull"],
        "known_good": False,
        "roundtrip_status": "stripped",
        "garmin_actual_exercise_name": "",
        "garmin_upload_accepts": True,
        "notes": "Garmin accepts upload but has been observed fetching this back with blank exerciseName.",
    },
    {
        "display_name": "Face Pull with External Rotation",
        "exercise_name": "FACE_PULL",
        "category": "SHOULDER_STABILITY",
        "canonical": "face_pull_external_rotation",
        "aliases": ["face pull external rotation", "face pull to external rotation"],
        "known_good": False,
        "roundtrip_status": "stripped",
        "garmin_actual_exercise_name": "",
        "garmin_upload_accepts": True,
        "notes": "Garmin may preserve the description, but FACE_PULL has been observed fetching back with blank exerciseName.",
    },
    {
        "display_name": "Band External Rotation",
        "exercise_name": "BAND_EXTERNAL_ROTATION",
        "category": "SHOULDER_STABILITY",
        "canonical": "external_rotation",
        "aliases": ["banded external rotation", "rotator cuff external rotation", "shoulder external rotation"],
        "known_good": False,
        "notes": "Unverified Garmin display; validate after upload.",
    },
    {
        "display_name": "Banded External Rotation",
        "exercise_name": "BAND_EXTERNAL_ROTATION",
        "category": "SHOULDER_STABILITY",
        "canonical": "external_rotation",
        "aliases": ["band external rotation"],
        "known_good": False,
    },
    {
        "display_name": "Cable External Rotation",
        "exercise_name": "CABLE_EXTERNAL_ROTATION",
        "category": "SHOULDER_STABILITY",
        "canonical": "external_rotation",
        "aliases": ["cable shoulder external rotation"],
        "known_good": False,
    },
    {
        "display_name": "Lying External Rotation",
        "exercise_name": "LYING_EXTERNAL_ROTATION",
        "category": "SHOULDER_STABILITY",
        "canonical": "external_rotation",
        "aliases": ["side lying external rotation", "sidelying external rotation"],
        "known_good": False,
    },
    {
        "display_name": "Farmer's Carry",
        "exercise_name": "FARMERS_CARRY",
        "category": "CARRY",
        "canonical": "farmers_carry",
        "aliases": ["farmers carry", "farmer carry", "loaded carry"],
        "known_good": False,
        "notes": "Garmin support for carry categories varies by account/app version.",
    },
    {
        "display_name": "Plank",
        "exercise_name": "PLANK",
        "category": "CORE",
        "canonical": "plank",
        "aliases": ["front plank"],
        "known_good": False,
        "roundtrip_status": "stripped",
        "garmin_actual_exercise_name": "",
        "garmin_upload_accepts": True,
        "notes": "Garmin accepts upload but may fetch PLANK back blank or omit it from exercise-step lists.",
    },
    {
        "display_name": "Barbell Shoulder Press",
        "exercise_name": "BARBELL_SHOULDER_PRESS",
        "category": "SHOULDER_PRESS",
        "canonical": "barbell_shoulder_press",
        "aliases": ["shoulder press with barbell", "standing barbell shoulder press"],
        "known_good": False,
        "roundtrip_status": "rewritten",
        "garmin_rewrite_to": "OVERHEAD_BARBELL_PRESS",
        "garmin_actual_exercise_name": "OVERHEAD_BARBELL_PRESS",
        "garmin_upload_accepts": True,
        "notes": "Garmin has been observed rewriting this to OVERHEAD_BARBELL_PRESS after upload.",
    },
]


def _default_entries() -> list[dict[str, Any]]:
    names = [
        "Dumbbell Front Raise",
        "Seated Lateral Raise",
        "X Abs",
        "Face Pull",
        "Seated Cable Row",
        "Straight-arm Pull-down",
        "Incline Dumbbell Fly",
        "Weighted Russian Twist on Swiss Ball",
        "Seated Barbell Shoulder Press",
        "Seated Alternating Dumbbell Biceps Curl",
        "Lat Pull-down",
        "Weighted Dead Bug",
        "Smith Machine Bench Press",
        "Dead Bug",
        "Russian Twist",
        "Arm Circles",
        "Dumbbell Floor Press",
        "Floor Y Raise",
        "Abs Jabs",
        "30-degree Lat Pull-down",
        "Barbell Bench Press",
        "Barbell Biceps Curl",
        "Barbell Squat Clean",
        "Barbell Lateral Step-up",
        "Dumbbell Bulgarian Split Squat",
        "Dumbbell Hammer Curl",
        "Dumbbell Lateral Raise",
        "Dumbbell Shoulder Press",
        "Dumbbell Squat",
        "Lat Pull-down",
        "Overhead Dumbbell Triceps Extension",
        "Push-up",
        "Romanian Deadlift",
        "Cable Woodchop",
    ]
    overrides = {
        "Seated Lateral Raise": ("DUMBBELL_LATERAL_RAISE", "SHOULDER_STABILITY"),
        "X Abs": ("X_ABS", "CORE"),
        "Face Pull": ("FACE_PULL", "SHOULDER_STABILITY"),
        "Seated Cable Row": ("SEATED_CABLE_ROW", "ROW"),
        "Straight-arm Pull-down": ("STRAIGHT_ARM_PULL_DOWN", "PULL_UP"),
        "Incline Dumbbell Fly": ("INCLINE_DUMBBELL_FLY", "FLYE"),
        "Weighted Russian Twist on Swiss Ball": ("WEIGHTED_RUSSIAN_TWIST_ON_SWISS_BALL", "CORE"),
        "Lat Pull-down": ("LAT_PULLDOWN", "PULL_UP"),
        "Weighted Dead Bug": ("WEIGHTED_DEAD_BUG", "HIP_STABILITY"),
        "Smith Machine Bench Press": ("SMITH_MACHINE_BENCH_PRESS", "BENCH_PRESS"),
        "Floor Y Raise": ("FLOOR_Y_RAISE", "SHOULDER_STABILITY"),
        "30-degree Lat Pull-down": ("30_DEGREE_LAT_PULL_DOWN", "PULL_UP"),
    }
    entries = []
    seen = set()
    for name in names:
        key = normalize_name(name)
        if key in seen:
            continue
        seen.add(key)
        exercise_name, category = overrides.get(name, (None, None))
        entries.append(make_entry(name, exercise_name, category))
    _merge_canonical_entries(entries)
    return entries


def _merge_canonical_entries(entries: list[dict[str, Any]]) -> None:
    by_name = {normalize_name(entry.get("display_name", "")): entry for entry in entries}
    for canonical in CANONICAL_STRENGTH_ENTRIES:
        key = normalize_name(canonical["display_name"])
        existing = by_name.get(key)
        if existing is None:
            entry = dict(canonical)
            entry.setdefault("aliases", [])
            entry.setdefault("source", "curated")
            entry.setdefault("updated_at", int(time.time()))
            entries.append(entry)
            by_name[key] = entry
        else:
            existing.update({k: v for k, v in canonical.items() if v not in (None, "", [])})
            existing.setdefault("source", "curated")


def load_catalog() -> dict[str, Any]:
    ensure_catalog()
    with CATALOG_PATH.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict) or not isinstance(data.get("exercises"), list):
        raise ValueError(f"Invalid exercise catalog at {CATALOG_PATH}")
    changed = _normalize_loaded_catalog(data)
    if changed:
        save_catalog(data)
    return data


def _normalize_loaded_catalog(data: dict[str, Any]) -> bool:
    changed = False
    for entry in data.get("exercises", []):
        if "exercise_name" not in entry and "garmin_exercise_name" in entry:
            entry["exercise_name"] = entry.get("garmin_exercise_name")
            changed = True
        if "canonical" not in entry:
            entry["canonical"] = normalize_name(entry.get("display_name", "")).replace(" ", "_")
            changed = True
        if "known_good" not in entry:
            entry["known_good"] = False
            changed = True
        if "aliases" not in entry or entry["aliases"] is None:
            entry["aliases"] = []
            changed = True
        local_valid = bool(entry.get("exercise_name") and entry.get("category"))
        if entry.get("local_valid") != local_valid:
            entry["local_valid"] = local_valid
            changed = True
        if entry.get("roundtrip_status") not in ROUNDTRIP_STATUSES:
            entry["roundtrip_status"] = "preserved" if entry.get("known_good") else "unknown"
            changed = True
        status = entry.get("roundtrip_status")
        expected_preserved = status == "preserved"
        if entry.get("garmin_roundtrip_preserves") != expected_preserved:
            entry["garmin_roundtrip_preserves"] = expected_preserved
            changed = True
        expected_safe = status == "preserved"
        if entry.get("safe_for_exact_tracking") != expected_safe:
            entry["safe_for_exact_tracking"] = expected_safe
            changed = True
        if entry.get("known_good") != expected_safe:
            entry["known_good"] = expected_safe
            changed = True
        for key in ("garmin_upload_accepts", "garmin_actual_exercise_name", "garmin_rewrite_to", "last_roundtrip_verified_at"):
            if key not in entry:
                entry[key] = None
                changed = True
    before = json.dumps(data.get("exercises", []), sort_keys=True)
    _merge_canonical_entries(data["exercises"])
    after = json.dumps(data.get("exercises", []), sort_keys=True)
    return changed or before != after


def save_catalog(data: dict[str, Any]) -> None:
    CATALOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = int(time.time())
    encoded = json.dumps(data, indent=2, sort_keys=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=str(CATALOG_PATH.parent), delete=False) as handle:
        handle.write(encoded)
        handle.write("\n")
        temp_name = handle.name
    Path(temp_name).replace(CATALOG_PATH)


def _fetch_text(url: str, timeout_seconds: int = 20) -> str:
    request = Request(url, headers={"User-Agent": "garmin-mcp/strength-exercise-catalog"})
    with urlopen(request, timeout=timeout_seconds) as response:
        return response.read().decode("utf-8")


def _parse_properties(text: str) -> dict[str, str]:
    translations: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key and value:
            translations[key] = value
    return translations


def _garmin_translation_key(category: str, exercise_name: str) -> str:
    return f"{category}_{exercise_name}"


def _walk_garmin_exercise_catalog(data: dict[str, Any], translations: dict[str, str] | None = None) -> list[dict[str, Any]]:
    translations = translations or {}
    categories = data.get("categories") or {}
    imported = []
    seen: set[tuple[str, str]] = set()
    for category, category_data in categories.items():
        if not isinstance(category_data, dict):
            continue
        exercises = category_data.get("exercises") or {}
        if not isinstance(exercises, dict):
            continue
        for exercise_name, exercise_data in exercises.items():
            if not isinstance(exercise_data, dict):
                exercise_data = {}
            key = (str(category), str(exercise_name))
            if key in seen:
                continue
            seen.add(key)
            translation_key = _garmin_translation_key(str(category), str(exercise_name))
            display_name = translations.get(translation_key) or display_name_from_garmin_key(str(exercise_name))
            imported.append(
                {
                    "display_name": display_name,
                    "exercise_name": str(exercise_name),
                    "category": str(category),
                    "aliases": [],
                    "canonical": normalize_name(display_name).replace(" ", "_"),
                    "local_valid": True,
                    "garmin_upload_accepts": None,
                    "garmin_roundtrip_preserves": False,
                    "roundtrip_status": "unknown",
                    "garmin_actual_exercise_name": None,
                    "garmin_rewrite_to": None,
                    "last_roundtrip_verified_at": None,
                    "safe_for_exact_tracking": False,
                    "known_good": False,
                    "source": "garmin_public_catalog",
                    "primary_muscles": exercise_data.get("primaryMuscles") or category_data.get("primaryMuscles") or [],
                    "secondary_muscles": exercise_data.get("secondaryMuscles") or category_data.get("secondaryMuscles") or [],
                    "counterpart": exercise_data.get("counterpart"),
                    "is_body_weight": exercise_data.get("isBodyWeight"),
                    "garmin_translation_key": translation_key,
                    "updated_at": int(time.time()),
                }
            )
    return sorted(imported, key=lambda item: (normalize_name(item["display_name"]), item["category"], item["exercise_name"]))


def _merge_imported_exercise(existing: dict[str, Any], incoming: dict[str, Any], update_existing: bool) -> bool:
    changed = False
    fields_always = (
        "local_valid",
        "primary_muscles",
        "secondary_muscles",
        "counterpart",
        "is_body_weight",
        "garmin_translation_key",
    )
    for key in fields_always:
        if incoming.get(key) not in (None, [], "") and existing.get(key) != incoming.get(key):
            existing[key] = incoming.get(key)
            changed = True
    for key in ("display_name", "exercise_name", "category", "canonical"):
        if not existing.get(key) and incoming.get(key):
            existing[key] = incoming[key]
            changed = True
    if existing.get("source") in (None, "", "local"):
        existing["source"] = incoming["source"]
        changed = True
    if update_existing:
        for key in ("display_name", "exercise_name", "category", "canonical"):
            if incoming.get(key) and existing.get(key) != incoming.get(key):
                existing[key] = incoming[key]
                changed = True
    for key, default in (
        ("roundtrip_status", "unknown"),
        ("garmin_upload_accepts", None),
        ("garmin_roundtrip_preserves", False),
        ("garmin_actual_exercise_name", None),
        ("garmin_rewrite_to", None),
        ("last_roundtrip_verified_at", None),
        ("safe_for_exact_tracking", False),
    ):
        if key not in existing:
            existing[key] = default
            changed = True
    safe = existing.get("roundtrip_status") == "preserved"
    if existing.get("known_good") != safe:
        existing["known_good"] = safe
        changed = True
    if changed:
        existing["updated_at"] = int(time.time())
    return changed


def sync_garmin_public_exercise_catalog(
    update_existing: bool = False,
    exercises_json_text: str | None = None,
    translations_text: str | None = None,
) -> dict[str, Any]:
    if exercises_json_text is None:
        exercises_json_text = _fetch_text(GARMIN_EXERCISES_URL)
    if translations_text is None:
        translations_text = _fetch_text(GARMIN_EXERCISE_TRANSLATIONS_URL)
    exercises_data = json.loads(exercises_json_text)
    translations = _parse_properties(translations_text)
    imported = _walk_garmin_exercise_catalog(exercises_data, translations)

    data = load_catalog()
    by_pair = {
        (str(entry.get("category")), str(entry.get("exercise_name"))): entry
        for entry in data["exercises"]
        if entry.get("category") and entry.get("exercise_name")
    }
    by_name = {normalize_name(str(entry.get("display_name", ""))): entry for entry in data["exercises"]}
    incomplete_by_exercise = {
        str(entry.get("exercise_name")): entry
        for entry in data["exercises"]
        if entry.get("exercise_name") and not entry.get("category")
    }
    created = 0
    updated = 0
    for incoming in imported:
        existing = by_pair.get((incoming["category"], incoming["exercise_name"]))
        if existing is None:
            existing = by_name.get(normalize_name(incoming["display_name"]))
        if existing is None:
            existing = incomplete_by_exercise.get(incoming["exercise_name"])
        if existing is None:
            data["exercises"].append(incoming)
            by_pair[(incoming["category"], incoming["exercise_name"])] = incoming
            by_name[normalize_name(incoming["display_name"])] = incoming
            created += 1
        elif _merge_imported_exercise(existing, incoming, update_existing=update_existing):
            updated += 1
    complete_by_exercise = {
        str(entry.get("exercise_name")): entry
        for entry in data["exercises"]
        if entry.get("exercise_name") and entry.get("category")
    }
    for entry in data["exercises"]:
        if not entry.get("exercise_name") or entry.get("category"):
            continue
        source = complete_by_exercise.get(str(entry.get("exercise_name")))
        if source is None:
            continue
        for key in ("category", "local_valid", "primary_muscles", "secondary_muscles", "garmin_translation_key"):
            if not entry.get(key) and source.get(key):
                entry[key] = source[key]
                updated += 1
        entry["updated_at"] = int(time.time())
    data["exercises"] = sorted(data["exercises"], key=lambda item: normalize_name(item.get("display_name", "")))
    save_catalog(data)
    return {
        "created": created,
        "updated": updated,
        "imported_count": len(imported),
        "total": len(data["exercises"]),
        "source_urls": [GARMIN_EXERCISES_URL, GARMIN_EXERCISE_TRANSLATIONS_URL],
    }


def ensure_catalog() -> None:
    if CATALOG_PATH.exists():
        return
    if SEED_PATH.exists():
        with SEED_PATH.open("r", encoding="utf-8") as handle:
            seed = json.load(handle)
    else:
        seed = {"version": 1, "exercises": _default_entries()}
    seed.setdefault("version", 1)
    seed.setdefault("created_at", int(time.time()))
    save_catalog(seed)


def find_entry(name: str) -> dict[str, Any] | None:
    needle = normalize_name(name)
    data = load_catalog()
    for entry in data["exercises"]:
        if normalize_name(str(entry.get("display_name", ""))) == needle:
            return entry
    for entry in data["exercises"]:
        if any(normalize_name(str(alias)) == needle for alias in entry.get("aliases") or []):
            return entry
    for entry in data["exercises"]:
        if normalize_name(str(entry.get("exercise_name", ""))) == needle:
            return entry
    return None


def _entry_search_text(entry: dict[str, Any]) -> str:
    values = [
        entry.get("display_name"),
        entry.get("exercise_name"),
        entry.get("garmin_exercise_name"),
        entry.get("category"),
        entry.get("canonical"),
        entry.get("roundtrip_status"),
        entry.get("garmin_actual_exercise_name"),
        entry.get("garmin_rewrite_to"),
        entry.get("equipment"),
        *(entry.get("aliases") or []),
        *(entry.get("muscle_groups") or []),
    ]
    return " ".join(str(value) for value in values if value)


def _search_score(entry: dict[str, Any], query: str, fuzzy: bool = True) -> tuple[float, str]:
    needle = normalize_name(query)
    if not needle:
        return 0.1, "all"
    display = normalize_name(entry.get("display_name", ""))
    exercise = normalize_name(entry.get("exercise_name", ""))
    aliases = [normalize_name(str(alias)) for alias in entry.get("aliases") or []]
    haystack = normalize_name(_entry_search_text(entry))
    
    # Exact matches always win
    if needle == display:
        return 1.0, "exact display_name"
    if needle == exercise:
        return 0.98, "exact Garmin exerciseName"
    if needle in aliases:
        return 0.95, "exact alias"
    
    # Multi-term query: split into individual terms and score each
    query_tokens = tokenize(query)
    
    # For multi-term queries like "pull up lat pulldown row", score each token
    if len(query_tokens) > 1:
        best_score = 0.0
        best_reason = "no match"
        
        # Check if all tokens are present in display name
        if query_tokens and all(token in display for token in query_tokens):
            return 0.85, "all tokens in display_name"
        
        # Check if exercise name contains all tokens
        if query_tokens and all(token in exercise for token in query_tokens):
            return 0.83, "all tokens in exercise_name"
        
        # Check individual tokens against display, exercise, aliases
        for token in query_tokens:
            token_score = 0.0
            token_reason = "no match"
            
            if token == display:
                token_score, token_reason = 0.9, "token exact display"
            elif token == exercise:
                token_score, token_reason = 0.88, "token exact exercise"
            elif token in aliases:
                token_score, token_reason = 0.86, "token in aliases"
            elif token in display:
                token_score, token_reason = 0.75, "token substring display"
            elif token in exercise:
                token_score, token_reason = 0.73, "token substring exercise"
            
            if token_score > best_score:
                best_score = token_score
                best_reason = token_reason
        
        # Partial token overlap
        if query_tokens:
            text_tokens = tokenize(haystack)
            overlap = len(query_tokens & text_tokens) / len(query_tokens)
            if overlap >= 0.8:
                return 0.65 + (overlap * 0.15), "high token overlap"
            if overlap >= 0.5:
                return 0.50 + (overlap * 0.15), "partial token overlap"
        
        if best_score > 0:
            return best_score, best_reason
    
    # Single-term query logic (original behavior)
    if needle and needle in haystack:
        return 0.82, "substring"
    if query_tokens:
        text_tokens = tokenize(haystack)
        if query_tokens:
            overlap = len(query_tokens & text_tokens) / len(query_tokens)
            if overlap >= 1:
                return 0.78, "token match"
            if overlap >= 0.5:
                return 0.55 + (overlap * 0.2), "partial token match"
    if fuzzy:
        candidates = [display, exercise, *aliases]
        ratio = max((SequenceMatcher(None, needle, candidate).ratio() for candidate in candidates if candidate), default=0.0)
        if ratio >= 0.72:
            return 0.4 + (ratio * 0.25), "fuzzy match"
    return 0.0, "no match"


def search_catalog(
    query: str = "",
    limit: int = 20,
    category: str | None = None,
    muscle_group: str | None = None,
    equipment: str | None = None,
    fuzzy: bool = True,
) -> list[dict[str, Any]]:
    data = load_catalog()
    limit = min(max(int(limit), 1), 1000)
    results = []
    category_filter = normalize_name(category or "")
    muscle_filter = normalize_name(muscle_group or "")
    equipment_filter = normalize_name(equipment or "")
    for entry in data["exercises"]:
        if category_filter and normalize_name(str(entry.get("category", ""))) != category_filter:
            continue
        if muscle_filter and muscle_filter not in normalize_name(" ".join(str(x) for x in entry.get("muscle_groups") or [])):
            continue
        if equipment_filter and equipment_filter not in normalize_name(str(entry.get("equipment", ""))):
            continue
        score, reason = _search_score(entry, query, fuzzy)
        if query and score <= 0:
            continue
        warning = roundtrip_warning(entry)
        if not entry.get("category") or not entry.get("exercise_name"):
            warning = "Incomplete Garmin mapping; uploads should be blocked until exercise_name and category are set."
        results.append(
            {
                "display_name": entry.get("display_name"),
                "garmin_exercise_name": entry.get("exercise_name"),
                "exercise_name": entry.get("exercise_name"),
                "category": entry.get("category"),
                "aliases": entry.get("aliases") or [],
                "canonical": entry.get("canonical"),
                "confidence": round(score, 3),
                "match_reason": reason,
                "known_good": bool(entry.get("known_good")),
                "local_valid": bool(entry.get("local_valid")),
                "garmin_upload_accepts": entry.get("garmin_upload_accepts"),
                "garmin_roundtrip_preserves": bool(entry.get("garmin_roundtrip_preserves")),
                "roundtrip_status": entry.get("roundtrip_status"),
                "garmin_actual_exercise_name": entry.get("garmin_actual_exercise_name"),
                "garmin_rewrite_to": entry.get("garmin_rewrite_to"),
                "safe_for_exact_tracking": bool(entry.get("safe_for_exact_tracking")),
                "last_roundtrip_verified_at": entry.get("last_roundtrip_verified_at"),
                "warning": warning,
                "notes": entry.get("notes"),
            }
        )
    return sorted(results, key=lambda item: (-item["confidence"], normalize_name(item.get("display_name") or "")))[:limit]


def resolve_exercise(name: str, safe_only: bool = False) -> dict[str, Any] | None:
    """Resolve an exercise by name.
    
    Args:
        name: Exercise name to resolve
        safe_only: If True, only return known_good exercises (roundtrip verified)
    """
    entry = find_entry(name)
    if not entry:
        matches = search_catalog(name, limit=1, fuzzy=True)
        if matches and matches[0]["confidence"] >= 0.72:
            entry = find_entry(matches[0]["display_name"])
    
    if safe_only and entry:
        # Only return if known_good (roundtrip verified)
        if not entry.get("known_good"):
            # Try to find a known_good alternative
            normalized = normalize_name(name)
            data = load_catalog()
            for e in data["exercises"]:
                if e.get("known_good") and (normalize_name(e.get("display_name", "")) in normalized or normalized in normalize_name(e.get("display_name", ""))):
                    entry = e
                    break
            else:
                return None
    
    if not entry:
        return None
    return {
        "display_name": entry.get("display_name"),
        "exercise_name": entry.get("exercise_name"),
        "category": entry.get("category"),
        "canonical": entry.get("canonical"),
        "known_good": bool(entry.get("known_good")),
        "local_valid": bool(entry.get("local_valid")),
        "garmin_upload_accepts": entry.get("garmin_upload_accepts"),
        "garmin_roundtrip_preserves": bool(entry.get("garmin_roundtrip_preserves")),
        "roundtrip_status": entry.get("roundtrip_status"),
        "garmin_actual_exercise_name": entry.get("garmin_actual_exercise_name"),
        "garmin_rewrite_to": entry.get("garmin_rewrite_to"),
        "safe_for_exact_tracking": bool(entry.get("safe_for_exact_tracking")),
        "last_roundtrip_verified_at": entry.get("last_roundtrip_verified_at"),
        "aliases": entry.get("aliases") or [],
        "notes": entry.get("notes"),
    }


def resolve_strength_exercises_bulk(exercises: list[str], safe_only: bool = False) -> list[dict[str, Any]]:
    """Resolve multiple exercises by name - handles multi-exercise queries.
    
    Splits long queries into individual exercises and resolves each one.
    Returns results for each requested exercise.
    """
    results = []
    for name in exercises:
        if not name or not str(name).strip():
            results.append({
                "requested": name,
                "status": "empty",
                "error": "Empty exercise name",
            })
            continue
        
        # Try exact match first, then fuzzy search
        resolved = resolve_exercise(name, safe_only=safe_only)
        
        if resolved and resolved.get("exercise_name") and resolved.get("category"):
            results.append({
                "requested": name,
                "status": "resolved",
                "display_name": resolved.get("display_name"),
                "exercise_name": resolved.get("exercise_name"),
                "category": resolved.get("category"),
                "known_good": resolved.get("known_good"),
                "safe_for_exact_tracking": resolved.get("safe_for_exact_tracking"),
            })
        else:
            # Try fuzzy search as fallback
            matches = search_catalog(name, limit=3, fuzzy=True)
            suggestions = []
            for match in matches:
                if match.get("confidence", 0) >= 0.5:
                    suggestions.append({
                        "display_name": match.get("display_name"),
                        "exercise_name": match.get("exercise_name"),
                        "category": match.get("category"),
                        "confidence": match.get("confidence"),
                    })
            
            results.append({
                "requested": name,
                "status": "not_found",
                "suggestions": suggestions[:3],
            })
    
    return results


def roundtrip_warning(entry: dict[str, Any]) -> str | None:
    status = entry.get("roundtrip_status") or "unknown"
    display = entry.get("display_name") or entry.get("exercise_name") or "Exercise"
    category = entry.get("category") or "category"
    if status == "preserved":
        return None
    if status == "stripped":
        return f"{display} is expected to render as a generic {category} step in Garmin after upload."
    if status == "rewritten":
        target = entry.get("garmin_rewrite_to") or entry.get("garmin_actual_exercise_name") or "a Garmin canonical exerciseName"
        return f"{display} is expected to round-trip as {target}."
    if status == "rejected":
        return f"{display} is known to be rejected by Garmin Connect."
    return "Garmin may accept this step but strip the internal exercise code after upload."


def find_entry_by_exercise_name(exercise_name: str) -> dict[str, Any] | None:
    needle = normalize_name(exercise_name)
    data = load_catalog()
    for entry in data["exercises"]:
        if normalize_name(str(entry.get("exercise_name", ""))) == needle:
            return entry
    return None


def _find_entry_in_data(data: dict[str, Any], name: str) -> dict[str, Any] | None:
    needle = normalize_name(name)
    for entry in data["exercises"]:
        if normalize_name(str(entry.get("display_name", ""))) == needle:
            return entry
    for entry in data["exercises"]:
        if any(normalize_name(str(alias)) == needle for alias in entry.get("aliases") or []):
            return entry
    for entry in data["exercises"]:
        if normalize_name(str(entry.get("exercise_name", ""))) == needle:
            return entry
    return None


def update_roundtrip_metadata(
    display_name: str,
    status: str,
    actual_exercise_name: str | None = None,
    rewrite_to: str | None = None,
    upload_accepts: bool | None = True,
    notes: str | None = None,
) -> dict[str, Any] | None:
    if status not in ROUNDTRIP_STATUSES:
        raise ValueError(f"roundtrip_status must be one of {sorted(ROUNDTRIP_STATUSES)}")
    data = load_catalog()
    entry = _find_entry_in_data(data, display_name)
    if entry is None:
        return None
    entry["roundtrip_status"] = status
    entry["garmin_actual_exercise_name"] = actual_exercise_name
    entry["garmin_rewrite_to"] = rewrite_to
    entry["garmin_upload_accepts"] = upload_accepts
    entry["garmin_roundtrip_preserves"] = status == "preserved"
    entry["safe_for_exact_tracking"] = status == "preserved"
    entry["known_good"] = status == "preserved"
    entry["last_roundtrip_verified_at"] = int(time.time())
    if notes is not None:
        entry["notes"] = notes
    entry["updated_at"] = int(time.time())
    save_catalog(data)
    return entry


def load_global_catalog() -> dict[str, Any]:
    """Load the global Garmin exercise catalog."""
    if not GLOBAL_CATALOG_PATH.exists():
        return {"version": 1, "exercises": [], "updated_at": int(time.time())}
    with GLOBAL_CATALOG_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def save_global_catalog(data: dict[str, Any]) -> None:
    """Save the global Garmin exercise catalog."""
    GLOBAL_CATALOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = int(time.time())
    encoded = json.dumps(data, indent=2, sort_keys=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=str(GLOBAL_CATALOG_PATH.parent), delete=False) as handle:
        handle.write(encoded)
        handle.write("\n")
        temp_name = handle.name
    Path(temp_name).replace(GLOBAL_CATALOG_PATH)


def resolve_strength_exercise_global(display_name: str) -> dict[str, Any] | None:
    """Resolve exercise using only Garmin's global catalog. De-duplicates by (category, exerciseName)."""
    needle = normalize_name(display_name)
    data = load_global_catalog()

    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for entry in data.get("exercises", []):
        if not entry.get("category") or not entry.get("exercise_name"):
            continue
        key = (str(entry["category"]), str(entry["exercise_name"]))
        if key not in by_key:
            by_key[key] = entry

    # Exact match on display_name takes priority
    for entry in data.get("exercises", []):
        if normalize_name(str(entry.get("display_name", ""))) == needle:
            result = dict(entry)
            result["source"] = "garmin_global_catalog"
            return result

    candidates = list(by_key.values())

    # Exact match on exercise_name
    for entry in candidates:
        if normalize_name(str(entry.get("exercise_name", ""))) == needle:
            result = dict(entry)
            result["source"] = "garmin_global_catalog"
            return result

    query_tokens = tokenize(needle)
    best_score = 0
    best_entry = None

    # Check for equipment-specific queries
    has_barbell = "barbell" in needle
    has_dumbbell = "dumbbell" in needle
    has_seated = "seated" in needle
    has_overhead = "overhead" in needle

    for entry in candidates:
        entry_text = normalize_name(str(entry.get("display_name", "")))
        entry_exercise = normalize_name(str(entry.get("exercise_name", "")))
        entry_tokens = tokenize(entry_text)

        if query_tokens and entry_tokens:
            overlap = len(query_tokens & entry_tokens) / len(query_tokens)
            if overlap >= 0.75:
                # Apply equipment bias
                score = overlap
                entry_is_barbell = "barbell" in entry_text.lower()
                entry_is_dumbbell = "dumbbell" in entry_text.lower()

                # If query specifies barbell, prefer barbell exercises
                if has_barbell and entry_is_barbell:
                    score += 0.3
                # If query specifies dumbbell, prefer dumbbell exercises
                elif has_dumbbell and entry_is_dumbbell:
                    score += 0.3
                # If query is generic (just "shoulder press"), prefer barbell
                elif not has_barbell and not has_dumbbell and entry_is_barbell:
                    score += 0.15

                if score > best_score:
                    best_score = score
                    best_entry = entry

    if best_entry:
        result = dict(best_entry)
        result["source"] = "garmin_global_catalog"
        return result
    return None


def sync_global_garmin_exercise_catalog() -> dict[str, Any]:
    """Sync from Garmin's global Exercises.json AND translations file."""
    exercises_json_text = _fetch_text(GARMIN_EXERCISES_URL)
    translations_text = _fetch_text(GARMIN_EXERCISE_TRANSLATIONS_URL)
    exercises_data = json.loads(exercises_json_text)
    translations = _parse_properties(translations_text)
    imported = _walk_garmin_exercise_catalog(exercises_data, translations)

    seen: set[tuple[str, str]] = set()
    deduplicated = []
    for entry in imported:
        key = (str(entry.get("category", "")), str(entry.get("exercise_name", "")))
        if key not in seen:
            seen.add(key)
            deduplicated.append(entry)

    # Also add exercises from translations that are not in Exercises.json
    # These are exercises that Garmin accepts but aren't in the public enum
    category_types = {}
    for key, value in translations.items():
        if key.startswith("category_type_"):
            cat = key.replace("category_type_", "")
            category_types[cat] = value

    translation_only_count = 0
    for key, value in translations.items():
        if key.startswith("exercise_type_"):
            exercise_name = key.replace("exercise_type_", "")
            display_name = value

            # Skip if already imported from Exercises.json
            key = (None, exercise_name)  # Category unknown
            if any(e.get("exercise_name") == exercise_name for e in deduplicated):
                continue

            # Determine category from exercise name patterns
            category = None
            exercise_upper = exercise_name.upper()
            if "SHOULDER" in exercise_upper and "PRESS" in exercise_upper:
                category = "SHOULDER_PRESS"
            elif "SHOULDER" in exercise_upper and "STABILITY" in exercise_upper:
                category = "SHOULDER_STABILITY"
            elif "ROW" in exercise_upper:
                category = "ROW"
            elif "BENCH" in exercise_upper and "PRESS" in exercise_upper:
                category = "BENCH_PRESS"
            elif "LAT" in exercise_upper and "PULL" in exercise_upper:
                category = "PULL_UP"
            elif "LATERAL" in exercise_upper and "RAISE" in exercise_upper:
                category = "LATERAL_RAISE"
            elif "PUSH" in exercise_upper and "UP" in exercise_upper:
                category = "PUSH_UP"
            elif "PLANK" in exercise_upper:
                category = "PLANK"
            elif "SQUAT" in exercise_upper:
                category = "SQUAT"
            elif "DEADLIFT" in exercise_upper:
                category = "DEADLIFT"
            elif "CURL" in exercise_upper:
                category = "CURL"
            elif "FLY" in exercise_upper:
                category = "FLYE"
            elif "CORE" in exercise_upper:
                category = "CORE"
            elif "CARRY" in exercise_upper:
                category = "CARRY"

            if category:
                key = (category, exercise_name)
                if key not in seen:
                    seen.add(key)
                    deduplicated.append({
                        "display_name": display_name,
                        "exercise_name": exercise_name,
                        "category": category,
                        "aliases": [],
                        "canonical": normalize_name(display_name).replace(" ", "_"),
                        "local_valid": True,
                        "garmin_upload_accepts": None,
                        "garmin_roundtrip_preserves": False,
                        "roundtrip_status": "unknown",
                        "garmin_actual_exercise_name": None,
                        "garmin_rewrite_to": None,
                        "last_roundtrip_verified_at": None,
                        "safe_for_exact_tracking": False,
                        "known_good": False,
                        "source": "garmin_translation_only",
                        "updated_at": int(time.time()),
                    })
                    translation_only_count += 1

    data = load_global_catalog()
    data["exercises"] = deduplicated
    save_global_catalog(data)
    return {"status": "success", "imported_count": len(deduplicated), "translation_only_count": translation_only_count, "path": str(GLOBAL_CATALOG_PATH)}


GARMIN_KNOWN_REWRITES = {
    "BARBELL_SHOULDER_PRESS": "OVERHEAD_BARBELL_PRESS",
}


def verify_strength_exercise_roundtrip(
    display_name: str,
    expected_category: str,
    expected_exercise_name: str,
    actual_category: str | None,
    actual_exercise_name: str | None,
) -> dict[str, Any]:
    """Verify roundtrip - marks unsafe if Garmin returns blank exerciseName, changed category, or changed enum.
    Known Garmin rewrites are accepted as safe."""
    result = {
        "display_name": display_name,
        "expected_category": expected_category,
        "expected_exercise_name": expected_exercise_name,
        "actual_category": actual_category,
        "actual_exercise_name": actual_exercise_name,
        "status": "unknown",
    }

    if not actual_exercise_name:
        result["status"] = "unsafe"
        result["reason"] = "Garmin returned blank exerciseName"
        return result

    if actual_category and actual_category != expected_category:
        result["status"] = "unsafe"
        result["reason"] = f"Category changed from {expected_category} to {actual_category}"
        return result

    if actual_exercise_name != expected_exercise_name:
        # Check if this is a known Garmin rewrite
        expected_key = str(expected_exercise_name).upper()
        if actual_exercise_name and actual_exercise_name.upper() == GARMIN_KNOWN_REWRITES.get(expected_key, "").upper():
            result["status"] = "safe_known_rewrite"
            result["reason"] = f"Known Garmin rewrite: {expected_exercise_name} -> {actual_exercise_name}"
            return result
        result["status"] = "unsafe"
        result["reason"] = f"exerciseName changed from {expected_exercise_name} to {actual_exercise_name}"
        return result

    result["status"] = "safe"
    return result


def register_tools(app: Any) -> Any:
    @app.tool()
    async def list_strength_exercises(query: str = "", limit: int = 100) -> str:
        """List/search the local Garmin strength exercise catalog."""
        limit = min(max(int(limit), 1), 1000)
        data = load_catalog()
        matches = search_catalog(query, limit=limit, fuzzy=True) if query else data["exercises"][:limit]
        return json.dumps({"status": "success", "path": str(CATALOG_PATH), "count": len(matches), "exercises": matches[:limit]}, indent=2)

    @app.tool()
    async def search_strength_exercises(
        query: str,
        limit: int = 20,
        category: str | None = None,
        muscle_group: str | None = None,
        equipment: str | None = None,
        fuzzy: bool = True,
    ) -> str:
        """Search strength exercises by exact name, alias, fuzzy text, Garmin exerciseName, category, muscle group, or equipment."""
        matches = search_catalog(query, limit=limit, category=category, muscle_group=muscle_group, equipment=equipment, fuzzy=fuzzy)
        return json.dumps({"status": "success", "query": query, "count": len(matches), "exercises": matches}, indent=2)

    @app.tool()
    async def sync_garmin_strength_exercise_catalog(update_existing: bool = False) -> str:
        """Import Garmin's public strength exercise enum list. Imported entries are not round-trip verified."""
        try:
            result = sync_garmin_public_exercise_catalog(update_existing=update_existing)
            return json.dumps(
                {
                    "status": "success",
                    "message": "Garmin public exercise catalog imported. Entries remain roundtrip_status='unknown' until empirical verification.",
                    "path": str(CATALOG_PATH),
                    **result,
                },
                indent=2,
            )
        except Exception as exc:
            logger.info("strength_exercise_catalog_sync_failed: %s", type(exc).__name__)
            return json.dumps({"status": "error", "message": f"Garmin public exercise catalog sync failed: {exc}"}, indent=2)

    @app.tool()
    async def get_strength_exercise(name: str) -> str:
        """Get one local strength exercise mapping by display name, alias, or Garmin exerciseName."""
        entry = find_entry(name)
        if not entry:
            return json.dumps({"status": "not_found", "name": name, "message": "Exercise is not in the local catalog."}, indent=2)
        return json.dumps({"status": "success", "exercise": entry}, indent=2)

    @app.tool()
    async def upsert_strength_exercise(
        display_name: str,
        exercise_name: str | None = None,
        category: str | None = None,
        aliases: list[str] | None = None,
        canonical: str | None = None,
        known_good: bool = False,
        roundtrip_status: str = "unknown",
        garmin_actual_exercise_name: str | None = None,
        garmin_rewrite_to: str | None = None,
        garmin_upload_accepts: bool | None = None,
        safe_for_exact_tracking: bool | None = None,
        notes: str | None = None,
    ) -> str:
        """Add or update one local strength exercise mapping. known_good means Garmin round-trip preserved."""
        if roundtrip_status not in ROUNDTRIP_STATUSES:
            return json.dumps({"status": "error", "message": f"roundtrip_status must be one of {sorted(ROUNDTRIP_STATUSES)}"}, indent=2)
        data = load_catalog()
        entry = _find_entry_in_data(data, display_name)
        if entry is None:
            entry = make_entry(display_name, exercise_name, category)
            data["exercises"].append(entry)
            action = "created"
        else:
            action = "updated"
            entry["display_name"] = display_name.strip()
            if exercise_name:
                entry["exercise_name"] = exercise_name.strip().upper()
            if category:
                entry["category"] = category.strip().upper()
        if aliases is not None:
            entry["aliases"] = sorted({alias.strip() for alias in aliases if alias and alias.strip()})
        if canonical:
            entry["canonical"] = canonical.strip()
        else:
            entry.setdefault("canonical", normalize_name(display_name).replace(" ", "_"))
        if known_good and roundtrip_status == "unknown":
            roundtrip_status = "preserved"
        entry["local_valid"] = bool(entry.get("exercise_name") and entry.get("category"))
        entry["roundtrip_status"] = roundtrip_status if roundtrip_status != "unknown" else entry.get("roundtrip_status", "unknown")
        if garmin_actual_exercise_name is not None:
            entry["garmin_actual_exercise_name"] = garmin_actual_exercise_name
        if garmin_rewrite_to is not None:
            entry["garmin_rewrite_to"] = garmin_rewrite_to
        if garmin_upload_accepts is not None:
            entry["garmin_upload_accepts"] = garmin_upload_accepts
        if safe_for_exact_tracking is None:
            safe_for_exact_tracking = entry["roundtrip_status"] == "preserved"
        entry["safe_for_exact_tracking"] = bool(safe_for_exact_tracking)
        entry["garmin_roundtrip_preserves"] = entry["roundtrip_status"] == "preserved"
        entry["known_good"] = bool(entry["safe_for_exact_tracking"])
        if notes is not None:
            entry["notes"] = notes
        entry["updated_at"] = int(time.time())
        data["exercises"] = sorted(data["exercises"], key=lambda item: normalize_name(item.get("display_name", "")))
        save_catalog(data)
        logger.info("strength_exercise_%s", action)
        return json.dumps({"status": "success", "action": action, "exercise": entry, "path": str(CATALOG_PATH)}, indent=2)

    @app.tool()
    async def bulk_upsert_strength_exercises(exercises: list[dict[str, Any]] | list[str]) -> str:
        """Bulk add/update local strength exercises from names or mapping objects. Does not write to Garmin."""
        data = load_catalog()
        created = 0
        updated = 0
        by_key = {normalize_name(entry.get("display_name", "")): entry for entry in data["exercises"]}
        for item in exercises:
            if isinstance(item, str):
                incoming = make_entry(item)
            elif isinstance(item, dict) and item.get("display_name"):
                incoming = make_entry(item["display_name"], item.get("exercise_name"), item.get("category"))
                incoming["aliases"] = item.get("aliases") or []
                incoming["canonical"] = item.get("canonical") or incoming["canonical"]
                incoming["roundtrip_status"] = item.get("roundtrip_status") or ("preserved" if item.get("known_good") else incoming["roundtrip_status"])
                incoming["safe_for_exact_tracking"] = incoming["roundtrip_status"] == "preserved"
                incoming["garmin_roundtrip_preserves"] = incoming["roundtrip_status"] == "preserved"
                incoming["known_good"] = incoming["roundtrip_status"] == "preserved"
                for key in ("garmin_upload_accepts", "garmin_actual_exercise_name", "garmin_rewrite_to", "last_roundtrip_verified_at"):
                    if key in item:
                        incoming[key] = item[key]
                if item.get("notes"):
                    incoming["notes"] = item["notes"]
            else:
                continue
            key = normalize_name(incoming["display_name"])
            if key in by_key:
                by_key[key].update({k: v for k, v in incoming.items() if v not in (None, [], "")})
                by_key[key]["updated_at"] = int(time.time())
                updated += 1
            else:
                data["exercises"].append(incoming)
                by_key[key] = incoming
                created += 1
        data["exercises"] = sorted(data["exercises"], key=lambda item: normalize_name(item.get("display_name", "")))
        save_catalog(data)
        logger.info("strength_exercise_bulk_upsert created=%s updated=%s", created, updated)
        return json.dumps({"status": "success", "created": created, "updated": updated, "total": len(data["exercises"]), "path": str(CATALOG_PATH)}, indent=2)

    @app.tool()
    async def delete_strength_exercise(name: str, confirmation: str = "") -> str:
        """Delete one local strength exercise mapping. Requires confirmation='DELETE_STRENGTH_EXERCISE'."""
        if confirmation != "DELETE_STRENGTH_EXERCISE":
            return json.dumps({"status": "error", "message": "Explicit confirmation required. Re-run with confirmation='DELETE_STRENGTH_EXERCISE'."}, indent=2)
        data = load_catalog()
        needle = normalize_name(name)
        original = len(data["exercises"])
        data["exercises"] = [
            entry for entry in data["exercises"]
            if normalize_name(entry.get("display_name", "")) != needle and normalize_name(entry.get("exercise_name", "")) != needle
        ]
        save_catalog(data)
        return json.dumps({"status": "success", "deleted": original - len(data["exercises"]), "path": str(CATALOG_PATH)}, indent=2)

    @app.tool()
    async def sync_global_garmin_exercise_catalog() -> str:
        """Sync the global Garmin exercise catalog from Exercises.json."""
        try:
            result = sync_global_garmin_exercise_catalog()
            return json.dumps({"status": "success", "message": "Global Garmin exercise catalog synced from Exercises.json", **result}, indent=2)
        except Exception as exc:
            logger.info("global_garmin_catalog_sync_failed: %s", type(exc).__name__)
            return json.dumps({"status": "error", "message": f"Global Garmin catalog sync failed: {exc}"}, indent=2)

    @app.tool()
    async def resolve_strength_exercise_global(display_name: str) -> str:
        """Resolve a strength exercise using only Garmin's global catalog."""
        entry = resolve_strength_exercise_global(display_name)
        if not entry:
            return json.dumps({"status": "not_found", "display_name": display_name, "message": "Exercise not found in global Garmin catalog."}, indent=2)
        return json.dumps({"status": "success", "display_name": entry.get("display_name"), "exercise_name": entry.get("exercise_name"), "category": entry.get("category"), "source": entry.get("source", "garmin_global_catalog")}, indent=2)

    @app.tool()
    async def verify_strength_exercise_roundtrip(display_name: str, expected_category: str, expected_exercise_name: str, actual_category: str | None = None, actual_exercise_name: str | None = None) -> str:
        """Verify exercise roundtrip - marks unsafe if Garmin returns blank exerciseName, changed category, or changed enum."""
        result = verify_strength_exercise_roundtrip(display_name, expected_category, expected_exercise_name, actual_category, actual_exercise_name)
        return json.dumps(result, indent=2)

    @app.tool()
    async def resolve_strength_exercises_bulk(
        exercises: list[str],
        safe_only: bool = False,
    ) -> str:
        """Resolve multiple exercises by name. Handles multi-exercise queries like 'pull up lat pulldown row'.
        
        Returns resolved exercise info for each requested exercise, or suggestions if not found.
        Use safe_only=true to only return known_good (verified) exercises.
        """
        results = resolve_strength_exercises_bulk(exercises, safe_only=safe_only)
        resolved_count = sum(1 for r in results if r.get("status") == "resolved")
        return json.dumps({
            "status": "success",
            "requested_count": len(exercises),
            "resolved_count": resolved_count,
            "results": results,
        }, indent=2)

    return app
