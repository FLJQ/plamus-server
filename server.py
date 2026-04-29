from flask import Flask, request, jsonify, send_file
from pytubefix import YouTube
import os
import tempfile

app = Flask(__name__)

@app.route('/download', methods=['POST'])
def download():
    data = request.json
    url = data.get('url', '').strip()
    
    if not url:
        return jsonify({'error': 'URL is required'}), 400
    
    try:
        yt = YouTube(url)
        stream = yt.streams.filter(only_audio=True).order_by('abr').last()
        
        tmpdir = tempfile.mkdtemp()
        out_path = stream.download(output_path=tmpdir)
        
        return send_file(out_path, as_attachment=True, download_name=os.path.basename(out_path))
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok'})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8000))
    app.run(host='0.0.0.0', port=port)
