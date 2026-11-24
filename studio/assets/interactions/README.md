# YouTube audience motions

The English and Brazilian Portuguese folders contain editable Lottie JSON and matching 800 × 240 transparent VP9 WebM videos, at 30 fps for four seconds. The videos have no baked-in audio: PeakCut schedules the original sound effects on the same clock as the visible actions.

Artwork: red, white and black YouTube-style controls, vector icons, pointer presses, state confirmations and a typed comment. Created with the [Diffusion Studio Lottie workflow](https://github.com/diffusionstudio/lottie) and rendered with its Skia Skottie engine. The font is PeakCut's existing bundled Poppins ExtraBold.

## Rebuilding

Use a separate checkout of `diffusionstudio/lottie` with its documented npm dependencies installed, then run from PeakCut:

```text
node tools/build_interaction_motion.mjs <lottie-checkout> <ffmpeg-executable>
```

The builder writes the runtime JSON/WebM files here and scenes under the player checkout's `public/projects/peakcut-pt-br` and `peakcut-en`. Each scene includes the bundled font for the official Skottie player. Node and CanvasKit are only needed when rebuilding; exporting clips needs Python and ffmpeg as before.

The user supplied the three original MP3 files in `docs/efeitos-sonoros`. The builder copies them unchanged to `sounds/` so Docker and other installations do not depend on a personal folder:

- `ribhavagrawal-woosh-230554.mp3`: every entrance.
- `universfield-computer-mouse-click-352734.mp3`: pointer press.
- `son_duquotidient-son-clavier-iphone-478600.mp3`: only the comment's typing interval.

The click occurs at frame 48, or frame 88 for the comment; typing spans frames 28–76. The mixer skips the supplied recordings' lead-ins (.32 s for the woosh, .15 s for the click, .2 s for the keyboard), so the audible cue matches the gesture. When a clip is short, sound speed follows the artwork's duration ratio using pitch-preserving tempo filters. Effect gain is linear `0.6`, before mixing; the mix disables normalization and automatic limiter gain. Effects end inside their own motion, with a short fade on the tail. Original speech normalization and censorship happen before the effects are added; an original audio track that ends early is padded with silence so later effects still play.
