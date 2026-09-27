"""Velox Cut – Schnitt-Pipeline (Schritt 1: provisorisch).

Erzeugt aus dem Quellvideo bis zu N Hochformat-Clips (1080x1920, max. 60 s), gleichmäßig über das Video verteilt,
mittig zugeschnitten, Lautstärke auf -14 LUFS. Noch OHNE KI-Momentsuche, Untertitel und Viral-Effekte –
die ersetzt Schritt 2 (Transkript mit mlx-whisper, Momentsuche mit Gemini, Remotion-Render Clean/Viral).

Ausgabe: Zeilen „PROGRESS n“ und am Ende eine JSON-Zeile {"clips": [{"file", "title", "seconds"}]}.
"""
import argparse, json, subprocess, sys

ap = argparse.ArgumentParser()
ap.add_argument("--src", required=True)
ap.add_argument("--out", required=True)
ap.add_argument("--style", default="clean")
ap.add_argument("--clips", type=int, default=1)
ap.add_argument("--caption", default="rise")
ap.add_argument("--color", default="#F6E27F")
a = ap.parse_args()


def duration(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path], capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        sys.exit("Video konnte nicht gelesen werden (Format nicht unterstützt?).")


total = duration(a.src)
clip_len = min(60.0, total)
n = max(1, min(a.clips, int(total // max(clip_len, 1)) or 1))
step = (total - clip_len) / n if n > 1 else 0
clips = []
for i in range(n):
    start = step * i
    out = f"{a.out}/clip-{i + 1}.mp4"
    vf = "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,format=yuv420p"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{start:.2f}", "-t", f"{clip_len:.2f}", "-i", a.src, "-vf", vf,
                    "-af", "loudnorm=I=-14:TP=-1.5:LRA=11", "-c:v", "libx264", "-preset", "medium", "-crf", "20",
                    "-profile:v", "high", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out], check=True)
    clips.append({"file": out, "title": f"Clip {i + 1}", "seconds": round(clip_len, 1)})
    print(f"PROGRESS {10 + int(85 * (i + 1) / n)}", flush=True)

print(json.dumps({"clips": clips}), flush=True)
