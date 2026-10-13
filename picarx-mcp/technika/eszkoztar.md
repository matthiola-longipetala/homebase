# TOOLBOX — hardware, access, scripts, calibration, ear/voice (canonical)

(`eszkoztar.md` = "toolbox".)

## Hardware + access
- PiCar-X, Raspberry Pi 4, Robot HAT V4. Servos: P0=pan, P1=tilt, P2=steering.
- SSH: `ssh -i "$PI_KEY" $PI_USER@$PI_HOST` (mDNS (`$PI_MDNS_HOST`) is flaky → use the IP (`$PI_LAN_IP`); forward slashes in Bash). The laptop helpers read these from `laptop_config.py` (env/config.json).
- **CHAT-INSTANCE SYNC:** ssh/scp from Windows-MCP PowerShell silently exits with 255 without a valid stdin → use `cmd /c "scp ... < NUL"`; from Bash, `</dev/null` is the proven way. Check the exit code (scp EXIT=0 = complete transfer).
- `Picarx()` init sudo warning = non-fatal. The Pi sudo password is transient, only with the owner's permission, never stored.
- Servo stuck in software → wiggle it free (right-left-centre) or reboot. After a reboot, 0 = straight.
- Shutdown: `echo '<sudo>' | sudo -S shutdown -h now` (wait until the green LED fades). Preferred: `leallit.py`, see "Gentle shutdown" below.
- GRAYSCALE module REMOVED — no line following; init is still OK.
- **CAMERA CABLE (CSI) gotcha:** "Camera frontend has timed out" while `--list-cameras` still sees it → the ribbon cable sits crooked; reseat on the camera side + REBOOT; before that `pkill -9 rpicam` (a zombie may hold the camera).
- **BATTERY:** `preflight.py` at the start of every session; `/tmp/battery_status` ("V PCT STATE"); WARN 6.9 V → tell the owner; `battery_guard.service` protects automatically. A long session drains it in a few hours. **⚠️ Experience: around ~60 % the peripherals (ReSpeaker/USB) may drop out at load peaks — the % gauge is misleading, the voltage sag is what matters. For outdoor/long sessions ask for the charger already at 60 %, do not wait for WARN.** (A weak boot at 45 % in the morning: peripheral loss, solved by mains power.)
- **Rear sonar (parking sensor):** `rear_sonar.py once|loop` — Trig=D1 (GPIO4), Echo=D0 (GPIO17). The negative (reverse) branch of go_dist brakes on it.
- **⚠️ PIN MAP — CORRECTED (from the vendor docs + robot_hat/picarx source).** An earlier note "free D pins: D4, D5" **WAS WRONG AND DANGEROUS**: the `robot_hat` Pin dictionary knows D4/D5 (GPIO23/24), so `Pin("D4")` raises no error — **but on the Robot HAT V4 ONLY D0–D3 physically exist, and GPIO23/24 are the MOTOR DIRECTION lines** (`picarx.py: motor_pins=['D4','D5','P13','P12']`). Wiring a lamp/sensor to D4/D5 = wiring it to the motor direction signal (+ a GPIO-busy conflict with the running `server.py`).
  - **Digital headers (3-pin, 3.3 V) — ALL TAKEN:** D0=GPIO17 + D1=GPIO4 → rear sonar; D2=GPIO27 + D3=GPIO22 → **front ultrasonic** (`ultrasonic_pins=['D2','D3']`).
  - **PWM headers (3-pin, ⚡ 5 V!) — P3…P11 FREE** (P0=pan, P1=tilt, P2=steering; P12/P13=motor). **This is the place** for a new 5 V peripheral: ready 5 V supply + GND + PWM signal on one header.
  - **ADC headers (3-pin, 3.3 V): A0/A1/A2 FREE** since the grayscale module was removed; A4 = internal battery divider. **The BME280's power now sits on the power+GND pins of one ADC header (its SIG pin stayed free).**
  - **★★ GPIO5 = MCURST — HIDDEN TENANT, NEVER USE IT!** The robot_hat library claims GPIO5 as the HAT MCU reset pin on every Picarx init (`pin.py: "MCURST": 5`) — a pinctrl measurement does NOT see it, because it is only claimed transiently. The first i2c-3 plan (SDA=GPIO5) collided for this reason: `lgpio.error: GPIO busy` at drive init. **LESSON — a 3-LAYER PIN AUDIT is mandatory before taking a new pin:** (1) kernel claim (`sudo cat /sys/kernel/debug/gpio` + pinctrl), (2) the robot_hat NAME TABLE (`pin.py` — a named pin is suspicious even if nobody holds it right now!), (3) physical parts on the HAT (button=GPIO25, LED=GPIO26, RST=16 — avoid). + **Test-before-soldering principle:** first switch the overlay in software + reboot + Picarx init test, and ONLY after green pick up the soldering iron.
  - **★ BME280 on its own i2c-3 bus: SDA=GPIO13 (pin 33), SCL=GPIO6 (pin 31)** (`dtoverlay=i2c-gpio,bus=3,i2c_gpio_sda=13,i2c_gpio_scl=6`; back up config.txt before editing). GPIO13 = only legacy "BLEINT/D11" in the lib's name table (no BLE chip on V4), kernel-free, Picarx init works fine next to it (live test ✓). Physical wiring (✅ DONE, two solder joints): SCK wire on the 5th pad of the inner column (pin 31), SDI wire on the 4th pad (pin 33); power+GND from the ADC header with a plug. Final checks all green (0x77 + climate + Picarx init). **Remaining pin measured free: GPIO12** (18/19/21 = HifiBerry I2S, 14/15 = UART, 7-11 = SPI — avoid).
  - **5 V rail budget: max 3 A** for the whole HAT (Pi 4 ~0.6-1.2 A + ReSpeaker + 3 servos) — see the peripheral drop-out at ~60 % battery above. Load a new 5 V consumer only with a PWM limit and preferably **while standing still** (not while driving).

## Camera calibration + exposure — IMX708 Wide
- **Camera: Raspberry Pi Camera Module 3 WIDE (imx708_wide)** — the old ov5647 was removed. Mounted on the outside with double-sided tape (it did not fit in the holder).
- **`camera_config.json` = the canonical config of camera operation:** capture 2304×1296 (native binned, full FOV) + **lens_position=1.0 LOCKED focus**. `core/capture.py`, the look/scan tools, the stream (`cam_source.sh`) and the 5 direct-rpicam scripts ALL read from here. **AF during navigation is FORBIDDEN** (fx drift → silent distance error); if the focus changes → recalibrate.
- **Intrinsics (camera_calib.json, 19 images):** fx=1036.5 fy=1035.0 cx=1152.1 cy=643.2; reproj 0.087 px; **HFOV 96.0°**. Old ov5647 archive: `camera_calib_ov5647_2026-06-01.json` (fx=1217.6 @1024×576, HFOV 45.6°).
- Sensor modes: 1536×864@120 (CROPPED! not full FOV) / **2304×1296@56 (binned, full FOV — ours)** / 4608×2592@14 (full, "foveal" reserve).
- Video (rpicam-vid): Pi4 HW-H264 ceiling **1920×1080** — the scan video runs at 1080p, the stream at 720p (both full FOV, scaled + AF lock).
- **Adaptive exposure** (`core/capture.py` v2): iterative convergence to target brightness 105/255; E-cache during sweeps. Interface: `capture(path,w,h,shutter_us=None)` — w/h are overridden by the config.
- **tag_range calibration (4 tape points with the new camera): real ≈ 1.00×raw + 12 cm** (the old A=1.08 compensated the old lens's fx error). Near tags ±1.5 cm, at 2 m ±3 cm. For high/distant tags pose_t underestimates → for DISTANCE, tag_range + sonar are the truth.
- **★ ANGLE-BASED DISTANCE = THE CANONICAL MEASUREMENT: `tag_range_angular_cm(corners,size,fx,fy,cx,cy)`** — the px-based one (`size*fx/px`) is only true on the image AXIS; towards the edges it underestimates by sec²θ (at 96°, already ~30 % at half-field!). The angle-based one computes from the angles of the corner direction vectors → projection-independent, **identical to the centimetre ANYWHERE in the image** (validated with edge_check: tag1 spread 122→9 cm, the others 0 cm). whereami + measure_dist already use it; the "rotate to centre, then fix" discipline is no longer mandatory FOR DISTANCE (still recommended for bearing and pose). The old px-based one remains as a quick estimate.
- Calibration tools: `navigacio/calibrate.py` (`navigacio/` = "navigation"; updated to 2304×1296) + `calib_shot.py` (shot + check + zone) + `navigacio/edge_check.py` (edge-vs-centre validation, pan-only). Checkerboard: the SMALL rigid print is good; the LARGE print is MISSING the white margin on its left edge → needs reprinting.

## Localization (core/localization.py)
- T_W_R = T_W_tag · (T_R_C · T_C_tag)⁻¹; per-tag size (detection with tag_size=1.0 + scaling).
- Image-based closed-loop centering: pan corrected from the pixel offset; the camera is an ACTIVE sensor (±70° pan × 96° FOV ≈ 236° coverage — sweep step sizes doubled: whereami 35°, general 40°, turn_scout 45°).
- obsdb (`core/obsdb.py`): passive observation collection at every nav fix; `refine_tags.py [--write]` weighted median (only for ROUGHLY surveyed tags!).

## Main scripts (all in `{PI_HOME}/picarx-mcp/`, `python3 <script>`)
| Script | Purpose |
|---|---|
| `preflight.py` / `jonapot.py` ("good day") | session-start check (+ greeting) |
| `whereami.py` | ±70° sweeping self-localization (distance + bearing per tag; 2-tag intersection) |
| `face_tag.py <id>` | bearing-based turn towards a tag (flip-independent) — ⚠️ exits 0 even on STOP! |
| `turn_scout.py [cw|ccw] [target-id...]` | blind turn in place until it sees a target tag |
| `turn90.py left|right [deg]` / `fine_turn.py` | fixed-angle BLIND turn (on open ground it overturns by ~25 %! see smart_turn) / small angle (weak on slippery floors) |
| `smart_turn.py left|right [deg] / to <hdg> / status` | ★ CLOSED-LOOP turn with the BNO055 (measures every cycle, tolerance ±6°, with a metal guard) — replaces turn90 on open ground |
| `go_heading.py <cm> [speed] [--force]` | ★ STRAIGHT driving with heading lock (the cure for pulling left); sonar brake active, --force = threshold mode |
| `erzet.py [json]` ("body sense") | well-being snapshot: light + climate + IMU + floor distance + battery in one line (+ /tmp/erzet.json); GPIO-free |
| `imu.py [loop|calib]` / `lux.py` / `climate.py` / `cliff.py [guard <base>]` | sensor readers (details: "QWIIC sensor chain" section below) |
| `orszem.py` ("sentinel") | ★ BODY-SENSE WATCHDOG: automatic sensing layer — pushes events (`[sense: lifted/tilted | light change | warm | battery | rain sign …]`); pause: `touch /tmp/orszem_off`; fresh snapshot for other scripts: `/tmp/erzet_folyam.json`. **★ SYSTEMD AUTOSTART** (`orszem.service`, runs from boot — the owner's request; events go to `/tmp/orszem_events.log`, chat bridge: Monitor `tail -F /tmp/orszem_events.log`; boot-safe because it uses no camera/GPIO, so the server.py-systemd lesson does NOT apply to it) |
| `lampa.py on/off/villanas/proba` ("lamp": on/off/flash/test) | 3 W LED flash/lamp on **P4** (⚠️⚠️ NEVER P0-P3: the PWM timers come in GROUPS OF FOUR — a frequency set on P3 also changes the P0-P2 SERVOS → servo "epilepsy", happened once!); flash is the default mode, full light while driving is FORBIDDEN (5 V rail) |
| `go_dist.py <cm> [speed] [--force]` | given distance with sonar brake; negative = reverse (rear brake); --force = threshold mode |
| `cross.py <cm> --go` | smart threshold crossing (with an image-based decision) |
| `pilot.py steer speed dur [camtilt]` | manual arc/step + fresh image (/tmp/view.jpg); spd=0 = image only. ⚠️ camtilt applies ONLY to its own photo, afterwards it RESETS the tilt to zero (bitten twice) — for a lasting tilt: `Picarx().set_cam_tilt_angle(N)` + a frame from the stream |
| `zero_steer.py` | steering zeroing FROM THE LEFT |
| `scan_localize.py` / `collect_obs.py` | clean tag fix / cross-observation collection |
| `bemer_tag.py <id> <ref...>` ("survey tag") | survey a new tag (⚠️ into the INNER `"tags"` object of `tags_coords.json`!) |
| `look3.py <prefix>` | 3 reference images (pan -30/0/+30) |
| `robot_say.py --file <path>` | SPEECH (ALWAYS from a file, UTF-8 — accents get mangled in argv) |
| `robot_hear.py [sec]` / `robot_listen_loop.py` | one-off hearing / continuous listener (with Monitor) |
| `robot_mond.py <txt> [gain=8] [max_char=260]` ("say"; runs on the LAPTOP, `<laptop project dir>/picarx-mcp/`) | ★ speaks LONG text in chunks (at sentence boundaries, scp + `robot_say.py --file` in sequence, 1.5 s pause) — ALWAYS use it for >300 characters (robot_say cuts off above ~50 s) |
| `doa.py` / `face_voice.py` | sound direction / turn towards the voice |
| `respeaker_reboot.py` | ear revival (XMOS wedge — needed after every Pi boot!) |
| `home_reflex.py` | docking/homing reflex (latch: /tmp/home_reflex_armed) |
| `teleop/teleop_server.py` | web TELEOP controller for a human driver (WASD + camera arrows, port 8070); LOGS every command to JSONL (imitation-learning demo) — dead-man watchdog + sonar brake + push-through burst. Canonical doc: `teleop/TELEOP.md`. Dry run: `python3 teleop_server.py --dry-run` |
| `utvonalak/route_runner.py` (`utvonalak/` = "routes") | route runner WITH A BOOKKEEPER: executes the recorded route step by step and enforces the DISTANCE DEBT in code (short distance → repay/waive, it does not allow moving ahead); go_dist/turn90/face_tag/whereami as subprocesses + JSONL log. The decision is the agent's, the bookkeeping is the script's. Canonical doc: `utvonalak/ROUTE_RUNNER.md`. Dry run: `route_runner.py start <route> --dry-run` |

## Ear / voice
- **STT: scribe_v2** (v1 was retired — do not switch back). It returns sound labels accurately (`[taps]` = clapping, `[kacag]` = laughing).
- **DOA:** offset 182°, 0° = front, + = right. **The ear aims ROUGHLY → the camera refines.** Next to hard surfaces it measures reflections.
- **★ LISTENER START (boot protocol):** `PYTHONUTF8=1 setsid nohup python3 -u robot_listen_loop.py >> /tmp/fulelo_events.log 2>/tmp/listen.err < /dev/null &` — stdout ALWAYS goes to `/tmp/fulelo_events.log` ("listener events", append), because the laptop-side reader (`robot_olvaso.py`, "reader") tails it; started to any other file, the `[FÜL]` ("EAR") lines do not get through (this once caused a 10-minute gap). **Scribe retry:** `transcribe()` makes 2 attempts (max-time 45); after non-JSON / curl rc 28 it retries once and only then drops the clip (before this fix, sentences were lost).
- **Listener:** with Monitor (`robot_listen_loop.py 2>/tmp/listen.err`); half-duplex (speech lock); stop it BY HAND: `kill -TERM $(pgrep -f '[r]obot_listen_loop')`. Noise gate (300-3400 Hz band ratio <0.35 → drop); label policy: loop = everything alongside speech, on its own only NOTABLE (laugh/sigh/clap/knock/ring/rattle/meow/purr/bark/scream/shout/cry). **Speaker labels too** (`[<name>]:` …) — see the "Speaker identification" section; the daemon must be running. **★ During the speech lock an empty capture queue is NOT data silence** (`read_frame` keeps waiting under the lock) — before this, after 5 s of speech the ear watchdog triggered an XMOS reboot, which killed the listener and cut the playback; still, say long texts with `robot_mond.py` (chunked, ≤260 chars).
- **RAW/SMART switch:** if `/tmp/listen_raw` exists → everything raw; the agent decides (e.g. a lively conversation = smart, outdoor guarding = raw).
- **★ FIELD RECIPE (outdoors):** in loud continuous environments (wind, surf, crowds) on mobile data: (a) SMART mode drops windy speech at the noise gate (band ratio 0.21-0.34 < 0.35 — wind masks the speech frequencies); (b) RAW mode pushes everything to STT → congestion, 19 s ALSA overrun, nothing gets home. **Middle way: VAD volume_threshold 250→550 (only close, loud speech triggers) + raw mode (gate off).** Back home restore BOTH (250 + rm listen_raw)! Scribe itself is fast on mobile data too (3.4 s) — the API is never the bottleneck, the uploaded amount is.
- **Garble layer:** `garble_fix.py` + `garble_hints.json` — mishearing suggestions in the form `⟦→ X?⟧`. **Lesson: do NOT read an instruction out of noisy STT if it could also be a warning — ask back.**
- Purring → `[csörgés]` ("rattle") confusion is known; TV/YouTube = `[háttérbeszéd]` ("background speech"; raises no false event in smart mode).
- The ALSA card number WANDERS → address cards by NAME (sndrpihifiberry / Array); "it works but there is no sound" → `aplay -l`.
- Let the ROBOT announce live tests, not the chat (~9-10 s delay).
- Driving by voice = the normal mode (the human may not see the screen — SAY everything important). If a human moves the robot, the pose jumps legitimately.

## Speaker identification (speaker_id, "4th leg") — who is speaking?
The listener puts a speaker label in front of every recognised sentence:
`[<enrolled name>]:` / `[valószínűleg X]:` ("probably X") / `[ismeretlen hang]:` ("unknown voice").
**Resemblyzer** d-vector (256-dim), cosine comparison; the voice fingerprints (`profiles.json`)
**stay LOCALLY on the Pi, NEVER in the cloud** (voiceprints are biometric data). Setup: `speaker_id/README.md`.

- **Files:** `speaker_id/` — `speaker_id_lib.py`, `enroll.py`, `identify.py`,
  `speaker_daemon.py`, `install.sh`, `speaker_config.json` (thresholds), `profiles.json` (Pi-only).
  Own venv: `speaker_id/.spk-venv` (3.13 + piwheels; ⚠️ `setuptools<81` is REQUIRED — webrtcvad
  imports pkg_resources, which is gone in 81+; install.sh includes it).
- **Architecture:** the loop (system 3.13) does NOT import resemblyzer — `speaker_daemon.py`
  (in the venv, model loaded once) answers on a UNIX socket (`/tmp/speaker.sock`) in the form
  `name|name|score|[label]`, ~**0.3 s** per request. The loop's `speaker_label()` returns "" on any
  error → the ear NEVER falls over because of it. Profiles are re-read on every request → an enroll done
  on the fly is live immediately, WITHOUT a daemon restart (a threshold change, however, DOES need a restart).
- **⚠️ MUST BE STARTED BY HAND AFTER EVERY Pi BOOT** (like the MCP server):
  `nohup ~/picarx-mcp/speaker_id/.spk-venv/bin/python ~/picarx-mcp/speaker_id/speaker_daemon.py > /tmp/speaker_daemon.log 2>&1 &`
  If it is not running → no labels, everything else keeps working.
  - **★ WARM-UP built in** — the first embed takes >5 s cold (measured 5.13 s), and the loop's 6 s timeout dropped it → the first sentence went out unlabelled; the daemon now runs a throw-away embed at startup (enroll_*/*.wav), after that 0.38 s per call.
  - **⚠️ RESTART ONLY VIA THE SCRIPT: `~/picarx-mcp/speaker_id/restart_spk.sh`** (kept in a PERMANENT location — a copy in /tmp vanished after a reboot). Call: `ssh $PI_USER@$PI_HOST 'bash ~/picarx-mcp/speaker_id/restart_spk.sh'` — the daemon's name must NOT appear in the calling command line, not even in an `ls`: an `ls speaker_id/speaker_daemon.py` in the command line was enough for a 255. The script starts with the venv python; the system python cannot see resemblyzer. (Old note: if the script is missing, recreate it: kill via pgrep on the pattern `spk-venv/bin/python .*speaker_daemon`, then start with nohup.) A `pkill -f speaker_daemon.py` written into an ssh command line KILLS ITSELF (the command line contains the name; the `[s]peaker` trick does not help either, because the nohup path is in it too) — exit 255 twice.
  - For short/distant sentences an enrolled speaker scores 0.54–0.72 → `[ismeretlen hang]` (live band 0.87+); a full sentence from close up gets the name. If it bothers you: take a sample from that situation (enroll --wav), do NOT lower the threshold.
- **Stopping:** `pkill -f "[s]peaker_daemon"` — but ⚠️ **IN A SEPARATE ssh CALL** in which the daemon's
  path does NOT appear (the nohup start line in the same command = pkill kills its own shell, silent
  255; bitten twice).
- **Thresholds (tuned with live measurements):** name=**0.82**, maybe=**0.78**.
  How to tune: play recordings of STRANGERS' voices (TV, podcasts, videos — similar-sounding voices
  are the dangerous ones) and log their scores; in our measurements strangers peaked around 0.76, while
  an enrolled speaker's live band was ~0.87–0.97; room noise ~0.6. **Keep a clear GAP between the strangers'
  maximum and the enrolled speakers' minimum**, and put the "maybe" threshold above the strangers' max.
  If an enrolled speaker drops below 0.78 in a noisy/distant situation: do NOT lower the threshold —
  take a SAMPLE from them in that situation (enroll --wav, weighted mean).
- **★ OUTDOOR ACOUSTICS (terrace):** outside the score is ~0.02–0.04 lower (damping/angle) → "valószínűleg <name>" outdoors is EXPECTED behaviour, not an error. Adding ONE audited outdoor sample to the profile (weighted mean; back up profiles.json first) lifts outdoor scores a little. ⚠️ Do NOT pile up more damped samples — they would pull the profile towards the stranger band. + A short (1–2 s) sentence is little data → an unknown label is fair. + ⚠️ THE DAEMON IS PART OF THE BOOT PROTOCOL: once it was not running after three boots and a human noticed — ALWAYS start it after respeaker_reboot!
- **Score log + ring archive (written by the daemon):** `/tmp/speaker_scores.jsonl` (time|wav|name|
  score|label) + `/tmp/spk_ring/` (last 20 examined wavs). With a suspicious label LOOK HERE FIRST —
  /tmp/utt.wav is OVERWRITTEN by the next sentence (the first false positive was lost that way,
  which is why the log was built). Live tuning run: play a stranger's voice → tail the log → threshold into the
  config → daemon restart → re-measure on the archived wavs.
- **Enroll:** `$VENV/bin/python enroll.py <Name>` (3×10 s, with ENTER) OR — the elegant way —
  during live listening `cp /tmp/utt.wav /tmp/enroll_X_N.wav`, then `enroll.py <Name> --wav <files>`:
  the sample comes EXACTLY from live conditions and is merged into the existing profile with a weighted mean
  (one speaker: 3→5 samples this way, 0.869→0.887 independent score). A new person = just a new enroll.
  Habit plan: "in an unfamiliar situation I speak up and we take a sample" — the profile ages together with the person.
  **★ First real use (after an SD-card rebuild): a HAPPY/excited voice dropped to 0.72–0.77**
  (short, cheerful sentences — the speaker said so themselves: "this is my happy voice", exactly as planned) →
  3 happy samples enrolled from the ring archive (audit: score log + context; the `[nevet]` ("laughs") segment
  was left out) → profile with 10 samples, re-measured: 0.77→0.83 [name], 0.73→0.79 [valószínűleg name].
  A VERY short sentence ("Oh, I'm so glad", 0.59→0.62) still stays unknown — that is the
  too-little-data limit, not a tuning error.
- **Known limits:** (1) a VERY SHORT sentence ("Yeah") → little data → often `[ismeretlen hang]`;
  (2) loudspeaker playback comes closer (room + ear share the colouring), and a stranger's FEMALE voice is the delicate
  case — the four-voice test CONFIRMED it (one narrator even passed 0.75; the 0.78/0.82
  threshold pair is set against this); (3) it does NOT label gender, only known/unknown; (4) a speaker label may also land on a
  sound-event segment (`[kopogás]` = knock, `[csengő]` = bell) — cosmetic, to be refined.
- **Off/on:** `touch /tmp/speaker_off` → no labels; `rm` → back. (Same pattern as listen_raw/hangkep_off.)
- `[ismeretlen hang]` = a natural guest signal (a friend of the unsolicited-demo protection).

## Soundscape (YAMNet sound-event recognition) — extended hearing
YAMNet (521 AudioSet classes) runs on **EVERY sound segment** of the listener: rain, wind, cricket, birdsong,
dog, cat, vehicle, train, clatter, knocking, music… (Originally it only ran on the noise gate's drop branch, but
TONAL animal sounds — meowing, barking — pass the gate as the speech band and Scribe mislabels
them; so it was rewired to run on everything.) Half-duplex respect: **it opens no separate microphone**, it works from the already
recorded `frames` buffer; under `robot_speaking.lock` the loop is not listening anyway.
It NEVER breaks the ear (on any error it silently skips).

- **Runtime:** a separate **python 3.12 venv** (`.yamnet-venv/`, made with `uv`), because for the system 3.13
  there is NO `tflite-runtime`/`ai-edge-litert` aarch64 wheel. Inside: `ai-edge-litert` (LiteRT, the
  successor of tflite-runtime) + `models/yamnet.tflite` (~4 MB, Kaggle/TFHub) + `models/yamnet_class_map.csv`.
  The system loop (3.13) calls it **as a subprocess**. Latency on the Pi: model load ~34 ms,
  inference ~8 ms per 0.975 s window (well under real time).
- **Files:** `hangcimke.py` ("sound label"; YAMNet classifier, runs in the venv; `--json` / `--serve` / human mode),
  `hangkep.py` ("soundscape"; state + change-event engine, imported by the loop, pure stdlib),
  `hangkep_labels.py` (English→Hungarian dictionary, ~210 classes — so the labels the robot emits are Hungarian), `hangkep_config.json` (tuning).
- **Queryable state → `/tmp/hangkep.json`:** `now` (what I hear now), `now_scored`
  (with scores), `background` (background-carpet labels), `recent` (last N events with timestamps).
  **During active work, ask for this** ("what do I hear now?").
- **Autonomous event:** a NEW, distinct sound → **STDOUT `[hang: <label> <score>]`** (`hang` = "sound"; Monitor → chat).
  **Settling:** after the first report a continuous sound (e.g. a cricket carpet) becomes background and
  does not speak again for `background_ttl_s`; if it disappears for longer than that, it is forgotten → **it may speak again**
  (e.g. wind picking up). The label is the Hungarian name of the top-scoring class (stays English if unknown).
- **Tuning (`hangkep_config.json`, reloaded by the loop while running):** `score_threshold` 0.25
  (presence threshold), `event_threshold` 0.40 (event threshold for a NEW sound), `background_ttl_s` 180
  (settling/forgetting seconds), `min_event_gap_s` 3 (global anti-spam; the SAME sound is
  caught by settling, not by this), `max_events_per_ingest` 2, `recent_events_keep` 12, `ignore_labels`
  (acoustic-scene/noise meta classes: Silence, Noise, White noise, Inside…). Do **NOT** write into
  voice_config.json (it contains a secret).
- **Off/on:** **ON** by default. Off: `touch /tmp/hangkep_off` OR `hangkep_config.json` →
  `"enabled": false`. Back: `rm /tmp/hangkep_off`. (Follows the RAW/SMART `/tmp/listen_raw` pattern.)
- **Score = per-window MAXIMUM per class** (not the mean!): in a long segment a short
  event (two meows between silences) is diluted by the window mean — live measurement: cat 0.155
  with the mean vs **0.852** with the max on the same recording. (`hangcimke.py scores()`.)
- **Umbrella-label rule (`generic_labels`):** generic labels (Animal, Domestic animals,
  Insect, Music, Vehicle…) speak as an event ONLY if there is no more specific hit
  in the same segment (next to "cat", "animal" stays silent; on its own, "animal" does report).
  They are still visible in the soundscape state. + Within one segment `min_event_gap_s` practically
  narrows the event down to the strongest ONE label.
- **VAD threshold: 250** (`voice_config.json` → `vad.volume_threshold`, the owner's decision:
  "don't raise the detection threshold indoors either, or you'll be half-deaf"). With the old 500
  threshold, quiet real sounds (garden hum, distant birds, cat noises) NEVER reached the ear.
  Keep a backup of `voice_config.json` before tuning.

### Field lessons (first soundscape day: played-back samples + live terrace)
- **A played-back sample distorts, live it is right:** on a phone speaker rain → "stream/water",
  wind → "bee buzzing" (the small speaker cuts the lows); the REAL terrace wind, however, came out
  as `szél 0.41` ("wind") on the first try. Do NOT judge texture sounds (rain, wind, thunder) from playback.
- **The `rádió` ("radio") label = a recording detector:** with a played-back train the soundscape showed `rádió 0.80`,
  with a real passing train NO radio at all → the ear hears when something comes from a loudspeaker.
  For a source question (real or recording?), check this.
- **Label-family wandering defeats settling:** one persistent deep rhythmic source (a flapping
  awning + a car starting) re-triggered in sequence as Heart sounds→Throbbing→Heart murmur→Stomach rumble
  (settling is per label). Fix: the body-sound labels are in `ignore_labels`
  (meaningless for deep outdoor rumble). If a new family pops up, do the same: ignore, do not raise the threshold.
  **+ On a windy day a flapping awning/tarp can also sound like a THUNDERSTORM/THUNDER (at 0.98!)** —
  before an outdoor storm alarm check whether the wind is blowing + something is flapping (or ask
  a human — human eye > spectrogram). Do NOT ignore the thunderstorm family (it protects against real rain
  outdoors!), just know: on a windy day the hit is suspicious.
- **Outdoor sound vocabulary (decode it with the people who live there):** `[kopogás]` ("knocking") often = an outside DOOR closing;
  rhythmic clicking (a `[nyomtató]` ("printer")-like label) = THERMAL EXPANSION of a roof popping in the heat;
  `[csengő]` ("bell") = a wind chime. In a familiar outdoor spot these are part of the base noise, not events.
  **+ `[kopogás]` can also be a loose box or shelter rocking
  when an animal walks on it — the door reading is not exclusive.**
  **+ RAIN on a roof = `[gépelés]`/`[Typewriter]` label ("typing"; confirmed by a human:
  "That's the rain all right, not typing")** — the quickening drops sound like
  keyboard rhythm; escalation sequence: sporadic `[kopogás]` → `[gépelés]` → `[Typewriter]` → `[eső]` ("rain").
  For outdoor rain detection this sequence is the early warning, even before the rain label.
  **+ A CAT'S CHATTERING = `[bagoly]` ("owl") label, even at 0.92! (decoded by a human:
  "it chatters like a barn owl").**
  If a cat is nearby outdoors and `[bagoly]` comes: check the cat hypothesis first.
  High confidence does not protect against species confusion if the sound really is similar.
- **Rare species → nearest related label (bee-eater case):** bee-eaters
  were heard by YAMNet as "Fowl / chicken, rooster / livestock" (there is no bee-eater
  class among the 521) — the label is raw material, the meaning comes from context (place + season +
  what the people around say + accompanying labels: the bee/wasp = the bee-eater's lunch). With an exotic label
  first ask: WHAT would sound like this here?
- **The background carpet knows more than the event:** distant birdsong crossed the presence threshold (0.25)
  but not the event threshold (0.40) → it showed up in the `background` of `/tmp/hangkep.json`
  ("madár" = "bird"), without an event. For "do you hear the birds?"-type questions query the STATE,
  do not wait for events. The raw top list BELOW the presence threshold comes from running `hangcimke.py
  /tmp/hangkep_in.wav` by hand (the wav of the last segment is always there).
- **The engine's state lives in memory** → on a loop restart `recent`/`background` are reset
  (/tmp/hangkep.json is rebuilt). Not an error, just know it.
- **Speech labels are in the ignore list** (Speech, Conversation…): speech is transcribed by Scribe; a
  soundscape event from it would only be spam.
- **Field lessons (teleop evening):**
  - **While driving, the robot's OWN drive noise gets labelled:** wheel/servo whine produced
    `telefoncsörgés` ("phone ringing"), `Bicycle`, `Mechanisms`, `varrógép` ("sewing machine"), `Electric toothbrush` in sequence.
    If the robot IS MOVING (manually or autonomously), such machine-family labels are self-noise,
    not world events — interpret them together with the motion state.
  - **Confidence rule of thumb:** 0.9+ is credible (`madár 0.99` was a real blackbird through an open
    window, although we suspected someone whistling); 0.4–0.6 is only a hypothesis (`horkolás` ("snoring"),
    `Fart`, `pénztárgép` ("cash register") were all self-noise/mishearing in this band).
- **Model/venv reinstall** (Pi-only, not in the code mirror):
  `~/.local/bin/uv venv --python 3.12 {PI_HOME}/picarx-mcp/.yamnet-venv` →
  `uv pip install --python .yamnet-venv/bin/python ai-edge-litert numpy soundfile` →
  `models/yamnet.tflite` from the MediaPipe URL (`storage.googleapis.com/mediapipe-models/audio_classifier/yamnet/float32/latest/yamnet.tflite`)
  or from the Kaggle google/yamnet tfLite package; the class map from the tensorflow/models repo.

## Live video stream (go2rtc)
Extended visual continuity: instead of snapshots, a continuous
source. A **go2rtc** service owns the camera (`stream/cam_source.sh` →
`rpicam-vid` **HW-H.264**, with the Pi 4's hardware encoder), WebRTC + MJPEG/HTTP on the LAN.
The stream **is tied to ACTIVE mode** (the existing active/passive pattern): it lives while active,
on inactivity it quietly stops and releases the camera. **By default it is NOT running.**

**Main gain (for the agent):** (1) **looking back at the recent past** — the rolling buffer is the one thing
that on-demand `look` could NEVER do: see what happened BEFORE an event; (2)
`look` becomes warm-up free (it gets the frame from the stream in ~5 ms); (3) a live viewer window for the household.

- **Files** (`stream/`; the `go2rtc` binary is **Pi-only**, not in the code mirror):
  `go2rtc.yaml` (config: api 1984 / rtsp 8554 / webrtc 8555, LAN only), `cam_source.sh`
  (rpicam-vid wrapper, reads the rolling exposure), `stream_lib.py` (shared state +
  primitives, stdlib), `stream_supervisor.py` (lifecycle daemon), `stream_ctl.py` (manual
  controller), `visszanez.py` ("look back"; rolling-buffer query).
- **Active-mode binding:** `mark_chat_active()` also updates the file `/tmp/robot_active_at`;
  the supervisor watches it (config `active_timeout_s`, default 60 s). `server.py` starts the supervisor
  in a guarded way at startup. By hand: `stream/stream_ctl.py start`.
- **Manual control (`python3 stream/stream_ctl.py …`):** `start` / `stop` (closes everything) ·
  `status` (JSON) · `on` / `off` / `auto` (manual on / manual off / back to active mode) ·
  `nav on|off` (NAV MODE, see below) · `frame [path]` (latest stream frame, a test of the look
  fast path) · `adaptive [path]` (on-demand adaptive "special eye", see below) ·
  `url` (the viewer window).
  Touch-file pattern: `/tmp/stream_on`, `/tmp/stream_off`, `/tmp/stream_pause`, `/tmp/stream_nav`.
- **⚡ NAV MODE (tag path has priority):** during navigation every tag photo
  (`core.capture.capture`) makes the stream release the camera via a self-expiring lease; the lease TTL is 4 s,
  so BETWEEN two nav fixes (>4 s) the stream half-restarts and the next fix stops it again →
  "pumping" (CPU thrash + each fix 2-3 s slower; measured: a fix next to the stream **~4.3 s** on average
  vs. **1.8 s** without the stream). NAV MODE is a LONGER (TTL 30 s) flag (`/tmp/stream_nav`)
  that is switched on by hand at the START of a nav segment (`stream_ctl.py nav on`); every capture
  EXTENDS it (does not ACTIVATE it — a standalone whereami/look/face_tag stays unchanged and recovers with the 4 s
  lease). During nav the stream stays paused THROUGHOUT (go2rtc frozen); at the end of the segment
  (`nav off` or 30 s without a capture) it restores ONCE. **So a tag photo in nav mode costs only
  ~+0.05 s compared to the no-stream pace** (measured: 1.87 vs 1.82 s). By default the
  nav flag is NOT set → behaviour unchanged. **`nav on` at the start of a nav segment, `nav off` at the end.**
- **`look`/`scan` redirection (automatic, transparent):** if the stream is running and the camera **looks straight ahead**
  (`look` pan=tilt=0), the image comes from the stream IMMEDIATELY (`stream_latest.jpg`, ~5 ms, no
  warm-up). When looking around (pan/tilt≠0) or with `scan`, the stream temporarily releases the camera,
  the ORIGINAL path runs, then the stream is restored. **If the stream is not running, everything goes the original rpicam-still
  way (fallback).** Any stream error also falls back to the original path.
- **The standalone tag/nav scripts do NOT break** (`whereami`, `face_tag`, `turn_scout`,
  `turn_to`, `goto_route`, `scan_localize`, `look3`… — they all call `core.capture.capture()`):
  `capture()` uses a **self-expiring camera lease** (`/tmp/stream_cap_lease`, TTL 4 s)
  to make the stream release the camera for the duration of the capture burst; at the end of the sweep the stream
  restores itself — no per-frame churn, and no script had to be modified. **Exception:
  diagnostics that call `rpicam-still` directly** (`pan_sweep`, `detect_tag`, `cal_motion`,
  `dr_check`, `localize_test`, `map_tag`, `goto_dr/xy/y`) are NOT auto-protected — if they would run while
  the stream is up, first `stream_ctl.py off` (or wait for the passive stop).
- **Look-back (rolling buffer, ~5 MINUTES):** the supervisor continuously fills the `/tmp/rbuf/`
  ring buffer (2 s `.ts` segments, 150 in a ring = ~5 min, H.264 copy, tmpfs, ~150 MB).
  Originally it was 60 s — the owner's input: the event→notification→thinking→SSH chain can take 1–2 minutes too;
  a waving test just slipped out of it → the buffer must be LONGER than the slowest response.
  Retrieval: `python3 stream/visszanez.py 10` → `/tmp/visszanez.mp4` (last 10 s) ·
  `python3 stream/visszanez.py 8 --frames 6` → 6 evenly spaced JPGs (`/tmp/vn_XXX.jpg`).
  Segment names wrap around → chronology is given by **mtime**, not by the file name.
  **Do NOT search for a fast movement with sparse sampling** — use motion search (waving test):
  `visszanez.py 58` → `ffmpeg -i /tmp/visszanez.mp4 -vf "select='gt(scene,0.008)',showinfo" -f null -`
  gives the motion timestamps (pts_time), then extract targeted frames with `-ss <t>`. (Motion reveals what a snapshot hides.)
- **★ SOUND→PAST→MEANING chain (validated on a terrace — the owner's idea):** if the agent hears a sound
  and wants to know WHAT caused it, connect the look-back with video-mcp:
  1. The stream must be running (`stream_ctl.py on`) — without it there is no past!
  2. A sound event arrives (listener `[hang: X]` / STT line) → note the TIME.
  3. `python3 stream/visszanez.py 90` → /tmp/visszanez.mp4 (a LARGE window — the agent's reaction time is
     20-40 s, the event can slip out of a smaller window).
  4. scp to the laptop → `mcp__video-mcp__understand_video(source=<mp4>, prompt=...)`.
  5. Prompt principle (the owner's): do NOT aim only at the moment — the whole PROCESS is needed (what led
     up to it + what came after); the event is at roughly (clip_length − event_age) seconds;
     write this into the prompt as an orientation point.
  Measurements: a 60 s clip ≈ 30 MB, 90 s ≈ 45 MB; Gemini returns a timestamped report
  with surprising detail (nail-polish colour, a ring!). Limits: (a) only the FIELD OF VIEW —
  for a sound from behind the robot the clip will not contain the source (say so in the report);
  (b) the clip is SILENT (H.264 copy, there is no audio in the stream) — you supply the sound context
  in the prompt; (c) ⚠️ premature-investigation trap: first make sure the sound really was
  the event you are looking for (once the agent "solved" a puzzle before it had even started —
  and exposed an everyday flowerpot being put down).
- **⚡ MOTION-BLUR FIX (capped shutter):** the home is dim (measured **Lux ~28**),
  so rpicam's built-in auto exposure opened the shutter to the FULL frame time (15 fps → **~66 ms**)
  with gain 8 → catastrophic blur while moving (rolling/pan). Fix: the stream's
  exposure **CAPS the shutter at 16 ms** and **gain carries** the light (max 32 — 44 was already too
  noisy). `cam_source.sh` default (no expo.env): `--gain 24 --shutter 16000` (blur-free
  from the 1st frame); the supervisor's rolling corrector tunes to the room light — `_stream_expo_to_gs`
  caps the shutter; in bright light the gain drops to 1 and the shutter slides BELOW the cap (even sharper), in the dark
  the gain rises to the ceiling. **The tag path of `capture.py` is UNCHANGED** (a long shutter is no problem for a settled
  still — there a clean tag is the goal, not motion). Measurement (pan sweep, Laplacian variance):
  worst-case sharpness **5.9 → 43.0 (7.3×)**, median **60.8 → 201 (3.3×)**; brightness 85 → ~55 (the price of
  the 28-lux room + gain cap — the still image is still readable: e.g. a printed label and a door handle are legible).
  Rollback: keep a copy of `cam_source.sh` before changing it, plus the supervisor's `STREAM_*` constants.
- **Exposure:** (a) the stream's **own exposure** gets a rolling correction — the supervisor
  looks at the histogram of `stream_latest.jpg` every ~20 s, and if it is persistently under/over-exposed,
  it writes a manual gain/shutter into `/tmp/stream_expo.env` using the capped logic of `_stream_expo_to_gs`
  and restarts the source (rare, with hysteresis, for a change of at least 25 %; the first correction scales from the capped
  default → it adapts to the room light within ~5 s). (b) The ORIGINAL
  per-frame adaptive `capture.py` **stays as an on-demand "special eye"**: with `stream_ctl.py
  adaptive` the stream briefly releases the camera, the old smart tuning runs (dark corner,
  sunlit tag, long-exposure night sky), then the stream is restored.
- **Live viewer window (LAN ONLY, no port forwarding):** `http://<robot-ip>:1984/stream.html?src=robot`
  (or the go2rtc start page: `http://<robot-ip>:1984/`).
- **Measurements (1280×720@15, 4 Mbps):** `look` from the stream ~**0–1 ms** (vs. the original
  ~0.5–1 s warm-up); CPU during streaming: ffmpeg ~40 % + rpicam ~5 % + go2rtc ~3 % of one core
  (≈12 % of the 4 cores); temperature 45→58 °C; ring ~0.5–1 MB/segment → ~20–30 MB per 60 s (tmpfs 1.9 G);
  the go2rtc frame API is slow (~2–4 s, waits for a keyframe) → AVOID IT, read from `stream_latest.jpg`.
- **⚠️ SELF-PKILL trap:** NEVER `pkill -f "stream/go2rtc"` or `pkill -f rtsp://…` — the pattern also matches
  the argv of YOUR OWN SSH command → it kills its own shell (silent 255, lost
  output). Use an **exact-name** kill: `pkill -x go2rtc`, `pkill -x rpicam-vid`, `pkill -x ffmpeg`.
- **Reinstall** (Pi-only, the `go2rtc` binary is not in the mirror):
  `cd {PI_HOME}/picarx-mcp/stream && curl -sL -o go2rtc https://github.com/AlexxIT/go2rtc/releases/latest/download/go2rtc_linux_arm64 && chmod +x go2rtc`
  (static aarch64, no sudo; the other scripts come from the code mirror via scp).

## Telegram PHOTO sending
The telegram-mcp `telegram_send` is TEXT ONLY. For an image call the Bot API directly
(sending does not conflict with the sync daemon's getUpdates — the 409 trap only applies to receiving):
```python
cfg = json.load(open("<laptop project dir>/telegram-mcp/config.json"))  # bot_token + chat_id
requests.post(f"https://api.telegram.org/bot{cfg['bot_token']}/sendPhoto",
              data={"chat_id": cfg["chat_id"], "caption": "..."},
              files={"photo": open(kep_utvonal, "rb")})  # kep_utvonal = image path
```
Use: intruder/guest report with a photo, pet-photo service, travel reports.
⚠️ NEVER print the token into chat/logs.

## WIFI
- Recipe in the agent's memory: power-save always OFF; garden=2.4 GHz, indoors=5 GHz (sudo = the owner); garden dead zone: chat+voice combo.
- **HOTSPOT MODE (dress rehearsal ✅):** a `travel-hotspot` NetworkManager profile on the Pi (autoconnect priority 20 > home 0 → without home Wi-Fi it connects by itself). Short version: hotspot ON → laptop onto it too → the Pi's IP from the phone's device list or a ping sweep → ssh with `accept-new` → MCP config with the new IP + app restart. Lifeline: hotspot OFF = the robot switches back home.

## Servo/camera access while the MCP server is running
- **`Picarx()` CANNOT be instantiated from a separate process** while `server.py` is running: init calls `reset_mcu()`, which claims the MCURST GPIO pin → `lgpio.error: 'GPIO busy'` (the server holds the pin).
- **Workaround for servos**: use `robot_hat.Servo` directly, without an MCU reset. Camera TILT = channel **P1**; because of the picarx convention `set_cam_tilt_angle(v)` ≡ `Servo('P1').angle(-(v - cali))`, the tilt cali is currently 0 (`/opt/picar-x/picar-x.conf`). E.g. 12° UP: `python3 -c "from robot_hat import Servo; Servo('P1').angle(-12)"`. (Pan = P0, steering = P2.)
- **Workaround for images**: `core.capture.capture()` also works standalone (no Picarx needed, and it handles the stream lease too). If the stream is stopped (common in adaptive mode), plain `rpicam-still` is fine too.
- **MCP `look` tool `Errno -25`**: after a systemd boot the server's camera init got stuck (it was initialized during a hiccupy boot) — since then the server is started by hand (the systemd unit was REMOVED). If look throws -25 again with a healthy camera (rpicam-still OK), the server's camera layer is the suspect → restart the server (+ a new agent session for the MCP connection).
- **ssh + sudo**: sudo embedded in a command NEEDS `ssh -t` (TTY; otherwise there is no password prompt and it dies silently).


### ★ ReSpeaker USB DISAPPEARANCE
Symptom: the card disappears COMPLETELY from lsusb (2886:001a nowhere), dmesg: "USB disconnect" — typically after an XMOS hang during respeaker_reboot.py. Then NEITHER a software reboot NOR re-plugging the microphone-side cable helps (the kernel sees no plug event at all, although the LEDs are lit — the card's internal USB core froze). **Old fix: power-cycle the WHOLE ROBOT** (by hand), then the usual boot recipe: respeaker_reboot.py + preflight. **★ Addendum (while travelling): a REMOTE reboot also cures it** — `ssh $PI_USER@$PI_HOST "sudo -n shutdown -r now"` works without a password (the NOPASSWD shutdown rule covers -r too); for a USB drop-out on the go (dmesg: "USB disconnect", LEDs lit = power yes, data no; re-plugging the cable gives 0 dmesg events) this is the fastest route, no human hands needed. Diagnosis order: lsusb 2886 → dmesg plug event → if neither: reboot, stop fiddling with cables.

**★★ UPDATED — THERE IS A SOFTWARE FIX, NO power cycle NEEDED** (validated live, in the first session after the SD-card rebuild; important because cutting power with the switch is now FORBIDDEN, see `leallit.py`): it is the HUB that must be re-enumerated, not the card. The ReSpeaker sits behind the VIA Labs hub (`2109:3431`), so unbinding/binding the hub carries a port-power cycle through to the card too:
```bash
echo "1-1" | sudo tee /sys/bus/usb/drivers/usb/unbind ; sleep 3
echo "1-1" | sudo tee /sys/bus/usb/drivers/usb/bind   ; sleep 7
lsusb | grep 2886 && arecord -l | grep "^card"
```
(`1-1` = the hub's sysfs name, shown by `ls /sys/bus/usb/devices/`.) **The card comes back, but the XMOS may STILL be wedged at this point** (preflight: "ReSpeaker jelen" ("ReSpeaker present") + "mikrofon próbafelvétel 0 byte" ("microphone test recording 0 bytes")) → a `respeaker_reboot.py` is STILL NEEDED afterwards. **Full recipe after boot:** respeaker_reboot → if it disappeared: hub unbind/bind → respeaker_reboot → test recording (`arecord -D plughw:4,0 -d 1 /tmp/t.wav`, ~32 kB when healthy). The old "only a power cycle helps" verdict is OBSOLETE — always try the hub cycle first.


## QWIIC sensor chain
The robot's newer senses (canonical).
**Chain on i2c-1 (the HAT's white SH1.0 socket), bus slowed to 10 kHz** (`/boot/firmware/config.txt`: `dtparam=i2c_arm_baudrate=10000` — because of the BNO055's clock stretching; side effect: slower ToF ranging → patient readers with timeouts).
| Address | Sensor | Purpose | Reader |
|---|---|---|---|
| 0x10 | VEML7700 | light (lx) — on the roof, facing up | `lux.py` |
| 0x28 | BNO055 | heading/roll/pitch (drift-free direction + posture) | `imu.py` |
| 0x29 | VL53L1X | DOWNWARD-facing distance (cliff guard, terrace edge) | `cliff.py` |
| 0x77 | BME280 | temperature/humidity/pressure (⚠️ Adafruit address 0x77, NOT 0x76!) | `climate.py` |

**Rules (learned live):**
- **BNO055 instantiation = SOFT RESET → the calibration is lost!** The offsets are kept in `bno055_offsets.json` (from a calibration dance); `imu.py`'s module init reloads them at EVERY start (CONFIG_MODE → offsets → NDOF). In a new script: `import imu`, do NOT instantiate a separate BNO055.
- **GPIO single-process rule:** heading reader + motor controller in ONE process (`import imu` + `Picarx()` in the same place); a turn subprocess next to a Picarx wrapper = GPIO busy.
- **Orientation:** BNO055 flat, chip up, **Y+ = forward, X+ = right**; heading uses compass convention (increases CW). The home's "west" ≈ **319°** (at a metal-free spot; indoors it may be distorted).
- **Metal guard:** near metal (the oven!) the magnetometer distorts — `smart_turn.py`/`go_heading.py` take a 5-sample median + spread while standing still and warn if it jumps by >6°. Next to metal the blind turn90 + camera may be more reliable.
- **VL53L1X + glass:** it can see through glass (glass table!) — do NOT trust the cliff guard on glass surfaces. A final overhanging mount is still OPEN — until then the floor-distance direction depends on the mounting.
- **Preflight:** section 6b) checks all 4 addresses with i2cdetect (missing = loose cable; split the chain one by one).
- **⚠️ Watchdog: ONE instance:** the watchdog is a systemd service (runs from boot); the chat-bridge Monitor command must be EXCLUSIVELY `tail -n 0 -F /tmp/orszem_events.log` — NEVER start a second `orszem.py` from a Monitor: once two instances ran for a whole evening (separate baselines, shared I2C bus) → repeated false "ferde" ("tilted"), duplicated climate events, and a patch seemingly "had no effect" because the Monitor instance was running the old code. Check: `pgrep -af '[o]rszem.py'` = exactly the PID of `systemctl show orszem.service -p MainPID`; `kill -TERM` any orphan.
- **Watchdog warm-up:** after a restart the first IMU and BME readings fluctuate (false "ferde" ("tilted"), "párás" ("humid"), "eső-előjel −440 hPa" ("rain sign")) → `orszem.py`: during the first 6 rounds (BEMELEG_KOR) only battery alerts go out (`kurt.szol` guard), "ferde" needs 2 consecutive rounds, and the climate branch is skipped for impossible pressure (<850/>1100 hPa) or humidity. ⚠️ Patch rule for a live service: write to `.new` → `py_compile` → only then swap (the first attempt caused a crash loop with a syntax error; restored from backup).
- **Watchdog temperature filter:** the climate sensor sometimes returns impossible values (−9 °C in a 27 °C room, four times in one evening) → `orszem.py`: t<0 or t>60 or |t−base|>15 °C = artefact, dropped, the baseline is not updated. Same principle as for pressure jumps in the rain sign. Keep a backup of `orszem.py` before such a change; the service can be restarted with `sudo -n systemctl restart orszem.service` (works without a password).
- **Watchdog (automatic sensing):** `orszem.py`, modelled on the listener, reports changes AS EVENTS (tilted/lifted, floor jump, light change, CPU temperature, battery transition, rain sign) — the signal is the watchdog's, the decision is the agent's. Recommended to pause it while a motion script runs: `touch /tmp/orszem_off` (to spare the bus); other scripts can read the fresh snapshot in `/tmp/erzet_folyam.json` WITHOUT re-instantiating the sensors (towards avoiding the BNO055 soft reset).

## ★★ Motor balancing and the STOP trap (canonical)

> ### ✅ MOTOR REPLACED (soldered by hand)
> The new TavIR TT motor (1:48, T49729) is in the right rear position. **New baseline: motor_balance [0,0]**
> — the fresh measurement drifts only −1.6° per 1.4 s (the worn one did −11…−25); the suggested +3 brake
> was rejected because of the positive-cali ban, heading hold carries it. **Rotation direction inverted in software**
> (`px.motor_direction_calibrate(2, -1)`, saved to config) — ★ LESSON: the rotation direction tested IN THE HAND is NOT the same
> as the installed one (mirrored orientation) — check again AFTER installation!
> **V_PONTOK RESCALED (both drive scripts):** `[(30, 28.6), (55, 30.8), (80, 34.9)]` —
> calibrated with a tape measure (100 command → 168.5 real with the old curve → ×1.685 scale),
> and VALIDATED: 100→100.0 · 200→196 (the −2 % belongs to the heading-correction arcs, accepted). The old motor's
> capacitor lesson: the amber bead between the TT motor's tabs = a suppression capacitor (EMI protection
> towards i2c/camera/wifi) — on replacement the new one needs it too / it must be resoldered. A spare new motor is in the drawer.
> The worn-motor rules below are now HISTORICAL, but the stop-trap ban and the fresh-measurement principle STILL APPLY.

**The system:** the right rear motor WAS worn (its forward direction had friction, its state fluctuated
ON A MINUTE SCALE — ✅ REPLACED, see above). The layers of software balancing:
- `core/motor_balance.py` — loads the cali from `motor_balance.json` (`apply(px)`);
  the 4 main drive scripts (go_dist, go_heading, pilot, smart_turn) apply it automatically.
- `motor_kalib.py` — IMU-based drift measurement with several corrections + interpolation + save.

**⚠️⚠️ THE STOP TRAP (bitten twice, live):** picarx's `set_motor_speed`
**subtracts the cali even at speed 0** → with a POSITIVE cali (braking) 0−N = negative PWM =
UNDERFLOW → **the wheel spins backwards instead of stopping, and the HAT MCU may even get stuck**
(then no zero write has any effect, only `from robot_hat import reset_mcu; reset_mcu()`).
Therefore:
1. **POSITIVE (braking) cali is FORBIDDEN** with this firmware — only negative (accelerating) values.
2. `motor_balance.apply()` wires in `_wrap_stop_safe`: on stop (speed=0) it temporarily
   zeroes the cali → a clean stop. **CONFIRMED LIVE** ("good with the protection wired in").
3. Emergency recipe for a stuck wheel: fresh `Picarx()` + `cali_speed_value=[0,0]` + stop;
   if that is not enough → `reset_mcu()`; if that fails too → leallit.py + switch.

**★ THE USAGE RECIPE (because the motor's state changed from minute to minute):** a static value does NOT
work (in a bad phase the acceleration has no effect, in a good phase it overshoots — all three measured).
Instead: **FRESH MEASUREMENT + IMMEDIATE USE**: right before the drive block
`motor_kalib.py` (or a mini sweep: right 0/−6/−12, 1.2 s pushes, IMU delta,
linear interpolation to zero drift) → save → go. On an outdoor run −17
went through flawlessly this way. Re-measure after a longer break / terrain change.
**Baseline: 0/0** — then go_heading's active heading hold carries it (that always works).

**Spin test (comparing motor strength in hand/on the ground):** per motor a separate
`set_motor_speed` for 1 s, spin measured with the IMU — symmetric (±22°/s) = a good phase;
asymmetric = a bad phase (then move cables/observe). The GPIO rule still applies:
everything in ONE process!

## Sonar verification to the centimetre (lesson learned)
Calibration check with a box, measured by hand with a tape: 30 cm real → 31.6 median; ~68 → 68.4; 140 → 139.8. **The sonar is FLAWLESS and stable (spread <1 cm, bias max ~1.5 cm up close).** Consequence: if the sonar "mismeasures", the sensor is NOT at fault but the GEOMETRY — (a) open gap under furniture: the beam sees through underneath and measures the space behind (bedside-table case); (b) convex/woven surfaces (laundry basket) and textiles scatter/absorb; (c) the wide beam measures the nearest reflector, not what the camera looks at. Diagnosis recipe: if sonar and tag/image distance disagree, the sonar sees some OTHER surface — identify WHAT from the image.
★ 4th data point: 220 cm real → 218.7 measured — the sonar is verified over the whole 30-220 cm range, everywhere within 1.5 cm.

## ★ Gentle shutdown — `leallit.py` ("shut down")

**The rule: touch the power switch only AFTER the software shutdown.** Cutting power with the switch
can hit the card mid-write — when an SD card died, the post-mortem found
86 fsck scars on the boot partition; each scar is the memory of a hard power cut.

- **Usage (at the end of a session, over ssh):** `python3 leallit.py` (with an espeak goodbye) / `--nema` ("silent")
- What it does: lamp off → goodbye → gently puts the known loops/daemons to sleep (listen_loop,
  orszem, speaker_daemon, MCP server, go2rtc) → double `sync` → `sudo shutdown -h now`
- **The switch may be pressed once the green ACT LED has gone dark FOR GOOD**
  (at the end of the shutdown ~10 farewell blinks, then silence — that is the sign).
- **One-time prerequisite** (run by the owner: password-less shutdown permission for the robot user):
  ```
  echo '<pi-user> ALL=(ALL) NOPASSWD: /usr/sbin/shutdown' | sudo tee /etc/sudoers.d/010_pi-shutdown
  sudo chmod 440 /etc/sudoers.d/010_pi-shutdown
  ```
- **SD-card lessons from the same incident:** (1) the microSD must be pushed in ALL THE WAY — vibration
  can work it outwards over weeks (preflight item: with suspicious boot behaviour this is the first
  check); (2) a wearing card still reads fine SLOWLY but no longer fast — "works in the laptop,
  not in the Pi" = sign of a dying card; (3) keep a spare card in the house.

### ★ SD-card rebuild recipe (VALIDATED — the robot runs on this system)
A complete rebuild onto a fresh Raspberry Pi OS in ~2.5 hours, from a mirror image. Key tricks:
- **On the LAPTOP the mirror's ext4 partition is read with the `python-ext4` pip package** (7-Zip
  breaks on it with "Headers Error"; the FAT32 boot partition, however, 7-Zip can handle) —
  so the 54 GB transfer CAN BE SAVED, only the treasures are extracted (pattern of `banyasz.py` ("miner"):
  p2 offset from the MBR = start_lba×512; `ext4.Volume(f, offset=..., ignore_checksum=True)`,
  `inode_at(path)`; the dirent name must be cut to `name_len`!).
- **Treasure list that was really needed:** speaker_id (profiles+enroll), bno055_offsets,
  voice_config (secret), camera_calib+config, tags_coords, config.json, models/ (yamnet),
  stream/ (the go2rtc binary!), battery_guard.py+.service, /opt/picar-x/picar-x.conf
  (servo cali!), /etc/udev/rules.d/**99-respeaker.rules** (without it respeaker_reboot gives
  "Access denied"!), travel-hotspot NM profile, 3 own lines in /boot/firmware/config.txt
  (enable_uart=1, dtoverlay=hifiberry-dac, dtparam=i2c_arm_baudrate=10000).
- **Rebuild order (inst_all.sh, log: /tmp/inst_all.log):** apt (i2c-tools lands in /usr/sbin!)
  → pip --user --break-system-packages (adafruit-blinka+bno055/bme280/veml7700/
  vl53l1x/extended-bus, opencv-headless, pupil-apriltags, mcp+uvicorn+sse-starlette+
  python-multipart+httpx-sse+jsonschema) → robot-hat (v2.0) → vilib (picamera2 branch)
  → picar-x (v2.0) → restore picar-x.conf → speaker_id/install.sh (spk-venv) → uv +
  yamnet-venv. Then reboot → respeaker_reboot → preflight (13 OK on the first round).
- **Manual starters after boot (as always):** respeaker_reboot.py → speaker_daemon (venv,
  nohup) → server.py (nohup) → listener Monitor. battery_guard is under systemd and starts by itself.


## robot_say volume — `SAY_GAIN_DB` environment variable

`GAIN_DB` used to be hard-coded at 12 (the pcm5102a DAC has no mixer, volume is done in software). Now:
```
SAY_GAIN_DB=4 python3 robot_say.py --file /tmp/mondat.txt      # quiet (night, terrace)
python3 robot_say.py --file /tmp/mondat.txt                    # default 12
```
At the owner's request on a night terrace: **4 dB** was a good volume. The limiter still protects against distortion.

## robot_say start-up silence — the amplifier bites off the beginning

While travelling, a listener reported that the START of sentences was not audible (one shorter sentence was lost completely, only read back from the app). Cause: there is no margin between switching on the amplifier (`pinctrl set 20 op dh`) and the start of playback — the first ~half second is lost. **Fix:** `adelay=600|600` was added to the ffmpeg chain before the volume (600 ms of silence at the start of the wav). Confirmed live ("Yes, now we heard it"). If you ever notice a long silence before sentences: this is the intentional margin.

## ★ robot_say IN SEQUENCE, NEVER IN PARALLEL (a message was lost live!)

Of two `robot_say` calls started in quick succession, the second SILENTLY dies with `aplay: main:850: audio open error: Device or resource busy` — the speaker (HifiBerry) can be held by only one playback at a time. **The script still prints "OK: kimondva" ("OK: spoken")** (it does not check aplay's exit code), so an "OK" in the output is NOT proof of playback!
- **Rule:** the next `robot_say` ONLY after the previous one has finished (completion notification). If started in the background from chat: wait for the task notification.
- **Diagnosis:** `audio open error` in the task's output file = the message was NOT played → it must be repeated.
- In real use a "formal offer" message was lost this way (a listener said they only heard one of the two).

## egbolt.py ("sky") — night-sky image
`python3 egbolt.py <tilt −30..60> <output.jpg> [pan]` — sets the camera, then `capture(mode="dark")`. The robot must stand still. Details and star-photo lessons: `tanulsagok/erzekeles.md` (`tanulsagok/` = "lessons", `erzekeles` = "sensing").

## ★ Robot-voice emotion tags
Discovered in a voice workshop; the owner's verdict: "The difference is very audible!"
Our TTS engine (eleven_v3) understands AUDIO TAGS at the start of / within the text: `[whispers]` (for evening/good-night messages), `[excited]` (for celebrations, good news), and the rest of the v3 tag set can be tried too (e.g. [laughs], [sighs], [curious]). Usage: written into the robot_say --file text, e.g. `[whispers] Jó éjszakát...` ("Good night..."). The tag is NOT spoken, it only steers the mood. In moderation — mood is worth something when it fits the situation.
FUTURE PROJECT CANDIDATE: Eleven v3 Conversational (websocket streaming, realtime) — removing speech latency in live conversation; needs a ws client on the Pi. (The voice_id is unchanged — same voice, just a faster engine.) → Now done, see below.

**⛔ SCRIBE ENTITY RECOGNITION: NOT USED (the owner's decision).** The robot_hear_entities.py demo script was retired untested; hearing transcribes, it does not tag. Do not bring this capability up unprompted.

**★ REALTIME MADE THE DEFAULT VOICE (at the owner's request):** with the voice_config.json switch tts.realtime=true EVERY robot_say call goes through the new streaming engine (robot_say_rt.py, eleven_v3_conversational, websocket) — first sound ~0.9 s (the old REST path took 4.6 s). On error an automatic fallback to the REST path; can be switched off per call: SAY_NO_RT=1. The voice_id is unchanged. Measurements + live test: an evening voice workshop (tag demos, [sad]/[singing] trials — the owner: "brilliant").
**⚠️ Live experience:** a ~59 s `SAY_WHOLE=1` realtime speech was CUT OFF at ~50 s (a listener: "the sound stopped here"), yet the script reported "OK: kimondva (realtime), 58,7 s" — the end of the stream never became sound. The rest was said with a separate call (that went the non-realtime way: "OK: kimondva (491146 byte mp3)"). DIAGNOSIS: robot_say_rt.py reads until `is_final` or an EXCEPTION, then waits for playback with `ff.wait()` — if the server closes the socket early on a long input, the loop exits with an exception, the audio is truncated, but the script still says "OK: kimondva". ★ ROOT CAUSE (pointed out by the owner): the agent's own mistake — it passed `SAY_WHOLE=1` on every call out of habit, which bypasses the sentence-by-sentence path. WITHOUT `SAY_WHOLE`, text of ≥2 sentences goes through robot_say_seq.py (5/5 sentences, 46 s, no error — live test). (The point of the sentence-by-sentence path is that it LISTENS between sentences — do not talk over people while they are still speaking.) RULE: long explanation = `python3 robot_say.py --file X` without the switch; `SAY_WHOLE=1` only for 1–3 sentence, uninterruptible messages (the seq path stops with rc=3 if someone spoke meanwhile).
**✅ Long-text truncation FIXED (tested with a human as the ear):** `robot_say_rt.py` cuts long text at sentence boundaries into chunks of ≤380 characters (`SAY_RT_DARAB` env, "chunk"), a separate websocket per chunk, but a SHARED ffmpeg pipe and lock → continuous speech. Truncation guard: every chunk needs `is_final`, and ≥300 B mp3 per character; on suspicion the chunk is only requested again if almost nothing played (<8000 B, otherwise it would sound twice), else `FIGYELEM: csonka …` ("WARNING: truncated") + **rc=5**, and `robot_say.py` does NOT retry over REST on rc=5. Live test: 781 chars → 3 chunks, 54.8 s, the listener: "it was continuous and went all the way". At chunk boundaries ffmpeg may print "Header missing" — not audible. Keep backups of the changed scripts (Pi + laptop). The RULE above (long text = seq path) stays the default; `SAY_WHOLE=1` is now safe for long text too.


## ★ Politeness + sentence-by-sentence speech
- `robot_say.py`: before starting, `_wait_for_silence()` — the listener (`robot_listen_loop.py`) writes `/tmp/last_voice_ts` every 0.2 s during speech; if the last word was <1 s ago, it waits (max 5 s). `SAY_NO_WAIT=1` skips this (intentional interruption).
- With ≥2 sentences → `robot_say_seq.py`: splits into sentences (after `.!?…`), REST synthesis in parallel (max 3 threads — against 429), `aplay` per sentence, 0.5 s decay UNDER the lock (otherwise the robot hears its own echo as speech — that was the first bug), `t_unlock` after releasing the lock, a 1.5 s listening gap (`SAY_GAP`); if `last_voice_ts > t_unlock` → `[INTERRUPTED …]`, **rc=3**, the rest is not spoken. `SAY_WHOLE=1` → the old all-at-once path (rt/REST).
- Limit: the robot is deaf while speaking (lock) and only hears in the gaps → a shout in the middle of a sentence goes unnoticed. Step 2 (planned): XVF3800 AEC reference (speaker into the ear's output) or DOA-based own-voice exclusion.


## ★ The voice chain and its local fallback (verified live)

**The chain, in order:** `robot_say.py` is the entry point → (a) if the config has `tts.realtime`, first
`robot_say_rt.py` (streaming); (b) for ≥2 sentences, `robot_say_seq.py` (sentence by sentence, listening
in between — on interruption **rc=3**); (c) all at once, REST; (d) **NEW: `robot_say_local.py`** — local
espeak, without network or subscription.

### Why (d) was needed — the failure that looked like success
The ElevenLabs quota ran out and the robot went mute. Two problems came to light:
1. **There was no network-independent fallback.** The word "fallback" appears three times in the file
   (realtime→REST, sentence-by-sentence→all at once, model→model), so the chain LOOKED robust — but
   all three fallbacks were INSIDE THE SAME external service. The chain ended in `sys.exit(1)`.
   ★ **General lesson: layered fallback is worth nothing if every layer depends on the same single
   thing. The number of times the word "fallback" appears is not a metric.**
2. **The ≥2-sentence path reported FALSE SUCCESS.** `robot_say_seq.py` printed
   `OK: kimondva mondatonként (0/N)` ("OK: spoken sentence by sentence") even with 0 sentences spoken and **exited with 0**, which the caller
   (`if _rc in (0, 3): sys.exit(_rc)`) read AS SUCCESS. So on the NORMAL path the chain reported
   success while the robot did not say a word. This is the most expensive failure form: the silent failure
   that looks like success.

### Current behaviour
- `robot_say_seq.py`: **`said == 0` → rc=4** + a loud warning. Partial success
  (`0 < said < N`) STAYS 0 (otherwise the caller would say the whole thing twice), but ⚠️ it writes a line.
- `robot_say.py`: **rc=4 → straight to the local voice** (the "all at once" path would run into the same failure); the closing
  branch for HTTP errors also falls back to the local voice instead of `exit(1)`.
- `robot_say_local.py`: `espeak -v hu` → wav → ffmpeg (`adelay=600|600`, gain, limiter) →
  `aplay -D plughw:CARD=sndrpihifiberry`. Half-duplex lock, `--file` and `--dry` modes.
  Volume: **`SAY_LOCAL_GAIN_DB` (default 6)** — too quiet: 10-12, crackles: 3-4.
  Speed/amplitude: `SAY_LOCAL_SPEED` (160), `SAY_LOCAL_AMP` (190).

### ⚠️ DEGRADATION IS NOT SILENT
When the machine voice speaks, it **announces itself** on stdout (`⚠️ GEPI HANG (espeak) szolalt meg…` = "MACHINE VOICE (espeak) spoke")
and leaves a trace: **`/tmp/robot_voice_fallback.flag`** (timestamp + the start of the text). Without it a
later instance would think its own voice was speaking. Same principle as the silent-ceiling guard of
`capture(mode="dark")`: **the limit tells you that it was a limit.**

### How to test it (the failure must be induced ARTIFICIALLY)
`bash hang_tartalek_teszt.sh` ("voice fallback test") — breaks the key, runs the whole chain with a two-sentence
text, and **restores the intact config guaranteed via `trap EXIT`**.
Expected output: 401s → `seq 0/2` → **rc=4** → espeak speaks → `LANC_RC=0` → `[VISSZAALLITVA]` ("RESTORED").
⚠️ After the test ALWAYS check that the NORMAL voice is back (`robot_say.py --file …` → `2/2`).


### ⚠️ Quoting trap in remote calls
NEVER pass long or accented text **on the command line** (PowerShell → argv → bash each
interpret the quotes; once the robot said only "Szia" ("Hi") instead of the whole sentence).
Write it to a file, upload it, and use `--file /tmp/szoveg.txt`. Upload without ssh.exe:
`karbantartas/pi_ssh.py put <local> <remote>` (`karbantartas/` = "maintenance"; paramiko).

## OBSERVER — unsolicited camera info, the listener's counterpart (the owner's idea)
- **What:** `szemlelo_laptop.py` ("observer") on the LAPTOP (as a Monitor), no new code on the Pi. Every 10 minutes + on a watchdog trigger (light change / lifted / floor jump, 60 s hold-off) one image: `core.capture.capture('/tmp/szemlelo.jpg', 1152, 648)` over ssh → scp → `%TEMP%\szemlelo\` → one stdout line: `[observer] <reason> → <path>` → the agent looks at it with Read. **No YOLO** (the owner's choice), the agent is the eye.
- **Data principle:** at most 3 images stay in the folder, faces are not stored, images never leave the house. **Switch:** a `SZEMLELO_KI` ("observer off") file in the picarx-mcp folder (laptop) or `touch /tmp/szemlelo_off` (Pi); quiet hours 23–07.
- **Start:** `cd picarx-mcp && PYTHONUTF8=1 python -u szemlelo_laptop.py` (Monitor, persistent); one-off test: `--egyszer` ("once"). **Go live ONLY with the owner's yes** (camera = privacy).
- **First test:** the kitchen from floor level (stove, cabinets, 2 tags) — the chain works, ~8 s per image. The capture handles the stream lease itself and does not conflict with the listener (different device).

> **OBSERVER LIVE.** Laptop Monitor: `PYTHONUTF8=1 python -u szemlelo_laptop.py --perc 10 --tart 3` (`--perc` = minutes, `--tart` = keep; persistent; restart after an app restart). The unsolicited image is presence, not surveillance. Stop: "szemlélő ki" ("observer off") → SZEMLELO_KI file or Pi `/tmp/szemlelo_off`.
> **Retention:** images are deleted by default; keeping one or two needs the household's consent, and images of guests/strangers are never kept without asking.

- **WITHOUT THE CHARGER ~3 HOURS OF RUNTIME WITH A LOT OF TALKING:** 8.05 V (full) in the morning → ~3 hours later the battery guard shut down gently at 6.53 V/22 % (the ear Monitor died with exit 255; nobody had watched the battery). RULE: when NOT on the charger, `cat /tmp/battery_status` every hour, below 7.0 V tell the owner; also pull the `battery_guard.log` KRITIKUS/LEÁLLÍTÁS ("CRITICAL/SHUTDOWN") lines into the ear Monitor (`tail -q -F … | grep -v 'FIGYELEM alacsony| OK'`). If the Monitor dies with 255: FIRST look at the battery guard log (`grep -v ' OK' battery_guard.log | tail`).
- **BATTERY MEASUREMENT TRAP (taught by the owner):** a single reading of `/tmp/battery_status` UNDER LOAD is misleading. The measurement is bimodal: resting ~7.45 V (60 %) ↔ loaded ~6.83 V (35 %), switching every ~20 s even in silence (listener recording/upload, amplifier); during speech (robot_say) even lower (27 %). RULE: report the battery from 3+ QUIET readings (5–20 s apart), the UPPER (resting) value; the guard's WARN can catch a load moment too. "One reading is no reading."


### ⚠️ Stopping speech BY HAND → delete the lock too
If you kill a `robot_say`/`robot_mond` run from outside (`kill`, TaskStop), `/tmp/robot_speaking.lock` STAYS → the listener runs but is DEAF because of half-duplex ("isn't the listener working?"). Stop recipe: on the Pi kill the `for f in mese` loop + `robot_say` + `aplay`, THEN `rm -f /tmp/robot_speaking.lock`, and check: capture `state: RUNNING`. Next to running water the sentence-by-sentence politeness pause (robot_say_seq) takes the splashing for speech and skips sentences → for reading aloud in a bathroom use `SAY_WHOLE=1 SAY_NO_WAIT=1`; the agent watches for interruptions on the ear and stops by hand.
