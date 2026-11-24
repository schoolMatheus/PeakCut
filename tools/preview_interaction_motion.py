"""Make a disposable example of all five motions without opening anyone's videos."""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main():
    output = ROOT / "outputs"
    output.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="pk-motion-preview-") as directory:
        temp = Path(directory)
        os.environ.update(PEAKCUT_DATA=str(temp / "data"), DATABASE_URL="", PEAKCUT_ACCOUNTS="0")
        from studio import render, tools

        if tools.setup():
            raise RuntimeError(tools.HELP)
        source = temp / "source.mp4"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                        "color=c=0x1d242f:s=640x360:r=30:d=24", "-f", "lavfi", "-i",
                        "anullsrc=r=48000:cl=stereo", "-t", "24", "-c:v", "libx264", "-c:a", "aac", str(source)], check=True)
        render.install_fonts()
        words = [{"w": word, "s": i * .45, "e": i * .45 + .4}
                 for i, word in enumerate(("Os pedidos animados ficam separados das legendas do vídeo ".split()) * 6)
                 if i * .45 < 24]
        clip = {"start": 0., "end": 24., "layout": "crop", "captions": True, "style": "clean",
                "hook": "INTERAÇÃO COM O PÚBLICO", "caption_language": "pt-BR",
                "interactions": ["subscribe", "like", "comment", "share", "save"]}
        result = render.render_clip(source, temp, "preview", clip, words, 640, 360)
        target = output / "motion-youtube-preview.mp4"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(temp / result["file"]), "-vf", "scale=540:960",
                        "-c:v", "libx264", "-crf", "20", "-c:a", "copy", "-movflags", "+faststart", str(target)], check=True)
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", "2.5", "-i", str(target), "-frames:v", "1",
                        str(target.with_suffix(".jpg"))], check=True)
        print(target)


if __name__ == "__main__":
    main()
