from flask import Flask, request, jsonify, send_file
from urllib.parse import urlparse, parse_qs, quote
import os
import tempfile
import traceback
import requests
import isodate
import subprocess
import json
import glob

app = Flask(__name__)

API_KEYS = [
    'AIzaSyBddO94_haO-8ZTCWUQ8ATR3mN38_rz3HY',
    'AIzaSyAjgN1O-09ffAIj9MtFlKfeBHBkWGz133Q',
    'AIzaSyD93Er0K0NTGmO1B9mAxI5z98qLL3tLoFY',
]

_key_index = 0

def get_key():
    return API_KEYS[_key_index % len(API_KEYS)]

def youtube_get(url, params, retry=True):
    global _key_index
    params['key'] = get_key()
    resp = requests.get(url, params=params)
    data = resp.json()
    if retry and 'error' in data:
        for err in data['error'].get('errors', []):
            if err.get('reason') in ('quotaExceeded', 'dailyLimitExceeded'):
                _key_index += 1
                if _key_index % len(API_KEYS) == 0:
                    return data
                params['key'] = get_key()
                resp = requests.get(url, params=params)
                return resp.json()
    return data


def fetch_video_details(video_ids):
    if not video_ids:
        return {}
    data = youtube_get(
        'https://www.googleapis.com/youtube/v3/videos',
        {'part': 'contentDetails', 'id': ','.join(video_ids)}
    )
    result = {}
    for item in data.get('items', []):
        vid_id = item['id']
        duration_iso = item.get('contentDetails', {}).get('duration', 'PT0S')
        try:
            seconds = int(isodate.parse_duration(duration_iso).total_seconds())
        except Exception:
            seconds = 0
        result[vid_id] = seconds
    return result


def fetch_playlist_items(playlist_id):
    items = []
    page_token = None
    while True:
        params = {
            'part': 'snippet',
            'playlistId': playlist_id,
            'maxResults': 50,
        }
        if page_token:
            params['pageToken'] = page_token
        data = youtube_get(
            'https://www.googleapis.com/youtube/v3/playlistItems',
            params
        )
        for item in data.get('items', []):
            snippet = item.get('snippet', {})
            resource = snippet.get('resourceId', {})
            if resource.get('kind') != 'youtube#video':
                continue
            video_id = resource.get('videoId')
            if not video_id:
                continue
            thumbnails = snippet.get('thumbnails', {})
            thumb = (
                thumbnails.get('medium', {}).get('url') or
                thumbnails.get('default', {}).get('url') or ''
            )
            items.append({
                'id': video_id,
                'title': snippet.get('title', 'Unknown'),
                'channel': snippet.get('videoOwnerChannelTitle', snippet.get('channelTitle', '')),
                'thumbnail': thumb,
                'url': f'https://www.youtube.com/watch?v={video_id}',
            })
        page_token = data.get('nextPageToken')
        if not page_token:
            break
    return items


@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok', 'active_key_index': _key_index % len(API_KEYS)})


@app.route('/download', methods=['POST'])
def download():
    data = request.json
    url = data.get('url', '').strip()
    if not url:
        return jsonify({'error': 'URL is required'}), 400

    try:
        tmpdir = tempfile.mkdtemp()

        # получаем метаданные
        info_result = subprocess.run(
            ['yt-dlp', '--no-playlist', '-J', '--no-warnings', url],
            capture_output=True, text=True, timeout=60
        )
        if info_result.returncode != 0:
            return jsonify({'error': info_result.stderr.strip()}), 500

        info = json.loads(info_result.stdout)
        title = info.get('title', 'Unknown')
        artist = info.get('uploader') or info.get('channel') or info.get('creator', '')

        # скачиваем аудио
        subprocess.run([
            'yt-dlp', '--no-playlist', '--no-warnings',
            '-x', '--audio-format', 'm4a', '--audio-quality', '0',
            '-o', os.path.join(tmpdir, '%(title)s.%(ext)s'),
            url
        ], check=True, timeout=300)

        files = glob.glob(os.path.join(tmpdir, '*'))
        if not files:
            return jsonify({'error': 'Download produced no file'}), 500

        out_path = files[0]
        response = send_file(
            out_path,
            as_attachment=True,
            download_name=os.path.basename(out_path)
        )
        response.headers['X-Track-Title'] = quote(title)
        response.headers['X-Track-Artist'] = quote(artist)
        return response

    except subprocess.CalledProcessError as e:
        return jsonify({'error': f'yt-dlp error: {e.stderr}'}), 500
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/search', methods=['GET'])
def search():
    query = request.args.get('q', '').strip()
    if not query:
        return jsonify({'error': 'Query is required'}), 400

    try:
        video_data = youtube_get(
            'https://www.googleapis.com/youtube/v3/search',
            {'part': 'snippet', 'q': query, 'type': 'video', 'maxResults': 8}
        )
        playlist_data = youtube_get(
            'https://www.googleapis.com/youtube/v3/search',
            {'part': 'snippet', 'q': query, 'type': 'playlist', 'maxResults': 3}
        )

        video_ids = [
            item['id']['videoId']
            for item in video_data.get('items', [])
            if item.get('id', {}).get('videoId')
        ]
        duration_map = fetch_video_details(video_ids)

        playlist_ids = [
            item['id']['playlistId']
            for item in playlist_data.get('items', [])
            if item.get('id', {}).get('playlistId')
        ]
        playlist_details = {}
        if playlist_ids:
            pl_data = youtube_get(
                'https://www.googleapis.com/youtube/v3/playlists',
                {'part': 'contentDetails', 'id': ','.join(playlist_ids)}
            )
            for item in pl_data.get('items', []):
                playlist_details[item['id']] = item.get('contentDetails', {}).get('itemCount', 0)

        results = []

        for item in video_data.get('items', []):
            if item.get('id', {}).get('kind') != 'youtube#video':
                continue
            video_id = item['id'].get('videoId')
            if not video_id:
                continue
            snippet = item['snippet']
            thumbnails = snippet.get('thumbnails', {})
            thumb = thumbnails.get('medium', {}).get('url') or thumbnails.get('default', {}).get('url') or ''
            results.append({
                'type': 'video',
                'id': video_id,
                'title': snippet['title'],
                'channel': snippet['channelTitle'],
                'thumbnail': thumb,
                'url': f'https://www.youtube.com/watch?v={video_id}',
                'duration_seconds': duration_map.get(video_id, 0),
            })

        for item in playlist_data.get('items', []):
            if item.get('id', {}).get('kind') != 'youtube#playlist':
                continue
            pl_id = item['id'].get('playlistId')
            if not pl_id:
                continue
            snippet = item['snippet']
            thumbnails = snippet.get('thumbnails', {})
            thumb = thumbnails.get('medium', {}).get('url') or thumbnails.get('default', {}).get('url') or ''
            results.append({
                'type': 'playlist',
                'id': pl_id,
                'title': snippet['title'],
                'channel': snippet['channelTitle'],
                'thumbnail': thumb,
                'url': f'https://www.youtube.com/playlist?list={pl_id}',
                'track_count': playlist_details.get(pl_id, 0),
            })

        return jsonify({'results': results})

    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/playlist', methods=['GET'])
def playlist_info():
    playlist_id = request.args.get('id', '').strip()
    url = request.args.get('url', '').strip()

    if not playlist_id and url:
        parsed = urlparse(url)
        playlist_id = parse_qs(parsed.query).get('list', [None])[0]

    if not playlist_id:
        return jsonify({'error': 'Playlist ID or URL is required'}), 400

    try:
        pl_data = youtube_get(
            'https://www.googleapis.com/youtube/v3/playlists',
            {'part': 'snippet,contentDetails', 'id': playlist_id}
        )
        pl_items = pl_data.get('items', [])
        if not pl_items:
            return jsonify({'error': 'Playlist not found'}), 404

        pl_snippet = pl_items[0]['snippet']
        pl_count = pl_items[0].get('contentDetails', {}).get('itemCount', 0)
        thumbnails = pl_snippet.get('thumbnails', {})
        pl_thumb = thumbnails.get('medium', {}).get('url') or thumbnails.get('default', {}).get('url') or ''

        tracks_raw = fetch_playlist_items(playlist_id)

        all_durations = {}
        ids = [t['id'] for t in tracks_raw]
        for i in range(0, len(ids), 50):
            all_durations.update(fetch_video_details(ids[i:i + 50]))

        tracks = []
        total_duration = 0
        for t in tracks_raw:
            dur = all_durations.get(t['id'], 0)
            total_duration += dur
            tracks.append({**t, 'duration_seconds': dur})

        return jsonify({
            'id': playlist_id,
            'title': pl_snippet.get('title', ''),
            'channel': pl_snippet.get('channelTitle', ''),
            'thumbnail': pl_thumb,
            'url': f'https://www.youtube.com/playlist?list={playlist_id}',
            'track_count': pl_count,
            'total_duration_seconds': total_duration,
            'tracks': tracks,
        })

    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8000))
    app.run(host='0.0.0.0', port=port, debug=True)