#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""route_runner.py — ROUTE RUNNER WITH A LEDGER (the "bridge" principle).

THE PROBLEM it solves: when following learned routes (utvonalak/routes/*.json), execution
sometimes happens only "roughly" — 100 cm becomes 90, a turn comes out slightly different —
and the small deviations add up and lead to the wrong place. The DISTANCE-DEBT rule
(tanulsagok/vezetes_alapok.md) is written down, but nothing enforces it at the moment of
action. This script puts the rule AS CODE right where the movement happens.

THE PHILOSOPHY (5 lines, see also ROUTE_RUNNER.md):
  1. The DECISION is Claude's: what I see, whether I may go, whether the obstacle is real, what the scene is.
  2. The BOOKKEEPING is the script's: how much was ordered, how much was driven, how much is owed.
  3. The script CANNOT be lenient: a short distance -> DEBT -> repay it or justify it.
  4. The script NEVER improvises a route: it only executes the recorded waypoint acts.
  5. Every step is ONE subprocess movement, after which it STOPS and waits (it never moves unasked).

OPERATING MODEL — STEP MACHINE WITH PERSISTENT STATE (not an interactive session):
  Claude calls the commands ONE BY ONE over ssh; the state lives in a file (in the script's
  folder: utvonalak/.runner_state.json), so every call is a separate process. The location is
  REBOOT-SAFE: a Pi reboot wipes /tmp, which is why the state file lives next to the script.
  Writes are ATOMIC (tmp file + os.replace), so a brownout/power loss during a write cannot
  leave a truncated state. Commands:
     start <route> [--dry-run] [--restart]   new run + FULL printout of wp0
     status                                  where we are + debt ledger + pending checks
     go [--speed N] [--force] [--sim TOKEN]  execute the next MOVEMENT step + bookkeeping
     repay [--speed N] [--force] [--sim T]   repay the recorded debt
     waive "<reason>"                        forgive the debt with a MANDATORY reason
     manual "<what-why>" [--dist N] [--done-step]   record a manual correction in the ledger
     verify [--sim TOKEN]                     check the anchor (face_tag / tag fingerprint)
     confirm                                  Claude's manual scene confirmation (after a photo)
     next                                     advance to the next waypoint (if allowed)
     abort "<reason>"                         close the run + summary log

  Bookkeeping: a JSONL log for every run (utvonalak/naplok/runner_<route>_<ts>.jsonl).

  --dry-run: the robot is asleep -> simulated outputs instead of subprocess calls. The --sim
  token selects the simulated output:  go/repay: full (default) | short | nomove ;
  verify: ok (default, FACING) | stop (STOP trap) | miss (fingerprint not found).
"""
import sys, os, re, json, time, subprocess, argparse
from datetime import datetime

# ---- paths (repo root derived from this file's location, so it works on the Pi AND in the local mirror) ----
HERE = os.path.dirname(os.path.abspath(__file__))          # .../picarx-mcp/utvonalak
REPO = os.path.dirname(HERE)                                # .../picarx-mcp
ROUTES_INDEX = os.path.join(REPO, "routes.json")
ROUTES_DIR = os.path.join(REPO, "utvonalak", "routes")
NAPLOK_DIR = os.path.join(REPO, "utvonalak", "naplok")
STATE_PATH = os.environ.get("ROUTE_RUNNER_STATE",
                            os.path.join(HERE, ".runner_state.json"))
PYBIN = os.environ.get("ROUTE_RUNNER_PYBIN", "python3")

# ---- constants ----
DEBT_TOL_CM = 4.0        # below this no debt is recorded (model rounding, not drift)
MOVE_MIN_DELTA_CM = 3.0  # if the sonar changed less than this during a significant move -> suspicious
SIG_MOVE_CM = 20.0       # "significant" move threshold for the sonar reality check

# ---- act-parser regexes (structured plan from the free text of to_next.act) ----
MOVE_RE   = re.compile(r'go_dist\s+(?:--\s+)?(-?\d+)(?:\s+(\d+))?', re.I)
TURN_RE   = re.compile(r'turn90\s+(left|right|balra|jobbra)(?:\s+(\d+))?', re.I)
ANCHOR_RE = re.compile(r'face_tag\s+(\d+)', re.I)

# =====================================================================================
#  STATE + LOG
# =====================================================================================
def load_state():
    if not os.path.exists(STATE_PATH):
        return None
    with open(STATE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def save_state(st):
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STATE_PATH)

def clear_state():
    try:
        os.remove(STATE_PATH)
    except FileNotFoundError:
        pass

def log_event(st, etype, **fields):
    """One JSONL line in the run log (etype = event type; **fields are free-form fields)."""
    rec = {"ts": datetime.now().isoformat(timespec="seconds"), "event": etype}
    rec.update(fields)
    try:
        with open(st["log_path"], "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"  (log write error: {e})")

# =====================================================================================
#  ROUTE LOADING
# =====================================================================================
def resolve_route(arg):
    """From the route argument (index key OR file basename) -> (key, absolute file path, slug)."""
    with open(ROUTES_INDEX, "r", encoding="utf-8") as f:
        idx = json.load(f)
    routes = idx.get("routes", {})
    # 1) direct index key (e.g. "hall_dock->balcony_door")
    if arg in routes and routes[arg].get("file"):
        rel = routes[arg]["file"]
        path = os.path.join(REPO, rel)
        return arg, path, os.path.splitext(os.path.basename(rel))[0]
    # 2) file basename match (e.g. "hall_dock_TO_balcony_door")
    for key, val in routes.items():
        rel = val.get("file")
        if rel and os.path.splitext(os.path.basename(rel))[0] == arg:
            return key, os.path.join(REPO, rel), arg
    # 3) direct file in utvonalak/routes/
    cand = os.path.join(ROUTES_DIR, arg if arg.endswith(".json") else arg + ".json")
    if os.path.exists(cand):
        return arg, cand, os.path.splitext(os.path.basename(cand))[0]
    return None, None, None

def load_waypoints(path):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data, data.get("waypoints", [])

# =====================================================================================
#  ACT -> PLAN (structured step list)
# =====================================================================================
def parse_plan(act):
    """Ordered, structured plan from the free text of to_next.act.
    Recognized primitives: move (go_dist), turn (turn90), anchor (face_tag).
    Everything else (pilot / turn_scout / S-maneuver) is NOT automated -> Claude does it from the raw act."""
    if not act:
        return []
    hits = []
    for m in MOVE_RE.finditer(act):
        dist = int(m.group(1)); spd = int(m.group(2)) if m.group(2) else None
        hits.append((m.start(), {"type": "move", "dist_cm": dist, "speed": spd,
                                 "status": "pending", "ordered_cm": dist,
                                 "actual_cm": None, "braked": None}))
    for m in TURN_RE.finditer(act):
        d = m.group(1).lower()
        direction = "left" if d in ("left", "balra") else "right"
        deg = int(m.group(2)) if m.group(2) else 90
        hits.append((m.start(), {"type": "turn", "dir": direction, "deg": deg,
                                 "status": "pending", "braked": None}))
    for m in ANCHOR_RE.finditer(act):
        hits.append((m.start(), {"type": "anchor", "tag": int(m.group(1)),
                                 "status": "pending", "result": None}))
    hits.sort(key=lambda h: h[0])
    plan = []
    for i, (_, step) in enumerate(hits):
        step["i"] = i
        plan.append(step)
    return plan

def step_label(s):
    if s["type"] == "move":
        sp = f" @spd{s['speed']}" if s.get("speed") else ""
        return f"go_dist {s['dist_cm']:+d}{sp}"
    if s["type"] == "turn":
        return f"turn90 {s['dir']} {s['deg']}"
    if s["type"] == "anchor":
        return f"face_tag {s['tag']} (ANCHOR)"
    return "?"

def first_pending(plan):
    for s in plan:
        if s["status"] == "pending":
            return s
    return None

# =====================================================================================
#  SUBPROCESS / SIMULATION
# =====================================================================================
def run_cmd(argv):
    """Real subprocess call; returns the full stdout+stderr text (regardless of exit code)."""
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=120)
        return (p.stdout or "") + (p.stderr or "")
    except Exception as e:
        return f"ERROR in subprocess: {e}\n"

def read_front_sonar(st):
    """Front sonar (cm), ONE measurement. dry-run: simulated; live: inline core.motion.sonar_cm."""
    if st.get("dry_run"):
        return float(st.get("last_after_sonar") or 80.0)
    snippet = ("import os,sys,time;sys.path.insert(0,os.path.expanduser('~/picarx-mcp'));"
               "from core.motion import sonar_cm;from picarx import Picarx;"
               "px=Picarx();px.set_cam_pan_angle(0);time.sleep(0.2);"
               "v=sorted(s for s in (sonar_cm(px) for _ in range(6)) if s and s>0);"
               "print('FRONT_SONAR', v[len(v)//2] if v else 'NONE')")
    out = run_cmd([PYBIN, "-c", snippet])
    m = re.search(r'FRONT_SONAR\s+([\d.]+)', out)
    return float(m.group(1)) if m else None

def sim_go_dist_output(ordered, speed, before_sonar, sim):
    """Simulated go_dist stdout — SAME format as the real one (so the parser runs identically).
    The text deliberately mirrors go_dist.py's own output; do not change it independently."""
    v = 31.4 if speed <= 30 else (37.4 if speed <= 55 else 43.0)
    total_s = abs(ordered) / v
    head = f"CEL: {ordered:+.0f} cm @speed {speed}  (~{total_s:.1f} mp, modell v={v:.1f} cm/s)"
    if sim == "short":
        actual = int(round(ordered * 0.6))
        after = max(20.0, before_sonar - abs(actual))
        return (head + "\n"
                f"  ! SZONAR-FEK: akadaly {after:.0f} cm-nel, megalltam (ha csak kuszob/felfa es szabad a nyilas -> --force)\n"
                f"KESZ: becsult megtett tav {actual:+.0f} cm (fek miatt rovidebb); elore-szonar most: {after:.0f} cm\n"
                "vege\n")
    if sim == "nomove":
        # reports a "full segment", BUT the sonar did NOT change -> movement-reality suspicion
        after = before_sonar
        return (head + "\n"
                f"KESZ: becsult megtett tav {ordered:+.0f} cm (teljes szakasz); elore-szonar most: {after:.0f} cm\n"
                "vege\n")
    # full (default)
    after = max(20.0, before_sonar - abs(ordered))
    return (head + "\n"
            f"KESZ: becsult megtett tav {ordered:+.0f} cm (teljes szakasz); elore-szonar most: {after:.0f} cm\n"
            "vege\n")

def parse_go_dist_output(out):
    """From the go_dist output: (actual_cm, braked, after_sonar)."""
    actual = None; braked = "fek miatt rovidebb" in out
    m = re.search(r'becsult megtett tav\s*([+-]?\d+)\s*cm', out)
    if m:
        actual = int(m.group(1))
    m2 = re.search(r'(?:elore|hatso)-szonar most:\s*([\d.]+)\s*cm', out)
    after = float(m2.group(1)) if m2 else None
    return actual, braked, after

# =====================================================================================
#  PRINT HELPERS
# =====================================================================================
def print_waypoint_full(wp, transition_note=""):
    """The FULL content of the waypoint, without truncation."""
    print(f"\n===== WAYPOINT {wp['i']}: {wp.get('name','')} =====")
    c = wp.get("coord", {})
    if c:
        print(f"  COORD: x={c.get('x_cm')} y={c.get('y_cm')} hdg={c.get('heading_deg')} "
              f"spread={c.get('spread_cm')} conf={c.get('conf','')}")
    ts = wp.get("tags_seen") or []
    if ts:
        fp = ", ".join(f"tag{t['id']}(dist{t.get('range_cm','?')},bear{t.get('bearing_deg','?')},m{t.get('margin','?')})"
                       for t in ts)
        print(f"  TAG FINGERPRINT: {fp}")
    if wp.get("scene"):
        print(f"  SCENE: {wp['scene']}")
    tn = wp.get("to_next")
    if tn:
        print(f"  TO_NEXT.act : {tn.get('act','')}")
        if tn.get("note"):
            print(f"  TO_NEXT.note: {tn['note']}")
    else:
        print("  TO_NEXT: (none) — this is the DESTINATION waypoint.")
    for a in (wp.get("annot") or []):
        print(f"    - annot: {a}")
    if transition_note:
        print(transition_note)

def print_plan(plan):
    if not plan:
        print("  PLAN: (no recognized automated primitive — drive manually from the raw act)")
        return
    print("  PLAN (in order):")
    for s in plan:
        mark = {"pending": "[ ]", "done": "[x]", "manual": "[M]"}.get(s["status"], "[?]")
        extra = ""
        if s["type"] == "move" and s.get("actual_cm") is not None:
            extra = f"  (ordered {s['ordered_cm']:+d} -> driven {s['actual_cm']:+d})"
        if s["type"] == "anchor" and s.get("result"):
            extra = f"  -> {s['result']}"
        print(f"    {mark} {s['i']}. {step_label(s)}{extra}")

def next_blockers(st, wps):
    """List of reasons blocking 'next' (empty = allowed)."""
    blk = []
    if st["debt_cm"] > DEBT_TOL_CM:
        blk.append(f"unpaid debt {st['debt_cm']:.0f} cm (repay or waive)")
    pend = first_pending(st["plan"])
    if pend is not None:
        if pend["type"] == "anchor":
            blk.append(f"unverified ANCHOR: {step_label(pend)} (verify)")
        else:
            blk.append(f"unexecuted movement step: {step_label(pend)} (go)")
    dest = st["current_wp"] + 1
    if dest < len(wps):
        if wps[dest].get("scene") and not st.get("scene_confirmed"):
            blk.append(f"the destination scene (wp{dest}) has not been confirmed manually (confirm)")
    return blk

# =====================================================================================
#  COMMANDS
# =====================================================================================
def cmd_start(args):
    key, path, slug = resolve_route(args.route)
    if not path:
        print(f"ROUTE NOT FOUND: '{args.route}'.")
        print("Available routes (routes.json index):")
        with open(ROUTES_INDEX, "r", encoding="utf-8") as f:
            for k in json.load(f).get("routes", {}):
                print(f"   - {k}")
        return 1
    data, wps = load_waypoints(path)
    if not wps:
        print(f"This route ('{key}') is NOT waypoint-based (the detailed log is in an .md file; "
              f"waypoints=[]). The runner only works with waypoint-list routes.")
        return 1

    existing = load_state()
    if existing and not args.restart:
        print(f"A route is ALREADY RUNNING: '{existing['route_key']}', wp {existing['current_wp']}. "
              f"A new start would overwrite it. Add --restart, or close it: abort \"<reason>\".")
        return 1

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(NAPLOK_DIR, f"runner_{slug}_{ts}.jsonl")
    st = {
        "route_key": key, "route_file": path, "route_slug": slug,
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "log_path": log_path, "dry_run": bool(args.dry_run),
        "n_waypoints": len(wps), "current_wp": 0,
        "plan": parse_plan((wps[0].get("to_next") or {}).get("act", "")),
        "debt_cm": 0.0, "scene_confirmed": False,
        "last_after_sonar": None,
        "total_ordered_cm": 0.0, "total_actual_cm": 0.0,
        "debt_history": [],
    }
    save_state(st)
    log_event(st, "run_start", route=key, file=os.path.relpath(path, REPO),
              n_waypoints=len(wps), dry_run=st["dry_run"])
    log_event(st, "wp_enter", i=0, name=wps[0].get("name", ""))

    mode = "  [DRY-RUN: simulated movement, the robot does NOT move]" if st["dry_run"] else ""
    print(f"ROUTE STARTED: {key}  ({len(wps)} waypoints){mode}")
    print(f"Log: {os.path.relpath(log_path, REPO)}")
    print_waypoint_full(wps[0])
    print_plan(st["plan"])
    print("\nNext: 'go' to move (or 'verify' if the plan starts with an ANCHOR), then 'status'.")
    return 0

def cmd_status(args):
    st = load_state()
    if not st:
        print("No route running. Start one: start <route> [--dry-run].")
        return 1
    data, wps = load_waypoints(st["route_file"])
    cur = st["current_wp"]
    print(f"ROUTE: {st['route_key']}  ({'DRY-RUN' if st['dry_run'] else 'LIVE'})")
    print(f"POSITION: waypoint {cur}/{st['n_waypoints']-1} — {wps[cur].get('name','')}")
    print(f"Log: {os.path.relpath(st['log_path'], REPO)}")
    tn = wps[cur].get("to_next")
    if tn:
        print(f"\nCURRENT TO_NEXT.act : {tn.get('act','')}")
        if tn.get("note"):
            print(f"CURRENT TO_NEXT.note: {tn['note']}")
    else:
        print("\n(this is the DESTINATION waypoint — no further movement)")
    print()
    print_plan(st["plan"])
    print("\nDEBT LEDGER:")
    print(f"  unpaid debt: {st['debt_cm']:.0f} cm")
    print(f"  total ordered / driven: {st['total_ordered_cm']:.0f} / {st['total_actual_cm']:.0f} cm")
    if st["debt_history"]:
        for d in st["debt_history"]:
            print(f"    - {d}")
    dest = cur + 1
    print("\nPENDING CHECKS:")
    if dest < len(wps) and wps[dest].get("scene"):
        print(f"  destination scene (wp{dest}) confirmed: {'YES' if st.get('scene_confirmed') else 'NO (confirm)'}")
    blk = next_blockers(st, wps)
    if blk:
        print("\n'next' is NOT allowed yet:")
        for b in blk:
            print(f"  - {b}")
    else:
        print("\n'next' is ALLOWED (no blockers).")
    return 0

def cmd_go(args):
    st = load_state()
    if not st:
        print("No route running. start <route> [--dry-run].")
        return 1
    if st["debt_cm"] > DEBT_TOL_CM:
        print(f"REJECTED: settle the {st['debt_cm']:.0f} cm debt first (repay or waive) — "
              f"no moving forward until then.")
        return 1
    pend = first_pending(st["plan"])
    if pend is None:
        print("All planned movements on this segment are DONE. Next: verify (if there is an anchor) / "
              "confirm / next.")
        return 0
    if pend["type"] == "anchor":
        print(f"The next planned step is an ANCHOR: {step_label(pend)}. Run: verify "
              f"(do NOT move ahead of it).")
        return 1
    data, wps = load_waypoints(st["route_file"])

    if pend["type"] == "move":
        ordered = pend["dist_cm"]
        speed = args.speed or pend.get("speed") or 30
        force = bool(args.force)
        before = read_front_sonar(st)
        print(f"MOVE: go_dist {ordered:+d} cm @spd{speed}{' --force' if force else ''}  "
              f"(front sonar: {before if before is not None else '?'} cm)")
        if st["dry_run"]:
            out = sim_go_dist_output(ordered, speed, before if before else 80.0, args.sim or "full")
        else:
            argv = [PYBIN, os.path.join(REPO, "go_dist.py")]
            if ordered < 0:
                argv += ["--", str(ordered)]
            else:
                argv += [str(ordered), str(speed)]
            if force:
                argv.append("--force")
            out = run_cmd(argv)
        print(out.rstrip())
        actual, braked, after = parse_go_dist_output(out)
        if actual is None:
            print("  WARNING: could not read the driven distance from the output — check manually!")
            actual = 0
        st["last_after_sonar"] = after if after is not None else st.get("last_after_sonar")
        pend["actual_cm"] = actual; pend["braked"] = braked; pend["status"] = "done"
        st["total_ordered_cm"] += abs(ordered)
        st["total_actual_cm"] += abs(actual)

        # --- movement reality check (sonar before/after) ---
        print(f"  SONAR before/after: {before if before is not None else '?'} -> "
              f"{after if after is not None else '?'} cm")
        if (before is not None and after is not None and abs(ordered) >= SIG_MOVE_CM
                and abs(after - before) < MOVE_MIN_DELTA_CM):
            print("  ⚠ MOVEMENT REALITY: the sonar barely changed during a significant move — you may NOT "
                  "have moved (stuck on the rug?). TAKE a fresh photo before moving on!")

        # --- debt bookkeeping ---
        shortfall = abs(ordered) - abs(actual)
        log_event(st, "move_go", step=pend["i"], ordered_cm=ordered, speed=speed, force=force,
                  before_sonar=before, after_sonar=after, actual_cm=actual, braked=braked)
        if shortfall > DEBT_TOL_CM:
            st["debt_cm"] += shortfall
            note = f"wp{st['current_wp']} step{pend['i']}: ordered {abs(ordered)} - driven {abs(actual)} = {shortfall:.0f} cm"
            st["debt_history"].append(note)
            log_event(st, "debt_open", cm=shortfall, reason="sonar brake/short segment", detail=note)
            print(f"\n  DEBT: {shortfall:.0f} cm — repay it (repay) or justify it (waive), "
                  f"no moving forward until then")
        else:
            print("  (no new debt — the full ordered distance was driven)")
        save_state(st)
        return 0

    if pend["type"] == "turn":
        direction, deg = pend["dir"], pend["deg"]
        print(f"TURN: turn90 {direction} {deg}")
        if st["dry_run"]:
            out = (f"FORDULAS: {deg} fok {'BALRA/CCW' if direction=='left' else 'JOBBRA/CW'}...\n"
                   f"KESZ: ~{deg} fok lefordulva.\nvege\n")
            if args.sim == "short":
                out = (f"FORDULAS: {deg} fok...\n"
                       f"  [1/4] elore blokkolva - csak hatra-iv\n"
                       f"FIGYELEM: fek volt -> a tenyleges szog rovidebb lehet; ellenorizz\nvege\n")
        else:
            out = run_cmd([PYBIN, os.path.join(REPO, "turn90.py"), direction, str(deg)])
        print(out.rstrip())
        braked = "FIGYELEM" in out and "fek" in out
        pend["braked"] = braked; pend["status"] = "done"
        log_event(st, "turn", step=pend["i"], dir=direction, deg=deg, braked=braked)
        if deg >= 30:
            print("  ★ >30° turn: CLOSED LOOP mandatory — if the plan has a face_tag, run verify; "
                  "otherwise check a photo/anchor before moving on.")
        if braked:
            print("  ⚠ braked during the turn -> the angle is short; confirm with an anchor (verify) / a photo.")
        save_state(st)
        return 0
    return 0

def cmd_repay(args):
    st = load_state()
    if not st:
        print("No route running.")
        return 1
    if st["debt_cm"] <= DEBT_TOL_CM:
        print("No unpaid debt.")
        return 0
    remaining = st["debt_cm"]
    speed = args.speed or 30
    force = bool(args.force)
    before = read_front_sonar(st)
    print(f"REPAYMENT: go_dist {remaining:+.0f} cm @spd{speed}{' --force' if force else ''}  "
          f"(front sonar: {before if before is not None else '?'} cm)")
    if st["dry_run"]:
        out = sim_go_dist_output(int(round(remaining)), speed, before if before else 80.0,
                                 args.sim or "full")
    else:
        argv = [PYBIN, os.path.join(REPO, "go_dist.py"), str(int(round(remaining))), str(speed)]
        if force:
            argv.append("--force")
        out = run_cmd(argv)
    print(out.rstrip())
    actual, braked, after = parse_go_dist_output(out)
    if actual is None:
        print("  WARNING: driven distance not readable — check manually.")
        actual = 0
    st["last_after_sonar"] = after if after is not None else st.get("last_after_sonar")
    st["total_actual_cm"] += abs(actual)
    paid = abs(actual)
    st["debt_cm"] = max(0.0, st["debt_cm"] - paid)
    log_event(st, "repay", repaid_cm=paid, after_debt=st["debt_cm"], braked=braked,
              before_sonar=before, after_sonar=after)
    if st["debt_cm"] > DEBT_TOL_CM:
        st["debt_history"].append(f"partially repaid: -{paid:.0f} cm, remaining {st['debt_cm']:.0f} cm")
        print(f"\n  PARTIAL REPAYMENT: {paid:.0f} cm done, remaining DEBT {st['debt_cm']:.0f} cm "
              f"(repay again or waive).")
    else:
        st["debt_cm"] = 0.0
        st["debt_history"].append(f"DEBT REPAID (+{paid:.0f} cm)")
        print(f"\n  DEBT REPAID. Go ahead with verify/confirm/next.")
    save_state(st)
    return 0

def cmd_waive(args):
    st = load_state()
    if not st:
        print("No route running.")
        return 1
    if not args.reason or not args.reason.strip():
        print("waive REQUIRES a written reason: waive \"<reason>\".")
        return 1
    if st["debt_cm"] <= DEBT_TOL_CM:
        print("No debt to waive.")
        return 0
    amount = st["debt_cm"]
    log_event(st, "waive", cm=amount, reason=args.reason.strip())
    st["debt_history"].append(f"WAIVED {amount:.0f} cm — reason: {args.reason.strip()}")
    st["debt_cm"] = 0.0
    save_state(st)
    print(f"DEBT WAIVED: {amount:.0f} cm. Reason logged: \"{args.reason.strip()}\"")
    return 0

def cmd_manual(args):
    st = load_state()
    if not st:
        print("No route running.")
        return 1
    if not args.reason or not args.reason.strip():
        print("manual REQUIRES a written what-why reason.")
        return 1
    reason = args.reason.strip()
    dist = args.dist
    done_step = bool(args.done_step)
    log_event(st, "manual", reason=reason, dist_cm=dist, done_step=done_step)
    st["debt_history"].append(f"MANUAL CORRECTION: {reason}")
    print(f"MANUAL CORRECTION RECORDED: \"{reason}\"")

    # --- manually driven distance: counts toward the driven total, repays debt ---
    if dist is not None:
        st["total_actual_cm"] += abs(dist)
        if st["debt_cm"] > DEBT_TOL_CM:
            paid = min(st["debt_cm"], abs(dist))
            st["debt_cm"] -= paid
            if st["debt_cm"] <= DEBT_TOL_CM:
                st["debt_cm"] = 0.0
                st["debt_history"].append(f"DEBT REPAID (manually, +{paid:.0f} cm)")
                print(f"  MANUAL DISTANCE: {abs(dist):.0f} cm -> DEBT REPAID (manually).")
            else:
                st["debt_history"].append(f"partially repaid (manually): -{paid:.0f} cm, remaining {st['debt_cm']:.0f} cm")
                print(f"  MANUAL DISTANCE: {paid:.0f} cm repaid, remaining DEBT {st['debt_cm']:.0f} cm "
                      f"(repay/manual again or waive).")
        else:
            print(f"  MANUAL DISTANCE: {abs(dist):.0f} cm added to the driven total (no open debt).")

    # --- manually close the current pending plan step ---
    if done_step:
        pend = first_pending(st["plan"])
        if pend is None:
            print("  --done-step: no pending plan step to close.")
        else:
            pend["status"] = "manual"
            if pend["type"] == "move":
                pend["actual_cm"] = None   # the manual distance is booked via --dist, not on the step
            elif pend["type"] == "anchor":
                pend["result"] = f"CONFIRMED MANUALLY: {reason}"
            print(f"  --done-step: step {pend['i']} ({step_label(pend)}) closed MANUALLY [M].")

    save_state(st)
    return 0

def cmd_verify(args):
    st = load_state()
    if not st:
        print("No route running.")
        return 1
    data, wps = load_waypoints(st["route_file"])
    pend = first_pending(st["plan"])
    # 1) explicit face_tag anchor step in the plan
    if pend is not None and pend["type"] == "anchor":
        tag = pend["tag"]
        print(f"ANCHOR CHECK: face_tag {tag}")
        if st["dry_run"]:
            if args.sim == "stop":
                out = (f"  [0] nem latom a tag{tag}-t a panban -> STOP\nvege\n")   # exit 0 TRAP!
            else:
                out = (f"  [0] tag{tag} bearing=-3.2 margin=40\n"
                       f"  >>> SZEMBE a tag{tag}-tel (nyugat)!\nvege\n")
        else:
            # ⚠ SEPARATE command (face_tag exits with 0 even on STOP)
            out = run_cmd([PYBIN, os.path.join(REPO, "face_tag.py"), str(tag)])
        print(out.rstrip())
        # ⚠ EXIT-CODE GOTCHA: decide from the TEXT, NOT from the exit code
        seen = "SZEMBE" in out
        lost = ("STOP" in out) or ("nem latom" in out)
        if seen and not lost:
            pend["status"] = "done"; pend["result"] = "IGAZOLT"
            log_event(st, "verify", kind="face_tag", target=tag, result="IGAZOLT")
            print(f"  -> VERIFIED: facing tag{tag}.")
        else:
            pend["result"] = "NEM TALALOM"
            log_event(st, "verify", kind="face_tag", target=tag, result="NEM TALALOM",
                      note="face_tag STOP/not seen (despite exit 0!)")
            print(f"  -> tag{tag} NOT FOUND (face_tag exited with STOP, DESPITE exit 0!). "
                  f"The anchor is NOT verified — do not run next; reposition and try again.")
        save_state(st)
        return 0
    # 2) no explicit anchor step -> the DESTINATION waypoint's tag fingerprint (arrival check)
    dest = st["current_wp"] + 1
    if dest < len(wps) and (wps[dest].get("tags_seen")):
        expected = [t["id"] for t in wps[dest]["tags_seen"]]
        primary = wps[dest]["tags_seen"][0]["id"]
        print(f"ARRIVAL CHECK (wp{dest} tag fingerprint): expected tags {expected}, primary tag{primary}")
        if st["dry_run"]:
            shown = [] if args.sim == "miss" else expected
            out = f"SZONAR (elore): 60 cm\nLATOTT ismert tagek: {shown}  (n={len(shown)})\nvege\n"
        else:
            out = run_cmd([PYBIN, os.path.join(REPO, "whereami.py")])
        print(out.rstrip())
        m = re.search(r'LATOTT ismert tagek:\s*\[([^\]]*)\]', out)
        seen_ids = [int(x) for x in re.findall(r'\d+', m.group(1))] if m else []
        matched = [t for t in expected if t in seen_ids]
        missing = [t for t in expected if t not in seen_ids]
        need = min(2, len(expected))
        ok = (primary in seen_ids) and (len(matched) >= need)
        result = "IGAZOLT" if ok else ("RESZLEGES" if matched else "NEM TALALOM")
        log_event(st, "verify", kind="ujjlenyomat", target=dest, expected=expected,
                  seen=seen_ids, matched=matched, need=need, result=result)
        print(f"  VERIFIED threshold: at least {need} matching tags + primary visible")
        print(f"  matching: {matched}  missing: {missing}  ->  {result}")
        if not ok:
            print("  (NOT VERIFIED — either the primary tag is not visible, or too few tags match "
                  "(1 tag alone is NOT enough); confirm the scene with confirm (photo).)")
        return 0
    print("This segment has no anchor (neither a face_tag step nor a tag fingerprint). "
          "Confirm the scene with confirm (photo).")
    return 0

def cmd_confirm(args):
    st = load_state()
    if not st:
        print("No route running.")
        return 1
    data, wps = load_waypoints(st["route_file"])
    dest = st["current_wp"] + 1
    if dest >= len(wps):
        print("This is the DESTINATION waypoint — no further scene to confirm.")
        return 0
    scene = wps[dest].get("scene")
    if not scene:
        print(f"The destination waypoint (wp{dest}) has no scene field — no separate confirm needed.")
        return 0
    st["scene_confirmed"] = True
    log_event(st, "confirm", i=dest, scene=scene[:200])
    save_state(st)
    print(f"SCENE CONFIRMED (manual) — wp{dest}:")
    print(f"  \"{scene}\"")
    print("  (Claude looked at the photo and it matches.) 'next' may proceed if there are no other blockers.")
    return 0

def cmd_next(args):
    st = load_state()
    if not st:
        print("No route running.")
        return 1
    data, wps = load_waypoints(st["route_file"])
    cur = st["current_wp"]
    if cur >= len(wps) - 1:
        print("This is the LAST (destination) waypoint — nowhere further. Close it: abort \"<reason>\".")
        return 0
    blk = next_blockers(st, wps)
    if blk:
        log_event(st, "next_blocked", from_i=cur, reasons=blk)
        print(f"NEXT REJECTED (wp{cur} -> wp{cur+1}):")
        for b in blk:
            print(f"  - {b}")
        print("Resolve these, then run next again.")
        return 1
    # advance
    log_event(st, "next_ok", from_i=cur, to_i=cur + 1)
    st["current_wp"] = cur + 1
    log_event(st, "wp_arrive", i=st["current_wp"], name=wps[st["current_wp"]].get("name", ""))
    log_event(st, "wp_enter", i=st["current_wp"])
    # state for the new segment
    st["plan"] = parse_plan((wps[st["current_wp"]].get("to_next") or {}).get("act", ""))
    st["debt_cm"] = 0.0
    st["scene_confirmed"] = False
    save_state(st)
    print(f"ADVANCED: you are now at waypoint {st['current_wp']}.")
    print_waypoint_full(wps[st["current_wp"]])
    print_plan(st["plan"])
    return 0

def cmd_abort(args):
    st = load_state()
    if not st:
        print("No route running (nothing to close).")
        return 1
    reason = (args.reason or "").strip() or "(no reason given)"
    summary = {
        "waypoints_reached": st["current_wp"],
        "n_waypoints": st["n_waypoints"],
        "total_ordered_cm": st["total_ordered_cm"],
        "total_actual_cm": st["total_actual_cm"],
        "outstanding_debt_cm": st["debt_cm"],
        "debt_history": st["debt_history"],
    }
    log_event(st, "abort", reason=reason, summary=summary)
    print(f"RUN CLOSED. Reason: {reason}")
    print(f"  waypoint reached: {st['current_wp']}/{st['n_waypoints']-1}")
    print(f"  total ordered / driven: {st['total_ordered_cm']:.0f} / {st['total_actual_cm']:.0f} cm")
    print(f"  open debt at close: {st['debt_cm']:.0f} cm")
    if st["debt_history"]:
        print("  debt events:")
        for d in st["debt_history"]:
            print(f"    - {d}")
    print(f"  log: {os.path.relpath(st['log_path'], REPO)}")
    clear_state()
    return 0

# =====================================================================================
#  ENTRY POINT
# =====================================================================================
def build_parser():
    p = argparse.ArgumentParser(prog="route_runner.py",
                                description="Route runner with a ledger (distance debt enforced).")
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("start", help="new run + print wp0")
    s.add_argument("route")
    s.add_argument("--dry-run", action="store_true", help="simulated movement (the robot is asleep)")
    s.add_argument("--restart", action="store_true", help="overwrite an existing run")

    sub.add_parser("status", help="where we are + debt ledger")

    g = sub.add_parser("go", help="execute the next movement step")
    g.add_argument("--speed", type=int, default=None)
    g.add_argument("--force", action="store_true")
    g.add_argument("--sim", default=None, help="dry-run output: full|short|nomove")

    r = sub.add_parser("repay", help="repay the debt")
    r.add_argument("--speed", type=int, default=None)
    r.add_argument("--force", action="store_true")
    r.add_argument("--sim", default=None, help="dry-run output: full|short")

    w = sub.add_parser("waive", help="waive the debt with a MANDATORY reason")
    w.add_argument("reason")

    m = sub.add_parser("manual", help="record a manual correction with a MANDATORY what-why reason")
    m.add_argument("reason")
    m.add_argument("--dist", type=int, default=None,
                   help="manually driven distance in cm (repays debt, counts toward the driven total)")
    m.add_argument("--done-step", action="store_true",
                   help="the current pending plan step was completed manually (marked [M] in the plan)")

    v = sub.add_parser("verify", help="anchor check (face_tag / tag fingerprint)")
    v.add_argument("--sim", default=None, help="dry-run output: ok|stop|miss")

    sub.add_parser("confirm", help="manual scene confirmation (after a photo)")
    sub.add_parser("next", help="advance to the next waypoint")

    a = sub.add_parser("abort", help="close the run + summary log")
    a.add_argument("reason")
    return p

def main():
    parser = build_parser()
    args = parser.parse_args()
    if not args.cmd:
        parser.print_help()
        return 1
    dispatch = {
        "start": cmd_start, "status": cmd_status, "go": cmd_go, "repay": cmd_repay,
        "waive": cmd_waive, "manual": cmd_manual, "verify": cmd_verify, "confirm": cmd_confirm,
        "next": cmd_next, "abort": cmd_abort,
    }
    return dispatch[args.cmd](args) or 0

if __name__ == "__main__":
    sys.exit(main())
