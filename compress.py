import os
import subprocess
import sys
import threading
import io
import zipfile
import shutil
import tempfile
import datetime
from PIL import Image
from pypdf import PdfReader, PdfWriter
import winreg
import argparse

def find_ffmpeg_tools():
    try:
        subprocess.run(["ffmpeg", "-version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["ffprobe", "-version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return "ffmpeg", "ffprobe"
    except FileNotFoundError:
        pass

    winget_dir = os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Packages")
    if os.path.exists(winget_dir):
        ffmpeg_path = None
        ffprobe_path = None
        for root, dirs, files in os.walk(winget_dir):
            if "ffmpeg.exe" in files:
                ffmpeg_path = os.path.join(root, "ffmpeg.exe")
            if "ffprobe.exe" in files:
                ffprobe_path = os.path.join(root, "ffprobe.exe")
            if ffmpeg_path and ffprobe_path:
                return ffmpeg_path, ffprobe_path

    return "ffmpeg", "ffprobe"

_supported_hardware_encoders = None

def detect_supported_hardware_encoders():
    global _supported_hardware_encoders
    if _supported_hardware_encoders is not None:
        return _supported_hardware_encoders
    
    ffmpeg_path, _ = find_ffmpeg_tools()
    encoders_to_test = ["h264_nvenc", "h264_amf", "h264_qsv", "h264_mf", "vp9_qsv"]
    supported = []
    
    for enc in encoders_to_test:
        try:
            cmd = [
                ffmpeg_path, "-y",
                "-f", "lavfi", "-i", "color=c=black:s=320x240:d=0.1",
                "-c:v", enc,
                "-f", "null", "-"
            ]
            res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if res.returncode == 0:
                supported.append(enc)
        except Exception:
            pass
            
    _supported_hardware_encoders = supported
    return supported

class SelfHealer:
    @staticmethod
    def heal_python_dependencies(log_callback=print):
        dependencies = {
            "PIL": "Pillow",
            "pypdf": "pypdf"
        }
        for module_name, pip_name in dependencies.items():
            try:
                __import__(module_name)
            except ImportError:
                log_callback(f"Self-Healing: Python library '{pip_name}' is missing. Attempting auto-installation...")
                try:
                    subprocess.run([sys.executable, "-m", "pip", "install", pip_name], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    log_callback(f"Self-Healing: Successfully installed '{pip_name}'.")
                except Exception as e:
                    log_callback(f"Self-Healing: Failed to auto-install '{pip_name}': {e}")

    @staticmethod
    def heal_ffmpeg(log_callback=print):
        ffmpeg_path, ffprobe_path = find_ffmpeg_tools()
        if ffmpeg_path == "ffmpeg" or ffprobe_path == "ffprobe":
            try:
                subprocess.run(["ffmpeg", "-version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            except (FileNotFoundError, subprocess.CalledProcessError):
                log_callback("Self-Healing: FFmpeg not detected in PATH. Attempting automatic installation via winget...")
                try:
                    subprocess.Popen(["winget", "install", "Gyan.FFmpeg", "--silent", "--accept-package-agreements", "--accept-source-agreements"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    log_callback("Self-Healing: winget installation triggered in background. Please restart after a few minutes.")
                except Exception as e:
                    log_callback(f"Self-Healing: Failed to trigger winget: {e}")

def parse_ffmpeg_metrics(line_str):
    metrics = {}
    if "fps=" in line_str:
        try:
            parts = line_str.split("fps=")
            if len(parts) > 1:
                metrics["fps"] = parts[1].split()[0]
        except Exception:
            pass
    if "speed=" in line_str:
        try:
            parts = line_str.split("speed=")
            if len(parts) > 1:
                metrics["speed"] = parts[1].split()[0]
        except Exception:
            pass
    return metrics

def get_non_colliding_path(input_path, output_dir, base_name, out_ext):
    target_path = os.path.normpath(os.path.join(output_dir, base_name + out_ext))
    input_norm = os.path.normpath(input_path)
    
    if target_path == input_norm:
        base_name = base_name + "_compressed"
        target_path = os.path.normpath(os.path.join(output_dir, base_name + out_ext))
        
    counter = 1
    final_path = target_path
    while os.path.exists(final_path):
        final_path = os.path.normpath(os.path.join(output_dir, f"{base_name} ({counter}){out_ext}"))
        counter += 1
        
    return final_path

def get_audio_duration(ffprobe_path, input_file):
    cmd = [
        ffprobe_path,
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        input_file
    ]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
    return float(result.stdout.strip())

def get_atempo_filter(speed):
    if speed == 1.0:
        return ""
    filters = []
    temp_speed = speed
    while temp_speed > 2.0:
        filters.append("atempo=2.0")
        temp_speed /= 2.0
    while temp_speed < 0.5:
        filters.append("atempo=0.5")
        temp_speed /= 0.5
    if temp_speed != 1.0:
        filters.append(f"atempo={temp_speed:.4f}")
    return ",".join(filters)

def format_duration(seconds):
    mins, secs = divmod(seconds, 60)
    hrs, mins = divmod(mins, 60)
    if hrs > 0:
        return f"{int(hrs):02d}:{int(mins):02d}:{secs:05.2f}"
    return f"{int(mins):02d}:{secs:05.2f}"

def parse_time_field(line_str):
    if "time=" in line_str:
        try:
            parts = line_str.split("time=")
            if len(parts) > 1:
                t_str = parts[1].split()[0]
                hms = t_str.split(":")
                if len(hms) == 3:
                    hours = float(hms[0])
                    minutes = float(hms[1])
                    seconds = float(hms[2])
                    return hours * 3600 + minutes * 60 + seconds
        except Exception:
            pass
    return None

def compress_audio(input_file, output_file, max_size_mb=14.9, speed=1.0, log_callback=print, progress_callback=None):
    ffmpeg_path, ffprobe_path = find_ffmpeg_tools()
    log_callback(f"Using ffmpeg: {ffmpeg_path}")
    log_callback(f"Using ffprobe: {ffprobe_path}")
    
    try:
        duration = get_audio_duration(ffprobe_path, input_file)
    except Exception as e:
        log_callback(f"Error reading duration: {e}")
        return None
        
    original_duration = duration
    if speed != 1.0:
        duration = original_duration / speed
        log_callback(f"Audio duration adjusted for speed ({speed}x): {original_duration:.2f}s ➔ {duration:.2f}s")
    else:
        log_callback(f"Audio duration: {duration:.2f} seconds ({duration/60:.2f} minutes)")
    
    target_size_bytes = max_size_mb * 1024 * 1024 * 0.96
    target_total_bits = target_size_bytes * 8
    
    raw_bitrate_kbps = (target_total_bits / duration) / 1000
    log_callback(f"Raw target bitrate: {raw_bitrate_kbps:.2f} kbps")
    
    out_ext = os.path.splitext(output_file)[1].lower()
            
    codec = "libmp3lame"
    if out_ext == '.mp3':
        codec = "libmp3lame"
    elif out_ext == '.ogg':
        codec = "libvorbis"
    elif out_ext in ('.m4a', '.aac'):
        codec = "aac"
    elif out_ext == '.wav':
        codec = "pcm_s16le"
    elif out_ext == '.flac':
        codec = "flac"
    elif out_ext == '.wma':
        codec = "wmav2"
    else:
        codec = "libmp3lame"
        
    selected_kbps = min(256, max(16, int(raw_bitrate_kbps)))
    if codec == "libmp3lame":
        standard_bitrates = [8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320]
        selected_kbps = 8
        for b in sorted(standard_bitrates, reverse=True):
            if b <= raw_bitrate_kbps:
                selected_kbps = b
                break
                
    log_callback(f"Selected bitrate: {selected_kbps} kbps")
    
    cmd = [ffmpeg_path, "-y", "-i", input_file, "-map", "0:a:0", "-codec:a", codec]
    
    if codec not in ("pcm_s16le", "flac"):
        if selected_kbps < 64:
            cmd.extend(["-ac", "1"])
        if selected_kbps < 32:
            cmd.extend(["-ar", "22050"])
        cmd.extend(["-b:a", f"{selected_kbps}k"])
        
    if speed != 1.0:
        atempo_str = get_atempo_filter(speed)
        if atempo_str:
            cmd.extend(["-filter:a", atempo_str])
            
    cmd.append(output_file)
    log_callback(f"Running FFmpeg: {' '.join(cmd)}")
    
    try:
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        while True:
            line = process.stdout.readline()
            if not line:
                break
            line_str = line.strip()
            if line_str and ("size=" in line_str or "time=" in line_str or "bitrate=" in line_str):
                log_callback(line_str)
        process.wait()
        return output_file if process.returncode == 0 else None
    except Exception as e:
        log_callback(f"Error running FFmpeg: {e}")
        return None

def compress_video(input_file, output_file, max_size_mb=14.9, speed=1.0, log_callback=print, progress_callback=None, preset="ultrafast", hw_accel="Auto-Detect"):
    ffmpeg_path, ffprobe_path = find_ffmpeg_tools()
    log_callback(f"Using ffmpeg: {ffmpeg_path}")
    log_callback(f"Using ffprobe: {ffprobe_path}")
    
    try:
        duration = get_audio_duration(ffprobe_path, input_file)
    except Exception as e:
        log_callback(f"Error reading video duration: {e}")
        return None
        
    original_duration = duration
    if speed != 1.0:
        duration = original_duration / speed
        log_callback(f"Video duration adjusted for speed ({speed}x): {original_duration:.2f}s ➔ {duration:.2f}s")
    else:
        log_callback(f"Video duration: {duration:.2f} seconds ({duration/60:.2f} minutes)")
    
    has_audio = True
    try:
        cmd_probe = [ffprobe_path, "-v", "error", "-select_streams", "a", "-show_entries", "stream=codec_type", "-of", "csv=p=0", input_file]
        res = subprocess.run(cmd_probe, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if not res.stdout.strip():
            has_audio = False
    except Exception:
        pass
    
    target_size_bytes = max_size_mb * 1024 * 1024 * 0.94
    target_total_bits = target_size_bytes * 8
    
    target_overall_bitrate_kbps = (target_total_bits / duration) / 1000
    log_callback(f"Target overall bitrate: {target_overall_bitrate_kbps:.2f} kbps")
    
    if has_audio:
        audio_bitrate_kbps = min(128, max(32, int(target_overall_bitrate_kbps * 0.15)))
        video_bitrate_kbps = max(50, int(target_overall_bitrate_kbps - audio_bitrate_kbps))
    else:
        audio_bitrate_kbps = 0
        video_bitrate_kbps = max(50, int(target_overall_bitrate_kbps))
        
    log_callback(f"Allocated bitrates: Video {video_bitrate_kbps} kbps, Audio {audio_bitrate_kbps} kbps")
    
    out_ext = os.path.splitext(output_file)[1].lower()
    
    vcodec = "libx264"
    acodec = "aac"
    
    if out_ext == '.webm':
        vcodec = "libvpx-vp9"
        acodec = "libopus"
    elif out_ext == '.avi':
        vcodec = "mpeg4"
        acodec = "libmp3lame"
    elif out_ext == '.flv':
        vcodec = "flv1"
        acodec = "libmp3lame"
    else:
        vcodec = "libx264"
        acodec = "aac"
        
    if vcodec == "libx264":
        selected_hw = None
        if hw_accel == "Auto-Detect":
            supported = detect_supported_hardware_encoders()
            if supported:
                h264_hw = [e for e in supported if e.startswith("h264_")]
                if h264_hw:
                    selected_hw = h264_hw[0]
                    log_callback(f"Auto-detected working H.264 GPU encoder: {selected_hw}")
        elif hw_accel == "Nvidia NVENC":
            selected_hw = "h264_nvenc"
        elif hw_accel == "AMD AMF":
            selected_hw = "h264_amf"
        elif hw_accel == "Intel QSV":
            selected_hw = "h264_qsv"
        elif hw_accel == "Windows MediaFoundation":
            selected_hw = "h264_mf"
            
        if selected_hw:
            vcodec = selected_hw
            
    if vcodec == "libvpx-vp9":
        if hw_accel == "Intel QSV" or (hw_accel == "Auto-Detect" and "vp9_qsv" in (detect_supported_hardware_encoders() or [])):
            vcodec = "vp9_qsv"
    
    scale_val = 720
    if video_bitrate_kbps < 200:
        scale_val = 240
    elif video_bitrate_kbps < 500:
        scale_val = 360
    elif video_bitrate_kbps < 1000:
        scale_val = 480
    else:
        scale_val = 720
        
    cmd = [ffmpeg_path, "-y", "-i", input_file, "-map", "0:v:0"]
    
    video_filters = []
    if speed != 1.0:
        video_filters.append(f"setpts=PTS/{speed}")
    video_filters.append(f"scale=-2:{scale_val}")
    
    cmd.extend(["-vf", ",".join(video_filters)])
    
    if vcodec == "libx264":
        cmd.extend(["-codec:v", "libx264", "-preset", preset, "-b:v", f"{video_bitrate_kbps}k"])
    elif vcodec == "h264_nvenc":
        nvenc_preset = "p1" if preset in ("ultrafast", "superfast") else "p2" if preset in ("veryfast", "faster") else "p4"
        cmd.extend(["-codec:v", "h264_nvenc", "-preset", nvenc_preset, "-b:v", f"{video_bitrate_kbps}k"])
    elif vcodec == "h264_amf":
        amf_quality = "speed" if preset in ("ultrafast", "superfast", "veryfast", "faster", "fast") else "quality"
        cmd.extend(["-codec:v", "h264_amf", "-quality", amf_quality, "-b:v", f"{video_bitrate_kbps}k"])
    elif vcodec == "h264_qsv":
        qsv_preset = "veryfast" if preset in ("ultrafast", "superfast") else preset
        cmd.extend(["-codec:v", "h264_qsv", "-preset", qsv_preset, "-b:v", f"{video_bitrate_kbps}k"])
    elif vcodec == "h264_mf":
        cmd.extend(["-codec:v", "h264_mf", "-b:v", f"{video_bitrate_kbps}k"])
    elif vcodec == "vp9_qsv":
        cmd.extend(["-codec:v", "vp9_qsv", "-b:v", f"{video_bitrate_kbps}k"])
    elif vcodec == "libvpx-vp9":
        cpu_used = "8" if preset in ("ultrafast", "superfast") else "4"
        cmd.extend(["-codec:v", "libvpx-vp9", "-b:v", f"{video_bitrate_kbps}k", "-deadline", "realtime", "-cpu-used", cpu_used])
    else:
        cmd.extend(["-codec:v", vcodec, "-b:v", f"{video_bitrate_kbps}k"])
    
    if has_audio:
        cmd.extend(["-map", "0:a:0", "-codec:a", acodec])
        if acodec not in ("pcm_s16le", "flac") and audio_bitrate_kbps > 0:
            cmd.extend(["-b:a", f"{audio_bitrate_kbps}k"])
            if audio_bitrate_kbps < 64:
                cmd.extend(["-ac", "1"])
        if speed != 1.0:
            atempo_str = get_atempo_filter(speed)
            if atempo_str:
                cmd.extend(["-filter:a", atempo_str])
                
    cmd.append(output_file)
    log_callback(f"Running video compression: {' '.join(cmd)}")
    
    try:
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        while True:
            line = process.stdout.readline()
            if not line:
                break
            line_str = line.strip()
            if line_str and ("size=" in line_str or "time=" in line_str or "bitrate=" in line_str or "frame=" in line_str):
                log_callback(line_str)
        process.wait()
        if process.returncode == 0 and os.path.exists(output_file) and os.path.getsize(output_file) > 0:
            return output_file
        else:
            log_callback(f"FFmpeg process returned code {process.returncode}")
    except Exception as e:
        log_callback(f"Error running FFmpeg: {e}")
        
    if vcodec != "libx264" and out_ext != ".webm":
        log_callback(f"[Self-Healing] GPU encoder '{vcodec}' failed. Auto-retrying with CPU (libx264) software encoder...")
        return compress_video(input_file, output_file, max_size_mb, speed, log_callback, progress_callback, preset, hw_accel="CPU")
        
    return None

def compress_image(input_path, output_path, max_size_mb=14.9, user_scale=1.0, log_callback=print, progress_callback=None):
    target_bytes = max_size_mb * 1024 * 1024
    _, ext = os.path.splitext(input_path.lower())
    
    try:
        img = Image.open(input_path)
    except Exception as e:
        log_callback(f"Error opening image: {e}")
        return False
        
    img_format = img.format or ('JPEG' if ext in ('.jpg', '.jpeg') else 'PNG' if ext == '.png' else 'GIF')
    quality = 85
    scale = user_scale
    
    for attempt in range(5):
        img_temp = img.copy()
        if scale < 1.0:
            new_size = (int(img_temp.width * scale), int(img_temp.height * scale))
            img_temp = img_temp.resize(new_size, Image.Resampling.LANCZOS)
            
        img_bytes = io.BytesIO()
        if img_format == 'JPEG' or ext in ('.jpg', '.jpeg'):
            img_temp.save(img_bytes, format='JPEG', quality=quality, optimize=True)
        elif img_format == 'PNG' or ext == '.png':
            if attempt >= 2:
                img_temp = img_temp.convert('P', palette=Image.ADAPTIVE, colors=256)
            img_temp.save(img_bytes, format='PNG', optimize=True)
        elif img_format == 'WEBP' or ext == '.webp':
            img_temp.save(img_bytes, format='WEBP', quality=quality, optimize=True)
        elif img_format == 'BMP' or ext == '.bmp':
            img_temp.save(img_bytes, format='BMP')
        elif img_format == 'TIFF' or ext in ('.tiff', '.tif'):
            img_temp.save(img_bytes, format='TIFF', compression='tiff_lzw')
        elif img_format == 'GIF' or ext == '.gif':
            if getattr(img, "is_animated", False):
                frames = []
                for frame_idx in range(img.n_frames):
                    img.seek(frame_idx)
                    frame = img.copy()
                    if scale < 1.0:
                        new_size = (int(frame.width * scale), int(frame.height * scale))
                        frame = frame.resize(new_size, Image.Resampling.LANCZOS)
                    if attempt >= 2:
                        frame = frame.convert('P', palette=Image.ADAPTIVE, colors=256)
                    frames.append(frame)
                
                frames[0].save(
                    img_bytes,
                    format='GIF',
                    save_all=True,
                    append_images=frames[1:],
                    loop=img.info.get('loop', 0),
                    duration=img.info.get('duration', 100),
                    optimize=True
                )
            else:
                img_temp.save(img_bytes, format='GIF', optimize=True)
        else:
            img_temp.save(img_bytes, format=img_format)
            
        data = img_bytes.getvalue()
        current_size = len(data)
        log_callback(f"Attempt {attempt+1}: Quality={quality}, Scale={scale:.2f} -> Size={current_size/(1024*1024):.2f} MB")
            
        if current_size <= target_bytes:
            with open(output_path, 'wb') as f:
                f.write(data)
            return True
            
        quality = max(20, quality - 20)
        if attempt >= 2:
            scale = scale * 0.8
        
    with open(output_path, 'wb') as f:
        f.write(data)
    return False

def compress_pdf(input_path, output_path, max_size_mb=14.9, log_callback=print, progress_callback=None):
    target_bytes = max_size_mb * 1024 * 1024
    quality = 80
    
    for attempt in range(4):
        try:
            writer = PdfWriter(clone_from=input_path)
            if hasattr(writer, "compress_identical_objects"):
                try:
                    writer.compress_identical_objects(remove_duplicates=True, remove_unreferenced=True)
                except Exception:
                    pass
            
            for page in writer.pages:
                page.compress_content_streams()
                for img in page.images:
                    try:
                        img.replace(img.image, quality=quality)
                    except Exception:
                        pass
                        
            pdf_bytes = io.BytesIO()
            writer.write(pdf_bytes)
            data = pdf_bytes.getvalue()
            current_size = len(data)
            
            log_callback(f"Attempt {attempt+1}: PDF Quality={quality} -> Size={current_size/(1024*1024):.2f} MB")
            
            if current_size <= target_bytes:
                with open(output_path, 'wb') as f:
                    f.write(data)
                return True
        except Exception as e:
            log_callback(f"PDF compression error: {e}")
            return False
            
        quality = max(20, quality - 20)
        
    with open(output_path, 'wb') as f:
        f.write(data)
    return False

def compress_docx_pptx(input_path, output_path, max_size_mb=14.9, log_callback=print, progress_callback=None):
    target_bytes = max_size_mb * 1024 * 1024
    quality = 80
    scale = 1.0
    
    for attempt in range(4):
        try:
            in_buf = io.BytesIO()
            with zipfile.ZipFile(input_path, 'r') as yin:
                with zipfile.ZipFile(in_buf, 'w', zipfile.ZIP_DEFLATED) as yout:
                    for item in yin.infolist():
                        data = yin.read(item.filename)
                        lower_name = item.filename.lower()
                        is_media_image = ('word/media/' in lower_name or 'ppt/media/' in lower_name or 'xl/media/' in lower_name) and lower_name.endswith(('.jpg', '.jpeg', '.png', '.gif'))
                                
                        if is_media_image:
                            try:
                                img = Image.open(io.BytesIO(data))
                                img_format = img.format
                                
                                if scale < 1.0:
                                     new_size = (int(img.width * scale), int(img.height * scale))
                                     img = img.resize(new_size, Image.Resampling.LANCZOS)
                                     
                                img_bytes = io.BytesIO()
                                if img_format == 'JPEG' or lower_name.endswith(('.jpg', '.jpeg')):
                                    img.save(img_bytes, format='JPEG', quality=quality, optimize=True)
                                elif img_format == 'PNG' or lower_name.endswith('.png'):
                                    if attempt >= 2:
                                        img = img.convert('P', palette=Image.ADAPTIVE, colors=256)
                                    img.save(img_bytes, format='PNG', optimize=True)
                                else:
                                    img.save(img_bytes, format=img_format)
                                    
                                data = img_bytes.getvalue()
                            except Exception as e:
                                pass
                                
                        yout.writestr(item, data)
                        
            compressed_data = in_buf.getvalue()
            current_size = len(compressed_data)
            log_callback(f"Attempt {attempt+1}: Size={current_size/(1024*1024):.2f} MB")
            
            if current_size <= target_bytes:
                with open(output_path, 'wb') as f:
                    f.write(compressed_data)
                return True
        except Exception as e:
            log_callback(f"Error compressing document: {e}")
            return False
            
        quality = max(20, quality - 20)
        scale = max(0.4, scale - 0.2)
        
    with open(output_path, 'wb') as f:
        f.write(compressed_data)
    return False

def compress_zip(input_path, output_path, max_size_mb=14.9, log_callback=print, progress_callback=None):
    target_bytes = max_size_mb * 1024 * 1024
    temp_dir = tempfile.mkdtemp(prefix="temp_zip_")
    
    try:
        log_callback("Extracting zip archive...")
        with zipfile.ZipFile(input_path, 'r') as z:
            z.extractall(temp_dir)
            
        compressible_files = []
        total_compressible_size = 0
        
        compressible_exts = (
            '.mp3', '.m4a', '.wav', '.flac', '.ogg', '.aac', '.wma',
            '.mp4', '.mkv', '.avi', '.mov', '.webm', '.flv', '.wmv',
            '.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.tiff',
            '.pdf', '.docx', '.pptx', '.xlsx'
        )
        
        for root, dirs, files in os.walk(temp_dir):
            for file in files:
                file_path = os.path.join(root, file)
                _, ext = os.path.splitext(file.lower())
                size = os.path.getsize(file_path)
                if ext in compressible_exts:
                    compressible_files.append((file_path, size))
                    total_compressible_size += size
                    
        if total_compressible_size > 0:
            budget_for_compressible = target_bytes * 0.90
            scale_factor = min(1.0, budget_for_compressible / total_compressible_size)
            
            for file_path, original_size in compressible_files:
                file_target_mb = max(0.2, (original_size * scale_factor) / (1024 * 1024))
                out_dir = os.path.dirname(file_path)
                filename = os.path.basename(file_path)
                temp_out = os.path.join(out_dir, "zipcomp_" + filename)
                
                success = False
                _, ext = os.path.splitext(file_path.lower())
                
                if ext in ('.mp3', '.m4a', '.wav', '.flac', '.ogg', '.aac', '.wma'):
                    out_ext = '.mp3' if ext in ('.wav', '.flac', '.wma') else ext
                    temp_out_audio = os.path.join(out_dir, "zipcomp_" + os.path.splitext(filename)[0] + out_ext)
                    success = compress_audio(file_path, temp_out_audio, file_target_mb, 1.0, lambda x: None)
                    temp_out = temp_out_audio
                elif ext in ('.mp4', '.mkv', '.avi', '.mov', '.webm', '.flv', '.wmv'):
                    out_ext = '.mp4' if ext == '.wmv' else ext
                    temp_out_video = os.path.join(out_dir, "zipcomp_" + os.path.splitext(filename)[0] + out_ext)
                    success = compress_video(file_path, temp_out_video, file_target_mb, 1.0, lambda x: None)
                    temp_out = temp_out_video
                elif ext in ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.tiff'):
                    success = compress_image(file_path, temp_out, file_target_mb, 1.0, lambda x: None)
                elif ext == '.pdf':
                    success = compress_pdf(file_path, temp_out, file_target_mb, lambda x: None)
                elif ext in ('.docx', '.pptx', '.xlsx'):
                    success = compress_docx_pptx(file_path, temp_out, file_target_mb, lambda x: None)
                    
                if success:
                    actual_out = success if isinstance(success, str) else temp_out
                    if os.path.exists(actual_out):
                        os.replace(actual_out, file_path)
                else:
                    if os.path.exists(temp_out):
                        os.remove(temp_out)
                        
        log_callback("Re-packing zip archive...")
        with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as z_out:
            for root, dirs, files in os.walk(temp_dir):
                for file in files:
                    full_path = os.path.join(root, file)
                    rel_path = os.path.relpath(full_path, temp_dir)
                    z_out.write(full_path, rel_path)
                    
        return True
    except Exception as e:
        log_callback(f"Error compressing ZIP archive: {e}")
        return False
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

def compress_file(input_file, output_dir=None, max_size_mb=14.9, speed=1.0, image_scale=1.0, log_callback=print, progress_callback=None, target_format=None, preset="ultrafast", hw_accel="Auto-Detect", naming_pattern="{filename}_compressed"):
    if not os.path.isfile(input_file):
        log_callback(f"Error: Input file '{input_file}' not found.")
        return None
        
    input_file = os.path.abspath(input_file)
    if not output_dir:
        output_dir = os.path.dirname(input_file)
        
    os.makedirs(output_dir, exist_ok=True)
    filename = os.path.basename(input_file)
    base, ext = os.path.splitext(filename)
    
    log_callback(f"Processing compression for '{filename}' (target size: {max_size_mb} MB)")
    
    audio_formats = ('mp3', 'm4a', 'wav', 'flac', 'ogg', 'aac', 'wma', 'opus', 'aiff')
    video_formats = ('mp4', 'mkv', 'avi', 'mov', 'webm', 'flv', 'wmv', 'm4v', '3gp', 'ts')
    image_formats = ('jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp', 'tiff')
    
    is_audio = False
    is_video = False
    is_image = False
    
    tgt_fmt = target_format.lower().replace(".", "") if target_format else None
    
    if tgt_fmt and tgt_fmt != "keep original":
        if tgt_fmt in audio_formats:
            is_audio = True
            out_ext = "." + tgt_fmt
        elif tgt_fmt in video_formats:
            is_video = True
            out_ext = "." + tgt_fmt
        elif tgt_fmt in image_formats:
            is_image = True
            out_ext = "." + tgt_fmt
        else:
            out_ext = "." + tgt_fmt
    else:
        ext_clean = ext[1:].lower()
        if ext_clean in audio_formats:
            is_audio = True
            out_ext = ext.lower()
            if out_ext in ('.wav', '.flac', '.wma'):
                out_ext = '.mp3'
        elif ext_clean in video_formats:
            is_video = True
            out_ext = ext.lower()
            if out_ext == '.wmv':
                out_ext = '.mp4'
        elif ext_clean in image_formats:
            is_image = True
            out_ext = ext.lower()
        else:
            out_ext = ext.lower()
            
    pattern = naming_pattern or "{filename}_compressed"
    out_name = pattern.replace("{filename}", base)
    output_file_path = get_non_colliding_path(input_file, output_dir, out_name, out_ext)
    
    success = False
    
    if is_audio:
        res = compress_audio(input_file, output_file_path, max_size_mb, speed, log_callback, progress_callback)
        success = (res is not None)
    elif is_video:
        res = compress_video(input_file, output_file_path, max_size_mb, speed, log_callback, progress_callback, preset, hw_accel)
        success = (res is not None)
    elif is_image:
        success = compress_image(input_file, output_file_path, max_size_mb, image_scale, log_callback, progress_callback)
    elif ext.lower() == '.pdf':
        success = compress_pdf(input_file, output_file_path, max_size_mb, log_callback, progress_callback)
    elif ext.lower() in ('.docx', '.pptx', '.xlsx'):
        success = compress_docx_pptx(input_file, output_file_path, max_size_mb, log_callback, progress_callback)
    elif ext.lower() == '.zip':
        success = compress_zip(input_file, output_file_path, max_size_mb, log_callback, progress_callback)
    else:
        # Fallback for generic/unrecognized file formats
        orig_size_mb = os.path.getsize(input_file) / (1024 * 1024)
        if orig_size_mb <= max_size_mb:
            log_callback(f"File size ({orig_size_mb:.2f} MB) is already under target limit ({max_size_mb} MB). Creating copy...")
            shutil.copy2(input_file, output_file_path)
            return output_file_path
        else:
            log_callback(f"File size ({orig_size_mb:.2f} MB) exceeds target ({max_size_mb} MB). Packing into compressed ZIP archive...")
            zip_out_path = get_non_colliding_path(input_file, output_dir, out_name, ".zip")
            with zipfile.ZipFile(zip_out_path, 'w', zipfile.ZIP_DEFLATED) as zout:
                zout.write(input_file, os.path.basename(input_file))
            output_file_path = zip_out_path
            success = True
        
    if success and os.path.exists(output_file_path):
        final_size_mb = os.path.getsize(output_file_path) / (1024 * 1024)
        log_callback(f"[SUCCESS] Final size: {final_size_mb:.2f} MB -> Saved to: {output_file_path}")
        return output_file_path
        
    log_callback("[ERROR] Compression failed.")
    return None

CONTEXT_MENU_KEY = r"Software\Classes\*\shell\CompressForDiscord"

def install_context_menu():
    try:
        if getattr(sys, 'frozen', False):
            exe_path = os.path.abspath(sys.executable)
            cmd = f'"{exe_path}" "%1"'
        else:
            script_path = os.path.abspath(__file__)
            python_exe = sys.executable
            cmd = f'"{python_exe}" "{script_path}" "%1"'
            
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, CONTEXT_MENU_KEY) as key:
            winreg.SetValue(key, "", winreg.REG_SZ, "Compress for discord")
            winreg.SetValueEx(key, "Icon", 0, winreg.REG_SZ, "imageres.dll,-68")
            with winreg.CreateKey(key, "command") as cmd_key:
                winreg.SetValue(cmd_key, "", winreg.REG_SZ, cmd)
        print("\n=======================================================")
        print("[SUCCESS] 'Compress for discord' option registered!")
        print("You can now right-click any file in File Explorer and select 'Compress for discord'.")
        print("=======================================================\n")
        return True
    except Exception as e:
        print(f"[ERROR] Failed to register context menu: {e}")
        return False

def uninstall_context_menu():
    try:
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, CONTEXT_MENU_KEY + r"\command")
        except FileNotFoundError:
            pass
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, CONTEXT_MENU_KEY)
        except FileNotFoundError:
            pass
        print("\n=======================================================")
        print("[SUCCESS] 'Compress for discord' context menu option uninstalled.")
        print("=======================================================\n")
        return True
    except Exception as e:
        print(f"[ERROR] Failed to uninstall context menu: {e}")
        return False

def main():
    parser = argparse.ArgumentParser(description="Headless Media & File Compressor for Discord")
    parser.add_argument("input_files", nargs="*", help="Path to input file(s) to compress")
    parser.add_argument("--install", "--install-context-menu", action="store_true", help="Install 'Compress for discord' right-click Explorer context menu")
    parser.add_argument("--uninstall", "--uninstall-context-menu", action="store_true", help="Uninstall 'Compress for discord' right-click Explorer context menu")
    parser.add_argument("-o", "--output-dir", default=None, help="Destination folder (default: same directory as input file)")
    parser.add_argument("-s", "--target-size", type=float, default=14.9, help="Target MB size limit (default: 14.9 MB for Discord)")
    parser.add_argument("--speed", type=float, default=1.0, help="Speed multiplier (0.5x to 3.0x) for audio/video")
    parser.add_argument("--resize", type=float, default=1.0, help="Image resize scale factor (0.1 to 1.0)")
    parser.add_argument("-f", "--format", default=None, help="Target conversion format (e.g. mp4, webm, mp3, png)")
    parser.add_argument("-p", "--preset", default="ultrafast", help="Encoding speed preset (ultrafast, superfast, fast, medium, slow)")
    parser.add_argument("-a", "--accel", default="Auto-Detect", choices=["Auto-Detect", "CPU", "Nvidia NVENC", "AMD AMF", "Intel QSV", "Windows MediaFoundation"], help="Hardware acceleration encoder")
    parser.add_argument("--no-pause", action="store_true", help="Do not pause console after processing")
    
    args = parser.parse_args()

    if args.install:
        install_context_menu()
        if not args.no_pause and len(sys.argv) == 2:
            input("Press Enter to exit...")
        return

    if args.uninstall:
        uninstall_context_menu()
        if not args.no_pause and len(sys.argv) == 2:
            input("Press Enter to exit...")
        return

    if not args.input_files:
        print("Media & File Compressor for Discord")
        print("Usage: compress.py <input_file1> [input_file2 ...] [options]")
        print("\nContext Menu Integration:")
        print("  python compress.py --install    (Register right-click 'Compress for discord' option)")
        print("  python compress.py --uninstall  (Remove right-click 'Compress for discord' option)")
        print("\nRun 'python compress.py --help' for full options list.")
        return

    SelfHealer.heal_python_dependencies()
    SelfHealer.heal_ffmpeg()

    success_count = 0
    total_count = len(args.input_files)

    for idx, input_path in enumerate(args.input_files, 1):
        print(f"\n--- [{idx}/{total_count}] Compressing '{input_path}' ---")
        res = compress_file(
            input_path,
            output_dir=args.output_dir,
            max_size_mb=args.target_size,
            speed=args.speed,
            image_scale=args.resize,
            target_format=args.format,
            preset=args.preset,
            hw_accel=args.accel
        )
        if res:
            success_count += 1

    print(f"\nCompleted: {success_count}/{total_count} files successfully compressed under {args.target_size} MB.")
    
    # Pause briefly when invoked interactively from Explorer context menu if single file
    if not args.no_pause and os.name == 'nt':
        import time
        time.sleep(2.5)

if __name__ == "__main__":
    main()
