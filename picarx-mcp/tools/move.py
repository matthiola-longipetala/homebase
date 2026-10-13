"""
`move` tool — motion at a simple command level.

IMPORTANT: the PiCar-X has NO encoders, so distance is a rough time-based approximation
from calibration constants (config.json -> movement_calibration). Calibrate it
live: `forward_cm_per_sec_at_30` starts from an early test value of ~22 cm/s (at speed 40 it was ~30 cm/s).

Lessons from live testing built in:
- **Steering offset** (`steering_center_offset`): at 0° the robot pulled LEFT; for
  straight driving the centre position is shifted (from config).
- **Sonar-guarded forward motion**: forward does NOT blindly sleep for one `sleep`, but
  advances in short slices while watching a MEDIAN-filtered distance — if an obstacle
  comes within `obstacle_stop_cm`, it brakes IMMEDIATELY. It does not react to a single
  noisy sample (see core/sensing.py).

The `finally` branch is a safety switch: whatever happens, the motors stop.
"""
import time
from typing import Optional

from .status import get_robot
from core.robot_state import mark_chat_active
from core.event_queue import drain
from core.sensing import read_distance_median


def register_move_tool(mcp, config):
    calib = config.get("movement_calibration", {})
    cm_per_sec_at_30 = calib.get("forward_cm_per_sec_at_30", 22.0)
    turn_sec_per_90deg = calib.get("turn_sec_per_90deg", 1.5)
    steer_center = calib.get("steering_center_offset", 0)

    safety = config.get("safety", {})
    obstacle_stop_cm = safety.get("obstacle_stop_cm", 20)
    distance_samples = safety.get("distance_samples", 5)

    @mcp.tool()
    def move(direction: str, distance_cm: float = 50, speed: int = 30,
             steer_trim: Optional[int] = None) -> dict:
        """Move the robot at a simple command level.

        Forward motion is sonar-guarded: the robot stops by itself if an obstacle appears ahead.

        Args:
            direction: "forward" / "backward" / "turn_left" / "turn_right"
            distance_cm: cm for forward/backward; for turns, the turn angle in degrees
            speed: 0-100 motor level (default 30)
            steer_trim: live override of the steering centre position in degrees (for calibration);
                if None, the config's 'steering_center_offset' is used. Positive =
                corrects to the right. Lets you tune straight driving without a restart.

        Returns:
            result text + (for forward) stopped_by / distance_cm + events
        """
        mark_chat_active()
        px = get_robot()
        center = steer_trim if steer_trim is not None else steer_center
        try:
            if direction == "forward":
                px.set_dir_servo_angle(center)
                duration_s = distance_cm / cm_per_sec_at_30 * (30 / max(speed, 1))
                stopped_by = "distance_reached"
                final_d = None
                # safety check before starting
                d0 = read_distance_median(px, distance_samples)
                if 0 < d0 < obstacle_stop_cm:
                    result = f"Did NOT move: obstacle already {d0}cm ahead (< {obstacle_stop_cm})"
                    events, events_age_s = drain()
                    return {"result": result, "stopped_by": "obstacle",
                            "distance_cm": d0, "events": events, "events_age_s": events_age_s}
                px.forward(speed)
                t0 = time.time()
                while time.time() - t0 < duration_s:
                    d = read_distance_median(px, 3)   # faster while driving, 3 samples
                    final_d = d
                    if 0 < d < obstacle_stop_cm:
                        stopped_by = "obstacle"
                        break
                    time.sleep(0.03)
                px.forward(0)
                if stopped_by == "obstacle":
                    result = f"Moved forward, then STOPPED at obstacle {final_d}cm ahead"
                else:
                    result = f"Moved forward ~{distance_cm}cm"
                events, events_age_s = drain()
                return {"result": result, "stopped_by": stopped_by,
                        "distance_cm": final_d, "events": events, "events_age_s": events_age_s}

            elif direction == "backward":
                # no rear sensor → time-based, carefully
                px.set_dir_servo_angle(center)
                duration_s = distance_cm / cm_per_sec_at_30 * (30 / max(speed, 1))
                px.backward(speed)
                time.sleep(duration_s)
                px.forward(0)
                result = f"Moved backward ~{distance_cm}cm (no rear sensor)"

            elif direction == "turn_left":
                px.set_dir_servo_angle(-30 + center)
                px.forward(speed)
                time.sleep(turn_sec_per_90deg * (distance_cm / 90))
                px.forward(0)
                px.set_dir_servo_angle(center)
                result = f"Turned left ~{distance_cm}deg"

            elif direction == "turn_right":
                px.set_dir_servo_angle(30 + center)
                px.forward(speed)
                time.sleep(turn_sec_per_90deg * (distance_cm / 90))
                px.forward(0)
                px.set_dir_servo_angle(center)
                result = f"Turned right ~{distance_cm}deg"

            else:
                result = f"Unknown direction: {direction}"

            events, events_age_s = drain()
            return {"result": result, "events": events, "events_age_s": events_age_s}
        finally:
            px.forward(0)  # safety: whatever happens, motors off
