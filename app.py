from flask import Flask, send_from_directory
from flask_cors import CORS
from api.index import api_bp
import os

app = Flask(__name__, static_folder='public')
CORS(app)

# Register API Blueprint
app.register_blueprint(api_bp, url_prefix='/api')

@app.route('/')
def home():
    return send_from_directory(app.static_folder, 'index.html')

@app.route('/<path:path>')
def serve_static(path):
    return send_from_directory(app.static_folder, path)

if __name__ == '__main__':
    app.run(debug=True, port=5000)
