#!/usr/bin/env python3
"""ml_key.py — sends keystrokes WITH SCANCODES (SendInput, KEYEVENTF_SCANCODE), for a Moonlight
game-streaming window ("távoli" = remote).

Why: Moonlight (SDL) drops synthetic keystrokes that carry only a virtual-key code and no scancode
(pyautogui, Windows-MCP): typing into a Steam search field simply did nothing. With a hardware
scancode, SDL sees it as a real key.

Usage:
  python ml_key.py type "pool"          # text (US layout: letters, digits, space, basic punctuation)
  python ml_key.py key enter            # one key (enter, esc, space, tab, backspace, up/down/left/right, f1..f12)
  python ml_key.py key ctrl+alt+shift+q # a combination
  python ml_key.py hold w 0.5           # hold for half a second (for games)
The Moonlight window must have focus (click on the stream first).
"""
import ctypes
import sys
import time
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008
KEYEVENTF_EXTENDEDKEY = 0x0001
MAPVK_VK_TO_VSC = 0

ULONG_PTR = ctypes.c_size_t


class KEYBDINPUT(ctypes.Structure):
    _fields_ = (("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR))


class _INPUTUNION(ctypes.Union):
    _fields_ = (("ki", KEYBDINPUT), ("_pad", ctypes.c_byte * 32))


class INPUT(ctypes.Structure):
    _fields_ = (("type", wintypes.DWORD), ("u", _INPUTUNION))


VK = {
    "enter": 0x0D, "esc": 0x1B, "escape": 0x1B, "space": 0x20, "tab": 0x09, "backspace": 0x08,
    "delete": 0x2E, "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27, "home": 0x24, "end": 0x23,
    "pageup": 0x21, "pagedown": 0x22, "shift": 0x10, "ctrl": 0x11, "alt": 0x12, "win": 0x5B,
    "capslock": 0x14,
}
for i in range(1, 13):
    VK[f"f{i}"] = 0x6F + i
EXTENDED = {"up", "down", "left", "right", "home", "end", "pageup", "pagedown", "delete", "win"}
SHIFTED = {'!': '1', '@': '2', '#': '3', '$': '4', '%': '5', '^': '6', '&': '7', '*': '8', '(': '9', ')': '0',
           '_': '-', '+': '=', '{': '[', '}': ']', '|': '\\', ':': ';', '"': "'", '<': ',', '>': '.', '?': '/', '~': '`'}


def _vk_of(name):
    n = name.lower()
    if n in VK:
        return VK[n]
    if len(n) == 1:
        r = user32.VkKeyScanW(ord(n))
        if r == -1:
            raise ValueError(f"no virtual-key code for: {name!r}")
        return r & 0xFF
    raise ValueError(f"unknown key: {name!r}")


def _send(vk, up=False, extended=False):
    scan = user32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC)
    flags = KEYEVENTF_SCANCODE | (KEYEVENTF_KEYUP if up else 0) | (KEYEVENTF_EXTENDEDKEY if extended else 0)
    inp = INPUT(type=INPUT_KEYBOARD)
    inp.u.ki = KEYBDINPUT(0, scan, flags, 0, 0)
    if user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT)) != 1:
        raise OSError(ctypes.get_last_error())


def tap(name, hold=0.03):
    vk = _vk_of(name); ext = name.lower() in EXTENDED
    _send(vk, False, ext); time.sleep(hold); _send(vk, True, ext); time.sleep(0.02)


def combo(spec, hold=0.05):
    parts = [p for p in spec.split("+") if p]
    vks = [(_vk_of(p), p.lower() in EXTENDED) for p in parts]
    for vk, ext in vks: _send(vk, False, ext); time.sleep(0.01)
    time.sleep(hold)
    for vk, ext in reversed(vks): _send(vk, True, ext); time.sleep(0.01)


def type_text(text, delay=0.04):
    for ch in text:
        if ch == " ": tap("space"); continue
        if ch == "\n": tap("enter"); continue
        shifted = ch.isupper() or ch in SHIFTED
        base = SHIFTED.get(ch, ch.lower())
        if shifted:
            _send(VK["shift"]); time.sleep(0.01)
        tap(base)
        if shifted:
            _send(VK["shift"], up=True)
        time.sleep(delay)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__); sys.exit(2)
    mode, arg = sys.argv[1], sys.argv[2]
    if mode == "type":
        type_text(arg); print(f"OK type {arg!r}")
    elif mode == "key":
        combo(arg) if "+" in arg else tap(arg); print(f"OK key {arg}")
    elif mode == "hold":
        secs = float(sys.argv[3]) if len(sys.argv) > 3 else 0.3
        vk = _vk_of(arg); ext = arg.lower() in EXTENDED
        _send(vk, False, ext); time.sleep(secs); _send(vk, True, ext); print(f"OK hold {arg} {secs}s")
    else:
        print(__doc__); sys.exit(2)
