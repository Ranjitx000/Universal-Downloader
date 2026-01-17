import os
import zipfile
import tarfile
import io
import requests
import platform
import shutil

def install_ffmpeg():
    system = platform.system().lower()
    arch = platform.machine().lower()
    
    print(f"Detected System: {system}, Arch: {arch}")
    
    if system == 'windows':
        url = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"
        ext_to_extract = ['.exe']
    elif system == 'linux':
        # Using John Van Sickle's static builds for Linux usually reliable
        # But for diverse archs, might be safer to stick to apt if possible. 
        # This script acts as a fallback downloader.
        if 'arm' in arch or 'aarch64' in arch:
             url = "https://johnvansickle.com/ffmpeg/builds/ffmpeg-git-arm64-static.tar.xz"
        else:
             url = "https://johnvansickle.com/ffmpeg/builds/ffmpeg-git-amd64-static.tar.xz"
        ext_to_extract = ['ffmpeg', 'ffprobe']
    else:
        print("This script currently supports Windows and Linux auto-install.")
        print("For MacOS, please use: brew install ffmpeg")
        return

    print(f"Downloading FFmpeg from {url}...")
    try:
        r = requests.get(url, timeout=60)
        r.raise_for_status()
        print("Download complete. Extracting...")
        
        file_obj = io.BytesIO(r.content)
        
        if url.endswith('.zip'):
            with zipfile.ZipFile(file_obj) as z:
                for file in z.namelist():
                    basename = os.path.basename(file)
                    if any(basename.lower() == name + suffix for name in ['ffmpeg', 'ffprobe'] for suffix in ['', '.exe']):
                         print(f"Extracting {basename}...")
                         with z.open(file) as source, open(basename, 'wb') as target:
                             target.write(source.read())
                             
        elif url.endswith('.tar.xz'):
            with tarfile.open(fileobj=file_obj, mode='r:xz') as t:
                for member in t.getmembers():
                     basename = os.path.basename(member.name)
                     if basename in ['ffmpeg', 'ffprobe']:
                        print(f"Extracting {basename}...")
                        f = t.extractfile(member)
                        if f:
                            with open(basename, 'wb') as target:
                                target.write(f.read())
                            os.chmod(basename, 0o755)

        print("FFmpeg installation successful!")
        print(f"Binaries located at: {os.getcwd()}")
        
    except Exception as e:
        print(f"Failed to install FFmpeg: {e}")

if __name__ == "__main__":
    install_ffmpeg()
