from flask import Flask, send_from_directory
from flask_cors import CORS
from api.index import api_bp
import os

app = Flask(__name__, static_folder='public', static_url_path='')
CORS(app)

# Register API Blueprint
app.register_blueprint(api_bp, url_prefix='/api')

@app.route('/')
def home():
    return app.send_static_file('index.html')

@app.route('/<path:path>')
def serve_static(path):
    return send_from_directory(app.static_folder, path)

# Railway-required configuration
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
