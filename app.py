from flask import Flask, render_template, request, jsonify, send_file, flash, redirect, url_for
import yt_dlp
import os
import time
from urllib.parse import urlparse, parse_qs

app = Flask(__name__)
app.secret_key = 'your-secret-key-here'

# Create downloads directory if it doesn't exist
DOWNLOADS_DIR = 'downloads'
if not os.path.exists(DOWNLOADS_DIR):
    os.makedirs(DOWNLOADS_DIR)


def validate_youtube_input(url_or_id):
    """Validate and return normalized YouTube URL, or raise ValueError if invalid"""
    if not url_or_id or not url_or_id.strip():
        raise ValueError("Please enter a YouTube video ID or URL")

    url_or_id = url_or_id.strip()

    # Check for obviously invalid characters or patterns
    if any(char in url_or_id for char in ['<', '>', '"', "'"]):
        raise ValueError("Invalid characters in URL")

    # If it's longer than reasonable URL length
    if len(url_or_id) > 2048:
        raise ValueError("URL too long")

    return normalize_youtube_url(url_or_id)


def normalize_youtube_url(url_or_id):
    """Normalize YouTube input to a valid URL that yt-dlp can handle"""
    url_or_id = url_or_id.strip()

    # If it's already a full URL, return as-is (yt-dlp handles all YouTube URL formats)
    if url_or_id.startswith(('http://', 'https://')):
        return url_or_id

    # If it looks like a video ID (11 characters, alphanumeric with hyphens/underscores), create a watch URL
    if len(url_or_id) == 11 and url_or_id.replace('-', '').replace(
            '_', '').isalnum():
        return f'https://www.youtube.com/watch?v={url_or_id}'

    # Try to handle partial URLs or malformed inputs
    if 'youtube.com' in url_or_id or 'youtu.be' in url_or_id:
        # Add https:// if missing
        if not url_or_id.startswith(('http://', 'https://')):
            return f'https://{url_or_id}'
        return url_or_id

    # If we can't determine the format, assume it's a video ID
    return f'https://www.youtube.com/watch?v={url_or_id}'


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/download', methods=['POST'])
def download_video():
    video_input = request.form.get('video_id', '')

    try:
        # Validate and normalize the input URL
        video_url = validate_youtube_input(video_input)

        # Configure yt-dlp options
        ydl_opts = {
            # 'outtmpl' will be updated after getting the video info
            'format':
            'best[height<=720]/best',  # Download best quality up to 720p
        }

        # Download the video
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # Get video info first to get the title and prepare filename
            info = ydl.extract_info(video_url, download=False)
            video_title = info.get('title', 'Unknown') if info else 'Unknown'

            # Sanitize the title to replace spaces with underscores
            # and then set the output template
            sanitized_title = video_title.replace(' ', '_').replace('/', '-')
            ydl.params['outtmpl'] = os.path.join(DOWNLOADS_DIR,
                                                 f'{sanitized_title}.%(ext)s')

            # Get the exact filename that will be created
            expected_filename = ydl.prepare_filename(info)
            expected_basename = os.path.basename(expected_filename)

            # Download the video
            ydl.download([video_url])

            # Check if the file exists at the expected location
            if os.path.exists(expected_filename):
                return jsonify({
                    'success':
                    True,
                    'message':
                    f'Video "{video_title}" downloaded successfully!',
                    'filename':
                    expected_basename,
                    'download_url':
                    f'/download_file/{expected_basename}'
                })
            else:
                # Fallback: try to find any new file in downloads directory
                # This handles cases where yt-dlp changes filename due to filesystem restrictions
                all_files = [
                    f for f in os.listdir(DOWNLOADS_DIR)
                    if os.path.isfile(os.path.join(DOWNLOADS_DIR, f))
                ]
                if all_files:
                    # Sort by modification time, get the most recent
                    newest_file = max(all_files,
                                      key=lambda f: os.path.getmtime(
                                          os.path.join(DOWNLOADS_DIR, f)))
                    return jsonify({
                        'success':
                        True,
                        'message':
                        f'Video "{video_title}" downloaded successfully!',
                        'filename':
                        newest_file,
                        'download_url':
                        f'/download_file/{newest_file}'
                    })
                else:
                    return jsonify({
                        'success':
                        False,
                        'message':
                        'Video was downloaded but file could not be located'
                    })

    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'Error downloading video: {str(e)}'
        })


@app.route('/download_file/<filename>')
def download_file(filename):
    """Serve the downloaded file to the user"""
    file_path = os.path.join(DOWNLOADS_DIR, filename)
    if os.path.exists(file_path):
        return send_file(file_path, as_attachment=True)
    else:
        flash('File not found')
        return redirect(url_for('index'))


@app.route('/delete_file/<filename>', methods=['DELETE'])
def delete_file(filename):
    """Delete a downloaded file from storage"""
    try:
        # Strong path validation to prevent directory traversal attacks
        if not filename or not filename.strip():
            return jsonify({
                'success': False,
                'message': 'Invalid filename'
            }), 400

        # Reject filenames with path separators or suspicious characters
        if '/' in filename or '\\' in filename or '..' in filename or filename.startswith(
                '.'):
            return jsonify({
                'success':
                False,
                'message':
                'Invalid filename - contains forbidden characters'
            }), 400

        # Ensure the absolute path stays within downloads directory
        file_path = os.path.join(DOWNLOADS_DIR, filename)
        downloads_abs_path = os.path.abspath(DOWNLOADS_DIR)
        file_abs_path = os.path.abspath(file_path)

        # Check if the resolved path is actually within the downloads directory
        if not file_abs_path.startswith(
                downloads_abs_path +
                os.sep) and file_abs_path != downloads_abs_path:
            return jsonify({
                'success': False,
                'message': 'Invalid file path'
            }), 400

        # Check if file exists and is actually a file (not a directory)
        if not os.path.exists(file_path):
            return jsonify({
                'success': False,
                'message': 'File not found'
            }), 404

        if not os.path.isfile(file_path):
            return jsonify({
                'success': False,
                'message': 'Invalid file type'
            }), 400

        # Delete the file
        os.remove(file_path)
        return '', 204  # 204 No Content for successful deletion

    except PermissionError:
        return jsonify({
            'success': False,
            'message': 'Permission denied - cannot delete file'
        }), 403
    except OSError as e:
        return jsonify({
            'success': False,
            'message': f'File system error: {str(e)}'
        }), 500
    except Exception as e:
        return jsonify({
            'success': False,
            'message': 'Internal server error'
        }), 500


@app.route('/list_downloads')
def list_downloads():
    """List all downloaded files"""
    files = []
    if os.path.exists(DOWNLOADS_DIR):
        for filename in os.listdir(DOWNLOADS_DIR):
            file_path = os.path.join(DOWNLOADS_DIR, filename)
            if os.path.isfile(file_path):
                files.append({
                    'name': filename,
                    'size': os.path.getsize(file_path),
                    'url': f'/download_file/{filename}'
                })
    return jsonify(files)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
