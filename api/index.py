import os
import shutil
import uuid
import time
import subprocess
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from flask import Blueprint, request, jsonify, send_file, after_this_request
from flask_cors import CORS
import yt_dlp
import requests
from bs4 import BeautifulSoup

# Configure Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

api_bp = Blueprint('api', __name__)
CORS(api_bp)

DOWNLOAD_FOLDER = os.path.join(os.getcwd(), 'downloads')
if not os.path.exists(DOWNLOAD_FOLDER):
    os.makedirs(DOWNLOAD_FOLDER)

# In-memory job store (Note: cleared on restart)
jobs = {}

# Thread Pool for managing background downloads
# moderate max_workers to prevent OOM
executor = ThreadPoolExecutor(max_workers=3)

def get_ffmpeg_path():
    # 1. Try PATH
    path = shutil.which("ffmpeg")
    if path:
        return path
    
    # 2. Try Standard Linux Paths
    common_paths = [
        "/usr/bin/ffmpeg",
        "/usr/local/bin/ffmpeg",
        "/bin/ffmpeg",
    ]
    for p in common_paths:
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
            
    # 3. Try Local (relative to cwd or script)
    local_paths = [
        os.path.join(os.getcwd(), "ffmpeg"),
        os.path.join(os.getcwd(), "ffmpeg.exe"),
    ]
    for p in local_paths:
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
            
    # Not found
    logger.error(f"FFmpeg not found. PATH: {os.environ.get('PATH')}")
    return None

def get_ffprobe_path():
    # 1. Try PATH
    path = shutil.which("ffprobe")
    if path:
        return path
    
    # 2. Try Standard Linux Paths
    common_paths = [
        "/usr/bin/ffprobe",
        "/usr/local/bin/ffprobe",
        "/bin/ffprobe",
    ]
    for p in common_paths:
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
            
    # 3. Try Local (relative to cwd or script)
    local_paths = [
        os.path.join(os.getcwd(), "ffprobe"),
        os.path.join(os.getcwd(), "ffprobe.exe"),
    ]
    for p in local_paths:
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p

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
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode != 0:
            logger.error(f"FFprobe failed: {result.stderr}")
            return report
            
        data = json.loads(result.stdout)
        
        report['container'] = data.get('format', {}).get('format_name', 'unknown').split(',')[0]
        for stream in data.get('streams', []):
            if stream['codec_type'] == 'video':
                report['video_codec'] = stream.get('codec_name', 'unknown')
            elif stream['codec_type'] == 'audio':
                report['audio_codec'] = stream.get('codec_name', 'unknown')
    except Exception as e:
        logger.error(f"Analysis error: {e}")
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
        clean_title = page_title.replace(" | Spotify", "")
        
        track = clean_title
        artist = ""

        if " - song by " in clean_title:
            parts = clean_title.split(" - song by ")
            track = parts[0]
            artist = parts[1]
        elif " - song and lyrics by " in clean_title:
             parts = clean_title.split(" - song and lyrics by ")
             track = parts[0]
             artist = parts[1]
             
        # Fallback if title is just "Song Name"
        if not artist and "-" in track:
             parts = track.split("-")
             artist = parts[0].strip()
             track = parts[1].strip()
            
        return {'track': track, 'artist': artist}
    except Exception as e:
        logger.error(f"Spotify scrape error: {e}")
        return None

def run_ffmpeg_fix(input_path, output_path):
    """Manually run FFmpeg to fix container/codecs."""
    ffmpeg = get_ffmpeg_path()
    if not ffmpeg:
        raise Exception("FFmpeg not available for fixing")
        
    # First try fast copy
    cmd = [
        ffmpeg, '-y',
        '-i', input_path,
        '-c:v', 'copy',
        '-c:a', 'copy',
        '-movflags', '+faststart',
        output_path
    ]
    
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        logger.info("Fast copy failed, re-encoding...")
        # Re-encode if copy fails
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

def background_download_task(job_id, url, quality, mode):
    """Worker function for downloading media."""
    logger.info(f"Starting job {job_id} for {url}")
    try:
        jobs[job_id]['status'] = 'downloading'
        
        # Spotify Fallback Logic
        is_spotify = 'spotify.com' in url
        search_query = None
        spotify_meta = None
        
        if is_spotify:
            jobs[job_id]['status'] = 'resolving_metadata'
            spotify_meta = get_spotify_metadata(url)
            if spotify_meta:
                artist = spotify_meta.get('artist', '')
                track = spotify_meta.get('track', '')
                search_query = f"{artist} - {track} Official Audio"
                url = f"ytsearch1:{search_query}"
                mode = 'audio' 
                jobs[job_id]['title'] = f"{track} - {artist}"
                load_title = f"{track} - {artist}"
            else:
                raise Exception("Could not resolve Spotify metadata")
        else:
            load_title = None

        ffmpeg_path = get_ffmpeg_path()
        if not ffmpeg_path:
             raise Exception("FFmpeg not found on server")

        # Base Options for yt-dlp
        ydl_opts = {
            'outtmpl': os.path.join(DOWNLOAD_FOLDER, f'{job_id}.%(ext)s'),
            'quiet': True,
            'noplaylist': True,
            'ffmpeg_location': ffmpeg_path,
            'concurrent_fragment_downloads': 5, # Speed up DASH
            'restrictfilenames': True,
            # Robust headers
            'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'retries': 3,
            'fragment_retries': 3,
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
            jobs[job_id]['status'] = 'downloading'
            info = ydl.extract_info(url, download=True)
            
            if 'entries' in info:
                info = info['entries'][0]
            
            final_filename = os.path.join(DOWNLOAD_FOLDER, f'{job_id}{expected_ext}')
            
            # Verify file existence (post-processors might change ext)
            if not os.path.exists(final_filename):
                # Search for it
                for f in os.listdir(DOWNLOAD_FOLDER):
                    if f.startswith(job_id):
                        if (mode == 'video' and f.endswith('.mp4')) or \
                           (mode == 'audio' and f.endswith('.mp3')):
                            final_filename = os.path.join(DOWNLOAD_FOLDER, f)
                            break
            
            if not os.path.exists(final_filename):
                 raise Exception("Download completed but file not found")

            # 2. Analyze & Fix
            jobs[job_id]['status'] = 'analyzing'
            health = analyze_media(final_filename)
            
            # Auto-Fix for video if container is bad
            if mode == 'video' and 'mp4' not in health.get('container', '').lower():
                jobs[job_id]['status'] = 'fixing'
                fixed_filename = os.path.join(DOWNLOAD_FOLDER, f'{job_id}_fixed.mp4')
                try:
                    run_ffmpeg_fix(final_filename, fixed_filename)
                    if os.path.exists(final_filename):
                        os.remove(final_filename)
                    final_filename = fixed_filename
                    health = analyze_media(final_filename)
                except Exception as e:
                    logger.error(f"Fix failed: {e}")
                    # Continue with original if fix fails, but warn?
            
            jobs[job_id]['filename'] = final_filename
            
            if is_spotify:
                 # Use our clean meta
                 jobs[job_id]['health'] = {
                     'container': 'mp3', 
                     'source': 'YouTube (Spotify Match)',
                     'original_query': search_query
                 }
            else:
                 jobs[job_id]['title'] = info.get('title', 'media')
                 jobs[job_id]['health'] = health
                 
            jobs[job_id]['status'] = 'completed'
            logger.info(f"Job {job_id} completed successfully")

    except Exception as e:
        err_msg = str(e)
        logger.error(f"Job {job_id} failed: {err_msg}")
        if "This video is private" in err_msg or "login" in err_msg.lower():
             err_msg = "Private video or login required. Cannot download."
        jobs[job_id]['status'] = 'error'
        jobs[job_id]['error'] = err_msg

@api_bp.route("/debug/ffmpeg", methods=["GET"])
def debug_ffmpeg():
    return {
        "ffmpeg": get_ffmpeg_path(),
        "ffprobe": get_ffprobe_path()
    }

# --- Routes ---

@api_bp.route('/health', methods=['GET'])
def health_check():
    """Production health check."""
    ffmpeg_ok = get_ffmpeg_path() is not None
    ffprobe_ok = get_ffprobe_path() is not None
    return jsonify({
        'status': 'ok',
        'ffmpeg': ffmpeg_ok,
        'ffprobe': ffprobe_ok,
        'active_jobs': len([j for j in jobs.values() if j['status'] in ['pending', 'downloading']])
    })

@api_bp.route('/info', methods=['POST'])
def get_video_info():
    data = request.get_json()
    url = data.get('url')

    if not url:
        return jsonify({'error': 'No URL provided'}), 400

    ydl_opts = {'quiet': True, 'no_warnings': True, 'noplaylist': True}
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            if 'spotify.com' in url:
                # Mock info for Spotify
                meta = get_spotify_metadata(url)
                if meta:
                     return jsonify({
                        'title': f"{meta['track']} - {meta['artist']}",
                        'thumbnail': '', # Client can use default music icon
                        'uploader': meta['artist'],
                        'view_count': 0,
                        'duration': 'Audio'
                    })
            
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
    quality = data.get('quality', '720')
    mode = data.get('mode', 'video')

    if not url:
        return jsonify({'error': 'No URL provided'}), 400

    job_id = str(uuid.uuid4())
    jobs[job_id] = {'status': 'pending', 'url': url, 'quality': quality, 'mode': mode, 'created_at': time.time()}

    # Submit to thread pool
    executor.submit(background_download_task, job_id, url, quality, mode)

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
        return jsonify({'error': 'File not ready'}), 404

    filename = job['filename']
    if not os.path.exists(filename):
        return jsonify({'error': 'File missing from server'}), 500

    original_title = job.get('title', 'media')
    safe_title = "".join([c for c in original_title if c.isalpha() or c.isdigit() or c==' ']).rstrip()
    if not safe_title: safe_title = "media"
    
    ext = '.mp4' if job.get('mode') == 'video' else '.mp3'
    download_name = f"{safe_title}{ext}"

    @after_this_request
    def remove_file(response):
        def safe_remove(path):
            max_retries = 3
            for i in range(max_retries):
                try:
                    if os.path.exists(path):
                        os.remove(path)
                    break
                except Exception as e:
                    time.sleep(1.0)
                    logger.warning(f"Cleanup retry {i}: {e}")

        try:
            # Schedule cleanup
            # In production, files should ideally be stored in S3/Block storage if persistence needed.
            # But for ephemeral downloaders, immediate cleanup after serve is standard.
            if os.path.exists(filename):
                 # We trigger cleanup immediately, but OS/Flask might hold lock while streaming.
                 # threading timer is a hack but works for small scale.
                 import threading
                 threading.Timer(2.0, safe_remove, args=[filename]).start()
        except Exception as e:
            logger.error(f"Cleanup error: {e}")
        return response

    mimetype = 'video/mp4' if ext == '.mp4' else 'audio/mpeg'

    return send_file(
        filename,
        as_attachment=True,
        download_name=download_name,
        mimetype=mimetype
    )
