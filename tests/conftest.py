import re

import pytest

from trainheroic_mcp import server
from trainheroic_mcp.api import BASE_URL, TrainHeroicAPI

BASE = BASE_URL

PROFILE = {"id": 1001, "nameFirst": "Test", "nameLast": "Athlete"}
LICENSES = {
    "licenses": {
        "team": [],
        "team_share": [],
        "unlicensed": [
            {"id": 500, "programId": 600, "title": "Coach Team", "coaches": [{"title": "Coach"}]}
        ],
    }
}
PERSONAL_TEAM = 900
PERSONAL_PROGRAM = 901


def url(path: str) -> re.Pattern:
    """Match an API path regardless of query string."""
    return re.compile(re.escape(BASE + path) + r"(\?.*)?$")


def set_exercise(wse_id: int, set_id: int, exercise_id: int, title: str, p1: int, p2: int | None):
    ex = {
        "id": str(wse_id),
        "set_id": str(set_id),
        "exercise_id": str(exercise_id),
        "order": "1",
        "title": title,
        "instruction": "",
        "no_sets": "0",
        "param_1_type": str(p1),
        "param_2_type": str(p2) if p2 is not None else "None",
        "param_count": "1",
        "reference_max_exercise_id": str(exercise_id),
        "workout_set_exercise_template_id": "None",
        "video_url": f"https://youtu.be/{exercise_id}",
    }
    for i in range(1, 11):
        ex[f"param_1_data_{i}"] = ""
        ex[f"param_2_data_{i}"] = ""
    return ex


def saved_exercise(template: dict, saved_id: int, **extra) -> dict:
    """The athlete's copy of a set exercise: same params, display units, `made` flags."""
    ex = {k: v for k, v in template.items() if k not in ("id", "set_id", "title")}
    ex.update(
        {
            "id": str(saved_id),
            "workout_set_exercise_id": template["id"],
            "exercise_title": template["title"],
            "abr": "",
            "abr_results": "",
            "completed": "0",
            "notes": "None",
        }
    )
    for i in range(1, 11):
        ex[f"param_{i}_made"] = "0"
    ex.update(extra)
    return ex


def personal_details(pw_id=10, workout_id=20, saved_id=30, with_template=True):
    """Shape of GET /v5/programWorkouts/{id}/teams/{team} for a personal session.

    The ``workout`` template is not always returned by the API (e.g. right after a
    session is created), so tests cover both shapes.
    """
    deadlift = set_exercise(101, 201, 68077, "Trap Bar Deadlift", 3, 1)
    deadlift.update({"param_count": "3", "instruction": "ramp"})
    for i, lb in enumerate(["66.14", "77.16", "88.18"], 1):
        deadlift[f"param_1_data_{i}"] = "8"
        deadlift[f"param_2_data_{i}"] = lb
    bird_dog = set_exercise(102, 202, 3870, "Bird Dog", 3, None)

    saved_deadlift = saved_exercise(deadlift, 301, abr="3 x 8 @ 30, 35, 40 kg")
    for i, kg in enumerate(["30", "35", "40"], 1):
        saved_deadlift[f"param_2_data_{i}"] = kg
    saved_bird_dog = saved_exercise(bird_dog, 302, abr="1 set")

    def saved_block(saved_block_id, set_id, order, ex):
        return {
            "id": str(saved_block_id),
            "workout_set_id": str(set_id),
            "title": "Strength/Power",
            "order": str(order),
            "phase": "0",
            "instruction": "",
            "plain_text": "0",
            "workoutSetExercises": [ex],
        }

    def block(set_id, order, ex):
        return {
            "id": str(set_id),
            "workout_id": str(workout_id),
            "title": "Strength/Power",
            "phase": "0",
            "order": str(order),
            "instruction": "",
            "plain_text": "0",
            "type": "4",
            "is_test": "0",
            "workoutSetExercises": [ex],
            "exercises": [ex],
        }

    details = {
        "id": pw_id,
        "program_id": PERSONAL_PROGRAM,
        "workout_id": workout_id,
        "workout_title": "Strength 1.1",
        "date": "2026-10-21",
        "personal_cal": True,
        "team_id": PERSONAL_TEAM,
        "team_title": "Test Athlete",
        "summarizedSavedWorkout": {
            "saved_workout": {
                "id": str(saved_id),
                "workout_id": str(workout_id),
                "title": "Strength 1.1",
                "instruction": "Easy week",
                "use_metric": "1",
                "completed": "0",
                "rpe": "None",
                "notes": "",
                "workoutSets": [
                    saved_block(401, 201, 1, saved_deadlift),
                    saved_block(402, 202, 2, saved_bird_dog),
                ],
            }
        },
    }
    if with_template:
        details["workout"] = {
            "id": str(workout_id),
            "title": "Strength 1.1",
            "instruction": "Easy week",
            "workoutSets": [block(201, 1, deadlift), block(202, 2, bird_dog)],
        }
    return details


@pytest.fixture
def config_dir(tmp_path):
    return tmp_path / "config"


@pytest.fixture
def client(config_dir, httpx_mock):
    httpx_mock.add_response(url=url("/v5/user"), json=PROFILE, is_optional=True, is_reusable=True)
    httpx_mock.add_response(
        url=url("/1.0/athlete/userlicense/"), json=LICENSES, is_optional=True, is_reusable=True
    )
    return TrainHeroicAPI(token="tok", config_dir=config_dir)


@pytest.fixture
def tools(client, monkeypatch):
    monkeypatch.setattr(server, "_api", client)
    return server


def survey(answer_ids=()):
    """Shape of one entry of GET /3.0/athlete/savedworkout/surveys."""
    titles = {8: "Sleep", 9: "Mood", 10: "Energy", 11: "Stress", 12: "Soreness"}
    questions = []
    for qid, title in titles.items():
        first = 28 + (qid - 8) * 5
        questions.append(
            {
                "id": qid,
                "title": title,
                "survey_question_answers": [
                    {"id": first + i, "value": str(i + 1), "label": f"{title} {i + 1}"}
                    for i in range(5)
                ],
            }
        )
    return {
        "survey": {"id": 2, "title": "Pre-Workout Survey", "survey_questions": questions},
        "user_survey_answers": [{"survey_question_answer_id": a} for a in answer_ids],
        "average_survey_response_value": 4 if answer_ids else None,
    }
