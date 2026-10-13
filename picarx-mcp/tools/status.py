"""
`status` tool — the robot's current state.

The `Picarx()` SINGLETON lives here (get_robot), because there may be only one
instance for the whole server lifetime — two parallel instances would clash on GPIO.
The other tools (move etc.) get the shared instance from here.
"""
from core.robot_state import is_active_mode, get_last_active_age_s
from core.event_queue import drain
from core.sensing import read_distance_median

_px = None  # singleton — one Picarx() instance for the server's lifetime


def get_robot():
    """The shared Picarx() singleton. Lazy initialisation (on the first tool call)."""
    global _px
    if _px is None:
        from picarx import Picarx
        _px = Picarx()
    return _px


def _read_cpu_temp_c():
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return round(int(f.read().strip()) / 1000.0, 1)
    except Exception:
        return None


def _read_battery():
    """Robot HAT v4 2-cell battery. get_battery_voltage() from robot_hat.

    2-cell Li: ~8.4 V full, ~6.0 V empty. The % estimate is rough (the voltage
    sags under load), but good enough for orientation.
    """
    try:
        from robot_hat import get_battery_voltage
        v = round(float(get_battery_voltage()), 2)
        pct = max(0, min(100, round((v - 6.0) / (8.4 - 6.0) * 100)))
        return v, pct
    except Exception:
        return None, "unknown"


def register_status_tool(mcp, config):
    samples = config.get("safety", {}).get("distance_samples", 5)

    @mcp.tool()
    def status() -> dict:
        """Return the robot's current state (without moving).

        Safe to query at any time; nothing happens in physical space. 'distance_cm' is
        the median-filtered ultrasonic distance straight ahead (-1 = no valid echo).
        The 'events' field stays empty until phase 3.

        Returns:
            mode, last_active_age_s, cpu_temp_c, battery_v, battery_pct,
            distance_cm, docked, events, events_age_s
        """
        events, events_age_s = drain()
        battery_v, battery_pct = _read_battery()
        try:
            distance_cm = read_distance_median(get_robot(), samples)
        except Exception:
            distance_cm = -1.0
        return {
            "mode": "active" if is_active_mode() else "passive",
            "last_active_age_s": round(get_last_active_age_s(), 1),
            "cpu_temp_c": _read_cpu_temp_c(),
            "battery_v": battery_v,
            "battery_pct": battery_pct,
            "distance_cm": distance_cm,
            "docked": False,            # phase 6 (AprilTag docking)
            "events": events,           # [] until phase 3
            "events_age_s": events_age_s,
        }
