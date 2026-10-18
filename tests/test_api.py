import json

import pytest

from trainheroic_mcp.api import TrainHeroicAPI, TrainHeroicError

from .conftest import url


def test_requires_credentials(config_dir):
    with pytest.raises(TrainHeroicError, match="TRAINHEROIC_SESSION_TOKEN"):
        TrainHeroicAPI(config_dir=config_dir)


def test_sends_session_token_and_mobile_headers(client, httpx_mock):
    httpx_mock.add_response(url=url("/v5/users/exercises/recent"), json=[])
    client.get("/v5/users/exercises/recent")
    req = httpx_mock.get_requests(url=url("/v5/users/exercises/recent"))[0]
    assert req.headers["session-token"] == "tok"
    assert req.headers["user-agent"].startswith("trainheroic-mcp/")
    assert "origin" not in req.headers


def test_plain_text_responses_are_returned_as_text(client, httpx_mock):
    httpx_mock.add_response(
        method="DELETE", url=url("/v5/programWorkouts/1"), text="Program Workout deleted"
    )
    assert client.delete("/v5/programWorkouts/1") == "Program Workout deleted"


def test_html_error_pages_are_summarized(client, httpx_mock):
    httpx_mock.add_response(
        method="POST",
        url=url("/2.0/coach/calendar/saveWorkoutSetExercises"),
        status_code=500,
        text="<!DOCTYPE html><html><head><title>Server Error</title></head>" + "x" * 5000,
    )
    with pytest.raises(TrainHeroicError) as err:
        client.post("/2.0/coach/calendar/saveWorkoutSetExercises", [])
    assert err.value.status_code == 500
    assert "HTML error page: Server Error" in str(err.value)
    assert len(str(err.value)) < 200


def test_login_caches_token(config_dir, httpx_mock):
    httpx_mock.add_response(method="POST", url=url("/auth"), json={"session_id": "fresh"})
    httpx_mock.add_response(url=url("/v5/users/exercises/recent"), json=[])

    api = TrainHeroicAPI(email="a@b.c", password="pw", config_dir=config_dir)
    api.get("/v5/users/exercises/recent")

    assert json.loads((config_dir / "token.json").read_text()) == {"session_token": "fresh"}
    assert (config_dir / "token.json").stat().st_mode & 0o777 == 0o600
    login = httpx_mock.get_requests(url=url("/auth"))[0]
    assert json.loads(login.content) == {"email": "a@b.c", "password": "pw"}


def test_expired_token_triggers_one_relogin(config_dir, httpx_mock):
    httpx_mock.add_response(url=url("/v5/users/exercises/recent"), status_code=401)
    httpx_mock.add_response(url=url("/v5/user"), status_code=401)
    httpx_mock.add_response(method="POST", url=url("/auth"), json={"session_id": "fresh"})
    httpx_mock.add_response(url=url("/v5/users/exercises/recent"), json=["ok"])

    api = TrainHeroicAPI(email="a@b.c", password="pw", token="old", config_dir=config_dir)
    assert api.get("/v5/users/exercises/recent") == ["ok"]
    retry = httpx_mock.get_requests(url=url("/v5/users/exercises/recent"))[-1]
    assert retry.headers["session-token"] == "fresh"


def test_forbidden_team_does_not_relogin(config_dir, httpx_mock):
    httpx_mock.add_response(url=url("/v5/programWorkouts/1/teams/5"), status_code=401)
    httpx_mock.add_response(url=url("/v5/user"), json={"id": 1})

    api = TrainHeroicAPI(email="a@b.c", password="pw", token="good", config_dir=config_dir)
    with pytest.raises(TrainHeroicError) as err:
        api.get("/v5/programWorkouts/1/teams/5")
    assert err.value.status_code == 401
    assert not httpx_mock.get_requests(url=url("/auth"))


def test_teams_and_remembered_personal_team(client, config_dir):
    assert client.teams[0]["team_id"] == 500 and client.teams[0]["licensed"] is False
    assert client.candidate_teams() == [500]

    client.remember_team(901, 900, personal=True)
    assert client.candidate_teams(901) == [900, 500]

    reloaded = TrainHeroicAPI(token="tok", config_dir=config_dir)
    assert reloaded.personal_team_id == 900


def test_library_is_cached_on_disk(client, httpx_mock, config_dir):
    httpx_mock.add_response(url=url("/v5/users/exercises"), json=[{"id": 1, "title": "Squat"}])
    assert client.exercise_library() == [{"id": 1, "title": "Squat"}]
    assert client.exercise_library() == [{"id": 1, "title": "Squat"}]
    assert len(httpx_mock.get_requests(url=url("/v5/users/exercises"))) == 1
    assert (config_dir / "cache" / "exercises.json").exists()
