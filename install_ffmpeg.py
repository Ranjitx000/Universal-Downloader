import os
import zipfile
import io
import requests

FFMPEG_URL = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"

def install_ffmpeg():
    print("Downloading FFmpeg from GitHub...")
    try:
        r = requests.get(FFMPEG_URL)
        r.raise_for_status()
        print("Download complete. Extracting...")
        
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            # Find the bin folder content
            for file in z.namelist():
                if file.endswith('ffmpeg.exe') or file.endswith('ffprobe.exe'):
                    filename = os.path.basename(file)
                    print(f"Extracting {filename}...")
                    with z.open(file) as source, open(filename, 'wb') as target:
                        target.write(source.read())
        
        print("FFmpeg installation successful!")
        print(f"ffmpeg.exe created at: {os.getcwd()}")
        
    except Exception as e:
        print(f"Failed to install FFmpeg: {e}")

if __name__ == "__main__":
    install_ffmpeg()
