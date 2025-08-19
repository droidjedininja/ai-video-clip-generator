import subprocess

def cut_clip(input_file, start, duration, output_file):
    # Scales to vertical 1080x1920. You can tweak crop/blur background later.
    cmd = [
        "ffmpeg",
        "-y",
        "-ss", str(start),
        "-i", input_file,
        "-t", str(duration),
        "-vf", "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "23",
        "-c:a", "aac",
        output_file
    ]
    subprocess.run(cmd, check=True)
    return output_file
