# ROUTE_RUNNER — route runner with bookkeeping (canonical doc)

`utvonalak/route_runner.py` (`utvonalak/` = "routes") — the step-by-step executor of the learned routes
(`utvonalak/routes/<route>.json`). It enforces the **DISTANCE-DEBT** rule (see `tanulsagok/vezetes_alapok.md`,
"lessons / driving basics") not as a memo but **as code**, at the moment of action.

## The philosophy (5 lines)
1. **Decisions belong to the agent**: what do I see, may I go, is the obstacle real, does the scene match.
2. **Bookkeeping belongs to the script**: how far I ordered, how far I went, how much debt there is, whether the
   anchor is verified.
3. The script **cannot be lenient**: a short move → DEBT → repay it (`repay`) or justify it (`waive`); it does
   not let you move on.
4. The script **never improvises a route**: it only executes the recorded waypoint `act`s; `--force` only on
   explicit request.
5. Every `go` is **ONE movement step**; afterwards it stops and waits (no movement without asking).

## Operating model
**A step machine with persistent state, not an interactive session.** The agent calls the commands ONE BY ONE
over ssh; the state lives in a file (`utvonalak/.runner_state.json` — in the script's folder), so every call is
an independent process. The location is **reboot-proof**: during a live run a mid-route Pi reboot wiped a state
file kept in `/tmp`, so it was moved next to the script. Writes are **atomic** (tmp file + `os.replace`), so a
brownout/power cut during a write cannot leave a truncated state file.
`go`/`repay`/`verify` call the `go_dist.py` / `turn90.py` / `face_tag.py` / `whereami.py` scripts via
subprocess and do the bookkeeping from **their output** (not from the exit code!).

## Command reference
| Command | What it does |
|---|---|
| `start <route> [--dry-run] [--restart]` | loads the route file, initialises the state, prints the FULL content of wp0 (act/note/scene, untruncated). `--restart` overrides a running route. |
| `status` | where we are: current waypoint, per-step status of the plan, debt ledger, pending checks, whether `next` is allowed. |
| `go [--speed N] [--force] [--sim T]` | executes the next MOVEMENT step (go_dist / turn90); books the distance travelled from the output. If go_dist went shorter (sonar brake) → a **DEBT** is recorded and it will not let you move on. `--force` only here, explicitly. If the next step is an ANCHOR, `go` refuses and points you to `verify`. |
| `repay [--speed N] [--force] [--sim T]` | repays the recorded debt (go_dist again for the remainder); clears it on a successful repayment. |
| `waive "<reason>"` | forgives the debt with a MANDATORY written reason (logged; e.g. "the owner asked me to stop"). |
| `manual "<what-and-why>" [--dist N] [--done-step]` | books a manual correction (e.g. avoiding an obstacle by manual driving) with a MANDATORY reason, so the debt calculation and the log stay consistent. `--dist` is the manually driven distance in cm (repays debt, counts towards the travelled total); `--done-step` manually closes the current pending plan step (`[M]` marker). |
| `verify [--sim T]` | anchor check: (a) if the plan has a face_tag step → runs it and decides from the **TEXT** (face_tag exits 0 on STOP too — that is the trap, and it detects it); (b) otherwise it checks the target waypoint's tag fingerprint (whereami). For fingerprint-based arrival, **VERIFIED** needs at least **2 matching tags** (1 if the waypoint's fingerprint has only 1 tag) **and** the primary tag must be visible — 1 tag on its own is only **PARTIAL**. |
| `confirm` | the agent's manual scene confirmation (after looking at an image); at a waypoint with a scene field, `next` is allowed ONLY after this. |
| `next` | moves on to the next waypoint — ONLY if there is no unpaid debt, every movement step is done, the anchor is verified and the target scene is confirmed; otherwise it refuses, listing the reasons. |
| `abort "<reason>"` | closes the run + summary log (waypoint reached, total ordered/travelled distance, debt events). |

**--dry-run simulation** (`--sim` token): `go`/`repay`: `full` (default) \| `short` (sonar brake, short) \|
`nomove` (sonar does not change → movement-reality warning). `verify`: `ok` (default, FACING) \| `stop`
(STOP trap) \| `miss` (fingerprint does not match).

## Bookkeeping (log)
One JSONL file per run: `utvonalak/naplok/runner_<route>_<timestamp>.jsonl` (`naplok/` = "logs") — waypoint
start/end, ordered vs. travelled distance, sonar before/after, debt events (created/repaid/waived + reason),
anchor-check results, reasons for `next` refusals, abort summary.

## Typical flow (one waypoint transition)
```
route_runner.py start hall_dock->balcony_door    # dock -> balcony door (example flat); prints wp0 in full
route_runner.py go                               # go_dist 50 -> booked
#   if "ADOSSAG: 20 cm" (= "DEBT") -> route_runner.py repay   (or waive "<reason>")
route_runner.py verify                            # if the plan says face_tag (anchor)
#   look at the image...
route_runner.py confirm                           # the scene matches
route_runner.py next                              # moves on to wp1 (if everything is OK)
```

## From act to plan
The runner parses an ordered, structured plan from the free text of `to_next.act`: it recognises the
`go_dist`, `turn90`, `face_tag` primitives (in order). It does NOT execute the **non-automated** steps
(pilot / turn_scout / S-manoeuvre) — the agent drives those manually from the raw `act`; `status` always shows
the full `act`/`note` text for the decision.
