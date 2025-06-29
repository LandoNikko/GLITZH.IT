import os
import subprocess
import uuid
import re
import time
import io
import shutil
import math # Import the math module for sine waves
from flask import Flask, request, render_template, Response, jsonify, send_file

# --- Configuration ---
TEMP_FOLDER = 'temp'
if os.path.exists(TEMP_FOLDER): shutil.rmtree(TEMP_FOLDER)
os.makedirs(TEMP_FOLDER, exist_ok=True)

app = Flask(__name__)
app.config['TEMP_FOLDER'] = TEMP_FOLDER
JOBS = {}

# --- NEW: Algorithm to generate byte data ---
def generate_sine_wave_bytes(path, length_mb=2):
    """Generates a file with byte data based on sine waves."""
    num_bytes = length_mb * 1024 * 1024
    with open(path, 'wb') as f:
        for i in range(num_bytes):
            # Create slightly different frequencies for R, G, B channels for color effects
            r = int((math.sin(i * 0.01) + 1) * 127.5)
            g = int((math.sin(i * 0.013) + 1) * 127.5)
            b = int((math.sin(i * 0.017) + 1) * 127.5)
            # We only write one byte, cycling through R, G, B to create the pattern
            # This is a simple way to interleave the channels.
            if i % 3 == 0:
                f.write(bytes([r]))
            elif i % 3 == 1:
                f.write(bytes([g]))
            else:
                f.write(bytes([b]))
    return num_bytes

# --- Shared Job Creation Logic ---
def create_job(input_path, form_data):
    job_id = str(uuid.uuid4())
    output_path = os.path.join(app.config['TEMP_FOLDER'], f"{job_id}_output.mp4")
    JOBS[job_id] = {
        "input_path": input_path, "output_path": output_path,
        "params": {
            "pixel_format": form_data.get('pixelFormat'),
            "input_width": int(form_data.get('inputWidth')),
            "input_height": int(form_data.get('inputHeight')),
            "framerate": float(form_data.get('framerate')),
            "output_res": int(form_data.get('outputResolution')),
        },
        "process": None
    }
    return job_id

def cleanup_job(job_id):
    job = JOBS.get(job_id)
    if not job: return
    print(f"CLEANUP: Removing files for job {job_id}")
    if os.path.exists(job['input_path']): os.remove(job['input_path'])
    if os.path.exists(job['output_path']): os.remove(job['output_path'])
    if job_id in JOBS: del JOBS[job_id]

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/start-conversion-file', methods=['POST'])
def start_conversion_file():
    for job_id in list(JOBS.keys()): cleanup_job(job_id)
    file = request.files['inputFile']
    input_path = os.path.join(app.config['TEMP_FOLDER'], f"{uuid.uuid4()}_input.dat")
    file.save(input_path)
    job_id = create_job(input_path, request.form)
    return jsonify({"job_id": job_id})

# --- NEW Endpoint for Generative Mode ---
@app.route('/start-conversion-generative', methods=['POST'])
def start_conversion_generative():
    for job_id in list(JOBS.keys()): cleanup_job(job_id)
    form_data = request.json
    algorithm = form_data.get('algorithm')
    input_path = os.path.join(app.config['TEMP_FOLDER'], f"{uuid.uuid4()}_input.dat")

    if algorithm == 'sine_wave':
        generate_sine_wave_bytes(input_path)
    else:
        # Placeholder for future algorithms (random, perlin noise, etc.)
        # For now, just generate a random file.
        with open(input_path, 'wb') as f:
            f.write(os.urandom(2 * 1024 * 1024)) # 2MB of random data

    job_id = create_job(input_path, form_data)
    return jsonify({"job_id": job_id})


@app.route('/get-video/<job_id>')
def get_video(job_id):
    job = JOBS.get(job_id)
    if not job or not os.path.exists(job['output_path']): return "Video data not found.", 404
    with open(job['output_path'], 'rb') as f: video_data = f.read()
    return send_file(io.BytesIO(video_data), mimetype='video/mp4')

@app.route('/extract-audio/<job_id>')
def extract_audio(job_id):
    job = JOBS.get(job_id)
    if not job or not os.path.exists(job['output_path']): return "Source video not found.", 404
    video_path, audio_path = job['output_path'], os.path.join(app.config['TEMP_FOLDER'], f"{job_id}_audio.mp3")
    command = ['ffmpeg', '-i', video_path, '-q:a', '2', '-y', audio_path]
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
        with open(audio_path, 'rb') as f: audio_data = f.read()
        return send_file(io.BytesIO(audio_data), mimetype='audio/mpeg', as_attachment=True, download_name='glitched_audio.mp3')
    except subprocess.CalledProcessError as e:
        print(f"FFMPEG AUDIO EXTRACTION ERROR: {e.stderr}")
        return "Failed to extract audio.", 500
    finally:
        if os.path.exists(audio_path): os.remove(audio_path)

@app.route('/cancel-job', methods=['POST'])
def cancel_job():
    job = JOBS.get(request.get_json().get('job_id'))
    if job and job.get('process'):
        try:
            job['process'].terminate()
            return jsonify({"status": "success"})
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500
    return jsonify({"status": "error", "message": "Job not found."}), 404

@app.route('/stream-progress')
def stream_progress():
    job_id = request.args.get('job_id')
    if not job_id or job_id not in JOBS: return "Job not found.", 404
    job = JOBS[job_id]
    params, input_path, output_path = job['params'], job['input_path'], job['output_path']
    def generate():
        process = None
        try:
            bytes_per_pixel = {'gray': 1, 'rgb24': 3, 'rgba': 4}.get(params['pixel_format'], 3)
            frame_size_bytes = params['input_width'] * params['input_height'] * bytes_per_pixel
            file_size_bytes = os.path.getsize(input_path)
            total_frames = int(file_size_bytes / frame_size_bytes) if frame_size_bytes > 0 else 0
            command = [ 'ffmpeg', '-f', 'rawvideo', '-pix_fmt', params['pixel_format'], '-s', f"{params['input_width']}x{params['input_height']}", '-r', str(params['framerate']), '-i', input_path, '-f', 'u8', '-ar', '44100', '-ac', '1', '-i', input_path, '-c:v', 'libx264', '-c:a', 'aac', '-pix_fmt', 'yuv420p', '-vf', f"scale={params['output_res']}:{params['output_res']}:flags=neighbor", '-shortest', '-y', output_path, '-progress', 'pipe:2' ]
            process = subprocess.Popen(command, stderr=subprocess.PIPE, universal_newlines=True, text=True)
            JOBS[job_id]['process'] = process
            frame_regex = re.compile(r"frame=\s*(\d+)")
            for line in process.stderr:
                match = frame_regex.search(line)
                if match:
                    current_frame = int(match.group(1))
                    percent = int((current_frame / total_frames) * 100) if total_frames > 0 else 0
                    yield f"data: {percent}\n\n"
            process.wait()
            if process.returncode == 0:
                time.sleep(0.25)
                yield f"event: success\ndata: {job_id}\n\n"
            elif process.returncode in (-9, -15):
                yield f"event: error\ndata: Processing canceled by user.\n\n"
            else:
                yield f"event: error\ndata: FFmpeg failed. Check console.\n\n"
        except Exception as e:
            yield f"event: error\ndata: An unexpected error occurred: {str(e)}\n\n"
        finally:
            if process and process.returncode != 0: cleanup_job(job_id)
    return Response(generate(), mimetype='text/event-stream')

if __name__ == '__main__':
    print("Starting GLITZH.IT server at http://127.0.0.1:5000")
    print("Make sure FFmpeg is installed and accessible in your system's PATH.")
    app.run(debug=True, threaded=True)