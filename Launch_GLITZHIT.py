import os
import subprocess
import uuid
import re
import json
import time
from flask import Flask, request, render_template, Response, jsonify

# --- Configuration ---
UPLOAD_FOLDER = 'uploads'
OUTPUT_FOLDER = os.path.join('static', 'output')

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['OUTPUT_FOLDER'] = OUTPUT_FOLDER

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

# In-memory dictionary to store job details, including the running process
JOBS = {}

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/start-conversion', methods=['POST'])
def start_conversion():
    job_id = str(uuid.uuid4())
    file = request.files['inputFile']
    input_filename = f"{job_id}.dat"
    input_path = os.path.join(app.config['UPLOAD_FOLDER'], input_filename)
    file.save(input_path)
    
    JOBS[job_id] = {
        "input_path": input_path,
        "output_filename": f"{job_id}.mp4",
        "params": { "pixel_format": request.form.get('pixelFormat'), "input_width": int(request.form.get('inputWidth')), "input_height": int(request.form.get('inputHeight')), "framerate": float(request.form.get('framerate')), "output_res": int(request.form.get('outputResolution')), },
        "process": None # Placeholder for the subprocess object
    }
    return jsonify({"job_id": job_id})

@app.route('/cancel-job', methods=['POST'])
def cancel_job():
    data = request.get_json()
    job_id = data.get('job_id')
    if job_id and job_id in JOBS and JOBS[job_id].get('process'):
        try:
            print(f"--- ATTEMPTING TO CANCEL JOB {job_id} ---")
            JOBS[job_id]['process'].terminate()
            return jsonify({"status": "success", "message": "Job cancellation initiated."})
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500
    return jsonify({"status": "error", "message": "Job not found or already completed."}), 404

@app.route('/stream-progress')
def stream_progress():
    job_id = request.args.get('job_id')
    if not job_id or job_id not in JOBS:
        return "Job not found.", 404

    job = JOBS[job_id]
    params = job['params']
    input_path = job['input_path']
    output_filename = job['output_filename']
    output_path = os.path.join(app.config['OUTPUT_FOLDER'], output_filename)

    def generate():
        try:
            bytes_per_pixel = {'gray': 1, 'rgb24': 3, 'rgba': 4}.get(params['pixel_format'], 3)
            frame_size_bytes = params['input_width'] * params['input_height'] * bytes_per_pixel
            file_size_bytes = os.path.getsize(input_path)
            total_frames = int(file_size_bytes / frame_size_bytes) if frame_size_bytes > 0 else 0

            command = [ 'ffmpeg', '-f', 'rawvideo', '-pix_fmt', params['pixel_format'], '-s', f"{params['input_width']}x{params['input_height']}", '-r', str(params['framerate']), '-i', input_path, '-f', 'u8', '-ar', '44100', '-ac', '1', '-i', input_path, '-c:v', 'libx264', '-c:a', 'aac', '-pix_fmt', 'yuv420p', '-vf', f"scale={params['output_res']}:{params['output_res']}:flags=neighbor", '-shortest', '-y', output_path, '-progress', 'pipe:2' ]
            
            process = subprocess.Popen(command, stderr=subprocess.PIPE, universal_newlines=True, text=True)
            JOBS[job_id]['process'] = process # Store the process object to allow cancellation

            frame_regex = re.compile(r"frame=\s*(\d+)")
            for line in process.stderr:
                match = frame_regex.search(line)
                if match:
                    current_frame = int(match.group(1))
                    percent = int((current_frame / total_frames) * 100) if total_frames > 0 else 0
                    yield f"data: {percent}\n\n"
            
            process.wait()

            if process.returncode == 0:
                yield f"event: success\ndata: {output_filename}\n\n"
            elif process.returncode == -9: # Code for termination
                yield f"event: error\ndata: Processing canceled by user.\n\n"
            else:
                yield f"event: error\ndata: FFmpeg failed with code {process.returncode}. Check server console.\n\n"
        
        except Exception as e:
            yield f"event: error\ndata: An unexpected error occurred: {str(e)}\n\n"
        finally:
            if os.path.exists(input_path): os.remove(input_path)
            if job_id in JOBS: del JOBS[job_id]

    return Response(generate(), mimetype='text/event-stream')

if __name__ == '__main__':
    print("Starting local server at http://127.0.0.1:5000")
    print("Make sure FFmpeg is installed and accessible in your system's PATH.")
    app.run(debug=True, threaded=True)