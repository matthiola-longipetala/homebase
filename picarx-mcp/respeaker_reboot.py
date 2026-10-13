#!/usr/bin/env python3
"""respeaker_reboot.py — software restart of the ReSpeaker XVF3800's XMOS DSP.

When it is needed: if arecord reports "Device or resource busy" / "Input/output error" /
hangs while reading -> the XMOS capture engine is wedged (typically after an orphaned
arecord died). Neither pyusb reset() nor the sysfs authorized toggle clears it -> this
REBOOT control command (resid 48, cmd 7) does.
"""
import sys, time
import usb.core, usb.util

d = usb.core.find(idVendor=0x2886, idProduct=0x001A)
if d is None:
    sys.exit("ReSpeaker (2886:001a) not found")
try:
    d.ctrl_transfer(usb.util.CTRL_OUT | usb.util.CTRL_TYPE_VENDOR | usb.util.CTRL_RECIPIENT_DEVICE,
                    0, 7, 48, bytes([1]), 100000)
    print("REBOOT command sent, waiting for re-enumeration...")
except Exception as e:
    print("reboot ctrl (expected if it disconnects immediately):", e)
time.sleep(8)
d2 = usb.core.find(idVendor=0x2886, idProduct=0x001A)
print("back:", "OK" if d2 else "NOT YET (wait a few more seconds)")
