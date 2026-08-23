import base64
import http.cookiejar
import os
import tempfile
from pathlib import Path
import base64
import html
import http.cookiejar
import json
import os
import tempfile
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from pathlib import Path

import requests
from flask import Flask, jsonify, request
from youtube_transcript_api import YouTubeTranscriptApi

app = Flask(__name__)

ANDROID_PLAYER_KEY = os.environ.get(
    "YOUTUBE_ANDROID_PLAYER_KEY",
    "AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8",
).strip()
ANDROID_CLIENT_VERSION = os.environ.get("YOUTUBE_ANDROID_CLIENT_VERSION", "20.10.38").strip()


def api_token_ok() -> bool:
    expected = os.environ.get("WORKER_TOKEN", "").strip()
    if not expected:
        return False
    return request.headers.get("Authorization", "") == f"Bearer {expected}"


def youtube_http_session() -> requests.Session:
    session = requests.Session()
    encoded = os.environ.get("YOUTUBE_COOKIES_B64", "").strip()
    if not encoded:
        return session
    raw = base64.b64decode(encoded, validate=True)
    with tempfile.NamedTemporaryFile(prefix="yt-cookies-", suffix=".txt", delete=False) as handle:
        cookie_path = Path(handle.name)
        handle.write(raw)
    try:
        jar = http.cookiejar.MozillaCookieJar(str(cookie_path))
        try:
            jar.load(ignore_discard=True, ignore_expires=False)
            session.cookies.update(jar)
        except Exception:
            # Chrome exports cookies as a JSON array, while yt-dlp/browser
            # exports commonly use Netscape's tab-separated format.
            entries = json.loads(raw.decode("utf-8"))
            if not isinstance(entries, list):
                raise
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                name = str(entry.get("name", "")).strip()
                if not name or "value" not in entry:
                    continue
                domain = str(entry.get("domain", "")).lstrip(".") or None
                path = str(entry.get("path", "/")) or "/"
                session.cookies.set(name, str(entry.get("value", "")), domain=domain, path=path)
        return session
    finally:
        cookie_path.unlink(missing_ok=True)


def youtube_api() -> YouTubeTranscriptApi:
    return YouTubeTranscriptApi(http_client=youtube_http_session())


def _caption_url(url: str) -> str:
    """Request JSON3 captions while preserving YouTube's signed query string."""
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["fmt"] = "json3"
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def android_transcript(video_id: str, language: str) -> tuple[str, str]:
    """Fetch YouTube's existing caption track through the Android InnerTube client."""
    session = youtube_http_session()
    endpoint = "https://www.youtube.com/youtubei/v1/player?key=" + ANDROID_PLAYER_KEY
    payload = {
        "context": {
            "client": {
                "clientName": "ANDROID",
                "clientVersion": ANDROID_CLIENT_VERSION,
                "androidSdkVersion": 35,
                "hl": language,
                "gl": "IT",
            }
        },
        "videoId": video_id,
        "contentCheckOk": True,
        "racyCheckOk": True,
    }
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": f"com.google.android.youtube/{ANDROID_CLIENT_VERSION} (Linux; U; Android 14) gzip",
        "X-YouTube-Client-Name": "3",
        "X-YouTube-Client-Version": ANDROID_CLIENT_VERSION,
    }
    response = session.post(endpoint, json=payload, headers=headers, timeout=(8, 25))
    response.raise_for_status()
    player = response.json()
    tracks = (((player.get("captions") or {}).get("playerCaptionsTracklistRenderer") or {}).get("captionTracks") or [])
    if not tracks:
        return "", ""

    selected = None
    wanted = language.lower()
    for track in tracks:
        code = str(track.get("languageCode", "")).lower()
        if code == wanted:
            selected = track
            break
        if selected is None and code.startswith(wanted + "-"):
            selected = track
    if selected is None:
        for track in tracks:
            if str(track.get("languageCode", "")).lower().startswith("en"):
                selected = track
                break
    if selected is None:
        selected = tracks[0]

    base_url = str(selected.get("baseUrl", ""))
    if not base_url:
        return "", ""
    captions = session.get(_caption_url(base_url), headers={"User-Agent": headers["User-Agent"]}, timeout=(8, 25))
    captions.raise_for_status()
    body = captions.text
    text_parts = []
    if body.lstrip().startswith("{"):
        data = json.loads(body)
        for event in data.get("events", []):
            for segment in event.get("segs", []) or []:
                text_parts.append(str(segment.get("utf8", "")))
    else:
        # Some tracks ignore fmt=json3 and return XML.
        import xml.etree.ElementTree as ET
        root = ET.fromstring(body)
        text_parts = [html.unescape("".join(node.itertext())) for node in root.findall(".//text")]
    text = " ".join(" ".join(text_parts).split())
    return text, str(selected.get("languageCode", ""))


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
        # Try the Android client first. This reads existing YouTube captions;
        # it does not perform speech recognition or download the video audio.
        android_text, android_language = android_transcript(video_id, language)
        if len(android_text) >= 100:
            return jsonify({"text": android_text, "language": android_language, "source": "youtube-android"})
    except Exception:
        pass
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
