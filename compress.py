import os
import subprocess
import sys
import threading
import io
import zipfile
import shutil
import tempfile
import datetime
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinter.scrolledtext import ScrolledText
from PIL import Image, ImageTk
from pypdf import PdfReader, PdfWriter
import uuid
import customtkinter as ctk

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
            # Run a brief 0.1-second dummy encode to test hardware capability
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
            "customtkinter": "customtkinter",
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
                # Test running ffmpeg
                subprocess.run(["ffmpeg", "-version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            except (FileNotFoundError, subprocess.CalledProcessError):
                log_callback("Self-Healing: FFmpeg not detected in PATH. Attempting automatic installation via winget...")
                try:
                    subprocess.Popen(["winget", "install", "Gyan.FFmpeg", "--silent", "--accept-package-agreements", "--accept-source-agreements"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    log_callback("Self-Healing: winget installation triggered in background. Please restart application after a few minutes.")
                except Exception as e:
                    log_callback(f"Self-Healing: Failed to trigger winget: {e}")

    @staticmethod
    def self_heal_failed_task(task, log_callback=print):
        if not hasattr(task, "retry_count"):
            task.retry_count = 0
        if task.retry_count >= 2:
            log_callback("Self-Healing: Maximum auto-retry limit (2) reached. Leaving task as Failed.")
            return False
            
        logs_str = "\n".join(task.log_messages).lower()
        healed = False
        
        # Scenario 1: GPU Encoder failure (nvcuda, MF_E_INVALIDMEDIATYPE, etc.)
        if any(x in logs_str for x in ["cuda", "nvenc", "amf", "qsv", "mediafoundation", "could not open encoder", "mft name", "operation not permitted", "generic error"]):
            if task.hw_accel != "CPU":
                log_callback("Self-Healing: GPU hardware encoder failed. Switching task to CPU (Software) mode and retrying...")
                task.hw_accel = "CPU"
                healed = True
                
        # Scenario 2: Target size limit warning (minimum bitrate issue)
        if "warning: compressed size still exceeds the target limit" in logs_str:
            if task.target_format != "WebM" and not task.input_path.lower().endswith(".webm"):
                log_callback("Self-Healing: Compression exceeded target size limit. Retrying by converting to WebM format for better low-bitrate compression...")
                task.target_format = "WebM"
                healed = True
            else:
                log_callback("Self-Healing: Already using WebM. Retrying compression with a slightly lower target threshold to force downsampling...")
                healed = True
                
        if healed:
            task.retry_count += 1
            task.status = "Pending"
            task.progress = 0.0
            task.log_messages.append(f"*** Self-Healed: Attempt {task.retry_count} ***")
            return True
            
        log_callback("Self-Healing: Could not identify a safe self-healing path for this task.")
        return False

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
    
    # 1. If output matches original input, force a suffix
    if target_path == input_norm:
        base_name = base_name + "_compressed"
        target_path = os.path.normpath(os.path.join(output_dir, base_name + out_ext))
        
    # 2. If file already exists, auto-increment with (1), (2), etc.
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
    secs = int(seconds)
    hours = secs // 3600
    minutes = (secs % 3600) // 60
    remaining_secs = secs % 60
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{remaining_secs:02d}"
    return f"{minutes:02d}:{remaining_secs:02d}"

def parse_time_field(line_str):
    if "time=" in line_str:
        try:
            parts = line_str.split("time=")
            if len(parts) > 1:
                time_part = parts[1].split()[0]
                hms = time_part.split(":")
                if len(hms) == 3:
                    hours = float(hms[0])
                    minutes = float(hms[1])
                    seconds = float(hms[2])
                    return hours * 3600 + minutes * 60 + seconds
        except Exception:
            pass
    return None

def compress_audio(input_file, output_file, max_size_mb=15.0, speed=1.0, log_callback=print, progress_callback=None):
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
        log_callback("Target format is .mp3. Using LAME encoder.")
    elif out_ext == '.ogg':
        codec = "libvorbis"
        log_callback("Target format is .ogg. Using Vorbis encoder.")
    elif out_ext in ('.m4a', '.aac'):
        codec = "aac"
        log_callback("Target format is AAC. Using native AAC encoder.")
    elif out_ext == '.wav':
        codec = "pcm_s16le"
        log_callback("Target format is lossless .wav. Using PCM 16-bit codec.")
    elif out_ext == '.flac':
        codec = "flac"
        log_callback("Target format is lossless .flac. Using FLAC encoder.")
    elif out_ext == '.wma':
        codec = "wmav2"
        log_callback("Target format is .wma. Using WMAv2 codec.")
    else:
        codec = "libmp3lame"
        log_callback(f"Defaulting to .mp3 format with LAME encoder.")
        
    selected_kbps = min(256, max(16, int(raw_bitrate_kbps)))
    if codec == "libmp3lame":
        standard_bitrates = [8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320]
        selected_kbps = 8
        for b in sorted(standard_bitrates, reverse=True):
            if b <= raw_bitrate_kbps:
                selected_kbps = b
                break
                
    log_callback(f"Selected bitrate: {selected_kbps} kbps (N/A for PCM/FLAC)")
    
    cmd = [ffmpeg_path, "-y", "-i", input_file, "-map", "0:a:0", "-codec:a", codec]
    
    # Lossless formats do not support custom bitrates or standard compression options
    if codec not in ("pcm_s16le", "flac"):
        if selected_kbps < 64:
            log_callback("Downmixing to mono to improve quality at lower bitrate.")
            cmd.extend(["-ac", "1"])
            
        if selected_kbps < 32:
            log_callback("Lowering sampling rate to 22050 Hz to reduce artifacts at very low bitrate.")
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
            if line_str:
                if "size=" in line_str or "time=" in line_str or "bitrate=" in line_str:
                    log_callback(line_str)
                if progress_callback and duration > 0:
                    time_pos = parse_time_field(line_str)
                    if time_pos is not None:
                        prog = min(100.0, max(0.0, (time_pos / duration) * 100.0))
                        metrics = parse_ffmpeg_metrics(line_str)
                        if metrics:
                            progress_callback(prog, metrics)
                        else:
                            progress_callback(prog)
        process.wait()
        return output_file if process.returncode == 0 else None
    except Exception as e:
        log_callback(f"Error running FFmpeg: {e}")
        return None

def compress_video(input_file, output_file, max_size_mb=15.0, speed=1.0, log_callback=print, progress_callback=None, preset="ultrafast", hw_accel="Auto-Detect"):
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
    
    # Check if video has an audio stream
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
        log_callback("Target format is .webm. Using VP9 & Opus codecs.")
        vcodec = "libvpx-vp9"
        acodec = "libopus"
    elif out_ext == '.avi':
        log_callback("Target format is .avi. Using MPEG4 & MP3 codecs.")
        vcodec = "mpeg4"
        acodec = "libmp3lame"
    elif out_ext == '.flv':
        log_callback("Target format is .flv. Using FLV1 & MP3 codecs.")
        vcodec = "flv1"
        acodec = "libmp3lame"
    else:
        log_callback(f"Target format is {out_ext}. Using standard H264 & AAC codecs.")
        vcodec = "libx264"
        acodec = "aac"
        
    # Hardware acceleration override for H264 codecs
    if vcodec == "libx264":
        selected_hw = None
        if hw_accel == "Auto-Detect":
            supported = detect_supported_hardware_encoders()
            if supported:
                h264_hw = [e for e in supported if e.startswith("h264_")]
                if h264_hw:
                    selected_hw = h264_hw[0]
                    log_callback(f"Auto-detected working H.264 GPU encoder: {selected_hw}")
            else:
                log_callback("Auto-detect: No working GPU encoder found. Falling back to CPU (libx264).")
        elif hw_accel == "Nvidia NVENC":
            selected_hw = "h264_nvenc"
        elif hw_accel == "AMD AMF":
            selected_hw = "h264_amf"
        elif hw_accel == "Intel QSV":
            selected_hw = "h264_qsv"
        elif hw_accel == "Windows MediaFoundation":
            selected_hw = "h264_mf"
            
        if selected_hw:
            log_callback(f"Using hardware accelerated video encoder: {selected_hw}")
            vcodec = selected_hw
            
    # For WebM VP9, also check if QSV is available and requested
    if vcodec == "libvpx-vp9":
        if hw_accel == "Intel QSV" or (hw_accel == "Auto-Detect" and "vp9_qsv" in (detect_supported_hardware_encoders() or [])):
            log_callback("Using Intel QSV hardware accelerated VP9 encoder (vp9_qsv)")
            vcodec = "vp9_qsv"
    
    scale_val = 720
    if video_bitrate_kbps < 200:
        log_callback("Bitrate is very low. Downscaling video to 240p.")
        scale_val = 240
    elif video_bitrate_kbps < 500:
        log_callback("Bitrate is low. Downscaling video to 360p.")
        scale_val = 360
    elif video_bitrate_kbps < 1000:
        log_callback("Bitrate is moderate. Downscaling video to 480p.")
        scale_val = 480
    else:
        log_callback("Bitrate is high. Downscaling video to 720p.")
        scale_val = 720
        
    cmd = [ffmpeg_path, "-y", "-i", input_file]
    
    # Combine speed and scaling filters into a single video filter chain
    video_filters = []
    if speed != 1.0:
        video_filters.append(f"setpts=PTS/{speed}")
    video_filters.append(f"scale=-2:{scale_val}")
    
    cmd.extend(["-vf", ",".join(video_filters)])
    
    if vcodec == "libx264":
        cmd.extend(["-codec:v", "libx264", "-preset", preset, "-b:v", f"{video_bitrate_kbps}k"])
    elif vcodec == "h264_nvenc":
        cmd.extend(["-codec:v", "h264_nvenc", "-preset", preset, "-b:v", f"{video_bitrate_kbps}k"])
    elif vcodec == "h264_amf":
        amf_quality = "balanced"
        if preset in ("ultrafast", "superfast", "veryfast", "faster", "fast"):
            amf_quality = "speed"
        elif preset in ("slow", "slower", "veryslow"):
            amf_quality = "quality"
        cmd.extend(["-codec:v", "h264_amf", "-quality", amf_quality, "-b:v", f"{video_bitrate_kbps}k"])
    elif vcodec == "h264_qsv":
        qsv_preset = preset
        if preset in ("ultrafast", "superfast"):
            qsv_preset = "veryfast"
        cmd.extend(["-codec:v", "h264_qsv", "-preset", qsv_preset, "-b:v", f"{video_bitrate_kbps}k"])
    elif vcodec == "h264_mf":
        cmd.extend(["-codec:v", "h264_mf", "-b:v", f"{video_bitrate_kbps}k"])
    elif vcodec == "vp9_qsv":
        cmd.extend(["-codec:v", "vp9_qsv", "-b:v", f"{video_bitrate_kbps}k"])
    elif vcodec == "libvpx-vp9":
        cpu_used = "4"
        if preset in ("ultrafast", "superfast"):
            cpu_used = "8"
        elif preset in ("veryfast", "faster"):
            cpu_used = "6"
        elif preset in ("slow", "slower", "veryslow"):
            cpu_used = "2"
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
            if line_str:
                if "size=" in line_str or "time=" in line_str or "bitrate=" in line_str or "frame=" in line_str:
                    log_callback(line_str)
                if progress_callback and duration > 0:
                    time_pos = parse_time_field(line_str)
                    if time_pos is not None:
                        prog = min(100.0, max(0.0, (time_pos / duration) * 100.0))
                        metrics = parse_ffmpeg_metrics(line_str)
                        if metrics:
                            progress_callback(prog, metrics)
                        else:
                            progress_callback(prog)
        process.wait()
        return output_file if process.returncode == 0 else None
    except Exception as e:
        log_callback(f"Error running FFmpeg: {e}")
        return None

def compress_image(input_path, output_path, max_size_mb, user_scale=1.0, log_callback=print, progress_callback=None):
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
        
        if progress_callback:
            progress_callback(((attempt + 1) / 5) * 100.0)
            
        if current_size <= target_bytes:
            with open(output_path, 'wb') as f:
                f.write(data)
            if progress_callback:
                progress_callback(100.0)
            return True
            
        quality = max(20, quality - 20)
        if attempt >= 2:
            scale = scale * 0.8
        
    with open(output_path, 'wb') as f:
        f.write(data)
    return False

def compress_pdf(input_path, output_path, max_size_mb, log_callback=print, progress_callback=None):
    target_bytes = max_size_mb * 1024 * 1024
    quality = 80
    
    for attempt in range(4):
        try:
            writer = PdfWriter(clone_from=input_path)
            if hasattr(writer, "compress_identical_objects"):
                try:
                    writer.compress_identical_objects(remove_duplicates=True, remove_unreferenced=True)
                except TypeError:
                    try:
                        writer.compress_identical_objects(remove_identicals=True, remove_orphans=True)
                    except Exception:
                        pass
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
            
            log_callback(f"Attempt {attempt+1}: PDF Image Quality={quality} -> Size={current_size/(1024*1024):.2f} MB")
            
            if progress_callback:
                progress_callback(((attempt + 1) / 4) * 100.0)
                
            if current_size <= target_bytes:
                with open(output_path, 'wb') as f:
                    f.write(data)
                if progress_callback:
                    progress_callback(100.0)
                return True
        except Exception as e:
            log_callback(f"PDF compression error: {e}")
            return False
            
        quality = max(20, quality - 20)
        
    with open(output_path, 'wb') as f:
        f.write(data)
    return False

def compress_docx_pptx(input_path, output_path, max_size_mb, log_callback=print, progress_callback=None):
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
                        is_media_image = False
                        lower_name = item.filename.lower()
                        if ('word/media/' in lower_name or 'ppt/media/' in lower_name or 'xl/media/' in lower_name):
                            if lower_name.endswith(('.jpg', '.jpeg', '.png', '.gif')):
                                is_media_image = True
                                
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
                                elif img_format == 'GIF' or lower_name.endswith('.gif'):
                                    img.save(img_bytes, format='GIF', optimize=True)
                                else:
                                    img.save(img_bytes, format=img_format)
                                    
                                data = img_bytes.getvalue()
                            except Exception as e:
                                log_callback(f"Failed to compress embedded image {item.filename}: {e}")
                                
                        yout.writestr(item, data)
                        
            compressed_data = in_buf.getvalue()
            current_size = len(compressed_data)
            log_callback(f"Attempt {attempt+1}: Quality={quality}, Scale={scale:.2f} -> Size={current_size/(1024*1024):.2f} MB")
            
            if progress_callback:
                progress_callback(((attempt + 1) / 4) * 100.0)
                
            if current_size <= target_bytes:
                with open(output_path, 'wb') as f:
                    f.write(compressed_data)
                if progress_callback:
                    progress_callback(100.0)
                return True
        except Exception as e:
            log_callback(f"Error compressing Docx/Pptx/Xlsx: {e}")
            return False
            
        quality = max(20, quality - 20)
        scale = max(0.4, scale - 0.2)
        
    with open(output_path, 'wb') as f:
        f.write(compressed_data)
    return False

def compress_zip(input_path, output_path, max_size_mb, log_callback=print, progress_callback=None):
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
                    
        log_callback(f"Found {len(compressible_files)} compressible file(s) inside zip (Total size: {total_compressible_size/(1024*1024):.2f} MB)")
        
        if total_compressible_size > 0:
            budget_for_compressible = target_bytes * 0.90
            scale_factor = min(1.0, budget_for_compressible / total_compressible_size)
            log_callback(f"Compression scaling factor for zip elements: {scale_factor:.2f}")
            
            num_files = len(compressible_files)
            for idx, (file_path, original_size) in enumerate(compressible_files):
                file_target_mb = (original_size * scale_factor) / (1024 * 1024)
                file_target_mb = max(0.2, file_target_mb)
                
                log_callback(f"Optimizing: {os.path.basename(file_path)} -> Target {file_target_mb:.2f} MB")
                
                out_dir = os.path.dirname(file_path)
                filename = os.path.basename(file_path)
                temp_out = os.path.join(out_dir, "zipcomp_" + filename)
                
                success = False
                _, ext = os.path.splitext(file_path.lower())
                
                if ext in ('.mp3', '.m4a', '.wav', '.flac', '.ogg', '.aac', '.wma'):
                    out_ext = ext
                    if ext in ('.wav', '.flac', '.wma'):
                        out_ext = '.mp3'
                    temp_out_audio = os.path.join(out_dir, "zipcomp_" + os.path.splitext(filename)[0] + out_ext)
                    success = compress_audio(file_path, temp_out_audio, file_target_mb, 1.0, lambda x: None)
                    temp_out = temp_out_audio
                elif ext in ('.mp4', '.mkv', '.avi', '.mov', '.webm', '.flv', '.wmv'):
                    out_ext = ext
                    if ext == '.wmv':
                        out_ext = '.mp4'
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
                
                if progress_callback:
                    progress_callback(((idx + 1) / num_files) * 100.0)
                        
        log_callback("Re-packing zip archive...")
        with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as z_out:
            for root, dirs, files in os.walk(temp_dir):
                for file in files:
                    full_path = os.path.join(root, file)
                    rel_path = os.path.relpath(full_path, temp_dir)
                    z_out.write(full_path, rel_path)
                    
        final_size = os.path.getsize(output_path)
        log_callback(f"ZIP packing complete! Final size: {final_size/(1024*1024):.2f} MB")
        if progress_callback:
            progress_callback(100.0)
        return True
    except Exception as e:
        log_callback(f"Error compressing ZIP archive: {e}")
        return False
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

def compress_file(input_file, output_dir, max_size_mb=15.0, speed=1.0, image_scale=1.0, log_callback=print, progress_callback=None, target_format=None, preset="ultrafast", hw_accel="Auto-Detect", naming_pattern="{filename}_compressed"):
    if not os.path.isfile(input_file):
        log_callback(f"Error: Input file '{input_file}' not found.")
        return None
        
    os.makedirs(output_dir, exist_ok=True)
    filename = os.path.basename(input_file)
    base, ext = os.path.splitext(filename)
    
    log_callback(f"Processing compression for {filename} (target: {max_size_mb} MB)")
    
    # Determine the target extension out_ext
    audio_formats = ('mp3', 'm4a', 'wav', 'flac', 'ogg', 'aac', 'wma')
    video_formats = ('mp4', 'mkv', 'avi', 'mov', 'webm', 'flv', 'wmv')
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
        if ext[1:].lower() in audio_formats:
            is_audio = True
            out_ext = ext.lower()
            if out_ext in ('.wav', '.flac', '.wma'):
                out_ext = '.mp3'
        elif ext[1:].lower() in video_formats:
            is_video = True
            out_ext = ext.lower()
            if out_ext == '.wmv':
                out_ext = '.mp4'
        elif ext[1:].lower() in image_formats:
            is_image = True
            out_ext = ext.lower()
        else:
            out_ext = ext.lower()
            
    # Format naming pattern
    pattern = naming_pattern or "{filename}_compressed"
    out_name = pattern.replace("{filename}", base)
    out_name = out_name.replace("{size}", f"{max_size_mb}MB")
    out_name = out_name.replace("{preset}", str(preset))
    out_name = out_name.replace("{date}", datetime.datetime.now().strftime("%Y-%m-%d"))
    
    output_file_path = get_non_colliding_path(input_file, output_dir, out_name, out_ext)
    log_callback(f"Target non-colliding output path: {output_file_path}")
    
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
        log_callback(f"Error: Unsupported file extension '{ext}'")
        return None
        
    if success:
        if os.path.exists(output_file_path):
            final_size = os.path.getsize(output_file_path)
            final_size_mb = final_size / (1024 * 1024)
            log_callback(f"Verification: output size is {final_size_mb:.2f} MB")
            if final_size_mb <= max_size_mb:
                log_callback("SUCCESS: File size is under the target limit.")
                return output_file_path
            else:
                log_callback("WARNING: Compressed size still exceeds the target limit.")
                return output_file_path
    return None

class CompressionTask:
    def __init__(self, input_path, output_dir, target_size, speed, image_scale, target_format=None, preset="ultrafast", hw_accel="Auto-Detect"):
        self.id = str(uuid.uuid4())
        self.input_path = input_path
        self.output_dir = output_dir
        self.target_size = target_size
        self.speed = speed
        self.image_scale = image_scale
        self.target_format = target_format
        self.preset = preset
        self.hw_accel = hw_accel
        self.status = "Pending"  # Pending, Queued, Compressing, Success, Failed, Cancelled
        self.progress = 0.0
        self.log_messages = []
        self.output_file_path = ""

class QueueManager:
    def __init__(self, max_workers=None):
        self.tasks = []
        self.max_workers = max_workers if max_workers is not None else (os.cpu_count() or 2)
        self.active_workers = {}  # task_id -> Thread
        self.lock = threading.Lock()
        self.is_running = False
        self.on_task_update_cb = None
        self.on_task_complete_cb = None

    def add_task(self, task):
        with self.lock:
            self.tasks.append(task)

    def remove_task(self, task_id):
        with self.lock:
            for t in self.tasks:
                if t.id == task_id:
                    if t.status in ("Pending", "Success", "Failed", "Cancelled"):
                        self.tasks.remove(t)
                        return True
            return False

    def update_task_settings(self, task_id, target_size, speed, image_scale, target_format=None, preset="ultrafast", hw_accel="Auto-Detect", naming_pattern="{filename}_compressed"):
        with self.lock:
            for t in self.tasks:
                if t.id == task_id:
                    if t.status == "Pending":
                        t.target_size = target_size
                        t.speed = speed
                        t.image_scale = image_scale
                        t.target_format = target_format
                        t.preset = preset
                        t.hw_accel = hw_accel
                        t.naming_pattern = naming_pattern
                        return True
            return False

    def start_processing(self, on_task_update_cb, on_task_complete_cb):
        self.on_task_update_cb = on_task_update_cb
        self.on_task_complete_cb = on_task_complete_cb
        self.is_running = True

    def stop_processing(self):
        self.is_running = False

    def process_queue(self):
        if not self.is_running:
            return

        with self.lock:
            # Cleanup finished workers
            finished_ids = []
            for tid, thread in self.active_workers.items():
                if not thread.is_alive():
                    finished_ids.append(tid)
            for tid in finished_ids:
                del self.active_workers[tid]

            # Spawn new workers up to max_workers
            if len(self.active_workers) < self.max_workers:
                for t in self.tasks:
                    if t.status == "Pending" and len(self.active_workers) < self.max_workers:
                        t.status = "Queued"
                        thread = threading.Thread(
                            target=self._run_worker,
                            args=(t,),
                            daemon=True
                        )
                        self.active_workers[t.id] = thread
                        thread.start()

    def _run_worker(self, task):
        task.status = "Compressing"
        task.progress = 0.0

        def log_cb(message):
            task.log_messages.append(message)
            if self.on_task_update_cb:
                self.on_task_update_cb(task.id)

        def progress_cb(prog, metrics=None):
            task.progress = prog
            if metrics:
                task.current_metrics = metrics
            if self.on_task_update_cb:
                self.on_task_update_cb(task.id)

        # Resolve custom naming pattern
        pattern = getattr(task, "naming_pattern", "{filename}_compressed")
        if not pattern:
            pattern = "{filename}_compressed"
            
        res_path = compress_file(
            task.input_path,
            task.output_dir,
            task.target_size,
            task.speed,
            task.image_scale,
            log_cb,
            progress_cb,
            target_format=task.target_format,
            preset=task.preset,
            hw_accel=task.hw_accel,
            naming_pattern=pattern
        )

        success = (res_path is not None)
        if success:
            task.output_file_path = res_path

        with self.lock:
            task.status = "Success" if success else "Failed"
            task.progress = 100.0 if success else task.progress

        if not success:
            # Trigger Self-Healing framework!
            healed = SelfHealer.self_heal_failed_task(task, log_cb)
            if healed:
                if self.on_task_update_cb:
                    self.on_task_update_cb(task.id)
                return

        if self.on_task_complete_cb:
            self.on_task_complete_cb(task.id, success)

class AudioCompressorGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Media Compressor Pro v2.0")
        self.root.geometry("1220x730")
        self.root.minsize(1050, 620)
        
        # Set theme and color options
        ctk.set_appearance_mode("Dark")
        ctk.set_default_color_theme("blue")
        
        # State variables
        self.input_path_var = tk.StringVar()
        self.output_path_var = tk.StringVar(value="")
        self.size_var = tk.StringVar(value="15.0")
        self.speed_var = tk.DoubleVar(value=1.0)
        self.image_scale_var = tk.IntVar(value=100)
        self.target_format_var = tk.StringVar(value="Keep Original")
        self.video_preset_var = tk.StringVar(value="ultrafast")
        self.hw_accel_var = tk.StringVar(value="Auto-Detect")
        self.naming_pattern_var = tk.StringVar(value="{filename}_compressed")
        
        self.selected_input_files = []
        self.selected_task_id = None
        self.editing_task_id = None
        self.task_cards = {}
        
        # Queue Manager Setup
        self.queue_manager = QueueManager(max_workers=os.cpu_count() or 2)
        self.queue_manager.start_processing(self.on_task_update, self.on_task_complete)
        
        # UI Elements Creation
        self.create_widgets()
        
        # Bind traces
        self.input_path_var.trace_add("write", self.on_path_changed)
        self.output_path_var.trace_add("write", self.on_output_path_changed)
        
        # Load output folder contents and start GUI Tick Loop
        self.refresh_folder_preview()
        self.gui_tick()

    def create_widgets(self):
        # Configure global window grid weights
        self.root.grid_columnconfigure(0, weight=1)
        self.root.grid_rowconfigure(1, weight=1)
        self.root.grid_rowconfigure(2, weight=0) # For status bar
        
        # --- TOP HEADER BAR ---
        header_bar = ctk.CTkFrame(self.root, height=55, corner_radius=0, fg_color="#1a1a1a")
        header_bar.grid(row=0, column=0, sticky="ew")
        header_bar.grid_propagate(False)
        
        lbl_title = ctk.CTkLabel(header_bar, text="⚡ MEDIA COMPRESSOR PRO", font=("Segoe UI", 18, "bold"), text_color="#3498db")
        lbl_title.pack(side=tk.LEFT, padx=20)
        
        # Global Controls
        self.btn_toggle_queue = ctk.CTkButton(header_bar, text="▶ Start Queue", font=("Segoe UI", 13, "bold"), width=130, height=32, fg_color="#2ecc71", hover_color="#27ae60", text_color="#ffffff", command=self.toggle_queue_processing)
        self.btn_toggle_queue.pack(side=tk.LEFT, padx=10)
        
        btn_clear_q = ctk.CTkButton(header_bar, text="🧹 Clear Completed", font=("Segoe UI", 13, "bold"), width=150, height=32, fg_color="#7f8c8d", hover_color="#95a5a6", text_color="#ffffff", command=self.clear_completed_tasks)
        btn_clear_q.pack(side=tk.LEFT, padx=5)
        
        # --- MAIN PANEL CONTAINER ---
        container = ctk.CTkFrame(self.root, fg_color="transparent")
        container.grid(row=1, column=0, sticky="nsew", padx=10, pady=10)
        
        container.grid_columnconfigure(0, weight=3) # Left (Settings)
        container.grid_columnconfigure(1, weight=4) # Middle (Queue List)
        container.grid_columnconfigure(2, weight=4) # Right (Logs/Details/Treeview)
        container.grid_rowconfigure(0, weight=1)
        
        # --- 1. LEFT PANEL (Settings) ---
        left_panel = ctk.CTkFrame(container)
        left_panel.grid(row=0, column=0, sticky="nsew", padx=5)
        
        lbl_sec_settings = ctk.CTkLabel(left_panel, text="COMPRESSION CONFIG", font=("Segoe UI", 14, "bold"), text_color="#aaaaaa")
        lbl_sec_settings.pack(anchor="w", pady=(0, 10))
        
        # Browse input files
        lbl_input = ctk.CTkLabel(left_panel, text="Input File(s) or Folder:", font=("Segoe UI", 13, "bold"))
        lbl_input.pack(anchor="w", pady=(0, 2))
        
        input_frame = ctk.CTkFrame(left_panel, fg_color="transparent")
        input_frame.pack(fill=tk.X, pady=(0, 4))
        self.entry_input = ctk.CTkEntry(input_frame, textvariable=self.input_path_var, font=("Segoe UI", 12))
        self.entry_input.pack(side=tk.LEFT, fill=tk.X, expand=True)
        
        browse_buttons_frame = ctk.CTkFrame(left_panel, fg_color="transparent")
        browse_buttons_frame.pack(fill=tk.X, pady=(0, 8))
        btn_browse_file = ctk.CTkButton(browse_buttons_frame, text="📄 File(s)...", font=("Segoe UI", 12), width=100, height=28, command=self.browse_input)
        btn_browse_file.pack(side=tk.LEFT, padx=(0, 5))
        btn_browse_dir = ctk.CTkButton(browse_buttons_frame, text="📁 Folder...", font=("Segoe UI", 12), width=100, height=28, command=self.browse_folder)
        btn_browse_dir.pack(side=tk.LEFT)
        
        # Browse output folder
        self.lbl_output = ctk.CTkLabel(left_panel, text="Output Directory:", font=("Segoe UI", 13, "bold"))
        self.lbl_output.pack(anchor="w", pady=(0, 2))
        
        self.output_frame = ctk.CTkFrame(left_panel, fg_color="transparent")
        self.output_frame.pack(fill=tk.X, pady=(0, 10))
        self.entry_output = ctk.CTkEntry(self.output_frame, textvariable=self.output_path_var, font=("Segoe UI", 12))
        self.entry_output.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        self.btn_browse_out = ctk.CTkButton(self.output_frame, text="Browse...", font=("Segoe UI", 12), width=90, height=28, command=self.browse_output)
        self.btn_browse_out.pack(side=tk.RIGHT)
        
        # Parameter row (Size limit, target format)
        lbl_size_title = ctk.CTkLabel(left_panel, text="Target Size Limit (MB):", font=("Segoe UI", 13, "bold"))
        lbl_size_title.pack(anchor="w", pady=(0, 2))
        size_frame = ctk.CTkFrame(left_panel, fg_color="transparent")
        size_frame.pack(fill=tk.X, pady=(0, 10))
        self.entry_size = ctk.CTkEntry(size_frame, textvariable=self.size_var, width=80, font=("Segoe UI", 12))
        self.entry_size.pack(side=tk.LEFT)
        
        # Target format conversion selection
        lbl_fmt_title = ctk.CTkLabel(left_panel, text="Convert Target Format:", font=("Segoe UI", 13, "bold"))
        lbl_fmt_title.pack(anchor="w", pady=(0, 2))
        format_frame = ctk.CTkFrame(left_panel, fg_color="transparent")
        format_frame.pack(fill=tk.X, pady=(0, 10))
        self.combo_format = ctk.CTkOptionMenu(format_frame, variable=self.target_format_var, values=["Keep Original"], font=("Segoe UI", 12), width=160)
        self.combo_format.pack(side=tk.LEFT)
        
        self.lbl_conditional_status = ctk.CTkLabel(left_panel, text="", font=("Segoe UI", 11, "italic"))
        self.lbl_conditional_status.pack(anchor="w", pady=(0, 5))
        
        # Conditional Options Container Frame
        self.conditional_container = ctk.CTkFrame(left_panel, fg_color="transparent")
        self.conditional_container.pack(fill=tk.X, expand=True, pady=(0, 10))
        
        # Speed Frame (Audio/Video speed adjuster)
        self.speed_frame = ctk.CTkFrame(self.conditional_container, fg_color="#202020", corner_radius=6)
        lbl_speed = ctk.CTkLabel(self.speed_frame, text="Playback Speed:", font=("Segoe UI", 13, "bold"))
        lbl_speed.pack(anchor="w")
        self.speed_slider = ctk.CTkSlider(self.speed_frame, from_=0.5, to=3.0, number_of_steps=25, variable=self.speed_var, command=self.update_speed_preview)
        self.speed_slider.pack(fill=tk.X, expand=True, pady=5)
        
        lbl_speed_val_frame = ctk.CTkFrame(self.speed_frame, fg_color="transparent")
        lbl_speed_val_frame.pack(fill=tk.X)
        self.lbl_speed_val = ctk.CTkLabel(lbl_speed_val_frame, text="1.0x", font=("Segoe UI", 12, "bold"))
        self.lbl_speed_val.pack(side=tk.LEFT)
        self.lbl_speed_preview = ctk.CTkLabel(lbl_speed_val_frame, text="Duration: 00:00 ➔ 00:00", font=("Segoe UI", 11, "italic"), text_color="#aaaaaa")
        self.lbl_speed_preview.pack(side=tk.RIGHT)
        
        # Image Resize Frame (Image dimensions scaling)
        self.image_resize_frame = ctk.CTkFrame(self.conditional_container, fg_color="#202020", corner_radius=6)
        lbl_scale = ctk.CTkLabel(self.image_resize_frame, text="Resize Scale (Dimensions):", font=("Segoe UI", 13, "bold"))
        lbl_scale.pack(anchor="w")
        self.image_slider = ctk.CTkSlider(self.image_resize_frame, from_=10, to=100, number_of_steps=90, variable=self.image_scale_var, command=self.update_image_preview)
        self.image_slider.pack(fill=tk.X, expand=True, pady=5)
        
        lbl_image_val_frame = ctk.CTkFrame(self.image_resize_frame, fg_color="transparent")
        lbl_image_val_frame.pack(fill=tk.X)
        self.lbl_image_scale_val = ctk.CTkLabel(lbl_image_val_frame, text="100%", font=("Segoe UI", 12, "bold"))
        self.lbl_image_scale_val.pack(side=tk.LEFT)
        self.lbl_image_preview = ctk.CTkLabel(lbl_image_val_frame, text="Resolution: 0x0 ➔ 0x0 px", font=("Segoe UI", 11, "italic"), text_color="#aaaaaa")
        self.lbl_image_preview.pack(side=tk.RIGHT)
        
        # Action Buttons
        self.btn_action = ctk.CTkButton(left_panel, text="➕ Add to Queue", font=("Segoe UI", 14, "bold"), fg_color="#1f538d", hover_color="#143d66", height=35, command=self.add_to_queue)
        self.btn_action.pack(side=tk.BOTTOM, fill=tk.X, pady=5)
        
        self.btn_cancel_edit = ctk.CTkButton(left_panel, text="Cancel Edit", font=("Segoe UI", 12), fg_color="#555555", hover_color="#444444", height=25, command=self.cancel_edit)
        
        # --- 2. MIDDLE PANEL (Active Queue) ---
        mid_panel = ctk.CTkFrame(container)
        mid_panel.grid(row=0, column=1, sticky="nsew", padx=5)
        
        lbl_sec_queue = ctk.CTkLabel(mid_panel, text="PROCESSING QUEUE", font=("Segoe UI", 14, "bold"), text_color="#aaaaaa")
        lbl_sec_queue.pack(anchor="w", pady=(0, 10))
        
        # Buttons row for Queue CRUD actions
        queue_actions_frame = ctk.CTkFrame(mid_panel, fg_color="transparent")
        queue_actions_frame.pack(fill=tk.X, pady=(0, 5))
        
        self.btn_edit_task = ctk.CTkButton(queue_actions_frame, text="✏️ Edit Selected", font=("Segoe UI", 11, "bold"), width=110, height=28, fg_color="#1f538d", hover_color="#143d66", command=self.edit_selected_task)
        self.btn_edit_task.pack(side=tk.LEFT, padx=(0, 5))
        
        self.btn_delete_task = ctk.CTkButton(queue_actions_frame, text="🗑️ Delete Selected", font=("Segoe UI", 11, "bold"), width=120, height=28, fg_color="#e74c3c", hover_color="#c0392b", command=self.delete_selected_task)
        self.btn_delete_task.pack(side=tk.LEFT)

        # Embed and style Treeview for queue list (spreadsheet style)
        self.queue_tree = ttk.Treeview(
            mid_panel, columns=("SNo", "FileName", "Status"),
            show="headings", selectmode="browse"
        )
        self.queue_tree.pack(fill=tk.BOTH, expand=True)
        
        # Configure columns
        self.queue_tree.heading("SNo", text="S.No.", anchor=tk.CENTER)
        self.queue_tree.heading("FileName", text="File Name", anchor=tk.W)
        self.queue_tree.heading("Status", text="Status", anchor=tk.CENTER)
        
        self.queue_tree.column("SNo", width=50, minwidth=40, stretch=tk.FALSE, anchor=tk.CENTER)
        self.queue_tree.column("FileName", width=250, minwidth=150, stretch=tk.TRUE)
        self.queue_tree.column("Status", width=130, minwidth=100, stretch=tk.FALSE, anchor=tk.CENTER)
        
        self.queue_tree.bind("<<TreeviewSelect>>", self.on_queue_tree_select)
        self.queue_tree.bind("<Delete>", self.on_queue_tree_delete)
        
        # --- 3. RIGHT PANEL (Task Details, Logs & Directory Listing) ---
        right_panel = ctk.CTkFrame(container)
        right_panel.grid(row=0, column=2, sticky="nsew", padx=5)
        
        # Tabview for details/logs vs directory preview
        self.tabview = ctk.CTkTabview(right_panel, fg_color="transparent")
        self.tabview.pack(fill=tk.BOTH, expand=True)
        
        tab_task = self.tabview.add("Task Info & Logs")
        tab_folder = self.tabview.add("Output Folder")
        
        # TAB 1: Task Details & Logs
        tab_task.grid_rowconfigure(2, weight=1)
        tab_task.grid_columnconfigure(0, weight=1)
        
        self.lbl_preview_info = ctk.CTkLabel(tab_task, text="Select a task in the queue to view details", font=("Segoe UI", 13), justify=tk.LEFT, anchor="w")
        self.lbl_preview_info.grid(row=0, column=0, sticky="ew", pady=(0, 5))
        
        self.lbl_preview_img = ctk.CTkLabel(tab_task, text="", anchor=tk.CENTER)
        self.lbl_preview_img.grid(row=1, column=0, sticky="ew", pady=5)
        
        lbl_logs = ctk.CTkLabel(tab_task, text="Live Output Logs:", font=("Segoe UI", 12, "bold"), text_color="#aaaaaa")
        lbl_logs.grid(row=2, column=0, sticky="w", pady=(5, 0))
        
        self.log_textbox = ctk.CTkTextbox(tab_task, font=("Consolas", 11), state=tk.DISABLED, wrap="none")
        self.log_textbox.grid(row=3, column=0, sticky="nsew", pady=(5, 0))
        
        # TAB 2: Output Folder Preview
        tab_folder.grid_rowconfigure(0, weight=1)
        tab_folder.grid_columnconfigure(0, weight=1)
        
        folder_frame = ctk.CTkFrame(tab_folder, fg_color="transparent")
        folder_frame.grid(row=0, column=0, sticky="nsew")
        
        # Embed and style Treeview
        tree_scroll = ttk.Scrollbar(folder_frame)
        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        
        self.style = ttk.Style()
        self.style.theme_use("clam")
        self.style.configure("Treeview", 
            background="#2d2d2d",
            foreground="#ffffff",
            fieldbackground="#2d2d2d",
            bordercolor="#1e1e1e",
            borderwidth=0,
            rowheight=30,
            font=("Segoe UI", 11)
        )
        self.style.map("Treeview", background=[("selected", "#1f538d")])
        self.style.configure("Treeview.Heading", 
            background="#1a1a1a",
            foreground="#ffffff",
            relief="flat",
            font=("Segoe UI", 11, "bold")
        )
        self.style.map("Treeview.Heading", background=[("active", "#2a2a2a")])
        
        self.tree_folder = ttk.Treeview(
            folder_frame, columns=("Size", "Modified"),
            yscrollcommand=tree_scroll.set
        )
        self.tree_folder.pack(fill=tk.BOTH, expand=True)
        tree_scroll.config(command=self.tree_folder.yview)
        
        self.tree_folder.heading("#0", text="File Name", anchor=tk.W)
        self.tree_folder.heading("Size", text="Size", anchor=tk.W)
        self.tree_folder.heading("Modified", text="Modified", anchor=tk.W)
        
        self.tree_folder.column("#0", width=180, minwidth=120)
        self.tree_folder.column("Size", width=80, minwidth=60, stretch=tk.FALSE)
        self.tree_folder.column("Modified", width=110, minwidth=80, stretch=tk.FALSE)
        
        self.tree_folder.bind("<Double-1>", self.on_tree_double_click)
        
        # --- BOTTOM STATUS BAR ---
        self.status_bar = ctk.CTkFrame(self.root, height=25, corner_radius=0, fg_color="#1a1a1a")
        self.status_bar.grid(row=2, column=0, sticky="ew")
        
        self.lbl_status_bar = ctk.CTkLabel(self.status_bar, text="Queue Status: Idle | Total Tasks: 0 | Completed: 0 | Failed: 0 | Active Workers: 0", font=("Segoe UI", 12))
        self.lbl_status_bar.pack(side=tk.LEFT, padx=20)

    def update_format_choices(self, ext):
        ext_lower = ext.lower()
        audio_exts = ('.mp3', '.m4a', '.wav', '.flac', '.ogg', '.aac', '.wma')
        video_exts = ('.mp4', '.mkv', '.avi', '.mov', '.webm', '.flv', '.wmv')
        image_exts = ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.tiff')
        
        if ext_lower in video_exts:
            choices = ["Keep Original", "MP4", "WebM", "MKV", "AVI", "MOV", "FLV"]
        elif ext_lower in audio_exts:
            choices = ["Keep Original", "MP3", "M4A", "WAV", "FLAC", "OGG", "AAC"]
        elif ext_lower in image_exts:
            choices = ["Keep Original", "JPEG", "PNG", "WebP", "BMP", "TIFF", "GIF"]
        else:
            choices = ["Keep Original"]
            
        self.combo_format.configure(values=choices)
        self.target_format_var.set("Keep Original")

    def browse_input(self):
        file_types = [
            ("All supported files", "*.mp3 *.m4a *.wav *.flac *.ogg *.aac *.wma *.mp4 *.mkv *.avi *.mov *.webm *.flv *.wmv *.jpg *.jpeg *.png *.gif *.webp *.bmp *.tiff *.pdf *.docx *.pptx *.xlsx *.zip"),
            ("Audio files", "*.mp3 *.m4a *.wav *.flac *.ogg *.aac *.wma"),
            ("Video files", "*.mp4 *.mkv *.avi *.mov *.webm *.flv *.wmv"),
            ("Image files", "*.jpg *.jpeg *.png *.gif *.webp *.bmp *.tiff"),
            ("PDF Documents", "*.pdf"),
            ("Office Documents", "*.docx *.pptx *.xlsx"),
            ("Zip Archives", "*.zip"),
            ("All files", "*.*")
        ]
        file_paths = filedialog.askopenfilenames(filetypes=file_types)
        if file_paths:
            self.selected_input_files = list(file_paths)
            self.input_path_var.set(self.selected_input_files[0])
            if len(self.selected_input_files) > 1:
                self.lbl_conditional_status.configure(text=f"{len(self.selected_input_files)} files selected", text_color="#2ecc71")
            else:
                self.lbl_conditional_status.configure(text="")
                self.on_path_changed()

    def browse_folder(self):
        dir_path = filedialog.askdirectory()
        if dir_path:
            self.selected_input_files = []
            compressible_exts = (
                '.mp3', '.m4a', '.wav', '.flac', '.ogg', '.aac', '.wma',
                '.mp4', '.mkv', '.avi', '.mov', '.webm', '.flv', '.wmv',
                '.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.tiff',
                '.pdf', '.docx', '.pptx', '.xlsx', '.zip'
            )
            try:
                for root, _, files in os.walk(dir_path):
                    for file in files:
                        _, ext = os.path.splitext(file.lower())
                        if ext in compressible_exts:
                            self.selected_input_files.append(os.path.join(root, file))
            except Exception as e:
                messagebox.showerror("Error", f"Error scanning directory: {e}")
                return
                
            if self.selected_input_files:
                self.input_path_var.set(dir_path)
                self.lbl_conditional_status.configure(text=f"{len(self.selected_input_files)} files found in folder", text_color="#2ecc71")
                self.hide_all_conditional_frames()
            else:
                self.input_path_var.set("")
                self.lbl_conditional_status.configure(text="No supported media files found in folder", text_color="#e74c3c")

    def browse_output(self):
        dir_path = filedialog.askdirectory()
        if dir_path:
            self.output_path_var.set(dir_path)

    def update_speed_preview(self, val=None):
        try:
            speed = float(self.speed_var.get())
        except ValueError:
            speed = 1.0
        self.lbl_speed_val.configure(text=f"{speed:.1f}x")
        
        if hasattr(self, "original_duration"):
            new_duration = self.original_duration / speed
            orig_str = format_duration(self.original_duration)
            new_str = format_duration(new_duration)
            self.lbl_speed_preview.configure(text=f"Duration: {orig_str} ➔ {new_str}")

    def update_image_preview(self, val=None):
        try:
            scale_percent = int(self.image_scale_var.get())
        except ValueError:
            scale_percent = 100
        self.lbl_image_scale_val.configure(text=f"{scale_percent}%")
        
        if hasattr(self, "original_width") and hasattr(self, "original_height"):
            new_w = int(self.original_width * (scale_percent / 100.0))
            new_h = int(self.original_height * (scale_percent / 100.0))
            self.lbl_image_preview.configure(text=f"Resolution: {self.original_width}×{self.original_height} ➔ {new_w}×{new_h} px")

    def on_path_changed(self, *args):
        file_path = self.input_path_var.get().strip()
        if file_path:
            if os.path.isdir(file_path):
                self.output_path_var.set(file_path)
            elif os.path.isfile(file_path):
                self.output_path_var.set(os.path.dirname(file_path))
        if len(self.selected_input_files) <= 1:
            self.start_probing(file_path)

    def on_output_path_changed(self, *args):
        self.refresh_folder_preview()

    def start_probing(self, file_path):
        if not file_path or not os.path.isfile(file_path):
            self.hide_all_conditional_frames()
            return
        self.lbl_conditional_status.configure(text="Probing file...", text_color="#3498db")
        threading.Thread(target=self.probe_metadata_thread, args=(file_path,), daemon=True).start()

    def probe_metadata_thread(self, file_path):
        _, ext = os.path.splitext(file_path.lower())
        
        audio_exts = ('.mp3', '.m4a', '.wav', '.flac', '.ogg', '.aac', '.wma')
        video_exts = ('.mp4', '.mkv', '.avi', '.mov', '.webm', '.flv', '.wmv')
        image_exts = ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.tiff')
        
        if ext in audio_exts or ext in video_exts:
            ffmpeg_path, ffprobe_path = find_ffmpeg_tools()
            try:
                duration = get_audio_duration(ffprobe_path, file_path)
                self.root.after(0, self.setup_audio_video_ui, duration)
            except Exception as e:
                self.root.after(0, self.log_probing_error, f"Probe error: {e}")
        elif ext in image_exts:
            try:
                with Image.open(file_path) as img:
                    w, h = img.size
                self.root.after(0, self.setup_image_ui, w, h)
            except Exception as e:
                self.root.after(0, self.log_probing_error, f"Probe error: {e}")
        else:
            self.root.after(0, self.hide_all_conditional_frames)

    def hide_all_conditional_frames(self):
        self.speed_frame.pack_forget()
        self.image_resize_frame.pack_forget()
        self.lbl_conditional_status.configure(text="")
        self.combo_format.configure(values=["Keep Original"])
        self.target_format_var.set("Keep Original")
        
        if hasattr(self, "original_duration"):
            delattr(self, "original_duration")
        if hasattr(self, "original_width"):
            delattr(self, "original_width")
        
        # If showing single file preview
        if not self.selected_task_id and self.input_path_var.get().strip():
            self.update_file_preview(self.input_path_var.get().strip())

    def log_probing_error(self, err_msg):
        self.hide_all_conditional_frames()
        self.lbl_conditional_status.configure(text=err_msg, text_color="#e74c3c")

    def setup_audio_video_ui(self, duration):
        self.hide_all_conditional_frames()
        self.original_duration = duration
        self.speed_frame.pack(fill=tk.X, expand=True, pady=5)
        self.update_speed_preview()
        
        # Dynamic format updates
        file_path = self.input_path_var.get().strip()
        _, ext = os.path.splitext(file_path.lower())
        self.update_format_choices(ext)
        
        if not self.selected_task_id:
            self.update_file_preview(self.input_path_var.get().strip())

    def setup_image_ui(self, w, h):
        self.hide_all_conditional_frames()
        self.original_width = w
        self.original_height = h
        self.image_resize_frame.pack(fill=tk.X, expand=True, pady=5)
        self.update_image_preview()
        
        # Dynamic format updates
        file_path = self.input_path_var.get().strip()
        _, ext = os.path.splitext(file_path.lower())
        self.update_format_choices(ext)
        
        if not self.selected_task_id:
            self.update_file_preview(self.input_path_var.get().strip())

    def toggle_theme(self):
        if self.switch_theme.get() == 1:
            ctk.set_appearance_mode("Light")
            self.style.configure("Treeview", background="#f0f0f0", foreground="#000000", fieldbackground="#f0f0f0")
            self.style.configure("Treeview.Heading", background="#e0e0e0", foreground="#000000")
        else:
            ctk.set_appearance_mode("Dark")
            self.style.configure("Treeview", background="#2d2d2d", foreground="#ffffff", fieldbackground="#2d2d2d")
            self.style.configure("Treeview.Heading", background="#1a1a1a", foreground="#ffffff")

    def change_max_threads(self, val):
        try:
            threads = int(val)
            self.queue_manager.max_workers = threads
        except ValueError:
            pass

    def refresh_folder_preview(self):
        for item in self.tree_folder.get_children():
            self.tree_folder.delete(item)
            
        output_dir = self.output_path_var.get().strip()
        if not output_dir or not os.path.isdir(output_dir):
            return
            
        try:
            for entry in os.scandir(output_dir):
                if entry.is_file():
                    stat = entry.stat()
                    size_mb = stat.st_size / (1024 * 1024)
                    mtime = datetime.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
                    ext = os.path.splitext(entry.name)[1].upper()
                    self.tree_folder.insert(
                        "", tk.END,
                        text=entry.name,
                        values=(f"{size_mb:.2f} MB", mtime, ext)
                    )
        except Exception:
            pass

    def update_file_preview(self, file_path):
        self.lbl_preview_img.configure(image="", text="")
        self.lbl_preview_img.image = None
        self.lbl_preview_info.configure(text="No file selected")
        
        if not file_path or not os.path.isfile(file_path):
            return
            
        filename = os.path.basename(file_path)
        size_mb = os.path.getsize(file_path) / (1024 * 1024)
        _, ext = os.path.splitext(file_path.lower())
        
        info_text = f"File: {filename}\nSize: {size_mb:.2f} MB\n"
        
        audio_exts = ('.mp3', '.m4a', '.wav', '.flac', '.ogg', '.aac', '.wma')
        video_exts = ('.mp4', '.mkv', '.avi', '.mov', '.webm', '.flv', '.wmv')
        image_exts = ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.tiff')
        
        if ext in image_exts:
            try:
                img = Image.open(file_path)
                w, h = img.size
                info_text += f"Type: Image ({img.format})\nResolution: {w}×{h} px"
                
                img_copy = img.copy()
                img_copy.thumbnail((160, 160), Image.Resampling.LANCZOS)
                photo = ImageTk.PhotoImage(img_copy)
                self.lbl_preview_img.configure(image=photo)
                self.lbl_preview_img.image = photo
            except Exception as e:
                info_text += f"\nImage error: {e}"
        elif ext in audio_exts or ext in video_exts:
            dur_str = "Unknown"
            if hasattr(self, "original_duration"):
                dur_str = format_duration(self.original_duration)
            info_text += f"Type: {'Audio' if ext in audio_exts else 'Video'}\nDuration: {dur_str}"
        elif ext == '.pdf':
            try:
                reader = PdfReader(file_path)
                info_text += f"Type: PDF Document\nPages: {len(reader.pages)}"
            except Exception:
                info_text += "Type: PDF Document"
        elif ext in ('.docx', '.pptx', '.xlsx'):
            doc_type = 'Word Document' if ext == '.docx' else 'Powerpoint' if ext == '.pptx' else 'Excel Sheet'
            info_text += f"Type: Office {doc_type}"
        elif ext == '.zip':
            try:
                with zipfile.ZipFile(file_path, 'r') as z:
                    num_files = len(z.namelist())
                info_text += f"Type: ZIP Archive\nContains: {num_files} elements"
            except Exception:
                info_text += "Type: ZIP Archive"
        else:
            info_text += f"Type: {ext.upper()[1:]} File"
            
        self.lbl_preview_info.configure(text=info_text)

    def on_tree_double_click(self, event):
        selected_item = self.tree_folder.selection()
        if not selected_item:
            return
        filename = self.tree_folder.item(selected_item[0], "text")
        output_dir = self.output_path_var.get().strip()
        if os.path.isdir(output_dir):
            file_path = os.path.join(output_dir, filename)
            if os.path.exists(file_path):
                path = os.path.normpath(file_path)
                try:
                    subprocess.Popen(f'explorer /select,"{path}"')
                except Exception as e:
                    messagebox.showerror("Error", f"Failed to open location: {e}")

    # --- QUEUE CRUD OPERATIONS ---
    
    def add_to_queue(self):
        # Check if we are currently editing a task
        if self.editing_task_id:
            self.save_edited_task()
            return
            
        input_file = self.input_path_var.get().strip()
        output_dir = self.output_path_var.get().strip()
        size_str = self.size_var.get().strip()
        
        if not input_file:
            messagebox.showerror("Error", "Please select an input file.")
            return
            
        if not output_dir:
            messagebox.showerror("Error", "Please select an output directory.")
            return
            
        try:
            max_size_mb = float(size_str)
            if max_size_mb <= 0:
                raise ValueError()
        except ValueError:
            messagebox.showerror("Error", "Please enter a valid positive number for target size.")
            return
            
        # Determine settings
        speed = 1.0
        image_scale = 1.0
        target_format = self.target_format_var.get()
        video_preset = self.video_preset_var.get()
        hw_accel_raw = self.hw_accel_var.get()
        hw_accel = hw_accel_raw.split(" (")[0] if " (" in hw_accel_raw else hw_accel_raw
        naming_pattern = self.naming_pattern_var.get()
        
        files_to_add = self.selected_input_files if self.selected_input_files else [input_file]
        
        for file_path in files_to_add:
            task_output_dir = output_dir
            
            _, ext = os.path.splitext(file_path.lower())
            audio_exts = ('.mp3', '.m4a', '.wav', '.flac', '.ogg', '.aac', '.wma')
            video_exts = ('.mp4', '.mkv', '.avi', '.mov', '.webm', '.flv', '.wmv')
            image_exts = ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.tiff')
            
            if ext in audio_exts or ext in video_exts:
                try:
                    speed = float(self.speed_var.get())
                except ValueError:
                    pass
            elif ext in image_exts:
                try:
                    image_scale = int(self.image_scale_var.get()) / 100.0
                except ValueError:
                    pass
                    
            task = CompressionTask(file_path, task_output_dir, max_size_mb, speed, image_scale, target_format, video_preset, hw_accel, naming_pattern)
            self.queue_manager.add_task(task)
            
        # Reset input selection
        self.selected_input_files = []
        self.input_path_var.set("")
        self.lbl_conditional_status.configure(text="")
        
        self.refresh_queue_ui()

    def edit_task(self, task_id):
        task = next((t for t in self.queue_manager.tasks if t.id == task_id), None)
        if not task or task.status != "Pending":
            return
            
        self.editing_task_id = task_id
        
        # Load fields
        self.input_path_var.set(task.input_path)
        self.output_path_var.set(task.output_dir)
        self.size_var.set(str(task.target_size))
        self.target_format_var.set(task.target_format if task.target_format else "Keep Original")
        self.video_preset_var.set(task.preset if task.preset else "ultrafast")
        self.naming_pattern_var.set(task.naming_pattern if getattr(task, "naming_pattern", None) else "{filename}_compressed")
        self.hw_accel_var.set(task.hw_accel if task.hw_accel else "Auto-Detect")
        
        _, ext = os.path.splitext(task.input_path.lower())
        self.update_format_choices(ext)
        if task.target_format:
            self.target_format_var.set(task.target_format)
            
        audio_exts = ('.mp3', '.m4a', '.wav', '.flac', '.ogg', '.aac', '.wma')
        video_exts = ('.mp4', '.mkv', '.avi', '.mov', '.webm', '.flv', '.wmv')
        image_exts = ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.tiff')
        
        if ext in audio_exts or ext in video_exts:
            self.speed_var.set(task.speed)
            self.setup_audio_video_ui(task.speed) 
        elif ext in image_exts:
            self.image_scale_var.set(int(task.image_scale * 100))
            self.setup_image_ui(100, 100) 
            
        # Change UI Button to save mode
        self.btn_action.configure(text="💾 Save Task Settings", fg_color="#e67e22", hover_color="#d35400")
        self.btn_cancel_edit.pack(side=tk.BOTTOM, fill=tk.X, pady=(0, 5))

    def save_edited_task(self):
        size_str = self.size_var.get().strip()
        try:
            max_size_mb = float(size_str)
            if max_size_mb <= 0:
                raise ValueError()
        except ValueError:
            messagebox.showerror("Error", "Please enter a valid positive number for target size.")
            return
            
        speed = 1.0
        image_scale = 1.0
        target_format = self.target_format_var.get()
        video_preset = self.video_preset_var.get()
        hw_accel_raw = self.hw_accel_var.get()
        hw_accel = hw_accel_raw.split(" (")[0] if " (" in hw_accel_raw else hw_accel_raw
        naming_pattern = self.naming_pattern_var.get()
        
        _, ext = os.path.splitext(self.input_path_var.get().lower())
        audio_exts = ('.mp3', '.m4a', '.wav', '.flac', '.ogg', '.aac', '.wma')
        video_exts = ('.mp4', '.mkv', '.avi', '.mov', '.webm', '.flv', '.wmv')
        image_exts = ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.tiff')
        
        if ext in audio_exts or ext in video_exts:
            speed = float(self.speed_var.get())
        elif ext in image_exts:
            image_scale = int(self.image_scale_var.get()) / 100.0
            
        success = self.queue_manager.update_task_settings(self.editing_task_id, max_size_mb, speed, image_scale, target_format, video_preset, hw_accel, naming_pattern)
        if success:
            self.cancel_edit()
            self.refresh_queue_ui()
        else:
            messagebox.showerror("Error", "Failed to update task settings. Is it still Pending?")

    def cancel_edit(self):
        self.editing_task_id = None
        self.input_path_var.set("")
        self.btn_action.configure(text="➕ Add to Queue", fg_color="#1f538d", hover_color="#143d66")
        self.btn_cancel_edit.pack_forget()
        self.hide_all_conditional_frames()

    def delete_task(self, task_id):
        self.queue_manager.remove_task(task_id)
        if self.selected_task_id == task_id:
            self.selected_task_id = None
            self.lbl_preview_info.configure(text="Select a task in the queue to view details")
            self.log_textbox.configure(state=tk.NORMAL)
            self.log_textbox.delete("1.0", tk.END)
            self.log_textbox.configure(state=tk.DISABLED)
            
        self.refresh_queue_ui()

    def clear_completed_tasks(self):
        completed_ids = [t.id for t in self.queue_manager.tasks if t.status in ("Success", "Failed", "Cancelled")]
        for tid in completed_ids:
            self.queue_manager.remove_task(tid)
            if self.selected_task_id == tid:
                self.selected_task_id = None
        self.refresh_queue_ui()

    def start_queue_processing(self):
        self.queue_manager.is_running = True

    def stop_queue_processing(self):
        self.queue_manager.is_running = False

    def select_task(self, task_id):
        self.selected_task_id = task_id
        
        # Redraw queue cards to highlight selected
        self.refresh_queue_ui()
        
        # Load details
        task = next((t for t in self.queue_manager.tasks if t.id == task_id), None)
        if task:
            filename = os.path.basename(task.input_path)
            size_mb = os.path.getsize(task.input_path) / (1024 * 1024)
            info_text = f"File: {filename}\nOriginal Size: {size_mb:.2f} MB\nTarget Limit: {task.target_size} MB\n"
            info_text += f"Target Format: {task.target_format}\nStatus: {task.status.upper()}"
            self.lbl_preview_info.configure(text=info_text)
            
            # Show image thumbnail if image
            self.lbl_preview_img.configure(image="")
            self.lbl_preview_img.image = None
            _, ext = os.path.splitext(task.input_path.lower())
            if ext in ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.tiff'):
                try:
                    img = Image.open(task.input_path)
                    img_copy = img.copy()
                    img_copy.thumbnail((160, 160), Image.Resampling.LANCZOS)
                    photo = ImageTk.PhotoImage(img_copy)
                    self.lbl_preview_img.configure(image=photo)
                    self.lbl_preview_img.image = photo
                except Exception:
                    pass
                    
            # Switch view tab to logs
            self.tabview.set("Task Info & Logs")
            
            # Populate logs immediately
            self.show_task_details_and_logs(task)

    def refresh_queue_ui(self):
        # Clear all rows in tree
        for item in self.queue_tree.get_children():
            self.queue_tree.delete(item)
            
        status_map = {
            "Pending": "Not processed",
            "Queued": "Not processed",
            "Compressing": "Processing",
            "Success": "Finished",
            "Failed": "Failed",
            "Cancelled": "Failed"
        }
        
        for idx, task in enumerate(self.queue_manager.tasks, start=1):
            fname = os.path.basename(task.input_path)
            status_val = status_map.get(task.status, task.status)
            if task.status == "Compressing":
                status_val = f"Processing ({task.progress:.0f}%)"
                
            self.queue_tree.insert(
                "", tk.END, iid=task.id,
                values=(idx, fname, status_val)
            )
            
        if self.selected_task_id and self.queue_tree.exists(self.selected_task_id):
            self.queue_tree.selection_set(self.selected_task_id)

    # --- THREAD WORKER CALLBACKS ---
    
    def on_task_update(self, task_id):
        self.root.after(0, self._on_task_update_main_thread, task_id)

    def _on_task_update_main_thread(self, task_id):
        task = next((t for t in self.queue_manager.tasks if t.id == task_id), None)
        if task:
            status_map = {
                "Pending": "Not processed",
                "Queued": "Not processed",
                "Compressing": f"Processing ({task.progress:.0f}%)",
                "Success": "Finished",
                "Failed": "Failed",
                "Cancelled": "Failed"
            }
            mapped_status = status_map.get(task.status, task.status)
            
            if self.queue_tree.exists(task_id):
                old_values = self.queue_tree.item(task_id, "values")
                if old_values:
                    self.queue_tree.item(task_id, values=(old_values[0], old_values[1], mapped_status))
            
            # If selected, show logs
            if self.selected_task_id == task_id:
                self.show_task_details_and_logs(task)

    def on_task_complete(self, task_id, success):
        self.root.after(0, self._on_task_complete_main_thread, task_id, success)

    def _on_task_complete_main_thread(self, task_id, success):
        self.refresh_folder_preview()
        self._on_task_update_main_thread(task_id)
        
        task = next((t for t in self.queue_manager.tasks if t.id == task_id), None)
        if task:
            # AUTO-OPEN behavior: open output folder and highlight output file
            if success and task.output_file_path and os.path.exists(task.output_file_path):
                path = os.path.normpath(task.output_file_path)
                try:
                    subprocess.Popen(f'explorer /select,"{path}"')
                except Exception:
                    pass

    def on_queue_tree_select(self, event):
        selected_items = self.queue_tree.selection()
        if not selected_items:
            return
        
        task_id = selected_items[0]
        self.selected_task_id = task_id
        
        task = next((t for t in self.queue_manager.tasks if t.id == task_id), None)
        if task:
            filename = os.path.basename(task.input_path)
            size_mb = os.path.getsize(task.input_path) / (1024 * 1024)
            info_text = f"File: {filename}\nOriginal Size: {size_mb:.2f} MB\nTarget Limit: {task.target_size} MB\n"
            info_text += f"Target Format: {task.target_format}\nStatus: {task.status.upper()}"
            self.lbl_preview_info.configure(text=info_text)
            
            self.lbl_preview_img.configure(image="")
            self.lbl_preview_img.image = None
            _, ext = os.path.splitext(task.input_path.lower())
            if ext in ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.tiff'):
                try:
                    img = Image.open(task.input_path)
                    img_copy = img.copy()
                    img_copy.thumbnail((160, 160), Image.Resampling.LANCZOS)
                    photo = ImageTk.PhotoImage(img_copy)
                    self.lbl_preview_img.configure(image=photo)
                    self.lbl_preview_img.image = photo
                except Exception:
                    pass
            
            self.tabview.set("Task Info & Logs")
            self.show_task_details_and_logs(task)

    def edit_selected_task(self):
        selected_items = self.queue_tree.selection()
        if not selected_items:
            messagebox.showwarning("Warning", "Please select a task to edit.")
            return
        task_id = selected_items[0]
        self.edit_task(task_id)

    def delete_selected_task(self):
        selected_items = self.queue_tree.selection()
        if not selected_items:
            messagebox.showwarning("Warning", "Please select a task to delete.")
            return
        task_id = selected_items[0]
        self.delete_task(task_id)

    def on_queue_tree_delete(self, event):
        self.delete_selected_task()

    def toggle_queue_processing(self):
        if self.queue_manager.is_running:
            self.stop_queue_processing()
        else:
            self.start_queue_processing()

    def start_queue_processing(self):
        self.queue_manager.is_running = True
        if hasattr(self, "btn_toggle_queue"):
            self.btn_toggle_queue.configure(text="🛑 Cancel Queue", fg_color="#e74c3c", hover_color="#c0392b")

    def stop_queue_processing(self):
        self.queue_manager.is_running = False
        if hasattr(self, "btn_toggle_queue"):
            self.btn_toggle_queue.configure(text="▶ Start Queue", fg_color="#2ecc71", hover_color="#27ae60")

    def show_task_details_and_logs(self, task):
        logs_text = "\n".join(task.log_messages)
        self.log_textbox.configure(state=tk.NORMAL)
        current_text = self.log_textbox.get("1.0", tk.END).strip()
        if current_text != logs_text.strip():
            self.log_textbox.delete("1.0", tk.END)
            self.log_textbox.insert(tk.END, logs_text)
            self.log_textbox.see(tk.END)
        self.log_textbox.configure(state=tk.DISABLED)

    def gui_tick(self):
        # Run process queue inside manager
        self.queue_manager.process_queue()
        
        # Update bottom status bar with metrics
        total = len(self.queue_manager.tasks)
        completed = len([t for t in self.queue_manager.tasks if t.status == "Success"])
        failed = len([t for t in self.queue_manager.tasks if t.status == "Failed"])
        compressing = len([t for t in self.queue_manager.tasks if t.status == "Compressing"])
        queued = len([t for t in self.queue_manager.tasks if t.status == "Queued"])
        pending = len([t for t in self.queue_manager.tasks if t.status == "Pending"])
        active = len(self.queue_manager.active_workers)
        status_text = "Idle" if not self.queue_manager.is_running else "Running"
        
        # Auto-reset queue processing button when all tasks complete
        if self.queue_manager.is_running and compressing == 0 and queued == 0 and pending == 0:
            self.stop_queue_processing()
            status_text = "Idle"
            
        bar_text = f"Queue Status: {status_text} | Total Tasks: {total} | Pending: {pending} | Queued: {queued} | Compressing: {compressing} | Success: {completed} | Failed: {failed} | Active Workers: {active}"
        self.lbl_status_bar.configure(text=bar_text)
        
        # Periodic update of queue counts/thread states
        self.root.after(100, self.gui_tick)

def main():
    if len(sys.argv) > 1:
        # CLI Mode
        import argparse
        parser = argparse.ArgumentParser(description="Media Compressor CLI Tool")
        parser.add_argument("input_file", help="Path to input file")
        parser.add_argument("output_dir", nargs="?", default=r"c:\Dev\tools\Compress\DONE", help="Destination folder")
        parser.add_argument("target_size", type=float, nargs="?", default=15.0, help="Target MB size limit")
        parser.add_argument("-s", "--speed", type=float, default=1.0, help="Speed multiplier (0.5x to 3.0x) for audio/video")
        parser.add_argument("-r", "--resize", type=float, default=1.0, help="Image resize scale factor (0.1 to 1.0)")
        parser.add_argument("-f", "--format", default=None, help="Target conversion format (e.g. mp4, webm, mp3, png, webp)")
        parser.add_argument("-p", "--preset", default="ultrafast", help="Encoding speed preset (ultrafast, superfast, veryfast, faster, fast, medium, slow)")
        parser.add_argument("-a", "--accel", default="Auto-Detect", choices=["Auto-Detect", "CPU", "Nvidia NVENC", "AMD AMF", "Intel QSV", "Windows MediaFoundation"], help="Hardware acceleration encoder to use")
        
        args = parser.parse_args()
        
        success = compress_file(
            args.input_file,
            args.output_dir,
            args.target_size,
            args.speed,
            args.resize,
            target_format=args.format,
            preset=args.preset,
            hw_accel=args.accel
        )
        sys.exit(0 if success else 1)
    else:
        # GUI Mode using CustomTkinter
        root = ctk.CTk()
        app = AudioCompressorGUI(root)
        root.mainloop()

if __name__ == "__main__":
    main()
