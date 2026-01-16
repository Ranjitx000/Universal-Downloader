from flask import Flask, send_from_directory
from flask_cors import CORS
from api.index import api_bp
import os

app = Flask(__name__, static_folder='public')
CORS(app)

# Register API Blueprint
app.register_blueprint(api_bp, url_prefix='/api')

@app.route('/health')
def health():
    return {"status": "ok"}, 200

@app.route('/favicon.ico')
def favicon():
    return '', 204

@app.route('/')
def home():
    return send_from_directory(app.static_folder, 'index.html')

@app.route('/<path:path>')
def serve_static(path):
    file_path = os.path.join(app.static_folder, path)
    if os.path.exists(file_path):
        return send_from_directory(app.static_folder, path)
    return send_from_directory(app.static_folder, 'index.html')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
