import base64
import http.cookiejar
import os
import tempfile
from pathlib import Path

import requests
from flask import Flask, jsonify, request
from youtube_transcript_api import YouTubeTranscriptApi

app = Flask(__name__)


def api_token_ok() -> bool:
    expected = os.environ.get("WORKER_TOKEN", "").strip()
    if not expected:
        return False
    return request.headers.get("Authorization", "") == f"Bearer {expected}"


def youtube_api() -> YouTubeTranscriptApi:
    encoded = os.environ.get("YOUTUBE_COOKIES_B64", "").strip()
    if not encoded:
        return YouTubeTranscriptApi()
    raw = base64.b64decode(encoded, validate=True)
    with tempfile.NamedTemporaryFile(prefix="yt-cookies-", suffix=".txt", delete=False) as handle:
        cookie_path = Path(handle.name)
        handle.write(raw)
    try:
        jar = http.cookiejar.MozillaCookieJar(str(cookie_path))
        jar.load(ignore_discard=True, ignore_expires=False)
        session = requests.Session()
        session.cookies.update(jar)
        return YouTubeTranscriptApi(http_client=session)
    finally:
        cookie_path.unlink(missing_ok=True)


@app.get("/health")
def health():
    return jsonify({"ok": True})


@app.get("/transcript")
def transcript():
    if not api_token_ok():
        return jsonify({"error": "unauthorized"}), 401
    video_id = request.args.get("video_id", "").strip()
    language = request.args.get("lang", "it").strip() or "it"
    if len(video_id) != 11:
        return jsonify({"error": "invalid_video_id"}), 400
    try:
        result = youtube_api().fetch(video_id, languages=[language, "en"])
        text = " ".join(snippet.text for snippet in result.snippets).strip()
        if len(text) < 100:
            return jsonify({"error": "transcript_unavailable"}), 404
        return jsonify({"text": text, "language": result.language_code})
    except Exception:
        return jsonify({"error": "transcript_unavailable"}), 404


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "10000")))
