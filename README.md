# ⚡ Media Compressor Pro (v0.5)

[![Python](https://img.shields.io/badge/Python-3.8+-blue.svg?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![FFmpeg](https://img.shields.io/badge/Dependency-FFmpeg-green.svg?style=flat-square&logo=ffmpeg&logoColor=white)](https://ffmpeg.org/)
[![Pillow](https://img.shields.io/badge/Library-Pillow_9.5+-orange.svg?style=flat-square&logo=python&logoColor=white)](https://python-pillow.org/)
[![PyPDF](https://img.shields.io/badge/Library-PyPDF_4.0+-yellow.svg?style=flat-square)](https://pypi.org/project/pypdf/)
[![Compiler](https://img.shields.io/badge/Compiler-PyInstaller-blue.svg?style=flat-square)](https://pyinstaller.org/)
[![License: CC0](https://img.shields.io/badge/License-CC0_1.0-lightgrey.svg?style=flat-square)](https://creativecommons.org/publicdomain/zero/1.0/)

A premium, multi-format media compression utility featuring a dual-pane desktop GUI and a powerful command-line interface. Precisely compress and convert audio, video, images, PDFs, Office documents, and ZIP archives to fit target sizes (such as **15MB** limits for messaging systems, email attachments, or web uploads).

Featuring **GPU hardware acceleration** and **parallel batch worker queues**, this tool is designed to be lightning-fast.

---

## 📖 Table of Contents
- [Key Features](#-key-features)
- [System Architecture](#-system-architecture)
- [Quick Setup & Installation](#-quick-setup--installation)
- [How to Use](#-how-to-use)
  - [Option A: Graphical Interface (GUI)](#option-a-graphical-interface-gui)
  - [Option B: Command-Line Interface (CLI)](#option-b-command-line-interface-cli)
- [Double-Click Batch Scripts](#-double-click-batch-scripts)
- [Standalone Executable Build (`.exe`)](#-standalone-executable-build-exe)
- [License](#-license)

---

## ✨ Key Features

- 📁 **Universal Format Support & Conversion**: 
  - **Audio**: `.mp3`, `.m4a`, `.wav`, `.flac`, `.ogg`, `.aac`, `.wma`
  - **Video**: `.mp4`, `.mkv`, `.avi`, `.mov`, `.webm`, `.flv`, `.wmv`
  - **Images**: `.jpg`, `.jpeg`, `.png`, `.gif`, `.webp`, `.bmp`, `.tiff`
  - **PDF Documents**: `.pdf`
  - **Office Documents**: `.docx`, `.pptx`, `.xlsx`
  - **Archives**: `.zip`
- ⚡ **GPU Hardware Acceleration (New)**:
  - Dynamically probes and checks host for working hardware encoders at startup (NVIDIA NVENC, AMD AMF, Intel QSV, Windows MediaFoundation).
  - Automatically routes H.264/HEVC/VP9 encoding to GPU cores, yielding **up to 10x faster encoding speed** while reducing CPU usage.
- 📁 **Batch Imports & Folder Scanning (New)**:
  - Select and load multiple files or use the **Folder...** import button to scan directory trees for supported media files instantly.
- ⏩ **Speed Adjustments**: 
  - Adjust speed multipliers from `0.5x` to `3.0x` with a live duration preview. 
  - Pitch-sync filters (`setpts` + chained `atempo` filters) keep audio pitch in sync.
- 📐 **Image Resizing**: 
  - Scale image dimensions from `10%` to `100%` using a slider with live resolution previews.
- ⚙️ **Smart Document & Archive Compression**:
  - Compresses `.docx`, `.pptx`, and `.xlsx` archives by optimizing internal media assets while keeping text formatting intact.
  - Automatically unzips `.zip` archives, recursively compresses all media elements using budget scaling, and re-packages them.
- 🖥️ **Thread-Safe Multi-Worker Queue**:
  - Full CRUD operations (Add, Edit settings, Pause, Delete, Clear) for queued items.
  - Concurrent thread pool worker counts selectable from 1 to 8 workers.
- 📁 **Explorer Integration & Live Logs**:
  - Automatically opens and highlights output folders on completion.
  - Live output log panels and dark-themed output folder Treeview.

---

## ⚙️ System Architecture

The following block diagram illustrates the routing and processing pipelines of the compressor:

```mermaid
graph TD
    User["User Input: File/Folder + Target Size"] --> ModeSelect{Execution Mode}
    ModeSelect -->|GUI Mode| Tkinter[Tkinter UI Panel]
    ModeSelect -->|CLI Mode| Parser[CLI Argument Parser]
    Tkinter --> Config[Config Options: Speed, Accel, Presets]
    Parser --> Config
    Config --> Router{File Format Router}
    
    Router -->|Audio/Video| GPU_Check{Hardware Accel?}
    GPU_Check -->|Yes| HW["GPU Encode: NVENC/AMF/QSV/MF"]
    GPU_Check -->|No| SW["CPU Software Encode"]
    Router -->|Images| Pillow["Pillow Scale & Quality Optimizer"]
    Router -->|PDF Documents| PyPDF["pypdf Object & Stream Compressor"]
    Router -->|Office Docs| ZipFile["ZipFile Media Packer"]
    Router -->|Zip Archives| ZipPack["ZIP Recursive Packer"]
    
    HW --> Verify[Size Verification]
    SW --> Verify
    Pillow --> Verify
    PyPDF --> Verify
    ZipFile --> Verify
    ZipPack --> Verify
    
    Verify --> Output[Compressed File]
    
    classDef default fill:#0f172a,stroke:#3b82f6,stroke-width:2px,color:#fff;
    classDef process fill:#1e1b4b,stroke:#a855f7,stroke-width:2px,color:#fff;
    class HW,SW,Pillow,PyPDF,ZipFile,ZipPack process;
```

---

## 🚀 Quick Setup & Installation

This utility requires **Python 3.8+** and **FFmpeg** installed on your system.

### 1️⃣ Install Python
- **Windows / macOS**: Download and run the official installer from [python.org](https://www.python.org/downloads/). Ensure you check **"Add Python to PATH"** during setup.
- **Linux**: Install via package manager:
  ```bash
  sudo apt update && sudo apt install python3 python3-pip
  ```

### 2️⃣ Install Python Libraries
Install the core dependencies:
```bash
pip install Pillow pypdf customtkinter
```

### 3️⃣ Install FFmpeg
The tool relies on FFmpeg for audio/video processing. Install it using the standard method for your OS:

- **Windows** (via PowerShell as Administrator):
  ```powershell
  winget install Gyan.FFmpeg
  ```
  *(Restart your computer or terminal after installation).*
  
- **macOS** (via Homebrew):
  ```bash
  brew install ffmpeg
  ```

- **Linux** (Debian/Ubuntu):
  ```bash
  sudo apt update && sudo apt install ffmpeg
  ```

---

## 🛠️ How to Use

### Option A: Graphical Interface (GUI)
Launch the application:
```bash
python compress.py
```
Or simply double-click `run.bat`.
1. Click **File(s)...** or **Folder...** to import input elements.
2. Select target format conversion options and speed presets.
3. Configure the **Hardware Acceleration** settings (e.g. Nvidia NVENC, Intel QSV, Auto-Detect).
4. Set the target size in MB, click **Add to Queue**, and click **Start Queue**.

### Option B: Command-Line Interface (CLI)
CLI usage syntax:
```bash
python compress.py <input_file> [output_dir] [target_size_mb] [options]
```

**Options**:
*   `-s`, `--speed`: Playback speed multiplier (0.5x to 3.0x).
*   `-r`, `--resize`: Image resize scale factor (0.1 to 1.0).
*   `-f`, `--format`: Convert target format (e.g. mp4, webm, mp3, png, webp).
*   `-p`, `--preset`: Speed presets (`ultrafast`, `superfast`, `veryfast`, `faster`, `fast`, `medium`, `slow`).
*   `-a`, `--accel`: GPU Hardware Acceleration (`Auto-Detect`, `CPU`, `Nvidia NVENC`, `AMD AMF`, `Intel QSV`, `Windows MediaFoundation`).

**Examples**:
```bash
# Compress a video using NVENC hardware acceleration under 15MB at 1.25x speed
python compress.py input.mp4 -s 1.25 -a "Nvidia NVENC"

# Convert and compress a WebM video to MP4 under 5MB using Auto-Detect GPU acceleration
python compress.py input.webm DONE 5.0 -f mp4

# Compress and convert a PNG to WebP under 1MB
python compress.py input.png DONE 1.0 -f webp
```

---

## ⚡ Double-Click Batch Scripts

We provide helper scripts for convenience on Windows:
- **`run.bat`**: Double-click to instantly launch the desktop graphical interface.
- **`build.bat`**: Double-click to automatically recompile/build a standalone binary using PyInstaller.

---

## 📦 Standalone Executable Build (`.exe`)

You can compile a portable, single-file Windows executable that bundles all dependencies (including CustomTkinter themes):

1. Install PyInstaller:
   ```bash
   pip install pyinstaller
   ```
2. Run compilation using the spec file:
   ```bash
   pyinstaller -y MediaCompressor.spec
   ```
3. Your portable executable will be created in the `dist/` directory as `MediaCompressor.exe`.

---

## 📄 License
This repository is licensed under the [CC0 1.0 Universal (CC0 1.0) Public Domain Dedication](LICENSE).
