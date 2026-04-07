import sys
from pathlib import Path

from waitress import serve

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app import APP_HOST, APP_PORT, app, init_db


if __name__ == "__main__":
    init_db()
    serve(app, host=APP_HOST, port=APP_PORT, threads=8)
