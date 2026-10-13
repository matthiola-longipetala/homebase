# tavoli/ — playing a streamed game together (optional, Windows)

*"távoli" = remote.*

Two small helpers that let Claude press keys and move the mouse inside a **Moonlight** game-streaming window (e.g. a game streamed from another PC through Steam / Sunshine), so Claude and the owner can play together. The screen comes in through screenshots (Windows-MCP or computer use).

- `ml_key.py`: keystrokes with hardware scancodes. Moonlight/SDL ignores the synthetic keystrokes ordinary automation sends, but accepts these.
- `ml_mouse.py`: relative mouse movement for first-person views, with a dead-reckoning compass (`ml_heading.json`). Calibrate `PX_PER_90` for your game and mouse speed.

The Moonlight window must have focus. Both scripts send input to whatever window is in front, so use them only while that window is the game.
