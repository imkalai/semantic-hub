"""Start Semantic Hub on this computer and open it in the browser.

    python run_local.py
"""

import threading
import webbrowser

import uvicorn

from app.config import get_settings


def main() -> None:
    s = get_settings()
    url = f"http://{s.host}:{s.port}/"
    if s.open_browser:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    print(f"Semantic Hub is running at {url}  (Ctrl+C to stop)")
    uvicorn.run("app.main:app", host=s.host, port=s.port, log_level="info")


if __name__ == "__main__":
    main()
