# mcp-server-minibrew

An MCP server for a [MiniBrew](https://minibrew.io) account. It lets Claude see what your
MiniBrew devices and kegs are doing, read brew sessions and recipes, create new beers and
recipes, and edit recipes that haven't been brewed yet.

It uses the unofficial REST API behind the MiniBrew Brewery Portal (`api.minibrew.io`),
as documented in [minibrew/enduser-docker-server](https://github.com/minibrew/enduser-docker-server).
Live sensor data (fan, Peltier, internal temperatures) comes from MiniBrew's MQTT broker,
decoded following [stuartp44/pymbrewclient](https://github.com/stuartp44/pymbrewclient).
MiniBrew can change either without notice.

## What it can and can't do

The server is **read-only for the hardware**. No tool can start, stop, advance or
command a device, session or keg. The writes copy requests captured from the MiniBrew portal:

- create a beer (`POST v1/beers/`) and give it its first recipe (`POST v1/recipes/`);
- validate a recipe without saving it (`POST v2/check_recipes/`);
- add a custom fermentable, hop, yeast or adjunct to your catalogue (`POST v1/fermentables/`,
  `v1/hops/`, `v1/yeasts/`, `v1/adjuncts/`), which can't be deleted afterwards;
- save changes to a recipe (`PUT v1/recipes/<id>/`), which overwrites that version
  in place. It is refused for versions that have been brewed (`times_brewed > 0`),
  because brew sessions point at those.

`client.py` refuses any other write, and nothing can delete. `get_device_telemetry`
connects to the MQTT broker only to subscribe to `devices/logs/<uuid>`; it never publishes. Delete test beers in the
portal. Adding a new version to a beer that already has recipes isn't supported
(the portal does that after a brew, and it hasn't been captured).

| Tool | What it does |
|---|---|
| `get_brewery_overview` | Every device and keg: stage, beer, current/target temperature, what it's waiting for you to do, cleaning status |
| `get_device_telemetry` | Listen to live device telemetry for a while: Peltier power and mode, fan duty, liquid/Peltier/room temperatures, and plain-language flags for cooling problems (fan stalled, cooling can't reach target). A device only sends this while it's open in the [Pro portal](https://pro.minibrew.io) |
| `list_sessions` | Brew and cleaning sessions, newest first; filter by beer, status, type, recipe |
| `get_session` | One session: live device state, step milestones with timestamps, and MiniBrew's step-by-step instructions when the device is waiting for you |
| `list_beers` | Your beers (each groups the versions of a recipe) |
| `list_recipes` | Recipes, latest version per beer by default |
| `get_recipe` | One recipe version in MiniBrew's own format: mash, boil, fermentation, dry hops |
| `search_ingredients` | MiniBrew's hop, fermentable, yeast and adjunct catalogue (for `ingredient_id`s) |
| `create_fermentable` | Add a custom fermentable (name, type, dry yield, moisture) for a malt the catalogue lacks. Can't be deleted afterwards |
| `create_hop` | Add a custom hop (name, usage) for a variety the catalogue lacks. Alpha acid goes on the recipe addition. Can't be deleted afterwards |
| `create_yeast` | Add a custom yeast (name, ale/lager, attenuation, lab, product code). Can't be deleted afterwards |
| `create_adjunct` | Add a custom adjunct (name, type: water agent, spice, herb, flavour, fining, other). Can't be deleted afterwards |
| `list_beer_styles` | Style ids with their OG/FG/ABV/IBU/colour/carbonation ranges |
| `create_beer` | Create an empty beer (name + style); carbonation defaults to the style's maximum |
| `check_recipe` | Have MiniBrew validate a recipe without saving it |
| `create_recipe` | Give a new beer its first recipe. MiniBrew stores OG/FG/ABV/IBU/SRM as sent, so calculate them first |
| `update_recipe` | Save changes to a never-brewed recipe version in place (send the full recipe in `get_recipe`'s format) |

## Instructions for the AI

The server sends general MiniBrew rules to the client when it connects (MCP server
instructions): the 5.5 L batch, 1.2–2.3 kg per mash, 6 carousel slots, required fermentation
stages, cold crash at 5 °C, and what the server can't do. They live in
[`src/mcp_server_minibrew/instructions.md`](src/mcp_server_minibrew/instructions.md); keep it under
2048 characters, where Claude Code cuts server instructions off.

There is also a `new_recipe` prompt (in Claude Code: `/mcp__minibrew__new_recipe <idea>`) that
walks the AI through designing a recipe and saving it to your account, starting from a blank recipe skeleton, so no existing recipe is needed.

## Setup

```bash
python3.13 -m venv .venv && .venv/bin/pip install -e .
cp .env.example .env    # then add your login or a token, see below
```

Add it to Claude Code (`.mcp.json`):

```json
{ "mcpServers": { "minibrew": { "command": "/path/to/mcp-server-minibrew/run.sh" } } }
```

## Login

The easiest way is to let the server log in with your MiniBrew account. Put these in `.env`:

```bash
MINIBREW_EMAIL=you@example.com
MINIBREW_PASSWORD='your password'   # quote it if it contains spaces or #
```

The server logs in the way MiniBrew's apps do (`POST v2/token/`) and renews the token
before it expires. The password is in plain text, so keep `.env` private (`chmod 600 .env`).

### Or paste a token

Without a password in `.env` (or if the login fails), the server uses a bearer token
copied from a logged-in portal session:

1. Log in at https://pro.minibrew.io and open DevTools → **Network**.
2. Click a device so the page makes requests, then select one to `api.minibrew.io`.
3. Copy the `Authorization` header value without `Bearer `.
4. Put it in `.env` as `MINIBREW_TOKEN=...`.

Tokens expire. When a tool says the token was rejected, paste a fresh one into `.env`.
The server re-reads the file when it changes, so you don't need to restart it.

## Development

```bash
.venv/bin/pip install pytest ruff
.venv/bin/pytest
.venv/bin/ruff check . && .venv/bin/ruff format --check .
```
