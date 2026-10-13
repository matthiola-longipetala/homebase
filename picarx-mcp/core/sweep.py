"""core/sweep.py — servo-friendly pan sweep order.

The old zigzag pattern (0, -20, +20, -40, +40, ...) made ever larger moves in
OPPOSITE directions at every step -> the pan servo worked hard, and the large
direction reversals tugged the camera ribbon cable (which had already come loose once).

New pattern: ONE quick "look ahead" from the current position (zero motion), then
sweep from the NEARER edge to the other edge, in one direction, in small steps.
Total servo travel: zigzag ~420-805 deg -> new ~180-210 deg, with only ONE larger
move (to the edge); all the rest are small same-direction steps.

Usage:
    from core.sweep import sweep_pans
    for pan in sweep_pans(-70, 70, step=20, current=0):
        ...
"""

def sweep_pans(lo=-70.0, hi=70.0, step=20.0, current=0.0, peek_current=True):
    """Monotonic pan sweep order from edge to edge.

    lo/hi: the sweep edges (deg). step: desired step size (may adjust slightly
    so the endpoints are exact). current: the camera's current pan position --
    we start from the edge NEARER to it, to minimise travel.
    peek_current: if True, the first element is current itself (the camera is already
    there, so we get a quick forward frame with zero motion -- for goto-style
    "find a tag quickly and bail out" usage this often saves the whole sweep).
    """
    lo, hi = (min(lo, hi), max(lo, hi))
    n = max(1, int(round((hi - lo) / float(step))))
    pans = [lo + i * (hi - lo) / n for i in range(n + 1)]
    if abs(current - hi) < abs(current - lo):
        pans.reverse()  # the upper edge is nearer -> sweep downward from there
    out = []
    if peek_current and lo <= current <= hi:
        out.append(float(current))
    out += [p for p in pans if abs(p - current) > 2.0 or not out]
    return out
