import asyncio
import json
import re

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from .conftest import PERSONAL_PROGRAM, PERSONAL_TEAM, personal_details, url


def call(tools, name, args):
    """Invoke a tool through the MCP server, like a real client would."""
    result = asyncio.run(tools.mcp.call_tool(name, args))
    return result


def test_all_tools_registered(tools):
    names = {t.name for t in asyncio.run(tools.mcp.list_tools())}
    assert {
        "get_workout_history",
        "get_workout",
        "search_exercises",
        "create_workout",
        "set_prescription",
        "update_block",
        "delete_session",
    } <= names


def test_search_exercises_matches_all_words(tools, httpx_mock):
    httpx_mock.add_response(
        url=url("/v5/users/exercises"),
        json=[
            {
                "id": 1,
                "title": "Trap Bar Deadlift",
                "hasVideo": True,
                "videoUrl": "v1",
                "prescription": "Reps @ Weight (lb)",
            },
            {"id": 2, "title": "Deadlift", "hasVideo": True, "videoUrl": "v2"},
            {"id": 3, "title": "Trap bar carry", "hasVideo": False, "videoUrl": ""},
        ],
    )
    found = tools.search_exercises("bar trap")
    assert [e["exercise_id"] for e in found] == [3, 1]
    assert found[1] == {
        "exercise_id": 1,
        "title": "Trap Bar Deadlift",
        "tracks": "Reps @ Weight (lb)",
        "video_url": "v1",
    }
    assert [e["exercise_id"] for e in tools.search_exercises("trap", with_video_only=True)] == [1]


def test_history_learns_personal_team(tools, httpx_mock, client):
    httpx_mock.add_response(url=url("/3.0/athlete/programworkout/range"), json=[personal_details()])
    rows = tools.get_workout_history("2026-10-19", "2026-10-25")
    assert rows[0]["program_workout_id"] == 10
    assert client.personal_team_id == PERSONAL_TEAM


def test_get_workout_falls_back_through_teams(tools, httpx_mock, client):
    # Personal team is tried first, the session belongs to the coach team.
    httpx_mock.add_response(
        url=url(f"/v5/programWorkouts/10/teams/{PERSONAL_TEAM}"), status_code=401
    )
    coach_session = {**personal_details(), "program_id": 600, "team_id": 500, "personal_cal": False}
    httpx_mock.add_response(url=url("/v5/programWorkouts/10/teams/500"), json=coach_session)
    client.remember_team(PERSONAL_PROGRAM, PERSONAL_TEAM, personal=True)

    result = tools.get_workout(10)
    assert result["title"] == "Strength 1.1"
    assert result["blocks"][0]["exercises"][0]["prescribed"] == "3 x 8 @ 30, 35, 40 kg"
    assert client.candidate_teams(600)[0] == 500


def test_set_prescription_posts_full_object(tools, httpx_mock):
    httpx_mock.add_response(
        url=url(f"/v5/programWorkouts/10/teams/{PERSONAL_TEAM}"), json=personal_details()
    )
    httpx_mock.add_response(
        method="POST",
        url=url("/2.0/coach/calendar/saveWorkoutSetExercises"),
        json=[
            {
                "id": 101,
                "title": "Trap Bar Deadlift",
                "abr": "4 x 5 @ 88.18 lb",
                "instruction": "heavy",
            }
        ],
    )
    out = tools.set_prescription(
        10, 101, sets=4, reps=5, weight_kg=40, instruction="heavy", team_id=PERSONAL_TEAM
    )
    assert out["prescribed"] == "4 x 5 @ 40 kg"
    body = json.loads(
        httpx_mock.get_requests(url=url("/2.0/coach/calendar/saveWorkoutSetExercises"))[0].content
    )
    assert body[0]["id"] == 101 and body[0]["param_count"] == 4
    assert body[0]["param_2_data_4"] == "88.18" and body[0]["param_2_data_5"] == ""


def test_set_prescription_validation_error_reaches_client(tools, httpx_mock):
    httpx_mock.add_response(
        url=url(f"/v5/programWorkouts/10/teams/{PERSONAL_TEAM}"), json=personal_details()
    )
    # ToolError (unlike an unexpected crash) carries its message to the model.
    with pytest.raises(ToolError, match="Bird Dog tracks reps; cannot set weight_kg"):
        call(
            tools,
            "set_prescription",
            {
                "program_workout_id": 10,
                "workout_set_exercise_id": 102,
                "reps": 10,
                "weight_kg": 5,
                "team_id": PERSONAL_TEAM,
            },
        )


def _mock_create(httpx_mock, exercises: int = 2):
    httpx_mock.add_response(
        method="POST",
        url=url("/v5/programWorkouts/personal"),
        json={
            "programWorkout": {
                "id": 10,
                "programId": PERSONAL_PROGRAM,
                "date": "2026-10-21",
                "workoutId": 20,
            },
            "savedWorkout": {"id": 30, "group_id": PERSONAL_TEAM},
        },
    )
    httpx_mock.add_response(
        method="PUT",
        url=url("/3.0/coach/workout/20"),
        json={"title": "Strength 1.1", "instruction": "Easy week"},
    )
    httpx_mock.add_response(
        method="PUT",
        url=url("/v5/personalCalendar/workouts/20/addExercises"),
        json=[
            {
                "workoutSetId": 201,
                "savedWorkoutSetExercises": [
                    {
                        "workoutSetExerciseId": 101,
                        "exerciseId": 68077,
                        "title": "Trap Bar Deadlift",
                        "videoURL": "https://youtu.be/68077",
                    }
                ],
            },
            {
                "workoutSetId": 202,
                "savedWorkoutSetExercises": [
                    {
                        "workoutSetExerciseId": 102,
                        "exerciseId": 3870,
                        "title": "Bird Dog",
                        "videoURL": "https://youtu.be/3870",
                    }
                ],
            },
        ][:exercises],
    )


def test_create_workout_end_to_end(tools, httpx_mock):
    _mock_create(httpx_mock)
    httpx_mock.add_response(
        url=url(f"/v5/programWorkouts/10/teams/{PERSONAL_TEAM}"),
        json=personal_details(),
        is_reusable=True,
    )
    httpx_mock.add_response(
        method="POST",
        url=url("/2.0/coach/calendar/saveWorkoutSetExercises"),
        json=[{}],
        is_reusable=True,
    )
    httpx_mock.add_response(
        method="POST",
        url=url("/2.0/coach/calendar/workout/workoutSet/save/202"),
        json={"title": "Core", "instruction": "slow"},
    )

    result = call(
        tools,
        "create_workout",
        {
            "date": "2026-10-21",
            "title": "Strength 1.1",
            "instruction": "Easy week",
            "exercises": [
                {"exercise_id": 68077, "reps": 8, "weight_kg": [30, 35, 40], "instruction": "ramp"},
                {
                    "exercise_id": 3870,
                    "sets": 2,
                    "reps": 8,
                    "instruction": "per side",
                    "block_title": "Core",
                    "block_instruction": "slow",
                },
            ],
        },
    )

    assert result is not None
    saves = httpx_mock.get_requests(url=url("/2.0/coach/calendar/saveWorkoutSetExercises"))
    assert [json.loads(r.content)[0]["id"] for r in saves] == [101, 102]
    block = json.loads(
        httpx_mock.get_requests(url=url("/2.0/coach/calendar/workout/workoutSet/save/202"))[
            0
        ].content
    )
    assert block["title"] == "Core" and block["instruction"] == "slow"
    assert not httpx_mock.get_requests(method="DELETE")


def test_create_workout_rolls_back_on_failure(tools, httpx_mock):
    _mock_create(httpx_mock, exercises=1)
    httpx_mock.add_response(
        url=url(f"/v5/programWorkouts/10/teams/{PERSONAL_TEAM}"), json=personal_details()
    )
    httpx_mock.add_response(
        method="POST", url=url("/2.0/coach/calendar/saveWorkoutSetExercises"), status_code=500
    )
    httpx_mock.add_response(
        method="DELETE", url=url("/v5/programWorkouts/10"), text="Program Workout deleted"
    )
    with pytest.raises(ToolError, match="failed with 500"):
        tools.create_workout(
            "2026-10-21",
            "Strength 1.1",
            [tools.PlannedExercise(exercise_id=68077, reps=8, weight_kg=30)],
        )
    assert httpx_mock.get_requests(method="DELETE")


def test_submit_survey_resolves_answer_id(tools, httpx_mock):
    from .conftest import survey

    surveys_url = url("/3.0/athlete/savedworkout/surveys")
    httpx_mock.add_response(url=surveys_url, json={"30": survey()})
    httpx_mock.add_response(
        method="POST", url=url("/3.0/athlete/survey/question/10"), json={"id": 1}
    )
    httpx_mock.add_response(url=surveys_url, json={"30": survey([41])})

    out = call(tools, "submit_survey", {"saved_workout_id": 30, "question": "energy", "score": 4})

    body = json.loads(
        httpx_mock.get_requests(url=url("/3.0/athlete/survey/question/10"))[0].content
    )
    assert body == {"saved_workout_id": 30, "survey_question_answer_id": 41}
    assert out is not None


LIBRARY = [
    {"id": 68077, "title": "Trap Bar Deadlift", "param1Type": 3, "param2Type": 1},
    {"id": 3870, "title": "Bird Dog", "param1Type": 3, "param2Type": None},
    {
        "id": 1971,
        "title": "Lat Pulldown",
        "param1Type": 3,
        "param2Type": 1,
        "videoUrl": "https://youtu.be/1971",
    },
]


def test_create_workout_groups_supersets_into_one_block(tools, httpx_mock):
    _mock_create(httpx_mock)
    httpx_mock.add_response(url=url("/v5/users/exercises"), json=LIBRARY)
    httpx_mock.add_response(
        url=url(f"/v5/programWorkouts/10/teams/{PERSONAL_TEAM}"),
        json=personal_details(),
        is_reusable=True,
    )
    httpx_mock.add_response(
        method="POST",
        url=url("/2.0/coach/calendar/saveWorkoutSetExercises"),
        json=[{"id": 555, "title": "Lat Pulldown", "video_url": "https://youtu.be/1971"}],
        is_reusable=True,
    )
    httpx_mock.add_response(
        method="POST",
        url=url("/2.0/coach/calendar/workout/workoutSet/save/201"),
        json={"title": "A"},
    )

    result = tools.create_workout(
        "2026-10-21",
        "Strength 1.1",
        [
            tools.PlannedExercise(exercise_id=68077, reps=8, weight_kg=30, superset="A"),
            tools.PlannedExercise(exercise_id=3870, sets=2, reps=8),
            tools.PlannedExercise(exercise_id=1971, reps=10, weight_kg=[25, 30, 30], superset="A"),
        ],
    )

    added = json.loads(
        httpx_mock.get_requests(url=url("/v5/personalCalendar/workouts/20/addExercises"))[0].content
    )
    assert [e["exerciseId"] for e in added["exercises"]] == [68077, 3870]
    saves = [
        json.loads(r.content)[0]
        for r in httpx_mock.get_requests(url=url("/2.0/coach/calendar/saveWorkoutSetExercises"))
    ]
    member = next(s for s in saves if s["exercise_id"] == 1971)
    assert "id" not in member and member["workout_set_id"] == 201 and member["order"] == 2
    assert member["param_2_data_2"] == "66.14"
    block = json.loads(
        httpx_mock.get_requests(url=url("/2.0/coach/calendar/workout/workoutSet/save/201"))[
            0
        ].content
    )
    assert block["title"] == "A"
    assert result["blocks"][0]["title"] == "A"
    # Reads lag behind writes, so the result reports the saved prescription.
    assert result["blocks"][0]["exercises"][0]["prescribed"] == "1 x 8 @ 30 kg"


def test_add_to_block_appends_after_existing_exercises(tools, httpx_mock):
    httpx_mock.add_response(url=url("/v5/users/exercises"), json=LIBRARY)
    httpx_mock.add_response(
        url=url(f"/v5/programWorkouts/10/teams/{PERSONAL_TEAM}"), json=personal_details()
    )
    httpx_mock.add_response(
        method="POST",
        url=url("/2.0/coach/calendar/saveWorkoutSetExercises"),
        json=[{"id": 777, "title": "Lat Pulldown", "video_url": "https://youtu.be/1971"}],
    )
    out = tools.add_to_block(10, 201, [1971], team_id=PERSONAL_TEAM)
    assert out == [
        {
            "block_id": 201,
            "workout_set_exercise_id": 777,
            "exercise_id": 1971,
            "title": "Lat Pulldown",
            "prescribed": "1 set",
            "video_url": "https://youtu.be/1971",
        }
    ]
    body = json.loads(
        httpx_mock.get_requests(url=url("/2.0/coach/calendar/saveWorkoutSetExercises"))[0].content
    )
    assert body[0]["order"] == 2 and "id" not in body[0]


def _details_mock(httpx_mock, details=None, reusable=False):
    httpx_mock.add_response(
        url=url(f"/v5/programWorkouts/10/teams/{PERSONAL_TEAM}"),
        json=details or personal_details(),
        is_reusable=reusable,
    )


def test_remove_exercise_from_superset_deletes_only_that_exercise(tools, httpx_mock, client):
    details = personal_details()
    saved_blocks = details["summarizedSavedWorkout"]["saved_workout"]["workoutSets"]
    saved_blocks[0]["workoutSetExercises"].append(saved_blocks.pop()["workoutSetExercises"][0])
    del details["workout"]
    client.remember_team(PERSONAL_PROGRAM, PERSONAL_TEAM, personal=True)
    _details_mock(httpx_mock, details)
    httpx_mock.add_response(
        method="DELETE",
        url=url("/2.0/coach/calendar/workout/workoutSetExercise/201/102"),
        json={"id": 201},
    )
    assert tools.remove_exercise(10, 102) == {"removed_exercise": 102, "removed_block": None}


def test_remove_last_exercise_removes_block(tools, httpx_mock, client):
    client.remember_team(PERSONAL_PROGRAM, PERSONAL_TEAM, personal=True)
    _details_mock(httpx_mock)
    httpx_mock.add_response(
        method="POST",
        url=url("/2.0/coach/calendar/workout/workoutSet/delete/202"),
        text="Workout Set Deleted",
    )
    assert tools.remove_exercise(10, 102) == {"removed_exercise": 102, "removed_block": 202}


def test_move_refuses_sessions_with_results(tools, httpx_mock, client):
    details = personal_details()
    details["summarizedSavedWorkout"]["saved_workout"]["completed"] = "1"
    client.remember_team(PERSONAL_PROGRAM, PERSONAL_TEAM, personal=True)
    _details_mock(httpx_mock, details)
    with pytest.raises(ToolError, match="already has logged results"):
        tools.move_workout(10, "2026-10-24")
    assert not httpx_mock.get_requests(method="DELETE")


def test_copy_workout_recreates_plan(tools, httpx_mock, client):
    client.remember_team(PERSONAL_PROGRAM, PERSONAL_TEAM, personal=True)
    _details_mock(httpx_mock, reusable=True)  # source and the new session share the mock
    _mock_create(httpx_mock)
    httpx_mock.add_response(
        method="POST",
        url=url("/2.0/coach/calendar/saveWorkoutSetExercises"),
        json=[{}],
        is_reusable=True,
    )
    httpx_mock.add_response(
        method="POST", url=re.compile(r".*/workoutSet/save/\d+$"), json={}, is_reusable=True
    )
    tools.copy_workout(10, "2026-10-28")
    created = json.loads(
        httpx_mock.get_requests(method="POST", url=url("/v5/programWorkouts/personal"))[0].content
    )
    assert created == {"date": "2026-10-28"}
    lift = json.loads(
        httpx_mock.get_requests(url=url("/2.0/coach/calendar/saveWorkoutSetExercises"))[0].content
    )[0]
    assert [lift[f"param_2_data_{i}"] for i in (1, 2, 3)] == ["66.14", "77.16", "88.18"]
