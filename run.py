#!/usr/bin/env python3
"""Development server.

    python3 run.py                # http://127.0.0.1:5173
    ZS_KB_MODE=rag python3 run.py
"""
import os

from app.main import create_app

app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("ZS_PORT", "5173"))
    app.run(host=os.environ.get("ZS_HOST", "127.0.0.1"), port=port, debug=False)
