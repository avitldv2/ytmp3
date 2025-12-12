from flask import Flask, render_template, request, send_file, after_this_request, redirect, url_for, flash
import yt_dlp
from yt_dlp import DownloadError
import os
import time
import threading
import sys
from pathlib import Path
from urllib.parse import urlparse

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'change-this-secret-key-in-production')

DOWNLOAD_FOLDER = Path('downloads')
DOWNLOAD_FOLDER.mkdir(exist_ok=True)

YOUTUBE_DOMAINS = [
    'youtube.com',
    'www.youtube.com',
    'm.youtube.com',
    'youtu.be',
    'youtube-nocookie.com',
    'www.youtube-nocookie.com'
]

def is_valid_url(url):
    try:
        result = urlparse(url)
        return all([result.scheme, result.netloc])
    except Exception:
        return False

def is_youtube_url(url):
    if not is_valid_url(url):
        return False
    try:
        result = urlparse(url)
        netloc = result.netloc.lower()
        is_youtube_domain = any(netloc == domain or netloc.endswith('.' + domain) for domain in YOUTUBE_DOMAINS)
        if not is_youtube_domain:
            return False
        if 'youtu.be' in netloc:
            return len(result.path.strip('/')) > 0
        if 'youtube.com' in netloc or 'youtube-nocookie.com' in netloc:
            return '/watch' in result.path or '/embed/' in result.path or '/v/' in result.path
        return True
    except Exception:
        return False

def delete_file(file_path, max_retries=5, initial_delay=1):
    file_path = Path(file_path)
    if not file_path.exists():
        return True
    delay = initial_delay
    for attempt in range(max_retries):
        try:
            time.sleep(delay)
            if sys.platform == 'win32':
                os.chmod(file_path, 0o777)
            file_path.unlink()
            return True
        except PermissionError:
            if attempt < max_retries - 1:
                delay *= 2
                continue
            else:
                app.logger.error(f"Permission denied deleting file after {max_retries} attempts: {file_path}")
                return False
        except FileNotFoundError:
            return True
        except Exception as e:
            if attempt < max_retries - 1:
                delay *= 2
                continue
            else:
                app.logger.error(f"Error deleting file after {max_retries} attempts: {file_path}, Error: {e}")
                return False
    return False

@app.route('/')
def index():
    saved_bitrate = request.cookies.get('bitrate', '192')
    return render_template('index.html', saved_bitrate=saved_bitrate)

@app.route('/download', methods=['POST'])
def download():
    url = request.form['url']
    bitrate = request.form['bitrate']
    
    if not is_valid_url(url):
        flash('Please enter a valid URL.')
        return redirect(url_for('index'))
    
    if not is_youtube_url(url):
        flash('Please enter a valid YouTube video URL.')
        return redirect(url_for('index'))

    ydl_opts = {
        'format': 'bestaudio[protocol!=m3u8][ext=m4a]/bestaudio[protocol!=m3u8][ext=webm]/bestaudio[protocol!=m3u8]/bestaudio/best',
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': bitrate,
        }],
        'outtmpl': str(DOWNLOAD_FOLDER / '%(title)s.%(ext)s'),
        'quiet': False,
        'no_warnings': False,
        'extractor_args': {
            'youtube': {
                'player_client': ['android', 'ios'],
            }
        },
        'noplaylist': True,
        'ignoreerrors': False,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info_dict = ydl.extract_info(url, download=True)
            base_path = ydl.prepare_filename(info_dict)
            file_path = Path(base_path).with_suffix('.mp3')
            
            if not file_path.exists():
                app.logger.error(f"Downloaded file not found: {file_path}")
                flash('Error: Downloaded file was not created. Please check if FFmpeg is installed.')
                return redirect(url_for('index'))
            
            if file_path.stat().st_size == 0:
                app.logger.error(f"Downloaded file is empty: {file_path}")
                if file_path.exists():
                    file_path.unlink()
                flash('Error: The downloaded file is empty. YouTube may be blocking the download. Please try updating yt-dlp with: pip install -U yt-dlp')
                return redirect(url_for('index'))
                
    except DownloadError as e:
        app.logger.error(f"yt-dlp download error: {e}")
        error_msg = str(e)
        if 'FFmpeg' in error_msg or 'ffmpeg' in error_msg:
            flash('Error: FFmpeg is required but not found. Please install FFmpeg to convert videos to MP3.')
        elif 'Private video' in error_msg or 'unavailable' in error_msg.lower():
            flash('Error: This video is unavailable or private.')
        else:
            flash(f'Error downloading video: {error_msg[:100]}')
        return redirect(url_for('index'))
    except Exception as e:
        app.logger.error(f"Error downloading video: {type(e).__name__}: {e}")
        error_msg = str(e)
        if 'FFmpeg' in error_msg or 'ffmpeg' in error_msg:
            flash('Error: FFmpeg is required but not found. Please install FFmpeg to convert videos to MP3.')
        else:
            flash(f'Error downloading video: {error_msg[:100]}')
        return redirect(url_for('index'))

    @after_this_request
    def cleanup(response):
        def delete_after_send():
            delete_file(file_path)
        threading.Thread(target=delete_after_send, daemon=True).start()
        return response

    download_name = file_path.name
    response = send_file(
        str(file_path),
        as_attachment=True,
        download_name=download_name,
        mimetype='audio/mpeg'
    )
    response.set_cookie('bitrate', bitrate)
    return response

if __name__ == '__main__':
    app.run(debug=True) 