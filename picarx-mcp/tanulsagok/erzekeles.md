# PERCEPTION — camera and sonar truths (canonical)

> The systematic illusions of the camera and the sonar, plus the methods that have proven to work against them.
> (This file lives in `tanulsagok/` ("lessons"); its name `erzekeles.md` means "perception".)

## ⚡ CAMERA GENERATION CHANGE: IMX708 Wide
- **HFOV 96.0° (calibrated; the old ov5647 was 45.6°)** — capture 2304×1296, calibration: `camera_calib.json` (reprojection error 0.087 px; old archive: `camera_calib_ov5647_2026-06-01.json`).
- **FOCUS LOCK: lens_position=1.0, autofocus FORBIDDEN during navigation** — canonical location: `camera_config.json`; capture()/look/scan/stream all read it from there. If the focus ever changes → RECALIBRATE.
- **Tag range roughly preserved** (fx=1036 at 2304 width): a 6 cm tag is detected even from ~3.5 m (validated). Distance measurement: `tag_range` A=1.0/B=12 (validated against 4 tape-measured points, ±1.5 cm up close).
- **⚠️ DISTANCE TO A TAG AT THE IMAGE EDGE IS NOT RELIABLE** (undistortion is not enough there: steep viewing angle + few pixels — live example: tag7 measured 353 vs 494 real). The rule stands: **take a fix only from a tag rotated to the CENTRE of the image!** The edge is good for FINDING a tag, not for MEASURING it.
- **Sweep coverage: ±70° pan + 96° ≈ 236°** → sweep step sizes doubled (whereami 35°, general 40°) — faster localization, less wear on the ribbon cable. One sweep typically yields 5-7 tags (the old record was 2).

## Camera illusions (low camera + lens tricks)
- **The Wide lens makes everything look FARTHER AWAY and SMALLER** (the old narrow lens did the opposite: it made things look closer). Older notes saying "everything looks closer" are now REVERSED — but the truth about free space still holds: the space is always WIDER than it feels (16 cm robot vs. an 85 cm doorway).
- **Distance to a non-tag target:** measure it with the SONAR (after turning to face it), or with tag_range — NEVER "by eye".
- **Expectation-driven vision (bitten twice):** when you are looking hard for something, you see it everywhere (dark gap → "door"; a glimpse of the washing machine → "I am at the junction"). Antidote: confirm every scene guess with a TAG FINGERPRINT before declaring a waypoint or arrival.
- **"I SEE THE GOAL ≠ I AM THERE":** at the goal, do a ±70° sweep and confirm the expected, DIRECTION-SPECIFIC set of tags. A single-tag fix (especially from an oblique angle) gives a jumpy pose.
- **Mirrors (e.g. a mirrored wardrobe):** they show false space — do not navigate "into" them; they fool the sonar too.

## Sonar truths (HC-SR04, ~2-400 cm; -1 = no echo)
- **★ −2 = TARGET TOO FAR, NOT A FAULT:** the 20 ms timeout in the picarx library's `_read()` ≈ **a ~340 cm measurement ceiling**; for targets farther than that (e.g. a far wall down a long hallway) the echo pulse (20.7 ms) is longer than the limit → −2/−1 error code, NOT a measurement. Proof: a custom perf_counter-based measurement gave 356.6 cm stably, 10/10. Symptom map: repeated −2 in an open hallway/large space + a clear camera image = a distant wall, not "something right in front of the nose". As soon as you get within ~330 cm of anything, readings return. The guarded drive's guard (0<d<25) treats −2 as "clear" — which is correct in this case. Do NOT hunt for a contact fault and do NOT stop the sentinel because of it (both were once done needlessly, costing half an hour).
- **It measures the NEAREST object in the beam, NOT your target!** If anything closer is in front of the target (rug edge, furniture leg, front of a sofa), that is what you see. Think through WHAT could be in the beam BEFORE the target.
- **Blind spots:** flat rugs, thin legs (glass table, chair legs, tag stand), it does NOT see DOWNWARD (terrace edge!), slanted/low furniture is detected late.
- **Textiles/fur ABSORB** (cat, blanket: jumpy or -1 values); slanted glass deflects it (mirror, glass door — be careful).
- **Jumpy ~11-23 cm readings at a doorway = the THRESHOLD**, not an obstacle (threshold mode: decide from the image + force).
- It SEES THROUGH under the bed (99 cm is misleading) — the bed frame at body height is closer.

## Methods
- **★ "CAMERA FIRST, WHEELS SECOND" search ladder (the owner's rule):** pan −30/+30 → −60/+60 → only turn the BODY once the camera has found the target. A pan photo is free, a blind body turn is a gamble.
- **PAN-STEERING rule:** the body rotation needed to face a target ≈ the target's current pan angle (+pan = turn right). Works for any recognised target: centre it → turn ≈ by the pan angle → check photo at pan 0 → iterate.
- **Heading from the TAG BEARING, not from the pose** (pose-localize FLIPS from a single oblique tag — "more than one minima").
- **Scene first:** if all visible tags are on one side → weak geometry → estimate from the image/room first, then from the tags. If the sources disagree: do NOT move.
- **Compass objects** (on the room cards): centring a wall/piece of furniture at pan 0 is the best heading reference. (The skirting-board compass did NOT work on shiny parquet.)
- **★ WALL-LINE LEVEL TEST (the owner's tip):** to face a wall squarely, **the floor–wall line must be HORIZONTAL in the image** — if it slopes right, turn right; if it slopes left, turn left. Simpler and more reliable than reading the skirting board.
- **Reading handwriting/notes:** FACE-ON, up close (~30-40 cm), without backlight — turn perpendicular to the surface plane.
- **★ MOTION DETECTION FROM TWO FRAMES:** something dark moving against a dark background (a bat in front of trees) is invisible in a still image — but the DIFFERENCE of two quick frames reveals it (on the laptop: PIL ImageChops.difference + numpy, blob search on pixels with delta>40). Motion shows what a snapshot hides (the owner's principle — the main argument for the video stream). Validated live: a 451 px blob = the dancing bat.
- **Camera freeze:** the same still image twice = stuck frame → `pkill rpicam` (if the md5 of 2 fresh frames differs = alive).
- **Video interpretation (video-mcp) in a tag-blind zone:** ~6 s sweeping clip → to the laptop → understand_video. Slow (not a control loop), but it recognises the room and the main obstacles. Needs GOOD LIGHT.
- **Exposure:** the adaptive capture() handles extremes; OVEREXPOSURE washes out tags in strong light — if a tag is not detected in a sunny spot, suspect this.


## Sonar verification to the centimetre (lesson learned)
Calibration check with a box, measured by hand with a tape: 30 cm real → 31.6 median; ~68 → 68.4; 140 → 139.8. **The sonar is FLAWLESS and stable (spread <1 cm, bias max ~1.5 cm up close).** Consequence: if the sonar "mismeasures", the sensor is NOT at fault but the GEOMETRY — (a) open gap under furniture: the beam sees through underneath and measures the space behind (bedside-table case); (b) convex/woven surfaces (laundry basket) and textiles scatter/absorb; (c) the wide beam measures the nearest reflector, not what the camera looks at. Diagnosis recipe: if sonar and tag/image distance disagree, the sonar sees some OTHER surface — identify WHAT from the image.
★ 4th data point: 220 cm real → 218.7 measured — the sonar is validated across the whole 30-220 cm range, within 1.5 cm everywhere.

## ★ CAMERA DISTANCE ERROR DEPENDS ON DISTANCE (lesson learned, checked against a tape measure)
- **From far away (~150+ cm) the camera reads ~16 % MORE** than reality (at one route turning point: real 150 ↔ camera 174; route target numbers are often recorded in this regime — compare camera numbers with camera numbers!).
- **Up close (~50–70 cm) the camera is ALMOST EXACT** (tag0 target: camera 55 ↔ tape 52; camera 62 ↔ real 58.8). Do NOT apply the +16 % margin up close — there is ±3-4 cm instrument noise and compensating overshoots.
- **In practice:** when approaching a target from far away, the route's camera target number is authoritative (same instrument); once close, accept that camera ≈ real, and do not chase the ±3-4 cm fluctuation by reversing back and forth — two measurements bracketing the target = you are there.
- Result: in a test run at tag0, 55/62 bracketed the target of 58 (real 58.8 — a direct hit); at tag13 the target of 70 was met with camera 74.


## NIGHT SKY — lessons from stargazing

**`egbolt.py <tilt> <kimenet.jpg> [pan]`** (`egbolt` = "sky", `kimenet` = "output") — camera tilt + `capture(mode="dark")`. ONLY with the robot standing still (exposure of seconds). Tilt range −30…+60; **Moon ~35°, stars ~60°** (angles suggested in advance; they worked).

- **For the sky, dark mode always runs to its CEILING** (E=32e6, mean ~15-29 instead of the target 105) — this is NOT a fault: the sky is dark, the ceiling is 4 s / gain 8. The underexposed image is the correct image.
- **With this camera the Moon is 9 pixels across** — there are NO craters and never will be; the field of view is made for rooms. With a short exposure (E=3000) the disc shape is discernible (not a perfect circle = not a full moon), everything else is pitch black.
- **⚠️ STAR-COUNTING TRAP:** "bringing it out better" with tone stretching amplifies the gain-8 noise — 99 % coloured grain that looks like stars. Blur-difference detection gives false hits along the **edges of tree silhouettes**. At 8σ it found 184 "stars"; on the annotated image most circles were EMPTY or sat on trees. **The raw image is the truth; ~15-20 points can be read off by eye.**
- **PANORAMA:** `cv2.Stitcher` **fails** on the night sky (code 3). What WORKS: brighten the images ×3.2 → ORB (6000 points) → pairwise `estimateAffinePartial2D` with RANSAC → chaining → `warpAffine` onto a common canvas with **max blending** (the stars survive). The anchor is provided by the **tree silhouettes**, 120-180 inliers per image pair.
- **★ Geometry check:** a 30° camera pan **at 60° tilt gives a ~290 px** shift. That is half of what the naive 22.6 px/degree predicts, because at high tilt the horizontal displacement shrinks by cos(tilt). It checked out.
- **★ EARTH'S ROTATION IS MEASURABLE:** two images with identical settings 10+ minutes apart → star detection (10σ, tree zone masked) → nearest-neighbour matching. In 632 s **39 of 41 points moved ~35 px in one direction, 2 stayed put — those 2 were hot pixels.** The expected value was 55-75 px; the measured value is a LOWER BOUND, because nearest-neighbour matching biases downward (another star can be closer than the true match). Better method: displacement histogram / RANSAC for a global translation.

## BME280 (0x77) — false temperature readings from bus collisions — ✅ SOLVED IN HARDWARE

`orszem.py` ("sentinel") sent "temperature events" of −126.6 / −141.1 / +118 °C every minute. **The sensor was INTACT** (chip id 0x60, direct read: 24.8 °C / 61.8 % / 1000 hPa). The cause: on the I²C bus the sentinel and **battery_guard** accessed the bus at the same time → corrupted register reads → meaningless compensated values. Humidity/pressure were distorted too (100 %, −393 hPa false "rain coming" alarms).

**✅ THE FIX (the owner's idea and soldering work — removing the cause, not treating the symptom):**
- **The BME280 moved to its OWN software bus: i2c-3 on pins GPIO13 (SDA) / GPIO6 (SCL)** — `dtoverlay=i2c-gpio,bus=3,i2c_gpio_sda=13,i2c_gpio_scl=6` in config.txt (back it up before editing; the first attempt's GPIO5 SDA had to be re-soldered to GPIO13 because of a conflict with robot_hat's MCURST). Physically collision-free from the battery guard's i2c-1; bonus: it is also off the chain that was slowed to 10 kHz.
- **Wiring:** REMOVED from the QWIIC chain; header pins soldered into the board, 4 wires: VIN+GND → ADC header power/GND pins (plug-in), SCK(=SCL)+SDI(=SDA) → soldered onto the SOLDER BUMPS of the 2×20 pass-through header (counting from the USB end, inner column: 5th = pin 31/GPIO6, 6th = pin 29/GPIO5). 0x77 DISAPPEARED from bus 1 and appeared on bus 3 — double proof. It worked first time.
- **`climate.py` rewritten:** ExtendedI2C(3) (fallback i2c-1) + **a non-silent sanity filter** — a reading outside the spec range (T −40..85 °C, p 300..1100 hPa, humidity 0..100 %) raises ValueError + increments a counter (/tmp/bme_eldobott.txt, "dropped"; successful readings also carry the `eldobott` ("dropped") field). orszem.py inherits both via climate.read_once().
- **Load test:** 15 rapid reads alongside battery_guard → 15/15 clean, 0 dropped. Note: under continuous reading the chip self-heats by ~1-2 °C (23.0→29.5 when hanging near the electronics) — it needs an airy spot, as before.

### ⚠️ "ETERNAL 23.0 °C" — the first read returns the reset phantom (spotted by a human)
After the hardware change the temperature was EXACTLY 23.0 °C all day (indoors in the morning AND outdoors in a 34-degree afternoon!), while humidity/pressure were alive. **The cause:** climate.py re-initialises the library at every start, which begins with a SOFT RESET; after reset the raw temperature register holds the factory default pattern (0x80000), and the FIRST read still catches it (on the slow software bus the "is the measurement ready" wait loop can finish before the measurement even STARTS). With our calibration coefficients 0x80000 gives exactly 22.96 ≈ 23.0 °C — a plausible number, so the spec filter did not catch it! p/h were fine because they are read AFTER T, by which time there is a real measurement. **Fix: a warm-up read after init (drop the first T + 0.15 s).** Lesson: a "plausible but suspiciously UNCHANGING" value is as much an error signal as an impossible one — the filter only catches the impossible; the constancy was noticed by a human. **Another lesson: the sensor sits ON THE PANEL → next to sun + electronics it measures its own micro-environment (43 °C while the air was 34) — real air temperature needs a shaded/ventilated spot or a longer cable.**

## ★ VL53 (floor distance) — dark/distant surface = false lift signals → IMU WITNESS
Standing on a box outdoors, above dark gravel, orszem.py raised a false "floor-distance jump | 3000-7900 mm" alarm every 5 minutes WHILE THE ROBOT STOOD STILL. **The sensor is FINE** (check at home: 16-19 mm stable, 8/8) — from a dark, distant surface the laser gets no reflection and reports a huge/out-of-range value. The error was in the INTERPRETATION.
**FIX (orszem.py):** the ToF alarm only fires with an `imu_mozgas` ("IMU motion") witness (roll/pitch change > 1.5° in the same cycle); without a witness the signal is silently dropped and the baseline does NOT creep up. **Validated live:** robot lifted by hand → alarm came (405 mm + IMU witness) ✔; 10 minutes on a box → 0 false alarms ✔ (outdoors the same setup had cried wolf every 5 minutes).

## ★ The two modes of the noise gate (robot_listen_loop.py)
`SPEECH_BAND_MIN_RATIO`: **at home 0.35** (strict — it drops recordings full of kitchen noise; otherwise Scribe transcribes garbage, e.g. a nonsense phrase like "Hunadban vagy"); **outdoors/in wind 0.10** (relaxed — with wind noise, real speech also has a band ratio of 0.10-0.28, and 0.35 dropped it too). Switching is a manual sed from shell history; back home, ALWAYS return to 0.35. Symptom map: speech does not come through + listen.err shows `zaj-kapu: eldobva` ("noise gate: dropped") lines = too strict; nonsensical transcripts = too loose.

## ★ Double buffering in the ear (robot_listen_loop.py)
**The symptom (on a slow 4G connection):** half the sentences were lost; listen.err showed `overrun!!! (at least 5662ms)` — NOT a Scribe timeout! **The cause:** the main thread read the arecord pipe, and during the slow STT/sound-scene cycle (20+ s on 4G) nobody drained it → the ALSA buffer overflowed and the audio was lost. **The fix:** a separate capture thread (`_capture_worker`) reads the microphone non-stop into a Queue; the main thread works from the Queue — speech uttered during processing is kept and comes out later, in order. The robot's own speech (LOCK) is already dropped in the capture thread; the gap is marked with a `"GAP"` marker (segment boundary). Speech-only mode: `touch /tmp/no_labels` — mutes the YAMNet labels AND the standalone STT labels (e.g. [kopogás] ("knocking")); labels attached to speech remain. ⚠️ /tmp is cleared at boot, so the flag must be recreated.

## USB autosuspend: the cause of the ear's spontaneous dropouts (CLOSED)
Symptom: the ReSpeaker spontaneously disconnects from USB WITHOUT being moved (LEDs stay lit: the power pins touch, the data link dies); a new cable + contact spray did NOT help. Cause: kernel USB autosuspend (default 2 s) puts the idle device to sleep and it never wakes up. The owner's diagnosis: "not physical, we have seen this before" (cf. Wi-Fi power-save). Fix: usbcore.autosuspend=-1 at runtime (echo -1 | sudo tee /sys/module/usbcore/parameters/autosuspend) AND permanently in /boot/firmware/cmdline.txt (back it up before editing). Decisive test: it stayed awake after a long silence. Useful pattern: SELF-HEALING EAR: run the listener inside a while-true wrapper that restarts it when the device returns. Plan: a LEGO microphone stand as mechanical protection.

## Black doormat = sonar phantom (terrace)
A black rubber doormat on the terrace ABSORBS ultrasound → the sonar returns -1 (no echo), just like slanted glass. Symptom: sudden -1 and braking on flat, safe ground. Do NOT immediately assume an edge: the image + a human's confirmation decided (the brake fired at the right time, but the cause was the doormat). Force only on a seen/approved path, then verify the movement with an image (the sonar is useless here).

## ★ IN DARK MODE THE CAMERA TURNS COLD WHITE INTO BLUE (lesson learned, corrected by a human)
On a terrace photo taken in `egbolt.py --tilt ... dark` (long-exposure night) mode, a string of lights
looked **blue** to me and I called it blue. A human corrected me: in reality that string is
**cold white**, not blue. **The cause:** the long exposure of dark mode + the fixed night white balance
is tuned for warm (yellowish) light sources, so it over-pulls the cold end of the spectrum — a neutral cold
white LED turns blue in the image. The same image rendered the warm white string CORRECTLY
(palette check: `FENYFUZER 0xffe2b0` = warm ✔).
**THE RULE:** on a night/dark-mode image **make no claim about colour temperature** — shape, place,
light spot yes, the "blue / cold / warm" adjective no. If colour matters (e.g. for a palette),
decide it from a **daytime image**, or ask. Related: the same evening a
window was blown out (photo on request, dark mode long exposure) — details of bright surfaces are LOST,
and the pets were not visible.
★ The generalisable part: dark mode is not "the same image, only brighter". In exchange for visibility
it **gives up two channels: colour fidelity and highlight detail.** Whatever I read from those is not
perception but guesswork.

## ★ I ONLY STATE A SENSOR NUMBER FROM A FRESH READING (battery case)
Asked about the battery level, I said **"above eighty" — it was 68 %.**
I had not measured: I spoke confidently from the memory of an earlier reading. The battery is exactly the kind of quantity
that CONSTANTLY decreases, so an old reading is not "slightly inaccurate" but **systematically
biased upward**.
**THE RULE:** if I state a number from a sensor (battery, temperature, distance, humidity), that number
must come from a reading run **this minute** (`battery_status`, `climate.read_once()`,
`cliff.read_mm()`). If I cannot measure, I must say "I don't know, shall I check?" — not a
number taken from memory. The only advantage a sensor system has over a human is MEASUREMENT; if I
speak about it from memory, I throw away exactly what it is there for.

## Battery: the sentinel's WARN during speech is not a fact
- Standing on the charger (red LED) the sentinel reported OK→WARN twice (6.87 V / 6.84 V), while a fresh `cat /tmp/battery_status` gave 8.04 V / 85 %, then 7.46 V / 61 % OK. **Explanation (from the owner): while speaking (speaker on) the voltage sags, and the sentinel sees that.** The % fluctuates the same way (35→85→61) — the Robot HAT's voltage-based % estimate is unreliable under load.
- **Rule:** before a battery alarm ALWAYS take a fresh `battery_status` (ideally measured without speech/motion); the sentinel event is only a trigger. Only alert the owner if the resting value is also WARN. (I once raised a needless alarm — and withdrew it.)
- Improvement idea: in the sentinel's battery branch, skip values measured while `/tmp/robot_speaking.lock` exists, and require 3 consecutive resting WARNs before switching state.

## Sound labels → pets (taught on the robot by the owner)
- A tonal pet sound may come through as `[hang: Singing bowl]` (`hang` = "sound"); Theremin/Opera labels around 0.5 in the same room in the evening are probably pet sounds too — YAMNet hears tonal meowing as a musical instrument.
- `[hang: dorombolás]` ("purring") / `[hang: macska]` ("cat") 0.8+ = a cat close to the robot.
- Principle (the owner's): we do NOT pre-filter the labels — the agent learns the mapping line by line.

## Sound-label translations (listener; checked by a human)
- "zene" ("music") 0.41 in a quiet room = a **battery-powered pet toy** buzzing around the floor by itself. Not music.
- "Crumpling, crinkling 0.74" = a snack wrapper, NOT page turning (the person was reading on a monitor, no paper).
- "horkolás" ("snoring") 0.85–0.92 in a quiet room in the daytime = probably purring, not a human.
- A vibrating/buzzing **phone** also comes through as "zene" (0.59) — corrected by a human. So the "zene" label is suspicious for household buzzing (toys, phone vibration); do not trust it at first.
