import os
import shutil
import uuid
import threading
import time
import subprocess
import json
from flask import Blueprint, request, jsonify, send_file, after_this_request, send_from_directory
import imageio_ffmpeg
from flask_cors import CORS
import yt_dlp
import requests
import re
from bs4 import BeautifulSoup

api_bp = Blueprint('api', __name__)
CORS(api_bp)

DOWNLOAD_FOLDER = os.path.join(os.getcwd(), 'downloads')
if not os.path.exists(DOWNLOAD_FOLDER):
    os.makedirs(DOWNLOAD_FOLDER)

# In-memory job store
jobs = {}

def get_ffmpeg_path():
    try:
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None

def get_ffprobe_path():
    ffmpeg = get_ffmpeg_path()
    if ffmpeg:
        base = os.path.dirname(ffmpeg)
        # Windows extension check could be robust, but usually it's in the same bin folder
        probe = os.path.join(base, 'ffprobe.exe')
        if os.path.exists(probe):
            return probe
    return None

def analyze_media(filepath):
    """Returns dict of media info: container, video_codec, audio_codec."""
    report = {'container': 'unknown', 'video_codec': 'none', 'audio_codec': 'none'}
    
    probe_exe = get_ffprobe_path()
    if not probe_exe:
        report['error'] = 'FFprobe not found'
        return report

    try:
        cmd = [
            probe_exe, 
            '-v', 'quiet', 
            '-print_format', 'json', 
            '-show_format', 
            '-show_streams', 
            filepath
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        data = json.loads(result.stdout)
        
        report['container'] = data.get('format', {}).get('format_name', 'unknown').split(',')[0]
        for stream in data.get('streams', []):
            if stream['codec_type'] == 'video':
                report['video_codec'] = stream.get('codec_name', 'unknown')
            elif stream['codec_type'] == 'audio':
                report['audio_codec'] = stream.get('codec_name', 'unknown')
    except Exception as e:
        report['error'] = str(e)
        
    return report

def get_spotify_metadata(url):
    """Scrapes public metadata from Spotify URL."""
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code != 200:
            return None
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Refined parsing
        page_title = soup.title.string if soup.title else ""
        # Remove " | Spotify"
        clean_title = page_title.replace(" | Spotify", "")
        
        track = clean_title
        artist = ""

        # If it contains " - song by ", split it
        if " - song by " in clean_title:
            parts = clean_title.split(" - song by ")
            track = parts[0]
            artist = parts[1]
        elif " - song and lyrics by " in clean_title:
             parts = clean_title.split(" - song and lyrics by ")
             track = parts[0]
             artist = parts[1]
            
        return {'track': track, 'artist': artist}
    except Exception as e:
        print(f"Spotify scrape error: {e}")
        return None

def run_ffmpeg_fix(input_path, output_path):
    """Manually run FFmpeg to fix container/codecs."""
    ffmpeg = get_ffmpeg_path()
    if not ffmpeg:
        raise Exception("FFmpeg not available for fixing")
        
    cmd = [
        ffmpeg, '-y',
        '-i', input_path,
        '-c:v', 'copy', # Try copy first for speed
        '-c:a', 'copy',
        '-movflags', '+faststart',
        output_path
    ]
    
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        # If copy fails (incompatible), try re-encode (slower but safer)
        cmd = [
            ffmpeg, '-y',
            '-i', input_path,
            '-c:v', 'libx264',
            '-preset', 'fast',
            '-crf', '23',
            '-c:a', 'aac',
            '-b:a', '128k',
            '-movflags', '+faststart',
            output_path
        ]
        subprocess.run(cmd, check=True)

def background_download(job_id, url, quality, mode):
    try:
        jobs[job_id]['status'] = 'downloading'
        
        # Spotify Fallback Logic
        is_spotify = 'spotify.com' in url
        search_query = None
        
        if is_spotify:
            jobs[job_id]['status'] = 'resolving_metadata'
            meta = get_spotify_metadata(url)
            if meta:
                # Construct YouTube Search Query
                artist = meta.get('artist', '')
                track = meta.get('track', '')
                search_query = f"{artist} - {track} Official Audio"
                url = f"ytsearch1:{search_query}"
                mode = 'audio' # Force audio
                jobs[job_id]['title'] = f"{track} - {artist}"
                # Update status for user feedback (optional if UI supports it, otherwise 'downloading')
                jobs[job_id]['status'] = 'downloading'
            else:
                raise Exception("Could not resolve Spotify metadata")

        ffmpeg_path = get_ffmpeg_path()
        
        # Base Options
        ydl_opts = {
            'outtmpl': os.path.join(DOWNLOAD_FOLDER, f'{job_id}.%(ext)s'),
            'quiet': True,
            'noplaylist': True,
            'ffmpeg_location': ffmpeg_path,
            'concurrent_fragment_downloads': 5,
            'restrictfilenames': True,
            # Facebook/Instagram often require a user agent to avoid 403s on public videos
            'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        }

        expected_ext = '.mp4'
        if mode == 'audio':
            expected_ext = '.mp3'
            ydl_opts.update({
                'format': 'bestaudio/best',
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                }],
            })
        else:
            # Video Mode
            if quality == '360':
                format_selector = 'bv*[height<=360][ext=mp4]+ba[ext=m4a]/b[ext=mp4] / best'
            else: 
                format_selector = 'bv*[height<=720][ext=mp4]+ba[ext=m4a]/b[ext=mp4] / best'
            
            ydl_opts.update({
                'format': format_selector,
                'merge_output_format': 'mp4',
            })

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # 1. Download
            info = ydl.extract_info(url, download=True)
            
            if 'entries' in info:
                # Search result is a playlist of 1
                info = info['entries'][0]
            
            # Predict filename
            # yt-dlp prepare_filename isn't always final if postprocessors run
            # We enforce our specific outtmpl so we know the base
            # If merged, it's job_id.mp4. If audio, job_id.mp3
            
            final_filename = os.path.join(DOWNLOAD_FOLDER, f'{job_id}{expected_ext}')
            
            # Fallback check: find what was actually created if prediction fails
            if not os.path.exists(final_filename):
                # pattern match
                for f in os.listdir(DOWNLOAD_FOLDER):
                    if f.startswith(job_id) and mode == 'video' and f.endswith('.mp4'):
                        final_filename = os.path.join(DOWNLOAD_FOLDER, f)
                        break
                    elif f.startswith(job_id) and mode == 'audio' and f.endswith('.mp3'):
                        final_filename = os.path.join(DOWNLOAD_FOLDER, f)
                        break
            
            # 2. Analyze & Fix
            jobs[job_id]['status'] = 'analyzing'
            health = analyze_media(final_filename)
            
            # Fix Logic: If video mode, but container not mp4/mov, or audio missing?
            # Note: yt-dlp merge_output_format='mp4' usually enforces mp4.
            # But if we got 'best' which was webm, and merge didn't happen (e.g. no audio stream to merge), 
            # we might have a webm.
            
            if mode == 'video' and 'mp4' not in health.get('container', '').lower():
                jobs[job_id]['status'] = 'fixing'
                fixed_filename = os.path.join(DOWNLOAD_FOLDER, f'{job_id}_fixed.mp4')
                run_ffmpeg_fix(final_filename, fixed_filename)
                
                # Cleanup old
                if os.path.exists(final_filename):
                    os.remove(final_filename)
                final_filename = fixed_filename
                
                # Re-analyze
                health = analyze_media(final_filename)
            
            jobs[job_id]['filename'] = final_filename
            if is_spotify:
                 jobs[job_id]['title'] = f"{meta.get('track')} (Spotify Match)"
                 jobs[job_id]['health'] = {
                     'container': 'mp3', 
                     'source': 'YouTube (Spotify Match)',
                     'original_query': search_query
                 }
            else:
                 jobs[job_id]['title'] = info.get('title', 'media')
                 jobs[job_id]['health'] = health
            jobs[job_id]['status'] = 'completed'

    except Exception as e:
        err_msg = str(e)
        # Friendly error messages for common Facebook issues
        if "This video is private" in err_msg or "login" in err_msg.lower():
             err_msg = "Private video or login required. Cannot download."
        jobs[job_id]['status'] = 'error'
        jobs[job_id]['error'] = err_msg


@api_bp.route('/info', methods=['POST'])
def get_video_info():
    data = request.get_json()
    url = data.get('url')

    if not url:
        return jsonify({'error': 'No URL provided'}), 400

    ydl_opts = {'quiet': True, 'no_warnings': True}
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return jsonify({
                'title': info.get('title'),
                'thumbnail': info.get('thumbnail'),
                'uploader': info.get('uploader'),
                'view_count': info.get('view_count'),
                'duration': info.get('duration_string')
            })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@api_bp.route('/download_job', methods=['POST'])
def start_download_job():
    data = request.get_json()
    url = data.get('url')
    # Default params
    quality = data.get('quality', '720')
    mode = data.get('mode', 'video')

    if not url:
        return jsonify({'error': 'No URL provided'}), 400

    job_id = str(uuid.uuid4())
    jobs[job_id] = {'status': 'pending', 'url': url, 'quality': quality, 'mode': mode}

    thread = threading.Thread(target=background_download, args=(job_id, url, quality, mode))
    thread.start()

    return jsonify({'job_id': job_id, 'status': 'pending'})

@api_bp.route('/status/<job_id>', methods=['GET'])
def get_job_status(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({'error': 'Job not found'}), 404
    return jsonify(job)

@api_bp.route('/file/<job_id>', methods=['GET'])
def get_file(job_id):
    job = jobs.get(job_id)
    if not job or job['status'] != 'completed':
        return jsonify({'error': 'File not ready or job not found'}), 404

    filename = job['filename']
    if not os.path.exists(filename):
        return jsonify({'error': 'File missing from server'}), 500

    original_title = job.get('title', 'media')
    safe_title = "".join([c for c in original_title if c.isalpha() or c.isdigit() or c==' ']).rstrip()
    
    ext = '.mp4' if job.get('mode') == 'video' else '.mp3'
    download_name = f"{safe_title}{ext}"

    @after_this_request
    def remove_file(response):
        def safe_remove(path):
            max_retries = 5
            for i in range(max_retries):
                try:
                    if os.path.exists(path):
                        os.remove(path)
                    break
                except PermissionError:
                    time.sleep(1.0) # Wait for handle release
                except Exception as e:
                    print(f"Error removing file: {e}")
                    break

        try:
            if os.path.exists(filename):
                # Run cleanup in a separate thread after a short delay
                # This ensures flask/OS releases the file handle used for serving
                threading.Timer(1.0, safe_remove, args=[filename]).start()
        except Exception as e:
            print(f"Cleanup error: {e}")
        return response

    mimetype = 'video/mp4' if ext == '.mp4' else 'audio/mpeg'

    return send_file(
        filename,
        as_attachment=True,
        download_name=download_name,
        mimetype=mimetype
    )
