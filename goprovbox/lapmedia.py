"""Join already encoded lap frames and mux one sample-aligned audio track."""
import wave

from .media import executable
from .sound import RATE, CHANNELS, _process, attach_audio


def join_chapters(files, parts, destination, cancel):
    folder = files[0].parent
    # Only generated, fixed filenames enter the concat manifest. The durations
    # come from frame counts, avoiding MP4 container millisecond rounding.
    manifest = folder / "chapters.txt"
    lines = []
    for path, part in zip(files, parts):
        lines.extend([f"file '{path.name}'", f"duration {part.video.duration:.9f}"])
    manifest.write_text("\n".join(lines) + "\n", encoding="ascii")
    _process([executable("ffmpeg"), "-v", "error", "-nostdin", "-n", "-f", "concat",
              "-safe", "1", "-i", manifest.name, "-map", "0:v:0", "-c:v", "copy",
              "-an", "-movflags", "+faststart", str(destination)], folder / "join.log", cancel,
             cwd=folder, activity="Joining lap chapters")
    sound = folder / "joined.wav"
    with wave.open(str(sound), "wb") as out:
        out.setparams((CHANNELS, 2, RATE, 0, "NONE", "not compressed"))
        for path in files:
            with wave.open(str(path.with_suffix(".audio.wav")), "rb") as source:
                while block := source.readframes(RATE):
                    if cancel.is_set():
                        raise InterruptedError("Cancelled")
                    out.writeframesraw(block)
    return attach_audio(destination, sound, sum(p.video.duration for p in parts), cancel)
