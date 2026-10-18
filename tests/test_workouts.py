import pytest

from trainheroic_mcp import workouts as w
from trainheroic_mcp.units import ParamType, format_number, kg_to_lb, lb_to_kg

from .conftest import personal_details, set_exercise


def test_kg_lb_round_trip():
    assert kg_to_lb(30) == 66.14
    assert kg_to_lb(20) == 44.09
    assert lb_to_kg(110.23) == 50.0


@pytest.mark.parametrize(
    ("value", "expected"),
    [(8, "8"), (66.14, "66.14"), (40.0, "40"), (2.5, "2.5"), ("8-10", "8-10")],
)
def test_format_number(value, expected):
    assert format_number(value) == expected


def test_param_type_parse_tolerates_api_noise():
    assert ParamType.parse("3") is ParamType.REPS
    assert ParamType.parse(None) is ParamType.NONE
    assert ParamType.parse("None") is ParamType.NONE


class TestBuildPrescription:
    def test_ramp_converts_kg_to_lb_and_clears_unused_sets(self):
        current = set_exercise(101, 201, 68077, "Trap Bar Deadlift", 3, 1)
        current["param_1_data_5"] = "5"  # leftover from an older prescription

        p = w.build_prescription(current, reps=8, weight_kg=[30, 35, 40], instruction="ramp")

        assert p["param_count"] == 3
        assert [p[f"param_1_data_{i}"] for i in range(1, 6)] == ["8", "8", "8", "", ""]
        assert [p[f"param_2_data_{i}"] for i in range(1, 4)] == ["66.14", "77.16", "88.18"]
        assert p["workout_set_id"] == "201"
        assert p["exercise_id"] == "68077"
        assert p["instruction"] == "ramp"
        assert p["no_sets"] == 0

    def test_explicit_sets_broadcast_scalar(self):
        current = set_exercise(102, 202, 3870, "Bird Dog", 3, None)
        p = w.build_prescription(current, sets=3, reps=8)
        assert [p[f"param_1_data_{i}"] for i in range(1, 4)] == ["8"] * 3
        assert p["param_2_data_1"] == ""

    def test_rep_ranges_only_for_rep_range_exercises(self):
        ranged = set_exercise(104, 204, 2, "Ranged", ParamType.REP_RANGE, None)
        p = w.build_prescription(ranged, sets=2, reps="8-10")
        assert p["param_1_data_2"] == "8-10"

        plain = set_exercise(102, 202, 3870, "Bird Dog", 3, None)
        with pytest.raises(ValueError, match="need a rep-range exercise"):
            w.build_prescription(plain, sets=2, reps="8-10")

    @pytest.mark.parametrize("bad", ["8/8", "8 each", 7.5, -1])
    def test_rejects_values_the_api_would_mangle(self, bad):
        current = set_exercise(102, 202, 3870, "Bird Dog", 3, None)
        with pytest.raises(ValueError):
            w.build_prescription(current, sets=2, reps=bad)

    def test_kg_typed_weight_is_not_converted(self):
        current = set_exercise(103, 203, 1, "Kg lift", ParamType.REPS, ParamType.WEIGHT_KG)
        p = w.build_prescription(current, sets=2, reps=5, weight_kg=50)
        assert p["param_2_data_1"] == "50"

    def test_rejects_targets_the_exercise_does_not_track(self):
        current = set_exercise(102, 202, 3870, "Bird Dog", 3, None)
        with pytest.raises(ValueError, match="Bird Dog tracks reps; cannot set weight_kg"):
            w.build_prescription(current, reps=10, weight_kg=5)

    def test_rejects_mismatched_list_lengths(self):
        current = set_exercise(101, 201, 68077, "Trap Bar Deadlift", 3, 1)
        with pytest.raises(ValueError, match="different lengths"):
            w.build_prescription(current, reps=[8, 8], weight_kg=[30, 35, 40])
        with pytest.raises(ValueError, match="3 values for 4 sets"):
            w.build_prescription(current, sets=4, weight_kg=[30, 35, 40])

    def test_rejects_too_many_sets(self):
        current = set_exercise(102, 202, 3870, "Bird Dog", 3, None)
        with pytest.raises(ValueError, match="1-10 sets"):
            w.build_prescription(current, sets=11, reps=5)


@pytest.mark.parametrize("with_template", [True, False])
def test_summarize_workout_joins_prescription_and_saved_copy(with_template):
    s = w.summarize_workout(personal_details(with_template=with_template))

    assert s["title"] == "Strength 1.1"
    assert s["instruction"] == "Easy week"
    assert s["personal"] is True
    assert s["saved_workout_id"] == 30
    first = s["blocks"][0]["exercises"][0]
    assert first == {
        "workout_set_exercise_id": 101,
        "exercise_id": 68077,
        "title": "Trap Bar Deadlift",
        "prescribed": "3 x 8 @ 30, 35, 40 kg",
        "results": None,
        "completed": False,
        "instruction": "ramp",
        "tracks": ["reps", "weight"],
        "video_url": "https://youtu.be/68077",
    }
    assert s["blocks"][1]["exercises"][0]["tracks"] == ["reps"]


def test_describe_set_exercise_lists_each_set():
    d = w.describe_set_exercise(personal_details(), 101)
    assert d["units"] == "kg"
    assert d["sets"] == [
        {"set": 1, "reps": "8", "weight": "30", "made": False},
        {"set": 2, "reps": "8", "weight": "35", "made": False},
        {"set": 3, "reps": "8", "weight": "40", "made": False},
    ]
    assert d["athlete_notes"] is None


@pytest.mark.parametrize("with_template", [True, False])
def test_set_exercise_has_everything_the_save_endpoint_needs(with_template):
    ex = w.find_set_exercise(personal_details(with_template=with_template), 101)
    payload = w.build_prescription(ex, reps=5, weight_kg=40)
    assert payload["id"] == 101 and payload["workout_set_id"] == 201
    assert payload["exercise_id"] == 68077 and payload["reference_max_exercise_id"] == 68077
    assert payload["title"] == "Trap Bar Deadlift" and payload["instruction"] == "ramp"
    assert payload["param_2_data_1"] == "88.18"
    assert "saved" not in payload and "template" not in payload


@pytest.mark.parametrize("with_template", [True, False])
def test_build_block_keeps_required_fields(with_template):
    block = w.find_block(personal_details(with_template=with_template), 201)
    payload = w.build_block(block, title="Main lift", instruction=None)
    assert payload["id"] == 201 and payload["workout_id"] == 20
    assert payload["title"] == "Main lift" and payload["instruction"] == ""
    assert payload["plain_text"] == "0" and payload["order"] == "1"
    assert "exercises" not in payload and "saved" not in payload


def test_summarize_history_item():
    item = personal_details()
    row = w.summarize_history_item(item)
    assert row["team_id"] == 900 and row["personal"] is True and row["saved_workout_id"] == 30


@pytest.mark.parametrize(
    ("p1", "p2", "inputs", "expected"),
    [
        (3, 1, {"reps": 5, "weight_kg": [30, 35, 40, 40]}, "4 x 5 @ 30, 35, 40, 40 kg"),
        (3, 1, {"sets": 3, "reps": 10, "weight_kg": 30}, "3 x 10 @ 30 kg"),
        (3, None, {"sets": 2, "reps": 8}, "2 x 8"),
        (4, None, {"sets": 1, "seconds": 600}, "1 x 600 s"),
        (3, 2, {"reps": 3, "percent": [70, 75, 80]}, "3 x 3 @ 70, 75, 80%"),
    ],
)
def test_describe_prescription(p1, p2, inputs, expected):
    current = set_exercise(1, 2, 3, "X", p1, p2)
    assert w.describe_prescription(w.build_prescription(current, **inputs)) == expected


def test_survey_answers_map_to_scores_and_back():
    from .conftest import survey

    assert w.survey_answer_id(survey(), "sleep", 4) == (8, 31)
    assert w.survey_answer_id(survey(), "Soreness", 1) == (12, 48)
    assert w.summarize_survey(survey([31, 37])) == {
        "answers": {
            "sleep": {"score": 4, "label": "Sleep 4"},
            "mood": {"score": 5, "label": "Mood 5"},
        },
        "average": 4,
    }
    with pytest.raises(ValueError, match="no question 'hunger'"):
        w.survey_answer_id(survey(), "hunger", 3)


def test_new_set_exercise_is_posted_without_id():
    entry = {"id": 1971, "title": "Lat Pulldown", "param1Type": 3, "param2Type": 1}
    current = w.new_set_exercise(entry, block_id=201, order=2)
    payload = w.build_prescription(current, reps=10, weight_kg=[25, 30, 30])
    assert "id" not in payload
    assert payload["workout_set_id"] == 201 and payload["order"] == 2
    assert payload["exercise_id"] == 1971 and payload["reference_max_exercise_id"] == 1971
    assert w.describe_prescription(payload) == "3 x 10 @ 25, 30, 30 kg"


@pytest.mark.parametrize("with_template", [True, False])
def test_plan_from_details_round_trips_targets_in_kg(with_template):
    plan = w.plan_from_details(personal_details(with_template=with_template))
    assert plan["title"] == "Strength 1.1" and plan["instruction"] == "Easy week"
    deadlift, bird_dog = plan["exercises"]
    assert deadlift["exercise_id"] == 68077 and deadlift["sets"] == 3
    assert [float(v) for v in deadlift["weight_kg"]] == [30, 35, 40]
    assert [int(v) for v in deadlift["reps"]] == [8, 8, 8]
    assert deadlift["instruction"] == "ramp" and deadlift["block_title"] == "Strength/Power"
    assert "superset" not in deadlift
    assert bird_dog == {"exercise_id": 3870, "block_title": "Strength/Power", "sets": 1}


def test_plan_from_details_keeps_supersets_together():
    details = personal_details()
    saved_blocks = details["summarizedSavedWorkout"]["saved_workout"]["workoutSets"]
    saved_blocks[0]["workoutSetExercises"].append(saved_blocks.pop()["workoutSetExercises"][0])
    details["workout"]["workoutSets"][0]["workoutSetExercises"].append(
        details["workout"]["workoutSets"].pop()["workoutSetExercises"][0]
    )
    first, second = w.plan_from_details(details)["exercises"]
    assert first["superset"] == second["superset"] == "block-1"
    assert "block_title" not in second


def test_has_results():
    details = personal_details()
    assert w.has_results(details) is False
    saved = details["summarizedSavedWorkout"]["saved_workout"]
    saved["workoutSets"][0]["workoutSetExercises"][0]["param_1_made"] = "1"
    assert w.has_results(details) is True


class TestKeepPlannedTargets:
    def test_changing_weight_keeps_reps_and_set_count(self):
        ex = w.find_set_exercise(personal_details(), 101)
        p = w.build_prescription(ex, weight_kg=[40, 42.5, 45])
        assert [p[f"param_1_data_{i}"] for i in (1, 2, 3)] == ["8", "8", "8"]
        assert p["param_count"] == 3

    def test_more_sets_repeat_the_last_planned_value(self):
        ex = w.find_set_exercise(personal_details(), 101)
        p = w.build_prescription(ex, sets=4)
        assert [p[f"param_2_data_{i}"] for i in (1, 2, 3, 4)] == [
            "66.14",
            "77.16",
            "88.18",
            "88.18",
        ]

    def test_without_template_unchanged_targets_are_left_blank(self):
        ex = w.find_set_exercise(personal_details(with_template=False), 101)
        p = w.build_prescription(ex, sets=3, weight_kg=40)
        assert p["param_1_data_1"] == ""  # the saved copy is never trusted as a plan


def test_copy_refuses_logged_session_without_plan():
    details = personal_details(with_template=False)
    saved = details["summarizedSavedWorkout"]["saved_workout"]
    saved["workoutSets"][0]["workoutSetExercises"][0]["param_1_made"] = "1"
    with pytest.raises(ValueError, match="logged results"):
        w.plan_from_details(details)


def test_copy_of_logged_session_uses_the_plan_not_the_results():
    details = personal_details()
    saved_ex = details["summarizedSavedWorkout"]["saved_workout"]["workoutSets"][0][
        "workoutSetExercises"
    ][0]
    saved_ex.update({"param_1_made": "1", "param_2_data_1": "50", "completed": "1"})
    deadlift = w.plan_from_details(details)["exercises"][0]
    assert [float(v) for v in deadlift["weight_kg"]] == [30, 35, 40]
