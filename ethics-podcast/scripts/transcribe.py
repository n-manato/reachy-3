import whisper
from pathlib import Path


# Relative path from the Reachy folder
VIDEO_DIR = Path("videos")
OUTPUT_DIR = Path("videos")

OUTPUT_DIR.mkdir(exist_ok=True)

model = whisper.load_model("base")

video_files = sorted(VIDEO_DIR.glob("*.mp4"))

if len(video_files) != 4:
    print(f"Expected 4 MP4 files, but found {len(video_files)}.")
    print("Please put exactly 4 MP4 files in the videos folder.")
    exit()

for video_path in video_files:
    print(f"Transcribing: {video_path.name}")

    result = model.transcribe(
        str(video_path),
        language="en"
    )

    output_file = OUTPUT_DIR / f"{video_path.stem}.txt"

    with open(output_file, "w", encoding="utf-8") as file:
        file.write(result["text"])

    print(f"Saved: {output_file}")

print("Done. All 4 videos have been transcribed.")