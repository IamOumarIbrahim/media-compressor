import os
import sys
import subprocess
import shutil
import tempfile
from PIL import Image, ImageDraw
from pypdf import PdfWriter

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from compress import compress_file, find_ffmpeg_tools

def create_sample_files(test_dir):
    os.makedirs(test_dir, exist_ok=True)
    ffmpeg_path, _ = find_ffmpeg_tools()
    
    samples = {}
    
    # 1. Video (MP4)
    video_path = os.path.join(test_dir, "sample_video.mp4")
    cmd_vid = [
        ffmpeg_path, "-y",
        "-f", "lavfi", "-i", "testsrc=size=640x480:rate=30:duration=5",
        "-f", "lavfi", "-i", "sine=frequency=1000:duration=5",
        "-c:v", "libx264", "-c:a", "aac",
        video_path
    ]
    subprocess.run(cmd_vid, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    samples["Video (.mp4)"] = video_path

    # 2. Audio (MP3)
    audio_path = os.path.join(test_dir, "sample_audio.mp3")
    cmd_aud = [
        ffmpeg_path, "-y",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=5",
        "-c:a", "libmp3lame",
        audio_path
    ]
    subprocess.run(cmd_aud, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    samples["Audio (.mp3)"] = audio_path

    # 3. Images (.png, .jpg, .webp, animated .gif)
    img_png_path = os.path.join(test_dir, "sample_image.png")
    img = Image.new("RGB", (1000, 1000), color=(73, 109, 137))
    d = ImageDraw.Draw(img)
    d.text((10, 10), "Test Image PNG", fill=(255, 255, 0))
    img.save(img_png_path, "PNG")
    samples["Image (.png)"] = img_png_path

    img_jpg_path = os.path.join(test_dir, "sample_image.jpg")
    img.save(img_jpg_path, "JPEG", quality=95)
    samples["Image (.jpg)"] = img_jpg_path

    img_webp_path = os.path.join(test_dir, "sample_image.webp")
    img.save(img_webp_path, "WEBP", quality=95)
    samples["Image (.webp)"] = img_webp_path

    # Animated GIF
    gif_path = os.path.join(test_dir, "sample_anim.gif")
    frames = []
    for i in range(5):
        f = Image.new("RGB", (400, 400), color=(i * 40, 100, 200 - i * 30))
        frames.append(f)
    frames[0].save(gif_path, save_all=True, append_images=frames[1:], duration=200, loop=0)
    samples["Image (.gif)"] = gif_path

    # 4. PDF
    pdf_path = os.path.join(test_dir, "sample_doc.pdf")
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    with open(pdf_path, "wb") as fp:
        writer.write(fp)
    samples["PDF (.pdf)"] = pdf_path

    # 5. Office Docx (ZIP structure with embedded image)
    docx_path = os.path.join(test_dir, "sample_doc.docx")
    import zipfile
    with zipfile.ZipFile(docx_path, 'w') as z:
        z.writestr("word/document.xml", "<w:document></w:document>")
        with open(img_jpg_path, "rb") as im_fp:
            z.writestr("word/media/image1.jpg", im_fp.read())
    samples["Office (.docx)"] = docx_path

    # 6. ZIP Archive
    zip_path = os.path.join(test_dir, "sample_archive.zip")
    with zipfile.ZipFile(zip_path, 'w') as z:
        z.write(video_path, "inside_video.mp4")
        z.write(img_png_path, "inside_img.png")
    samples["Archive (.zip)"] = zip_path

    # 7. Large Generic File (> 15 MB text/binary)
    generic_path = os.path.join(test_dir, "sample_large_file.dat")
    with open(generic_path, "wb") as f:
        f.write(b"A" * (16 * 1024 * 1024)) # 16 MB dummy data
    samples["Generic Large (.dat)"] = generic_path

    return samples

def run_tests():
    test_dir = tempfile.mkdtemp(prefix="compress_test_")
    out_dir = os.path.join(test_dir, "output")
    os.makedirs(out_dir, exist_ok=True)
    
    print("=======================================================")
    print("      RUNNING FULL FILE TYPE COMPRESSION TESTS")
    print("=======================================================")
    
    try:
        samples = create_sample_files(test_dir)
        results = []
        
        for name, file_path in samples.items():
            orig_size = os.path.getsize(file_path) / (1024 * 1024)
            print(f"\nTesting: {name} (Original Size: {orig_size:.2f} MB)")
            
            out_file = compress_file(file_path, output_dir=out_dir, max_size_mb=14.9, log_callback=lambda msg: None)
            
            if out_file and os.path.exists(out_file):
                final_size = os.path.getsize(out_file) / (1024 * 1024)
                passed = final_size <= 14.9
                status = "PASSED" if passed else "FAILED (Exceeded Target)"
                results.append((name, status, f"{orig_size:.2f} MB -> {final_size:.2f} MB", os.path.basename(out_file)))
            else:
                results.append((name, "FAILED (No Output)", f"{orig_size:.2f} MB -> N/A", "N/A"))
                
        print("\n=======================================================")
        print("               TEST SUMMARY RESULTS")
        print("=======================================================")
        all_passed = True
        for name, status, size_info, out_name in results:
            indicator = "OK" if "PASSED" in status else "FAIL"
            print(f"[{indicator:^4}] {name:<20} | Status: {status:<22} | {size_info:<22} | Out: {out_name}")
            if "PASSED" not in status:
                all_passed = False
                
        print("=======================================================")
        if all_passed:
            print("[SUCCESS] All file type tests PASSED successfully!")
        else:
            print("[FAILURE] Some tests failed.")
            
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)

if __name__ == "__main__":
    run_tests()
