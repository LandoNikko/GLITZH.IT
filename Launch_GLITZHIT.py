import os
import subprocess
import uuid
import re
import time
import io
import shutil
import math
import random
from flask import Flask, request, render_template, Response, jsonify, send_file

# --- Configuration ---
TEMP_FOLDER = 'temp'
if os.path.exists(TEMP_FOLDER): shutil.rmtree(TEMP_FOLDER)
os.makedirs(TEMP_FOLDER, exist_ok=True)

app = Flask(__name__)
app.config['TEMP_FOLDER'] = TEMP_FOLDER
JOBS = {}

# --- Generative Algorithms with Dials ---
def apply_dials(base_val, i, params):
    noise_amount = float(params.get('noiseAmount', 0.0))
    tremolo_amount = float(params.get('tremoloAmount', 0.0))
    tremolo_wave = (math.sin(i * 0.001) + 1) / 2
    amplitude_mod = 1.0 - (tremolo_amount * tremolo_wave)
    modulated_val = base_val * amplitude_mod
    noise = (random.random() - 0.5) * 2 * noise_amount * 127.5
    final_val = modulated_val + noise
    return max(0, min(255, int(final_val)))

def generate_sine_wave_bytes(path, num_bytes, params):
    freq_mult = float(params.get('frequencyMultiplier', 1.0)); freq_base = 0.01 * freq_mult
    with open(path, 'wb') as f:
        for i in range(num_bytes):
            val = (math.sin(i * freq_base) + 1) * 127.5
            f.write(bytes([apply_dials(val, i, params)]))

def generate_square_wave_bytes(path, num_bytes, params):
    freq_mult = float(params.get('frequencyMultiplier', 1.0)); freq_base = 0.01 * freq_mult
    with open(path, 'wb') as f:
        for i in range(num_bytes):
            val = 255 if math.sin(i * freq_base) > 0 else 0
            f.write(bytes([apply_dials(val, i, params)]))

def generate_triangle_wave_bytes(path, num_bytes, params):
    freq_mult = float(params.get('frequencyMultiplier', 1.0)); freq_base = 0.05 * freq_mult
    with open(path, 'wb') as f:
        for i in range(num_bytes):
            val = (math.asin(math.sin(i * freq_base)) / (math.pi / 2) + 1) * 127.5
            f.write(bytes([apply_dials(val, i, params)]))

def generate_sawtooth_wave_bytes(path, num_bytes, params):
    freq_mult = float(params.get('frequencyMultiplier', 1.0))
    with open(path, 'wb') as f:
        for i in range(num_bytes):
            val = (i * freq_mult * 5) % 255
            f.write(bytes([apply_dials(val, i, params)]))

def generate_random_bytes(path, num_bytes, params):
    with open(path, 'wb') as f: f.write(os.urandom(num_bytes))

# --- Job Management & Other Routes ---
def create_job(input_path, form_data):
    job_id = str(uuid.uuid4())
    output_path = os.path.join(app.config['TEMP_FOLDER'], f"{job_id}_output.mp4")
    res_preset = form_data.get('outputResolutionPreset')
    custom_res = form_data.get('outputResolutionCustom', '512')
    if res_preset == 'desktop': scale_filter = 'scale=1920:1080:flags=neighbor'
    elif res_preset == 'phone': scale_filter = 'scale=1080:1920:flags=neighbor'
    elif res_preset == 'square': scale_filter = 'scale=1080:1080:flags=neighbor'
    else: scale_filter = f'scale={custom_res}:{custom_res}:flags=neighbor'
    
    JOBS[job_id] = {
        "input_path": input_path, "output_path": output_path,
        "params": {
            "pixel_format": form_data.get('pixelFormat'), "input_width": int(form_data.get('inputWidth')),
            "input_height": int(form_data.get('inputHeight')), "framerate": float(form_data.get('framerate')),
            "scale_filter": scale_filter,
            # NEW: Store dynamic audio parameters
            "audio_f": form_data.get('audio_f', 'u8'),
            "audio_ar": form_data.get('audio_ar', '44100'),
            "audio_ac": form_data.get('audio_ac', '1'),
        }, "process": None
    }
    return job_id

def cleanup_job(job_id):
    job = JOBS.get(job_id)
    if not job: return
    if os.path.exists(job['input_path']): os.remove(job['input_path'])
    if os.path.exists(job['output_path']): os.remove(job['output_path'])
    if job_id in JOBS: del JOBS[job_id]

@app.route('/')
def index(): return render_template('index.html')

@app.route('/start-conversion-file', methods=['POST'])
def start_conversion_file():
    for job_id in list(JOBS.keys()): cleanup_job(job_id)
    file = request.files['inputFile']
    input_path = os.path.join(app.config['TEMP_FOLDER'], f"{uuid.uuid4()}_input.dat")
    file.save(input_path)
    job_id = create_job(input_path, request.form)
    return jsonify({"job_id": job_id})

@app.route('/start-conversion-generative', methods=['POST'])
def start_conversion_generative():
    for job_id in list(JOBS.keys()): cleanup_job(job_id)
    form_data = request.json
    duration = float(form_data.get('duration', 5))
    framerate = float(form_data.get('framerate'))
    input_width = int(form_data.get('inputWidth'))
    input_height = int(form_data.get('inputHeight'))
    bytes_per_pixel = 3
    total_bytes = int(duration * framerate * input_width * input_height * bytes_per_pixel)
    algorithm = form_data.get('algorithm')
    input_path = os.path.join(app.config['TEMP_FOLDER'], f"{uuid.uuid4()}_input.dat")
    algo_map = {
        'sine_wave': generate_sine_wave_bytes, 'square_wave': generate_square_wave_bytes,
        'triangle_wave': generate_triangle_wave_bytes, 'sawtooth_wave': generate_sawtooth_wave_bytes,
        'random': generate_random_bytes
    }
    generator_func = algo_map.get(algorithm, generate_random_bytes)
    generator_func(input_path, total_bytes, form_data)
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
        except Exception as e: return jsonify({"status": "error", "message": str(e)}), 500
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
            
            # Use dynamic audio params to build the command
            command = [
                'ffmpeg',
                '-f', 'rawvideo', '-pix_fmt', params['pixel_format'], '-s', f"{params['input_width']}x{params['input_height']}",
                '-r', str(params['framerate']), '-i', input_path,
                '-f', params['audio_f'], '-ar', params['audio_ar'], '-ac', params['audio_ac'], '-i', input_path,
                '-c:v', 'libx264', '-c:a', 'aac', '-pix_fmt', 'yuv420p', '-vf', params['scale_filter'],
                '-shortest', '-y', output_path, '-progress', 'pipe:2'
            ]
            
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