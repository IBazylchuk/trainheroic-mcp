"""Pure helpers that shape TrainHeroic workout payloads.

Vocabulary used by the API (and by this module):

* **program workout** – a workout placed on a calendar date (``program_workout_id``).
* **workout** – the template behind it (``workout_id``): title, instruction, blocks.
* **block** (``workoutSet``) – a group of exercises inside a workout.
* **set exercise** (``workoutSetExercise``) – one exercise in a block, with its
  prescription stored as ``param_{1,2}_data_{1..10}`` (one value per set).
* **saved workout** – the athlete's copy that holds logged results.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from .units import ParamType, format_number, kg_to_lb, lb_to_kg

MAX_SETS = 10

# Which keyword argument feeds which parameter type.
INPUT_FOR_TYPE: dict[ParamType, str] = {
    ParamType.REPS: "reps",
    ParamType.REP_RANGE: "reps",
    ParamType.WEIGHT: "weight_kg",
    ParamType.WEIGHT_KG: "weight_kg",
    ParamType.WEIGHT_PERCENT: "percent",
    ParamType.TIME: "seconds",
    ParamType.SECONDS: "seconds",
    ParamType.DISTANCE_METERS: "meters",
    ParamType.RPE: "rpe",
    ParamType.CALORIES: "calories",
}

# Fields the coach editor sends back when saving a set exercise. The endpoint
# rejects partial objects, so the full shape is always rebuilt from the workout.
SET_EXERCISE_FIELDS = (
    "id",
    "set_id",
    "exercise_id",
    "order",
    "title",
    "instruction",
    "no_sets",
    "param_1_type",
    "param_2_type",
    "param_count",
    "reference_max_exercise_id",
    "workout_set_exercise_template_id",
)

BLOCK_FIELDS = (
    "id",
    "workout_id",
    "title",
    "phase",
    "order",
    "instruction",
    "plain_text",
    "type",
    "is_test",
    "is_redzone",
    "test_instruction",
    "redzone_instruction",
    "redzone_type",
    "is_set",
    "workout_combo_id",
)


def to_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _clean(value: Any) -> Any:
    return None if value in (None, "", "None", "null") else value


# ── history ──────────────────────────────────────────────────────────────────


def summarize_history_item(item: dict) -> dict:
    saved = (item.get("summarizedSavedWorkout") or {}).get("saved_workout") or {}
    return {
        "program_workout_id": to_int(item.get("id")),
        "workout_id": to_int(item.get("workout_id")),
        "program_id": to_int(item.get("program_id")),
        "team_id": to_int(item.get("team_id")),
        "team_title": item.get("team_title"),
        "personal": bool(item.get("personal_cal")),
        "date": item.get("date"),
        "title": item.get("workout_title"),
        "saved_workout_id": to_int(saved.get("id")),
        "completed": saved.get("completed") in (1, "1", True),
        "rpe": _clean(saved.get("rpe")),
        "rating": _clean(saved.get("workout_rating")),
        "notes": _clean(saved.get("notes")),
    }


# ── workout details ──────────────────────────────────────────────────────────
#
# A details response always carries the athlete's saved copy
# (``summarizedSavedWorkout.saved_workout``) with every block and exercise id.
# The template (``workout``) is only present sometimes; when it is, it holds the
# raw prescription values (weights in lb) and wins for template fields.


def _saved_root(details: dict) -> dict:
    return (details.get("summarizedSavedWorkout") or {}).get("saved_workout") or {}


def _by_order(items: list[dict]) -> list[dict]:
    return sorted(items, key=lambda i: to_int(i.get("order")) or 0)


def blocks(details: dict) -> list[dict]:
    """Blocks with exercises, normalized to the shape the save endpoints expect.

    Each block/exercise keeps its source payloads under ``saved`` / ``template``.
    """
    root = _saved_root(details)
    templates = {
        to_int(b.get("id")): b for b in (details.get("workout") or {}).get("workoutSets") or []
    }
    template_exercises = {
        to_int(e.get("id")): e
        for b in templates.values()
        for e in b.get("workoutSetExercises") or []
    }
    workout_id = to_int(root.get("workout_id")) or to_int(details.get("workout_id"))
    out = []
    for saved_block in _by_order(root.get("workoutSets") or []):
        block_id = to_int(saved_block.get("workout_set_id"))
        template = templates.get(block_id, {})
        block = {k: saved_block.get(k) for k in BLOCK_FIELDS if k in saved_block}
        block.update({k: template[k] for k in BLOCK_FIELDS if k in template})
        block.update({"id": block_id, "workout_id": workout_id})
        block["instruction"] = _clean(block.get("instruction")) or ""
        exercises = []
        for saved_ex in _by_order(saved_block.get("workoutSetExercises") or []):
            wse_id = to_int(saved_ex.get("workout_set_exercise_id"))
            tpl_ex = template_exercises.get(wse_id, {})
            ex = {k: saved_ex.get(k) for k in SET_EXERCISE_FIELDS}
            ex.update({k: tpl_ex[k] for k in SET_EXERCISE_FIELDS if k in tpl_ex})
            ex.update(
                {
                    "id": wse_id,
                    "set_id": block_id,
                    "exercise_id": to_int(ex.get("exercise_id")),
                    "title": ex.get("title") or saved_ex.get("exercise_title"),
                    "instruction": _clean(ex.get("instruction")) or "",
                    "video_url": _clean(tpl_ex.get("video_url") or saved_ex.get("video_url")),
                    "saved": saved_ex,
                    "template": tpl_ex,
                }
            )
            ex["reference_max_exercise_id"] = (
                to_int(ex.get("reference_max_exercise_id")) or ex["exercise_id"]
            )
            ex["workout_set_exercise_template_id"] = to_int(
                ex.get("workout_set_exercise_template_id")
            )
            exercises.append(ex)
        block["exercises"] = exercises
        block["saved"] = saved_block
        out.append(block)
    return out


def find_set_exercise(details: dict, workout_set_exercise_id: int) -> dict:
    for block in blocks(details):
        for ex in block["exercises"]:
            if ex["id"] == workout_set_exercise_id:
                return ex
    raise KeyError(f"Exercise {workout_set_exercise_id} is not part of this workout")


def find_block(details: dict, block_id: int) -> dict:
    for block in blocks(details):
        if block["id"] == block_id:
            return block
    raise KeyError(f"Block {block_id} is not part of this workout")


def _param_types(ex: dict) -> list[str]:
    types = [ParamType.parse(ex.get("param_1_type")), ParamType.parse(ex.get("param_2_type"))]
    return [t.name.lower() for t in types if t is not ParamType.NONE]


def _done(value: Any) -> bool:
    return value in (1, "1", True)


def summarize_workout(details: dict) -> dict:
    root = _saved_root(details)
    workout = details.get("workout") or {}
    return {
        "program_workout_id": to_int(details.get("id")),
        "workout_id": to_int(details.get("workout_id")),
        "program_id": to_int(details.get("program_id")),
        "team_id": to_int(details.get("team_id")),
        "personal": bool(details.get("personal_cal")),
        "date": details.get("date"),
        "title": workout.get("title") or details.get("workout_title") or root.get("title"),
        "instruction": _clean(workout.get("instruction") or root.get("instruction")),
        "saved_workout_id": to_int(root.get("id")),
        "completed": _done(root.get("completed")),
        "rpe": _clean(root.get("rpe")),
        "notes": _clean(root.get("notes")),
        "blocks": [
            {
                "block_id": block["id"],
                "title": block.get("title"),
                "instruction": block["instruction"] or None,
                "exercises": [
                    {
                        "workout_set_exercise_id": ex["id"],
                        "exercise_id": ex["exercise_id"],
                        "title": ex["title"],
                        "prescribed": _clean(ex["saved"].get("abr")),
                        "results": _clean(ex["saved"].get("abr_results")),
                        "completed": _done(ex["saved"].get("completed")),
                        "instruction": ex["instruction"] or None,
                        "tracks": _param_types(ex),
                        "video_url": ex["video_url"],
                    }
                    for ex in block["exercises"]
                ],
            }
            for block in blocks(details)
        ],
    }


def set_rows(ex: dict) -> list[dict]:
    """Per-set values of a raw set-exercise payload (saved copy or template)."""
    count = to_int(ex.get("param_count")) or 0
    types = [ParamType.parse(ex.get("param_1_type")), ParamType.parse(ex.get("param_2_type"))]
    rows = []
    for i in range(1, min(count, MAX_SETS) + 1):
        row: dict[str, Any] = {"set": i}
        for slot, ptype in enumerate(types, start=1):
            if ptype is not ParamType.NONE:
                row[ptype.name.lower()] = _clean(ex.get(f"param_{slot}_data_{i}"))
        if f"param_{i}_made" in ex:
            row["made"] = _done(ex.get(f"param_{i}_made"))
        rows.append(row)
    return rows


def describe_set_exercise(details: dict, workout_set_exercise_id: int) -> dict:
    ex = find_set_exercise(details, workout_set_exercise_id)
    saved = ex["saved"]
    return {
        "workout_set_exercise_id": workout_set_exercise_id,
        "exercise_id": ex["exercise_id"],
        "title": ex["title"],
        "instruction": ex["instruction"] or None,
        "tracks": _param_types(ex),
        "prescribed": _clean(saved.get("abr")),
        "results": _clean(saved.get("abr_results")),
        # Values in the athlete's display units; `made` marks sets logged as done.
        "units": "kg" if str(_saved_root(details).get("use_metric")) == "1" else "lb",
        "sets": set_rows(saved),
        "athlete_notes": _clean(saved.get("notes")),
        "video_url": ex["video_url"],
    }


# ── readiness surveys ────────────────────────────────────────────────────────


def _survey_questions(raw: dict) -> list[dict]:
    return (raw.get("survey") or {}).get("survey_questions") or []


def summarize_survey(raw: dict) -> dict:
    """Map answer ids back to question titles, scores and labels."""
    answers_by_id = {
        to_int(a.get("id")): (q.get("title"), a)
        for q in _survey_questions(raw)
        for a in q.get("survey_question_answers") or []
    }
    answers = {}
    for given in raw.get("user_survey_answers") or []:
        title, answer = answers_by_id.get(
            to_int(given.get("survey_question_answer_id")), (None, {})
        )
        if title:
            answers[title.lower()] = {
                "score": to_int(answer.get("value")),
                "label": answer.get("label"),
            }
    return {"answers": answers, "average": raw.get("average_survey_response_value")}


def survey_answer_id(raw: dict, question: str, score: int) -> tuple[int, int]:
    """Resolve (question id, answer id) for a question title and a 1-5 score."""
    for q in _survey_questions(raw):
        if (q.get("title") or "").lower() == question.lower():
            for a in q.get("survey_question_answers") or []:
                if to_int(a.get("value")) == score:
                    return int(q["id"]), int(a["id"])
            raise ValueError(f"{question} has no answer with score {score}")
    raise ValueError(f"The survey has no question {question!r}")


# ── prescriptions ────────────────────────────────────────────────────────────

Value = float | int | str
Values = Value | Sequence[Value] | None


def _as_list(values: Values, sets: int) -> list[Value]:
    if values is None:
        return [""] * sets
    if isinstance(values, (str, int, float)):
        return [values] * sets
    values = list(values)
    if len(values) != sets:
        raise ValueError(f"Got {len(values)} values for {sets} sets")
    return values


def resolve_set_count(sets: int | None, inputs: dict[str, Values]) -> int:
    lengths = {
        len(v) for v in inputs.values() if v is not None and not isinstance(v, (str, int, float))
    }
    if len(lengths) > 1:
        raise ValueError(f"Per-set lists have different lengths: {sorted(lengths)}")
    count = sets or (lengths.pop() if lengths else 1)
    if not 1 <= count <= MAX_SETS:
        raise ValueError(f"TrainHeroic supports 1-{MAX_SETS} sets, got {count}")
    return count


def build_prescription(
    current: dict,
    *,
    sets: int | None = None,
    instruction: str | None = None,
    **inputs: Values,
) -> dict:
    """Return the full set-exercise object to POST with the new prescription.

    ``current`` is a normalized set exercise (see ``blocks``). ``inputs``
    are human-unit values keyed like ``INPUT_FOR_TYPE`` (``reps``, ``weight_kg``,
    ``seconds``...), either one value for every set or a per-set list.
    """
    provided = {k: v for k, v in inputs.items() if v is not None}
    types = [
        ParamType.parse(current.get("param_1_type")),
        ParamType.parse(current.get("param_2_type")),
    ]
    accepted = {INPUT_FOR_TYPE.get(t) for t in types} - {None}
    unknown = set(provided) - accepted
    if unknown:
        tracked = [t.name.lower() for t in types if t is not ParamType.NONE] or ["nothing"]
        raise ValueError(
            f"{current.get('title')} tracks {' + '.join(tracked)}; "
            f"cannot set {', '.join(sorted(unknown))}"
        )

    count = resolve_set_count(sets, provided)
    payload = {k: current.get(k) for k in SET_EXERCISE_FIELDS}
    payload["workout_set_id"] = current.get("set_id")
    payload["param_count"] = count
    payload["no_sets"] = 0
    if instruction is not None:
        payload["instruction"] = instruction

    for slot, ptype in enumerate(types, start=1):
        key = INPUT_FOR_TYPE.get(ptype)
        values = _as_list(provided.get(key) if key else None, count)
        for value in values:
            _check_value(ptype, value, key or "")
        if ptype is ParamType.WEIGHT:
            values = [kg_to_lb(float(v)) if v != "" else "" for v in values]
        for i in range(1, MAX_SETS + 1):
            value = values[i - 1] if i <= count else ""
            payload[f"param_{slot}_data_{i}"] = format_number(value) if value != "" else ""
    return payload


_RANGE = re.compile(r"^\d+(\.\d+)?\s*-\s*\d+(\.\d+)?$")


def _check_value(ptype: ParamType, value: Value, key: str) -> None:
    """The API silently truncates text like "8/8" to 8, so reject it up front."""
    if value == "":
        return
    if ptype is ParamType.REP_RANGE and isinstance(value, str) and _RANGE.match(value.strip()):
        return
    try:
        number = float(value)
    except (TypeError, ValueError):
        hint = ' (ranges like "8-10" need a rep-range exercise)' if key == "reps" else ""
        raise ValueError(
            f"{key}={value!r} must be a number{hint}; put per-side or tempo cues in the instruction"
        ) from None
    if ptype is ParamType.REPS and not number.is_integer():
        raise ValueError(f"reps must be whole numbers, got {value!r}")
    if number < 0:
        raise ValueError(f"{key} cannot be negative, got {value!r}")


_SUFFIX = {
    ParamType.WEIGHT: " kg",
    ParamType.WEIGHT_KG: " kg",
    ParamType.WEIGHT_PERCENT: "%",
    ParamType.TIME: " s",
    ParamType.SECONDS: " s",
    ParamType.DISTANCE_METERS: " m",
    ParamType.CALORIES: " cal",
}


def describe_prescription(payload: dict) -> str:
    """Human summary of a prescription payload, weights in kg: "4 x 5 @ 30, 35, 40 kg"."""
    count = to_int(payload.get("param_count")) or 0
    parts = []
    for slot in (1, 2):
        ptype = ParamType.parse(payload.get(f"param_{slot}_type"))
        values = [payload.get(f"param_{slot}_data_{i}") or "" for i in range(1, count + 1)]
        if ptype is ParamType.NONE or not any(values):
            continue
        if ptype is ParamType.WEIGHT:
            values = [format_number(lb_to_kg(float(v))) if v else "" for v in values]
        shown = values[0] if len(set(values)) == 1 else ", ".join(values)
        prefix = "RPE " if ptype is ParamType.RPE else ""
        parts.append(f"{prefix}{shown}{_SUFFIX.get(ptype, '')}")
    if not parts:
        return f"{count} sets"
    return f"{count} x " + " @ ".join(parts)


def build_block(current: dict, *, title: str | None, instruction: str | None) -> dict:
    payload = {k: current.get(k) for k in BLOCK_FIELDS if k in current}
    if title is not None:
        payload["title"] = title
    if instruction is not None:
        payload["instruction"] = instruction
    return payload
