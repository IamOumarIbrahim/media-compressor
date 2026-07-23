# ⚡ Media & File Compressor for Discord

[![Python](https://img.shields.io/badge/Python-3.8+-blue.svg?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![FFmpeg](https://img.shields.io/badge/Dependency-FFmpeg-green.svg?style=flat-square&logo=ffmpeg&logoColor=white)](https://ffmpeg.org/)
[![Pillow](https://img.shields.io/badge/Library-Pillow-orange.svg?style=flat-square&logo=python&logoColor=white)](https://python-pillow.org/)
[![PyPDF](https://img.shields.io/badge/Library-PyPDF-yellow.svg?style=flat-square)](https://pypi.org/project/pypdf/)

A fast, headless media and file compressor designed specifically for Windows File Explorer. Right-click any file (videos, audio, images, documents, archives, or generic files) and click **"Compress for discord"** to instantly compress it to under **15 MB** right next to the original file.

---

## ⚡ Quick Setup (1-Click Explorer Integration)

### Option 1: Right-Click Setup (Recommended)
Double-click `install_context_menu.bat` to register **"Compress for discord"** in Windows File Explorer.

### Option 2: Command Line
```powershell
python compress.py --install
```

Once installed, right-click any file in Windows File Explorer and click **Compress for discord**.

To remove the right-click option later:
- Double-click `uninstall_context_menu.bat` OR run `python compress.py --uninstall`

---

## ✨ Features

- 🖱️ **Zero-GUI / File Explorer Context Menu**: Select any file in Windows File Explorer, right-click, and select **"Compress for discord"**.
- 🎬 **Video Optimization**: Intelligently scales resolution (720p, 480p, 360p, 240p) and bitrates using 2-pass calculations so video files hit target file size (default: 14.5 MB).
- ⚡ **Hardware Acceleration**: Auto-detects NVIDIA NVENC, AMD AMF, Intel QSV, or Windows MediaFoundation for ultra-fast GPU video encoding.
- 📁 **Universal File Support**:
  - **Video**: `.mp4`, `.mkv`, `.avi`, `.mov`, `.webm`, `.flv`, `.wmv`, `.m4v`, `.3gp`, `.ts`
  - **Audio**: `.mp3`, `.m4a`, `.wav`, `.flac`, `.ogg`, `.aac`, `.wma`, `.opus`
  - **Images**: `.jpg`, `.jpeg`, `.png`, `.gif`, `.webp`, `.bmp`, `.tiff`
  - **PDF Documents**: `.pdf`
  - **Office Documents**: `.docx`, `.pptx`, `.xlsx`
  - **Archives**: `.zip`
  - **Generic Files**: Automatically compresses into a `.zip` archive if over 15 MB.
- 🛡️ **Non-Destructive Output**: Automatically saves compressed files alongside original files as `filename_compressed.ext` (increments `filename_compressed (1).ext` if it already exists).

---

## 💻 Command Line Usage

You can also run the compressor directly from PowerShell or Command Prompt:

```powershell
# Basic usage (defaults to 14.5 MB output next to input file)
python compress.py "C:\Users\Name\Videos\my_video.mp4"

# Compress multiple files
python compress.py "video1.mp4" "video2.mov" "image.png"

# Specify custom target size (e.g. 8 MB or 25 MB)
python compress.py "video.mp4" -s 8.0

# Specify custom output directory
python compress.py "video.mp4" -o "C:\Output"
```

---

## 📦 Building Standalone Executable (`.exe`)

To compile into a single standalone binary:
```powershell
build.bat
```
The compiled executable will be located in the `dist\compress.exe` directory.
