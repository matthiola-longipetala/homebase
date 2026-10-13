#!/bin/bash
# cam_source.sh — the camera source for go2rtc.
# rpicam-vid HW H.264 (Pi 4 hardware encoder), raw Annex-B to stdout.
# go2rtc calls it as an exec source (#video=h264). `exec` makes rpicam REPLACE
# the shell -> go2rtc's SIGKILL reaches rpicam directly.
#
# Rolling exposure: if /tmp/stream_expo.env exists (GAIN=.. SHUTTER=..), manual
# exposure is used (stream_supervisor sets it from the histogram); otherwise
# rpicam's built-in auto exposure (AEC/AGC). The supervisor switches the setting
# by restarting the source (kill rpicam-vid -> go2rtc re-exec).
EXTRA=""
if [ -f /tmp/stream_expo.env ]; then
  # shellcheck disable=SC1091
  source /tmp/stream_expo.env
  if [ -n "$GAIN" ] && [ -n "$SHUTTER" ]; then
    EXTRA="--gain $GAIN --shutter $SHUTTER"
  fi
fi
# AGAINST MOTION BLUR: in a dim indoor room rpicam's built-in auto exposure
# opens the shutter to the full frame time (15 fps -> ~66 ms) at gain 8 ->
# catastrophic blur while moving (a ~28 lux room is light-starved to begin with).
# So if there is no expo.env, use a CAPPED short shutter + moderate gain
# (blur-free from the first frame); the supervisor's rolling corrector tunes it
# to the room's light within a few seconds (bright: gain down, shutter slides
# below the cap; dark: gain up to the ceiling). Previous version:
# cam_source.sh.bak_preblur.
if [ -z "$EXTRA" ]; then
  EXTRA="--gain 24 --shutter 16000"
fi
# FOCUS LOCK (IMX708 Wide): the canonical place of lens_position is
# camera_config.json — with AF the lens would hunt and the fx of the navigation
# frames would drift.
LENS_POS=$(sed -n 's/.*"lens_position": *\([0-9.]*\).*/\1/p' $HOME/picarx-mcp/camera_config.json)
LENS_POS=${LENS_POS:-1.0}
exec rpicam-vid -t 0 --nopreview --camera 0 --codec h264 --inline --intra 15 \
  --width 1280 --height 720 --framerate 15 --bitrate 4000000 --level 4.2 \
  --autofocus-mode manual --lens-position "$LENS_POS" \
  --profile high --awb auto $EXTRA -o -
