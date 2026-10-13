"""ml_mouse.py - relative mouse movement with SendInput (MOUSEEVENTF_MOVE), for first-person game views
in a Moonlight stream.
Usage: python ml_mouse.py move <dx> <dy> [step_ms]   - moves the delta in small steps (+ updates the compass)
       python ml_mouse.py turn <degrees>             - horizontal turn in degrees (+ right, - left), based on PX_PER_90
       python ml_mouse.py heading                    - the computed heading (degrees, 0 = the view at reset, + = right)
       python ml_mouse.py reset [degrees]            - zero the compass (or set it to a given heading)
       python ml_mouse.py click [left|right]
The Moonlight window must have focus. The compass is dead reckoning only: a game has no IMU, so it is
computed from the accumulated horizontal mouse movement. Calibrate PX_PER_90 per game and mouse speed
(one measurement: 1200 px = 90 degrees at mouse speed 1.5). Vertical movement (dy) is not counted.
State: ml_heading.json next to the script."""
import ctypes, sys, time, json, os
from ctypes import wintypes
PX_PER_90 = 1200.0
STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ml_heading.json")
ULONG_PTR = ctypes.c_size_t
class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]
class INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT)]
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]
def send(dx, dy, flags):
    inp = INPUT(type=0)
    inp.mi = MOUSEINPUT(dx, dy, 0, flags, 0, 0)
    ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
def _load():
    try:
        with open(STATE, encoding="utf-8") as f: return json.load(f)
    except Exception: return {"yaw": 0.0, "px": 0}
def _save(st):
    with open(STATE, "w", encoding="utf-8") as f: json.dump(st, f)
def heading():
    return _load()["yaw"] % 360.0
def _add(dx):
    st = _load(); st["yaw"] = (st.get("yaw", 0.0) + dx / PX_PER_90 * 90.0) % 360.0; st["px"] = st.get("px", 0) + dx; _save(st)
    return st["yaw"]
def move(dx, dy, step_ms=8, step=10):
    n = max(1, int(max(abs(dx), abs(dy)) / step))
    sx, sy = dx / n, dy / n
    ax = ay = 0.0
    for _ in range(n):
        ax += sx; ay += sy
        ix, iy = int(round(ax)), int(round(ay))
        send(ix, iy, 0x0001); ax -= ix; ay -= iy
        time.sleep(step_ms / 1000)
    return _add(dx)
def turn(deg, step_ms=8):
    return move(int(round(deg / 90.0 * PX_PER_90)), 0, step_ms)
if __name__ == "__main__":
    a = sys.argv[1:]
    if not a: print(__doc__); sys.exit(0)
    if a[0] == "move":
        h = move(int(a[1]), int(a[2]), int(a[3]) if len(a) > 3 else 8); print(f"OK move {a[1]} {a[2]} | heading {h:.0f} deg")
    elif a[0] == "turn":
        h = turn(float(a[1]), int(a[2]) if len(a) > 2 else 8); print(f"OK turn {a[1]} | heading {h:.0f} deg")
    elif a[0] == "heading":
        st = _load(); print(f"heading {st['yaw'] % 360:.0f} deg (accumulated px {st.get('px', 0)})")
    elif a[0] == "reset":
        _save({"yaw": float(a[1]) if len(a) > 1 else 0.0, "px": 0}); print("OK reset", a[1] if len(a) > 1 else 0)
    elif a[0] == "click":
        b = a[1] if len(a) > 1 else "left"
        d, u = (0x0002, 0x0004) if b == "left" else (0x0008, 0x0010)
        send(0, 0, d); time.sleep(0.05); send(0, 0, u); print("OK click", b)
