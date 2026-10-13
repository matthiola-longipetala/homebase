# Navigation folder — contents

| File | What it is |
|------|------------|
| `gen_apriltags.py`, `gen_apriltags_uj.py`, `gen_apriltags_15-24.py` | Generate the printable AprilTag sheets (tag36h11) in several sizes — the PDFs are not shipped; run a generator, print at 100 %, do not trim the white border |
| `gen_checkerboard.py` | Generate the camera-calibration checkerboard PDF — print at 100 %, glue onto a flat board, measure the real square size |
| `calibrate.py`, `camera_calib.json` | Camera calibration script and an example result |
| `tags_template.json` | Skeleton of the tag -> world-coordinate table; measure and fill in your own home's values (saved on the robot as `tags_coords.json`) |
| `edge_check.py`, `localize_bench.py` | Offline checks / benchmark for the localization maths |

Physical steps: (1) generate and print the PDFs, (2) mount the tags LOW (about camera height, ~12–15 cm above the floor),
(3) run a short camera calibration. Your own floor plan, tag coordinates and recorded routes are private data
and are intentionally not part of this repository.

## The example flat (invented)
Every coordinate in the code examples, docstrings and tests (`core/test_localization.py`, `core/test_nogo.py`,
`home_reflex.py`, `goto_route.py`, `tags_template.json`) refers to ONE made-up flat, not a real home:
- a 900 × 600 cm rectangle, x −400…500, y −100…500 (cm); origin (0,0) = the dock spot in the hallway; +X east, +Y north;
- hallway x −50…50 running north; front door at its south end (y = −100);
- west: kitchen (y −100…150) and living room (y 150…500, balcony door in the north wall at x ≈ −225);
- east: bathroom (y −100…150, a FORBIDDEN no-go zone: step-down threshold) and bedroom (y 150…500);
- a rug in the living room (x −300…−200, y 300…400) as the second no-go example;
- tags 0–9 as listed in `tags_template.json`; example route key: `hall_dock->balcony_door`.
