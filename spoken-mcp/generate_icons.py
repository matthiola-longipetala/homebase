"""
Icon generator — creates the system tray icons in icons/.
Only needs to be run once; the files are kept afterwards.
"""

from PIL import Image, ImageDraw
from pathlib import Path

ICONS_DIR = Path(__file__).parent / "icons"
ICONS_DIR.mkdir(exist_ok=True)

SIZE = 64


def create_mic_icon(state: str) -> Image.Image:
    """Draws a microphone icon.

    Args:
        state: 'idle' (grey), 'active' (red) or 'listening' (green)
    """
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Background circle
    if state == "active":
        bg_color = (220, 50, 50, 255)      # Red — recording
    elif state == "listening":
        bg_color = (50, 180, 50, 255)      # Green — listening
    else:
        bg_color = (100, 100, 100, 255)     # Grey — idle

    draw.ellipse([2, 2, SIZE - 2, SIZE - 2], fill=bg_color)

    # Microphone body (rounded rectangle)
    mic_w, mic_h = 14, 22
    mic_x = (SIZE - mic_w) // 2
    mic_y = 14
    draw.rounded_rectangle(
        [mic_x, mic_y, mic_x + mic_w, mic_y + mic_h],
        radius=7,
        fill=(255, 255, 255, 255)
    )

    # Microphone arc (half circle below)
    arc_margin = 8
    arc_top = mic_y + 8
    arc_bottom = mic_y + mic_h + 6
    draw.arc(
        [mic_x - arc_margin, arc_top, mic_x + mic_w + arc_margin, arc_bottom],
        start=0, end=180,
        fill=(255, 255, 255, 255),
        width=3
    )

    # Microphone stem
    stem_x = SIZE // 2
    stem_top = arc_bottom - 2
    stem_bottom = stem_top + 8
    draw.line(
        [stem_x, stem_top, stem_x, stem_bottom],
        fill=(255, 255, 255, 255),
        width=3
    )

    # Base
    foot_w = 14
    draw.line(
        [stem_x - foot_w // 2, stem_bottom, stem_x + foot_w // 2, stem_bottom],
        fill=(255, 255, 255, 255),
        width=3
    )

    return img


if __name__ == "__main__":
    # Idle icon (grey)
    idle_icon = create_mic_icon("idle")
    idle_path = ICONS_DIR / "mic_idle.png"
    idle_icon.save(idle_path)
    print(f"Idle icon saved: {idle_path}")

    # Listening icon (green)
    listening_icon = create_mic_icon("listening")
    listening_path = ICONS_DIR / "mic_listening.png"
    listening_icon.save(listening_path)
    print(f"Listening icon saved: {listening_path}")

    # Active icon (red)
    active_icon = create_mic_icon("active")
    active_path = ICONS_DIR / "mic_active.png"
    active_icon.save(active_path)
    print(f"Active icon saved: {active_path}")

    print("Done!")
