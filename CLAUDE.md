# CLAUDE.md

MCP server (Python, mcp 2 `MCPServer` over stdio) for the unofficial MiniBrew Brewery Portal API
(`api.minibrew.io`). Modelled on `../mcp-server-brewfather`. User docs and tool table: README.md.

## Commands

```bash
.venv/bin/pytest
.venv/bin/ruff check . && .venv/bin/ruff format --check .
```

## Architecture

- `client.py`: httpx wrapper. Sends `client: Breweryportal` (the API answers 426 without it). Logs in with
  `MINIBREW_EMAIL`/`MINIBREW_PASSWORD` (POST v2/token/ with MiniBrew's fixed `Authorization: TOKEN <APP_KEY>`,
  as pymbrewclient does) and renews before expiry; otherwise, or if login fails, uses `MINIBREW_TOKEN`. All
  three come from the environment or `MINIBREW_ENV_FILE`, re-read when it changes. Bad credentials aren't retried
  until they change. Never put the password or a token in an error message. `ALLOWED_WRITES`
  is the trust boundary: only GET and the captured portal writes (POST v1/beers/, POST v1/recipes/,
  POST v1/{fermentables,hops,yeasts,adjuncts}/, POST v2/check_recipes/, PUT v1/recipes/<id>/, and the login POST v2/token/) ever leave the client.
- `telemetry.py`: live device telemetry. A stdlib MQTT 3.1.1-over-WebSocket client (broker.minibrew.io:15675/ws,
  user `breweryportal-<user uuid>`, the bearer token as password) that only CONNECTs, SUBSCRIBEs to
  `devices/logs/<uuid>`, PINGs and DISCONNECTs, never PUBLISHes. Plus a protobuf wire decoder; field numbers
  and the SensorType ids come from stuartp44/pymbrewclient (the real schema is private). Fan, Peltier and
  internal temperatures are only available here, not over REST.
- `labels.py`: numeric code → label tables (process_state/type/phase, user_action, session/device types).
- `normalize.py`: compact projections; epoch → ISO UTC; None/empty fields omitted.
- `server.py`: the `@mcp.tool()` functions. Validate input before any request.

Tools call `client.get_client()` through the module; the `fake_api` fixture monkeypatches it.

## Constraints

- No device, session or keg writes, ever (no commands, no session create/delete), and no deletes of anything.
  Writes are limited to creating beers, creating a new beer's first recipe, saving unbrewed recipes, and
  adding custom ingredients (fermentables, hops, yeasts, adjuncts).
- `create_fermentable` / `create_hop` / `create_yeast` / `create_adjunct` mirror the portal forms (Oct 2026):
  POST v1/fermentables/ with only name, fermentable_type, dry_yield, moisture (no colour or requires_mashing:
  MiniBrew stores null/false); POST v1/hops/ with only name, usage (no alpha acid); POST v1/yeasts/ with name,
  yeast_form (ALE/LAGER), attenuation, laboratory (a v1/laboratories/ id, sent as a string), product_id;
  POST v1/adjuncts/ with name, adjunct_type. Colour and alpha acid go on the recipe addition. Custom entries
  get `owner` = your user and `available: false` and can't be deleted, so all four refuse a name already in
  the catalogue.
- `update_recipe` mirrors the portal (captured Oct 2026): PUT the full recipe to `v1/recipes/<id>/`, which
  overwrites that version in place. MiniBrew only starts a new version after a recipe is brewed. Refuse versions with
  `times_brewed > 0` (brew sessions point at them).
- `create_recipe` mirrors the portal: POST v1/recipes/ with `beer_id` as a string and `shared: false`, only for a
  beer with no recipes (the only case captured; how the portal adds a version to an existing beer is unknown).
  The portal computes og/fg/abv/ibu/srm/kcal and water_amount/kettle_water client-side; MiniBrew stores them as
  sent (water left out is stored as null, and the recipe still counts as `brewable`) and sets `brewable`.
  `mash_in_water` is required. Efficiency isn't a recipe field.
- POSTs answer HTTP 500 unless the portal's `version` header (`PORTAL_VERSION` in client.py) is sent; GET and
  PUT work without it. If creates start failing with 500 again, the portal build number has probably moved on:
  check a fresh capture. v2/check_recipes/ answers 400 with field errors, which check_recipe returns as data.
- `get_recipe` returns MiniBrew's raw format on purpose, so it round-trips into `update_recipe` unchanged.
- Raise `ToolError` / `MiniBrewError` for anything the model should read.
- The broker grants only `devices/logs/<uuid>` topics: subscribing to anything else (e.g. `devices/events/…`)
  makes it drop the connection. A device only streams (about 1 message/s) while someone has it open in the Pro
portal (pro.minibrew.io); otherwise it sends nothing (none in 25 min, Oct 2026). The trigger isn't captured.
- Dependencies: `mcp` and `httpx` only.
- Adding/changing a tool: update the README tool table and the `server.py` module docstring.
