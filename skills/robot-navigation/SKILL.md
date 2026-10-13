---
name: robot-navigation
description: Driving and orientation of the PiCar-X robot — self-localization, turning, straight driving, AprilTags, learned routes, tag surveying. Load it when the robot's WHEELS will move, or when you must establish where it is. Triggers: "go", "turn", "navigate", "where are you", "come here", "go to <room>", "route", "tag", "whereami", "survey", "I'm lost".
---

# Robot — NAVIGATION

**Canonical knowledge: `picarx-mcp/tanulsagok/vezetes_alapok.md` ("driving basics"), your own room cards,
routes and tag coordinates (not shipped; templates: `picarx-mcp/routes.example.json`,
`picarx-mcp/navigacio/tags_template.json` → your `tags_coords.json`). This skill is navigation only.**
Numbers, tag coordinates, room facts and route steps do NOT go here.

## Load order — BEFORE any wheel movement, no exceptions
1. The `home-robot` index (safety!) + `preflight.py` has run.
2. **The card of the TARGET room and of every room on the way** (your own map cards, not shipped).
3. **`tanulsagok/vezetes_alapok.md` — IN FULL.** Not skimmed.
4. If you run a route: **the FULL file of that route** (your own `utvonalak/routes/<route>.json`, index
   `routes.json`, not shipped), with the full waypoint content (act/note/scene/annot) — **never truncated**.

## THE THREE GROUND RULES (without them the run goes wrong)
1. **THE WRITTEN STEP IS THE STEP THAT GETS FOLLOWED.** Before EVERY waypoint write into the chat: where am I →
   next act → success criterion. Writing it forces re-reading the route text; without it you slide into goal-driven
   improvisation. (Saying it aloud is an optional extra.)
2. **STRAIGHT DRIVING = ALWAYS WITH HEADING HOLD** (`go_heading`). Plain `go_dist` for long straights is FORBIDDEN.
3. **NO CHAINING.** Issue physical manoeuvres in SEPARATE calls, and READ the output before starting the next.

**On long free stretches do not crawl 20-30 cm at a time** — measure the free path and go 50-100 cm.

## Commands (all in `{PICARX_HOME}`, `python3 <script>`)
| Command | For | When |
|---|---|---|
| `whereami.py` | ±70° scanning self-localization (distance + bearing per tag, xy from 2 tags) | before starting, and whenever unsure |
| `face_tag.py <id>` | bearing-based turn towards a tag | aiming at a target; **SEPARATE call!** |
| `turn_scout.py [cw\|ccw] [target-id...]` | blind spin until a target is seen | when no tag is in view |
| `smart_turn.py left\|right [deg] \| to <hdg> \| status` | CLOSED-LOOP turn with the IMU | **primary turn on clear ground** |
| `turn90.py left\|right [deg]` / `fine_turn.py` | blind turn | fallback only (near metal, where the IMU is distorted) |
| `go_heading.py <cm> [speed] [--force]` | straight drive with heading lock | **every straight segment** |
| `go_dist.py <cm> [speed] [--force]` | distance with sonar brake; negative = reverse | short correction, reversing |
| `cross.py <cm> --go` | smart threshold crossing with an image decision | at thresholds |
| `pilot.py steer speed dur [camtilt]` | manual arc/step + fresh image; `speed=0` = image only | fine tuning, orientation |
| `zero_steer.py` | steering zero **FROM THE LEFT** | if the steering slipped |
| `bemer_tag.py <new_id> <ref...>` | survey a new tag ("survey tag") | map extension |
| `look3.py <prefix>` | 3 reference images (pan −30/0/+30) | quick overview |
| `rear_sonar.py once\|loop` | rear sonar | before reversing |
| `utvonalak/route_runner.py start <route> [--dry-run]` | route runner WITH A BOOKKEEPER (enforces distance debt in code) | replaying a learned route |
| `home_reflex.py` | return-to-dock reflex | before the battery runs out |

Canonical docs: `utvonalak/ROUTE_RUNNER.md`, `teleop/TELEOP.md`.

## Traps (they bite if you don't know them)
- **`face_tag.py` returns `exit 0` on STOP too** — the exit code is NOT proof of success; read the output.
- **`turn90` overturns on clear ground** → that is why `smart_turn` exists.
- **Near metal (e.g. an oven) the magnetometer is distorted** — `smart_turn`/`go_heading` warn (metal guard); then
  a blind turn + camera may be more reliable.
- **A frozen IMU-anchor phantom can slip through on a short segment too** → look at the heading value.
- **If sonar and image/tag distance disagree, the sonar sees a DIFFERENT surface** (gap under furniture, woven or
  textile surface, black doormat) — geometry, not a bad sensor. Identify from the image WHAT it sees.
- **Threshold mode:** if the sonar brake stops you at a threshold, decide from the image whether there is a REAL
  obstacle; if not → `--force`, briefly.
- **Carpet:** only with momentum, straight; never turn on it.
- **Hardware left drift** exists and cannot be calibrated out — hence `go_heading` is mandatory.
- **Motor STOP trap:** a POSITIVE (braking) motor calibration is FORBIDDEN with this firmware (underflow → the wheel
  spins backwards, the HAT MCU may hang). Details + emergency recipe: eszkoztar "Motor balancing and the STOP trap".
- **One-GPIO-process rule:** heading reader and motor control in ONE process; a separate subprocess = `GPIO busy`.
- **Put the stream in NAV mode while driving** (`stream/stream_ctl.py nav on` … `nav off`) — see `robot-vision-camera`.

## Lasting direction heuristics (route-independent)
Floorboard lines as a compass · fixed landmarks as compass points (pick a few per home and write them on your room
cards) · how many `turn90` cycles make a full turn. Details: `tanulsagok/vezetes_alapok.md`.

## Key principle
**Sound direction (DOA) aims only ROUGHLY; once roughly facing that way, SCAN with the CAMERA for the real target.
The ear is coarse, the eye is precise.** (Turning to a voice: `robot-hearing-speech`.)

## If you are lost
No idea where you are and no tag in view: **take camera images in several directions** and orient from them
(`look3.py`, `pilot.py 0 0 0`), then `turn_scout.py`. Never drive by guessing.

## Where to write new knowledge
Driving lesson → `tanulsagok/vezetes_alapok.md` · room fact → that room's card · route experience → the `status` of
the route's own JSON (new route = new file + index line) · tag coordinate → the INNER `"tags"` object of
`tags_coords.json`. Then **scp it to the Pi**.
