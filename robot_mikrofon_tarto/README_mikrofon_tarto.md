# ReSpeaker XVF3800 4-Mic Array — mount/standoff for the robot

Designed by the household's Claude (agency session, 2026-06) at the owner's request.

## What is this?

A 3D-printable **tripod stand** that lifts the 10 cm diameter ReSpeaker
microphone disc **horizontally, ~9.5 cm high** above the top of the robot.
Horizontal and high because the board is a **360°** microphone — facing up,
with a clear all-round view, it hears the surroundings best, and the robot's
own body does not shadow it.

The base stays within the requested **8 × 6 cm** footprint; three legs hold the
upper ring that the disc **sits in**, and a low lip + 3 small tabs hold it in
place so it does not pop out. The lip has a **cable gap** for the USB-C.

## Files

| File | Purpose |
|---|---|
| `respeaker_xvf3800_mount.stl` | **Ready to print** — give this one to the person printing it. |
| `respeaker_xvf3800_mount.scad` | Parametric OpenSCAD master — if anything needs adjusting, edit this and F6 → Export STL. |
| `mount_drawing.png` | Dimensioned sketch (side and top view). |
| `mount_preview.png` | 3D preview from three views. |

## Main dimensions

- Disc: Ø100 mm (based on your measurement), resting on a Ø88–108 mm ring.
- Total height: ~101 mm (the disc rests at 95 mm, with a 4 mm lip above it).
- Base: 80 × 60 × 4 mm, with rounded corners.
- Estimated material: ~74 cm³ ≈ **~90 g PLA**, ~4–6 hours of printing (depends on layer height).

## Print settings (recommendation)

- Material: **PLA** is enough (PETG if it stands somewhere warmer). The board is light, there is no big load.
- Orientation: **base down** — this way supports are hardly needed (the legs lean ~13°, well below 45°).
- Infill: 20–25 %, 3 perimeter walls. The legs are strong this way too (Ø12 mm solid rod).
- Layer: 0.2 mm is plenty.

## ⚠️ What to measure before printing (3 things)

I know the disc diameter (100 mm), but **I cannot see the robot's mounting
precisely** from the photo. The model has **parameters** for these — they can be
changed in the `.scad`, or tell me and I will re-render it:

1. **Spacing of the 2 front screw positions** on top of the robot (`screw_pair_dx`, currently a 40 mm
   placeholder). On the top-view drawing the red dots are the holes — align the middle pair
   to this. (The 4 corner holes are an option if mounting another way is easier.)
   If you would rather use double-sided tape / cable ties, the flat underside of the base works too.
2. **The thickness of the disc at its thinner edge** + whether there is a tall component
   **on the underside, on the outer ~6 mm rim** (that is where the ring touches it, `shelf_inner`).
   If there is, I will narrow the supporting ring a little.
3. **The exact position of the USB-C connector** on the edge of the disc — I will rotate the cable
   gap (currently the model's "front" direction) there. For now you can also rotate the disc so that
   the gap lines up with the connector.

## Two concepts — what I chose and why

You sketched two ideas: (A) legs within the 8×6 cm footprint + an upper holder, and
(B) two legs at the 2 front screw positions + an upward-facing L-bracket.

The model as built is **a combination of the two**: a **tripod** for stability (the
advantage of A — the 100 mm disc does not wobble on a narrow L), but I also put the
**2 front screw positions** into the base (B's mounting idea), plus 4 corner holes.
A tall, upright L-bracket with a 10 cm disc on top would be rather prone to tipping
on a rolling robot — the three-legged holder gives a lower centre of mass.
If you still want the pure L-bracket version, tell me and I will make it that way too.

## Next step

Measure the 3 things above (especially the **front screw spacing**), and either change
the `screw_pair_dx` value in the `.scad`, or send me the number and I will send a new
STL. Then it can go to the printer. 🖨️
