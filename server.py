from flask import Flask, request, jsonify, send_file
from pytubefix import YouTube
import os
import tempfile
import traceback

app = Flask(__name__)

CLIENTS = ['MWEB', 'WEB_EMBEDDED', 'WEB_CREATOR', 'IOS', 'ANDROID_VR']

@app.route('/download', methods=['POST'])
def download():
    data = request.json
    url = data.get('url', '').strip()
    
    if not url:
        return jsonify({'error': 'URL is required'}), 400
    
    last_error = None
    for client in CLIENTS:
        try:
            print(f"Trying client: {client}")
            yt = YouTube(url, client=client, use_po_token=False)
            stream = yt.streams.filter(only_audio=True).order_by('abr').last()
            if not stream:
                print(f"No stream for {client}, trying next...")
                continue
            print(f"Got stream with {client}!")
            tmpdir = tempfile.mkdtemp()
            out_path = stream.download(output_path=tmpdir)
            print(f"Done: {out_path}")
            return send_file(out_path, as_attachment=True, download_name=os.path.basename(out_path))
        except Exception as e:
            print(f"Client {client} failed: {e}")
            last_error = str(e)
            continue
    
    return jsonify({'error': last_error or 'All clients failed'}), 500

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok'})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8000))
    app.run(host='0.0.0.0', port=port, debug=True)