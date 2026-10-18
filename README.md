# trainheroic-mcp

An unofficial [Model Context Protocol](https://modelcontextprotocol.io) server for
[TrainHeroic](https://www.trainheroic.com). It lets Claude (or any MCP client) read
your training history and **plan complete workouts on your personal calendar**:
exercises from the TrainHeroic library (each with its video and technique notes),
sets, reps, per-set weights, supersets, session notes and block instructions.

> **Unofficial.** TrainHeroic has no public API. This server talks to the same
> endpoints the TrainHeroic apps use, so it can break whenever they change, and
> automated access may not be allowed by TrainHeroic's terms. Use it for your own
> account, at your own risk.

## What you can ask

- *"Build next week's two sessions: trap bar deadlift 3×8 at 30/35/40 kg, split squats, incline DB press, lat pulldown, calves."*
- *"Show Tuesday's workout with links to the exercise videos."*
- *"Bump all deadlift sets in Thursday's session by 2.5 kg."*
- *"Find a landmine press variation that has a video."*
- *"Copy this week's two sessions to the next three weeks, +2.5 kg on the main lifts."*

## Tools

| Area | Tool | What it does |
|---|---|---|
| Account | `get_profile` | Athlete id, name, coach teams |
| History | `get_workout_history` | Sessions in a date range (past or planned) |
| | `get_workout` | One session: blocks, exercises, prescription, results, video links |
| | `get_set_exercise` | Per-set values for one exercise |
| Library | `search_exercises`, `get_exercise` | Find exercises (all words must match), with video links |
| | `search_circuits`, `get_recent_exercises`, `get_recent_circuits` | |
| Performance | `get_exercise_stats`, `get_personal_records`, `get_working_max` | |
| Feedback | `get_workout_surveys`, `submit_survey`, `get_workout_comments`, `get_workout_leaderboard`, `save_workout_feedback` | |
| Planning | `create_workout` | Whole session in one call, supersets included; rolls back if any step fails |
| | `create_session`, `add_exercises`, `add_to_block`, `set_prescription` | Build or edit step by step; `add_to_block` turns a block into a superset |
| | `update_session`, `update_block` | Titles, notes, block instructions |
| | `remove_exercise`, `remove_block`, `delete_session` | Remove an exercise (an emptied block goes too), a block or a session |
| | `copy_workout`, `move_workout` | Repeat a session's plan on another date; reschedule a session |

Weights are always passed **in kilograms**; the server converts to the pounds the
API stores and the app shows them in your preferred units. Targets can be one value
for every set or a list with a value per set (`weight_kg: [30, 35, 40, 40]`). Only
targets the exercise actually tracks are accepted, so a typo like weight on a
bodyweight drill fails loudly instead of being dropped.

### Supersets

TrainHeroic has no separate superset object: a block that holds several exercises
is a superset. In `create_workout`, give exercises the same `superset` label to put
them in one block (the label becomes the block title unless `block_title` is set):

```json
[
  {"exercise_id": 68077, "reps": 8, "weight_kg": [30, 35, 40], "superset": "A"},
  {"exercise_id": 1971, "reps": 10, "weight_kg": 30, "superset": "A",
   "block_instruction": "Alternate, rest 90 s after each pair"}
]
```

## Setup

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/IBazylchuk/trainheroic-mcp
cd trainheroic-mcp
uv sync
```

### Credentials

Either of:

| Variable | Notes |
|---|---|
| `TRAINHEROIC_EMAIL` + `TRAINHEROIC_PASSWORD` | Logs in and caches the session token; re-logs in when it expires |
| `TRAINHEROIC_SESSION_TOKEN` | The `session-token` header from a logged-in TrainHeroic web session |

Optional: `TRAINHEROIC_CONFIG_DIR` (default `~/.config/trainheroic-mcp`) holds the cached
token, the exercise library cache and the ids it learned about your calendar.
`TRAINHEROIC_LOG_LEVEL=DEBUG` logs every API call to stderr.

### Claude Code

```bash
claude mcp add trainheroic \
  -e TRAINHEROIC_EMAIL=you@example.com -e TRAINHEROIC_PASSWORD=... \
  -- uv run --directory /path/to/trainheroic-mcp trainheroic-mcp
```

To keep the password out of config files, start the server through a secrets
manager, e.g. a wrapper script with the 1Password CLI:

```bash
#!/bin/sh
# op.env contains: TRAINHEROIC_EMAIL=op://Private/TrainHeroic/username
#                  TRAINHEROIC_PASSWORD=op://Private/TrainHeroic/password
exec op run --no-masking --env-file "$HOME/.config/trainheroic-mcp/op.env" -- \
  uv run --directory /path/to/trainheroic-mcp trainheroic-mcp
```

and register the script instead: `claude mcp add trainheroic -- /path/to/script.sh`.

### Claude Desktop / other clients

```json
{
  "mcpServers": {
    "trainheroic": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/trainheroic-mcp", "trainheroic-mcp"],
      "env": { "TRAINHEROIC_EMAIL": "you@example.com", "TRAINHEROIC_PASSWORD": "..." }
    }
  }
}
```

## Limitations

- Only **personal calendar** sessions can be created or edited. Coach programs are
  read-only, and once a coach license expires TrainHeroic hides those sessions from
  the API entirely (use the app's data export for that history).
- TrainHeroic has no reschedule call for personal calendars, so `move_workout`
  re-creates the session on the new date and deletes the old one (ids change).
  Sessions with logged results are never moved, only copied.
- For personal sessions the TrainHeroic app writes the reps and weights you type while
  logging back into the plan; the original targets are not kept anywhere. Copying a
  logged personal session therefore repeats what you entered, and `copy_workout` says
  so in its result.
- Logging set results and marking a session complete happen in the TrainHeroic app;
  `save_workout_feedback` only stores notes, RPE and rating.
- Reps must be whole numbers. Put "per side", tempo or rest cues in the exercise or
  block instruction.

## Development

```bash
uv sync --all-groups
uv run pytest          # no network, all HTTP is mocked
uv run ruff check . && uv run ruff format --check .
```

Code map:

- `api.py` — HTTP client: auth and token cache, errors, team resolution, library cache.
- `workouts.py` — pure functions that parse workout payloads and build prescriptions.
- `units.py` — parameter types and kg/lb conversion.
- `server.py` — MCP tools.

## License

[MIT](LICENSE)
