"""MCP tools for TrainHeroic."""

from __future__ import annotations

import functools
import json
import logging
import os
from collections.abc import Callable
from datetime import date, timedelta
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, Field

from . import __version__
from . import workouts as w
from .api import TrainHeroicAPI, TrainHeroicError

log = logging.getLogger(__name__)

INSTRUCTIONS = """\
Unofficial TrainHeroic access for one athlete account.

Reading: get_workout_history lists sessions in a date range (one row per session);
get_workout shows one session with its blocks, exercises, prescriptions, results
and exercise videos; get_set_exercise gives per-set values for one exercise.

Planning: create_workout builds a complete personal session in one call (title,
notes, exercises, sets/reps/weight, block notes). Exercises that share a
`superset` label go into one block, which is how TrainHeroic shows a superset.
For edits use create_session, add_exercises, add_to_block, set_prescription,
update_session, update_block, remove_exercise, remove_block and delete_session.
copy_workout repeats a session's plan on another date (handy for the weeks of a
block); move_workout reschedules a session that has no logged results yet.
Find exercise ids with search_exercises; every library exercise carries its own
video and technique notes, which appear automatically in the athlete's app.

Weights are always given in kilograms here; the server converts to the units the
API stores. Coach-assigned programs whose license has expired are not visible.
"""

SAVE_SET_EXERCISES = "/2.0/coach/calendar/saveWorkoutSetExercises"
SAVE_BLOCK = "/2.0/coach/calendar/workout/workoutSet/save/{block_id}"
DELETE_BLOCK = "/2.0/coach/calendar/workout/workoutSet/delete/{block_id}"
DELETE_SET_EXERCISE = "/2.0/coach/calendar/workout/workoutSetExercise/{block_id}/{wse_id}"

mcp = MCPServer("trainheroic", instructions=INSTRUCTIONS, version=__version__)

_api: TrainHeroicAPI | None = None


def tool(fn: Callable) -> Callable:
    """Register a tool whose expected failures reach the model as readable errors."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except KeyError as e:
            raise ToolError(str(e.args[0]) if e.args else "Not found") from e
        except (TrainHeroicError, ValueError) as e:
            raise ToolError(str(e)) from e

    mcp.tool()(wrapper)
    return wrapper


def api() -> TrainHeroicAPI:
    global _api
    if _api is None:
        _api = TrainHeroicAPI.from_env()
    return _api


def _fetch_details(program_workout_id: int, team_id: int | None, program_id: int | None = None):
    client = api()
    attempts = [team_id] if team_id else client.candidate_teams(program_id)
    used_team, details = client.first_success(
        attempts, lambda t: client.get(f"/v5/programWorkouts/{program_workout_id}/teams/{t}")
    )
    client.remember_team(
        details.get("program_id"), used_team, personal=bool(details.get("personal_cal"))
    )
    return used_team, details


# ── account ──────────────────────────────────────────────────────────────────


@tool
def get_profile() -> dict:
    """The athlete's id, name and the coach teams they belong to."""
    client = api()
    return {
        "user_id": client.user_id,
        "name": client.full_name,
        "personal_team_id": client.personal_team_id,
        "teams": client.teams,
    }


# ── history ──────────────────────────────────────────────────────────────────


@tool
def get_workout_history(
    start_date: str | None = None,
    end_date: str | None = None,
    days_back: int = 28,
) -> list[dict]:
    """Sessions scheduled between two dates (YYYY-MM-DD, inclusive), oldest first.

    Defaults to the last `days_back` days. Future dates work too, which is how to
    review a planned block. Each row has the ids needed by the other tools.
    """
    end = date.fromisoformat(end_date) if end_date else date.today()
    start = date.fromisoformat(start_date) if start_date else end - timedelta(days=days_back)
    client = api()
    rows: list[dict] = []
    cursor = start
    while cursor <= end:  # the range endpoint is reliable for windows of a few months
        chunk_end = min(end, cursor + timedelta(days=90))
        items = client.get(
            "/3.0/athlete/programworkout/range",
            startDate=cursor.isoformat(),
            endDate=chunk_end.isoformat(),
            preview="false",
        )
        for item in items or []:
            row = w.summarize_history_item(item)
            client.remember_team(row["program_id"], row["team_id"], personal=row["personal"])
            rows.append(row)
        cursor = chunk_end + timedelta(days=1)
    return sorted(rows, key=lambda r: (r["date"] or "", r["program_workout_id"] or 0))


@tool
def get_workout(program_workout_id: int, team_id: int | None = None) -> dict:
    """One session: title, notes, blocks, exercises with prescription, results and video.

    team_id is optional; the right team is found automatically.
    """
    _, details = _fetch_details(program_workout_id, team_id)
    return w.summarize_workout(details)


@tool
def get_set_exercise(
    program_workout_id: int, workout_set_exercise_id: int, team_id: int | None = None
) -> dict:
    """Per-set prescribed and logged values for one exercise of a session."""
    _, details = _fetch_details(program_workout_id, team_id)
    return w.describe_set_exercise(details, workout_set_exercise_id)


# ── library ──────────────────────────────────────────────────────────────────


def _compact_exercise(e: dict) -> dict:
    return {
        "exercise_id": e.get("id"),
        "title": e.get("title"),
        "tracks": e.get("prescription"),
        "video_url": e.get("videoUrl") or None,
    }


def _matches(title: str, query: str) -> bool:
    title = title.lower()
    return all(token in title for token in query.lower().split())


@tool
def search_exercises(
    query: str, limit: int = 20, with_video_only: bool = False, refresh: bool = False
) -> list[dict]:
    """Find exercises in the TrainHeroic library by name (all words must match).

    Returns id, title, what the exercise tracks (e.g. "Reps @ Weight (lb)") and
    the video link. The library is cached for 24h; pass refresh=True to reload.
    """
    found = [
        _compact_exercise(e)
        for e in api().exercise_library(refresh=refresh)
        if _matches(e.get("title", ""), query) and (e.get("hasVideo") or not with_video_only)
    ]
    found.sort(key=lambda e: (len(e["title"]), e["title"]))
    return found[:limit]


@tool
def get_exercise(exercise_id: int) -> dict:
    """Full library entry for one exercise: technique notes, video, tracked params."""
    return _library_entry(exercise_id)


def _library_entry(exercise_id: int) -> dict:
    for e in api().exercise_library():
        if e.get("id") == exercise_id:
            return e
    raise ValueError(f"Exercise {exercise_id} is not in the library")


@tool
def search_circuits(query: str = "", limit: int = 20) -> list[dict]:
    """Find circuits (predefined exercise groups) in the library by name."""
    found = [
        {k: c.get(k) for k in ("id", "title", "instructions", "prescription", "exerciseIds")}
        for c in api().circuit_library()
        if _matches(c.get("title", ""), query)
    ]
    return found[:limit]


@tool
def get_recent_exercises() -> Any:
    """Exercises the athlete used recently."""
    return api().get("/v5/users/exercises/recent")


@tool
def get_recent_circuits() -> Any:
    """Circuits the athlete used recently."""
    return api().get("/v5/users/circuits/recent")


# ── performance ──────────────────────────────────────────────────────────────


@tool
def get_exercise_stats(exercise_id: int, on_date: str | None = None) -> Any:
    """Last performance, personal record and working max for an exercise."""
    client = api()
    return client.get(
        f"/v5/exercises/{exercise_id}/stats",
        userId=client.user_id,
        date=on_date or date.today().isoformat(),
    )


@tool
def get_personal_records(exercise_id: int) -> Any:
    """Personal records for an exercise by rep count."""
    return api().get(f"/v5/exercises/{exercise_id}/personalRecords")


@tool
def get_working_max(exercise_id: int) -> Any:
    """Current working max for an exercise."""
    client = api()
    return client.get(f"/v5/users/{client.user_id}/workingMaxes/{exercise_id}")


# ── feedback ─────────────────────────────────────────────────────────────────


def _surveys(saved_workout_ids: list[int]) -> dict:
    return api().get("/3.0/athlete/savedworkout/surveys", swIds=json.dumps(saved_workout_ids))


@tool
def get_workout_surveys(saved_workout_ids: list[int]) -> dict:
    """Readiness answers (sleep, mood, energy, stress, soreness) for saved workouts.

    Scores run from 1 (worst) to 5 (best).
    """
    return {
        str(sw_id): w.summarize_survey(raw)
        for sw_id, raw in (_surveys(saved_workout_ids) or {}).items()
    }


@tool
def submit_survey(
    saved_workout_id: int,
    question: Literal["sleep", "mood", "energy", "stress", "soreness"],
    score: Annotated[int, Field(ge=1, le=5)],
) -> dict:
    """Answer one readiness question for a saved workout; score 1 (worst) to 5 (best)."""
    raw = (_surveys([saved_workout_id]) or {}).get(str(saved_workout_id))
    if not raw:
        raise ValueError(f"No survey is attached to saved workout {saved_workout_id}")
    question_id, answer_id = w.survey_answer_id(raw, question, score)
    api().post(
        f"/3.0/athlete/survey/question/{question_id}",
        {"saved_workout_id": saved_workout_id, "survey_question_answer_id": answer_id},
    )
    return w.summarize_survey((_surveys([saved_workout_id]) or {})[str(saved_workout_id)])


@tool
def get_workout_comments(program_workout_id: int, team_id: int | None = None) -> dict:
    """Comment thread of a session (coach and athlete messages)."""
    client = api()
    attempts = [team_id] if team_id else client.candidate_teams()
    _, stream = client.first_success(
        attempts,
        lambda t: client.get(f"/v5/messaging/streams/programWorkouts/{program_workout_id}/{t}"),
    )
    comments = client.get(f"/v5/messaging/streams/{stream['id']}/comments", lastCommentId="")
    return {"stream": stream, "comments": comments}


@tool
def get_workout_leaderboard(program_workout_id: int) -> Any:
    """Team leaderboard for a session."""
    return api().get(
        f"/3.0/athlete/leaderboard/{program_workout_id}", page=1, pageSize=9999, gender=0
    )


@tool
def save_workout_feedback(
    program_workout_id: int,
    notes: str | None = None,
    rpe: Annotated[int | None, Field(ge=1, le=10)] = None,
    rating: Annotated[float | None, Field(ge=0, le=10)] = None,
    team_id: int | None = None,
) -> dict:
    """Save the athlete's session notes, RPE (1-10) and rating.

    This does not log per-set results or mark the session completed; that is
    done in the TrainHeroic app.
    """
    client = api()
    used_team, details = _fetch_details(program_workout_id, team_id)
    summary = w.summarize_workout(details)
    body: dict[str, Any] = {
        "id": summary["saved_workout_id"],
        "workoutId": summary["workout_id"],
        "teamId": used_team,
        "date": f"{summary['date']}T00:00:00.000Z",
        "dateString": summary["date"],
        "blocks": [b["block_id"] for b in summary["blocks"]],
        "athleteFullName": client.full_name,
        "isPersonalCalendar": summary["personal"],
        "teamName": details.get("team_title"),
    }
    if notes is not None:
        body["notes"] = notes
    if rpe is not None:
        body["rpe"] = rpe
    if rating is not None:
        body["workout_rating"] = f"{rating:.1f}"
    saved = client.put(f"/1.0/athlete/savedworkout/{summary['saved_workout_id']}", body) or {}
    return {
        "saved_workout_id": summary["saved_workout_id"],
        "notes": _none_if_blank(saved.get("notes")),
        "rpe": saved.get("rpe"),
        "rating": saved.get("workout_rating"),
    }


def _none_if_blank(value: Any) -> Any:
    return value or None


# ── planning ─────────────────────────────────────────────────────────────────

PerSet = float | str | list[float | str] | None


@tool
def create_session(date: str, title: str | None = None, instruction: str | None = None) -> dict:
    """Create an empty personal session on a date (YYYY-MM-DD)."""
    client = api()
    created = client.post("/v5/programWorkouts/personal", {"date": date})
    pw, saved = created["programWorkout"], created["savedWorkout"]
    client.remember_team(pw.get("programId"), saved.get("group_id"), personal=True)
    if title is not None or instruction is not None:
        update_session(pw["workoutId"], title=title, instruction=instruction)
    return {
        "program_workout_id": pw["id"],
        "workout_id": pw["workoutId"],
        "saved_workout_id": saved["id"],
        "program_id": pw.get("programId"),
        "team_id": saved.get("group_id"),
        "date": pw["date"],
    }


@tool
def add_exercises(workout_id: int, exercise_ids: list[int]) -> list[dict]:
    """Append library exercises to a personal session, each in its own block.

    Returns the ids needed by set_prescription and update_block.
    """
    added = api().put(
        f"/v5/personalCalendar/workouts/{workout_id}/addExercises",
        {
            "exercises": [{"exerciseId": eid, "order": i} for i, eid in enumerate(exercise_ids, 1)],
            "circuits": [],
        },
    )
    out = []
    for block in added or []:
        for ex in block.get("savedWorkoutSetExercises") or []:
            out.append(
                {
                    "block_id": block.get("workoutSetId"),
                    "workout_set_exercise_id": ex.get("workoutSetExerciseId"),
                    "exercise_id": ex.get("exerciseId"),
                    "title": ex.get("title"),
                    "video_url": ex.get("videoURL") or None,
                }
            )
    return out


@tool
def set_prescription(
    program_workout_id: int,
    workout_set_exercise_id: int,
    sets: Annotated[int | None, Field(ge=1, le=10)] = None,
    reps: PerSet = None,
    weight_kg: PerSet = None,
    percent: PerSet = None,
    seconds: PerSet = None,
    meters: PerSet = None,
    rpe: PerSet = None,
    calories: PerSet = None,
    instruction: str | None = None,
    team_id: int | None = None,
) -> dict:
    """Set sets and per-set targets for one exercise of a session you own.

    Each target is one value for all sets or a list with one value per set, e.g.
    sets=4, reps=8, weight_kg=[30, 35, 40, 40]. Reps are whole numbers; write
    "per side" or tempo cues in `instruction`. Only targets the exercise tracks
    are accepted (see `tracks` in get_workout).
    """
    client = api()
    _, details = _fetch_details(program_workout_id, team_id)
    current = w.find_set_exercise(details, workout_set_exercise_id)
    payload = w.build_prescription(
        current,
        sets=sets,
        instruction=instruction,
        reps=reps,
        weight_kg=weight_kg,
        percent=percent,
        seconds=seconds,
        meters=meters,
        rpe=rpe,
        calories=calories,
    )
    client.post(SAVE_SET_EXERCISES, [payload])
    return {
        "workout_set_exercise_id": workout_set_exercise_id,
        "title": current.get("title"),
        "prescribed": w.describe_prescription(payload),
        "instruction": payload.get("instruction") or None,
    }


@tool
def update_session(
    workout_id: int, title: str | None = None, instruction: str | None = None
) -> dict:
    """Rename a session and/or set its notes. Omitted fields stay unchanged."""
    body: dict[str, Any] = {"id": workout_id}
    if title is not None:
        body["title"] = title
    if instruction is not None:
        body["instruction"] = instruction
    saved = api().put(f"/3.0/coach/workout/{workout_id}", body)
    return {
        "workout_id": workout_id,
        "title": saved.get("title"),
        "instruction": saved.get("instruction"),
    }


@tool
def update_block(
    program_workout_id: int,
    block_id: int,
    title: str | None = None,
    instruction: str | None = None,
    team_id: int | None = None,
) -> dict:
    """Rename a block and/or set its instruction (e.g. warm-up circuit, rest times)."""
    _, details = _fetch_details(program_workout_id, team_id)
    payload = w.build_block(w.find_block(details, block_id), title=title, instruction=instruction)
    saved = api().post(SAVE_BLOCK.format(block_id=block_id), payload)
    return {
        "block_id": block_id,
        "title": saved.get("title"),
        "instruction": saved.get("instruction"),
    }


@tool
def delete_session(program_workout_id: int) -> dict:
    """Delete a session from the calendar."""
    message = api().delete(f"/v5/programWorkouts/{program_workout_id}")
    return {"program_workout_id": program_workout_id, "deleted": True, "message": message}


def _insert_into_block(
    block_id: int,
    exercise_id: int,
    order: int,
    *,
    sets: int | None = None,
    instruction: str | None = None,
    **targets: PerSet,
) -> dict:
    current = w.new_set_exercise(_library_entry(exercise_id), block_id, order)
    payload = w.build_prescription(current, sets=sets, instruction=instruction, **targets)
    saved = api().post(SAVE_SET_EXERCISES, [payload])
    row = saved[0] if isinstance(saved, list) and saved else {}
    return {
        "block_id": block_id,
        "workout_set_exercise_id": row.get("id"),
        "exercise_id": exercise_id,
        "title": row.get("title", current["title"]),
        "prescribed": w.describe_prescription(payload),
        "video_url": row.get("video_url") or None,
    }


@tool
def add_to_block(
    program_workout_id: int,
    block_id: int,
    exercise_ids: list[int],
    team_id: int | None = None,
) -> list[dict]:
    """Append library exercises to an existing block, turning it into a superset.

    Set their targets afterwards with set_prescription.
    """
    _, details = _fetch_details(program_workout_id, team_id)
    order = len(w.find_block(details, block_id)["exercises"])
    return [_insert_into_block(block_id, eid, order + i) for i, eid in enumerate(exercise_ids, 1)]


class PlannedExercise(BaseModel):
    exercise_id: int = Field(description="Library exercise id (see search_exercises)")
    sets: int | None = Field(default=None, ge=1, le=10)
    reps: PerSet = None
    weight_kg: PerSet = None
    percent: PerSet = None
    seconds: PerSet = None
    meters: PerSet = None
    rpe: PerSet = None
    calories: PerSet = None
    instruction: str | None = Field(default=None, description="Coaching cue for this exercise")
    superset: str | None = Field(
        default=None,
        description='Exercises with the same label share one block (a superset), e.g. "A"',
    )
    block_title: str | None = Field(
        default=None, description='e.g. "Warm-up", "Main lift"; defaults to the superset label'
    )
    block_instruction: str | None = None

    def targets(self) -> dict[str, PerSet]:
        return {
            k: getattr(self, k)
            for k in ("reps", "weight_kg", "percent", "seconds", "meters", "rpe", "calories")
            if getattr(self, k) is not None
        }


def _group_blocks(exercises: list[PlannedExercise]) -> list[list[PlannedExercise]]:
    """One group per block, in order of first appearance; superset labels merge."""
    groups: list[list[PlannedExercise]] = []
    by_label: dict[str, list[PlannedExercise]] = {}
    for spec in exercises:
        if spec.superset is None:
            groups.append([spec])
        elif spec.superset in by_label:
            by_label[spec.superset].append(spec)
        else:
            by_label[spec.superset] = [spec]
            groups.append(by_label[spec.superset])
    return groups


@tool
def create_workout(
    date: str,
    title: str,
    exercises: list[PlannedExercise],
    instruction: str | None = None,
) -> dict:
    """Create a complete personal session in one call.

    Exercises go into blocks in the given order, each with its prescription and
    cue. Exercises sharing a `superset` label are placed together in one block.
    If any step fails the half-built session is deleted.
    """
    client = api()
    groups = _group_blocks(exercises)
    session = create_session(date, title=title, instruction=instruction)
    block_labels: dict[int, tuple[str | None, str | None]] = {}
    prescribed: dict[int, str] = {}
    try:
        added = add_exercises(session["workout_id"], [g[0].exercise_id for g in groups])
        if len(added) != len(groups):
            raise TrainHeroicError(f"Expected {len(groups)} blocks, API added {len(added)}")
        details = client.get(
            f"/v5/programWorkouts/{session['program_workout_id']}/teams/{session['team_id']}"
        )
        for group, ref in zip(groups, added, strict=True):
            block_id = int(ref["block_id"])
            lead = group[0]
            if lead.targets() or lead.sets or lead.instruction:
                current = w.find_set_exercise(details, int(ref["workout_set_exercise_id"]))
                payload = w.build_prescription(
                    current, sets=lead.sets, instruction=lead.instruction, **lead.targets()
                )
                client.post(SAVE_SET_EXERCISES, [payload])
                prescribed[int(ref["workout_set_exercise_id"])] = w.describe_prescription(payload)
            for order, spec in enumerate(group[1:], start=2):
                member = _insert_into_block(
                    block_id,
                    spec.exercise_id,
                    order,
                    sets=spec.sets,
                    instruction=spec.instruction,
                    **spec.targets(),
                )
                if member["workout_set_exercise_id"]:
                    prescribed[int(member["workout_set_exercise_id"])] = member["prescribed"]
            block_title = next((e.block_title for e in group if e.block_title), lead.superset)
            block_note = next((e.block_instruction for e in group if e.block_instruction), None)
            if block_title is not None or block_note is not None:
                block = w.build_block(
                    w.find_block(details, block_id), title=block_title, instruction=block_note
                )
                client.post(SAVE_BLOCK.format(block_id=block_id), block)
                block_labels[block_id] = (block_title, block_note)
    except Exception:
        log.exception("create_workout failed; removing partial session")
        delete_session(session["program_workout_id"])
        raise
    summary = get_workout(session["program_workout_id"], team_id=session["team_id"])
    # Fresh edits take a moment to show up in reads (and briefly in lb); report
    # what was saved instead.
    for block in summary["blocks"]:
        title_, note = block_labels.get(block["block_id"], (None, None))
        if title_ is not None:
            block["title"] = title_
        if note is not None:
            block["instruction"] = note
        for ex in block["exercises"]:
            ex["prescribed"] = prescribed.get(ex["workout_set_exercise_id"], ex["prescribed"])
    return summary


@tool
def remove_exercise(
    program_workout_id: int, workout_set_exercise_id: int, team_id: int | None = None
) -> dict:
    """Remove one exercise from a session; a block left empty is removed too."""
    _, details = _fetch_details(program_workout_id, team_id)
    for block in w.blocks(details):
        if any(ex["id"] == workout_set_exercise_id for ex in block["exercises"]):
            if len(block["exercises"]) == 1:
                api().post(DELETE_BLOCK.format(block_id=block["id"]))
                return {"removed_exercise": workout_set_exercise_id, "removed_block": block["id"]}
            api().delete(
                DELETE_SET_EXERCISE.format(block_id=block["id"], wse_id=workout_set_exercise_id)
            )
            return {"removed_exercise": workout_set_exercise_id, "removed_block": None}
    raise ValueError(f"Exercise {workout_set_exercise_id} is not part of this workout")


@tool
def remove_block(block_id: int) -> dict:
    """Remove a whole block (all its exercises) from a session."""
    api().post(DELETE_BLOCK.format(block_id=block_id))
    return {"removed_block": block_id}


@tool
def copy_workout(
    program_workout_id: int,
    to_date: str,
    title: str | None = None,
    team_id: int | None = None,
) -> dict:
    """Copy a session's plan to another date on your personal calendar.

    Blocks, supersets, sets/reps/weights, cues and notes are copied; logged
    results are not. Works for coach sessions too, as long as they are visible.
    """
    _, details = _fetch_details(program_workout_id, team_id)
    plan = w.plan_from_details(details)
    return create_workout(
        to_date,
        title or plan["title"] or to_date,
        [PlannedExercise(**spec) for spec in plan["exercises"]],
        instruction=plan["instruction"],
    )


@tool
def move_workout(program_workout_id: int, to_date: str, team_id: int | None = None) -> dict:
    """Reschedule a personal session that has no logged results yet.

    TrainHeroic offers no reschedule call for personal calendars, so the session
    is copied to `to_date` and the original deleted: its ids change.
    """
    _, details = _fetch_details(program_workout_id, team_id)
    if not details.get("personal_cal"):
        raise ValueError("Only personal sessions can be moved; use copy_workout instead")
    if w.has_results(details):
        raise ValueError("This session already has logged results; use copy_workout instead")
    moved = copy_workout(program_workout_id, to_date, team_id=team_id)
    delete_session(program_workout_id)
    return {**moved, "moved_from_program_workout_id": program_workout_id}


def main() -> None:
    logging.basicConfig(level=os.environ.get("TRAINHEROIC_LOG_LEVEL", "WARNING"))
    mcp.run()


if __name__ == "__main__":
    main()
