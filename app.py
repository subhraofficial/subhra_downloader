# app.py
import os
import uuid
import threading
from flask import Flask, request, jsonify, send_file
from yt_dlp import YoutubeDL
from werkzeug.utils import secure_filename

app = Flask(__name__, static_folder="static", static_url_path="/")
TEMP_DIR = os.path.join(os.getcwd(), "temp")
os.makedirs(TEMP_DIR, exist_ok=True)

# jobs: job_id -> metadata & progress
jobs = {}
# progress_data: job_id -> {percent: float, speed: str, status: str}
progress_data = {}

def human_readable_size_per_sec(bps):
    if not bps:
        return "0 B/s"
    units = ["B/s", "KB/s", "MB/s", "GB/s"]
    i = 0
    val = float(bps)
    while val >= 1024 and i < len(units)-1:
        val /= 1024.0
        i += 1
    return f"{val:.2f} {units[i]}"

def run_download(job_id, url, format_id):
    """
    Download using yt-dlp and update jobs & progress_data in real-time via hook.
    The outtmpl includes the job_id so we can locate the downloaded file.
    """
    # ensure a jobs entry exists
    jobs[job_id] = {"title": None, "filepath": None, "status": "starting"}

    # first get metadata (title) to set a friendly filename later
    try:
        with YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True}) as ydl:
            meta = ydl.extract_info(url, download=False)
            title = meta.get("title") or f"subhra_{job_id}"
            jobs[job_id]["title"] = title
    except Exception:
        title = f"subhra_{job_id}"
        jobs[job_id]["title"] = title

    def hook(d):
        # update progress_data[job_id] using downloaded bytes / total bytes when available
        status = d.get("status")
        if status == "downloading":
            downloaded = d.get("downloaded_bytes") or d.get("done_bytes") or 0
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            speed_bps = d.get("speed") or 0
            if total and total > 0:
                percent = float(downloaded) / float(total) * 100.0
            else:
                # if total unknown, derive percent from _percent_str if present, else use 0-99 heuristic
                pct_str = d.get("_percent_str") or d.get("percent")
                try:
                    percent = float(str(pct_str).replace("%","").strip())
                except Exception:
                    # increment a little so UI is not stuck at 0
                    prev = progress_data.get(job_id, {}).get("percent", 0.0)
                    percent = min(99.0, prev + 1.5)
            progress_data[job_id] = {
                "percent": round(percent, 2),
                "speed": human_readable_size_per_sec(speed_bps),
                "status": "downloading"
            }
        elif status == "finished":
            # d may contain 'filename' for the finished file
            filename = d.get("filename")
            if filename:
                jobs[job_id]["filepath"] = filename
            progress_data[job_id] = {"percent": 100.0, "speed": "Done", "status": "finished"}
            jobs[job_id]["status"] = "finished"
        elif status == "error":
            progress_data[job_id] = {"percent": progress_data.get(job_id, {}).get("percent", 0.0),
                                     "speed": "Error",
                                     "status": "error",
                                     "error": d.get("error", "download error")}
            jobs[job_id]["status"] = "error"

    # output template: prefix with job_id so we can find file
    outtmpl = os.path.join(TEMP_DIR, f"{job_id}-%(title)s.%(ext)s")
    ydl_opts = {
        "outtmpl": outtmpl,
        "format": format_id or "best",
        "progress_hooks": [hook],
        "quiet": True,
        "no_warnings": True,
        # let yt-dlp merge if needed (requires ffmpeg installed if merging)
        "merge_output_format": None
    }

    try:
        jobs[job_id]["status"] = "downloading"
        progress_data[job_id] = {"percent": 0.0, "speed": "Starting...", "status": "downloading"}
        with YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        # after download, try to find the file we produced
        # it should be in TEMP_DIR and start with job_id-
        final_fp = None
        for fname in os.listdir(TEMP_DIR):
            if fname.startswith(f"{job_id}-"):
                final_fp = os.path.join(TEMP_DIR, fname)
                break

        if final_fp and os.path.exists(final_fp):
            jobs[job_id]["filepath"] = final_fp
            jobs[job_id]["status"] = "finished"
            progress_data[job_id] = {"percent": 100.0, "speed": "Done", "status": "finished"}
        else:
            jobs[job_id]["status"] = "error"
            progress_data[job_id] = {"percent": progress_data.get(job_id, {}).get("percent", 0.0),
                                     "speed": "File missing",
                                     "status": "error",
                                     "error": "Downloaded file not found"}

    except Exception as e:
        jobs[job_id]["status"] = "error"
        progress_data[job_id] = {"percent": progress_data.get(job_id, {}).get("percent", 0.0),
                                 "speed": "Error",
                                 "status": "error",
                                 "error": str(e)}

@app.route("/api/info", methods=["POST"])
def get_info():
    data = request.get_json(force=True)
    url = data.get("url")
    if not url:
        return jsonify({"error": "url required"}), 400

    try:
        with YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True}) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:
        return jsonify({"error": f"failed to fetch info: {e}"}), 500

    formats = []
    for f in info.get("formats", []):
        # include both video+audio and video-only formats; frontend can choose
        formats.append({
            "format_id": f.get("format_id"),
            "ext": f.get("ext"),
            "height": f.get("height"),
            "width": f.get("width"),
            "format_note": f.get("format_note"),
            "fps": f.get("fps"),
            "filesize": f.get("filesize") or f.get("filesize_approx")
        })

    # sort by height desc (None last), so highest qualities first
    formats.sort(key=lambda x: (x["height"] or 0), reverse=True)

    return jsonify({
        "title": info.get("title"),
        "thumbnail": info.get("thumbnail"),
        "formats": formats
    }), 200

@app.route("/api/download", methods=["POST"])
def start_download():
    data = request.get_json(force=True)
    url = data.get("url")
    format_id = data.get("format_id")  # optional

    if not url:
        return jsonify({"error": "url required"}), 400

    job_id = uuid.uuid4().hex
    # initialize job and progress placeholders
    jobs[job_id] = {"title": None, "filepath": None, "status": "queued"}
    progress_data[job_id] = {"percent": 0.0, "speed": "Queued", "status": "queued"}

    t = threading.Thread(target=run_download, args=(job_id, url, format_id), daemon=True)
    t.start()

    return jsonify({"job_id": job_id}), 202

@app.route("/api/progress/<job_id>", methods=["GET"])
def get_progress(job_id):
    p = progress_data.get(job_id)
    j = jobs.get(job_id)
    if not p and not j:
        return jsonify({"error": "job not found"}), 404
    # include title & filename for convenience
    resp = {
        "percent": p.get("percent") if p else 0.0,
        "speed": p.get("speed") if p else "",
        "status": p.get("status") if p else (j.get("status") if j else "unknown"),
        "title": j.get("title"),
        "filename": os.path.basename(j.get("filepath")) if j.get("filepath") else None,
    }
    # include error if present
    if p and p.get("error"):
        resp["error"] = p.get("error")
    return jsonify(resp), 200

@app.route("/api/getfile/<job_id>", methods=["GET"])
def get_file(job_id):
    j = jobs.get(job_id)
    if not j:
        return jsonify({"error": "job not found"}), 404
    if j.get("status") != "finished":
        return jsonify({"error": "job not finished"}), 400
    fp = j.get("filepath")
    if not fp or not os.path.exists(fp):
        return jsonify({"error": "file not found"}), 404

    # use job title for download name if available; keep original extension
    title = j.get("title") or job_id
    # sanitize title for filename
    safe_name = secure_filename(title)
    ext = os.path.splitext(fp)[1] or ".mp4"
    download_name = f"{safe_name}{ext}"

    return send_file(fp, as_attachment=True, download_name=download_name)

@app.route("/", methods=["GET"])
def index():
    return app.send_static_file("index.html")

if __name__ == "__main__":
    app.run(debug=True, port=5000)
