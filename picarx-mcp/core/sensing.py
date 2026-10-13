"""
Sensor filtering — a lesson from live testing.

A SINGLE sample of the ultrasonic `get_distance()` is noisy: we have seen false-short
(29 cm instead of a real 50) and suspicious false-long values. NEVER make a decision
on a single sample (neither reflex braking nor obstacle avoidance) — take several quick
samples and use their MEDIAN. Unlike the mean, the median is insensitive to outliers
(noise).

Negative/0 values (sonar timeout, no echo) are filtered out; if every sample is
invalid, the return value is -1.0 (== "unknown").
"""
import statistics
import time


def read_distance_median(px, samples: int = 5, gap_s: float = 0.02) -> float:
    """Median-filtered distance in cm from the Picarx ultrasonic sensor.

    Args:
        px: the shared Picarx() singleton
        samples: how many quick samples to take (default 5)
        gap_s: pause between samples (default 20 ms)

    Returns:
        the median distance in cm, or -1.0 if there is no valid sample
    """
    vals = []
    for _ in range(max(1, samples)):
        try:
            d = px.get_distance()
        except Exception:
            d = -1
        if d is not None and d > 0:
            vals.append(d)
        time.sleep(gap_s)
    if not vals:
        return -1.0
    return round(statistics.median(vals), 1)
