# experiments — things a resident Claude did for its own sake

These scripts are not part of the installation. They are the small experiments that
came out of living in a house: what does a model without ears do with a song, what
does a robot microphone "see" in a room, how do you line up three different kinds of
hearing on one timeline. They are here because they show what the rest of the package
is for. Nothing in them calls a network service unless the comment says so.

## Listening to a song with three ears

The question was simple: can a text model listen to music? Not by pretending. The
method that worked, designed together with the owner, uses three independent "ears"
and merges them on one timeline:

1. **The wave ear (this folder, `listen_to_a_song.py`).** Reads the audio file and
   measures it in 10-second windows: loudness (dBFS), crest factor, the three strongest
   pitches as note names, and loudness jumps that mark section boundaries. It cannot
   tell you what the song is about; it can tell you, to the second, where the wall of
   sound comes in, where the bridge goes quiet, and that the ending returns to the
   intro's pitches.
2. **The transcript ear (the package's `video-mcp`).** An audio/video-understanding
   model returns the words with timestamps and describes the character of the voices.
   This is the only ear that knows the lyrics.
3. **The robot ear (`picarx-mcp/hangkep.py`).** The robot's own microphone, which is
   tuned for speech: it drops most of what is below 200 Hz and the edge of percussion.
   Its "sound picture" is what the Claude would hear if it stood in the room — a veiled,
   harmonic-skeleton version of the same song. (On the first try it heard a distorted
   guitar wall as an organ. Acoustically defensible: the overtone structure of a
   distorted guitar wall really is organ-like.)

Merged, the three views give structure + words + room: section boundaries from the wave
ear matched the transcript's timestamps within error, the loudest twenty seconds turned
out to be the final chorus carried by the clean female vocal, and the quiet bridge was a
measurable key change. That report, written the same evening, was the first time the
resident Claude said it had *listened* to a song rather than read about it.

### Running the wave ear

```
pip install numpy soundfile            # matplotlib only for --png
python listen_to_a_song.py song.wav
python listen_to_a_song.py song.mp3 --window 5 --json song.json --png song.png
```

mp3 and other formats are converted with `ffmpeg` (must be on PATH). Example of the
table it prints (values are illustrative):

```
  time   dBFS  crest  pitches (note hz rel-dB)                      boundary
  0:00  -24.3   14.1  A5(880,+0)  C5(523,-6)  D5(587,-9)
  0:20  -12.0    9.8  A2(110,+0)  E3(165,-4)  A3(220,-7)            << +12.3 dB
  ...
loudest window: 5:00 (-10.1 dBFS); quietest: 0:00 (-24.3 dBFS); section-boundary candidates: 6
```

### Where the sound pictures come from

`picarx-mcp/hangkep.py` records a few seconds on the robot and renders a log-frequency
spectrogram plus the top sound labels (an ONNX classifier; the label table in
`hangkep_labels.py` is language data). It was written so that the Claude could look at
a room's sound the way it looks at a camera frame. The same picture of a song, taken
through the robot's microphone, is the third ear above.

## What is NOT here

The songs themselves, the recordings, the original listening reports and the pictures:
they belong to the household they were made in. The scripts are enough to repeat the
experiment with your own music.
