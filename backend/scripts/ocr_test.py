"""
ocr_test.py — Phase 0 OCR feasibility test.

Generates 5 synthetic prescription-like images using Pillow,
runs Tesseract on each, and records accuracy.

Results go to stdout and should be copied into TODO.md.
"""

import os
import sys
import tempfile

# Test cases: each is (description, lines_of_text)
TEST_CASES = [
    (
        "Clean printed single line",
        ["Tab Augmentin 625 mg BD"],
    ),
    (
        "Two printed lines",
        ["Tab Dolo 650 mg TDS", "Tab Pan 40 mg OD"],
    ),
    (
        "Mixed case and spacing",
        ["tab augmentin 625mg bd", "CAP AMOXICILLIN 500 MG TDS"],
    ),
    (
        "Multiple medicines",
        [
            "Tab Warfarin 5 mg OD",
            "Tab Atorvastatin 20 mg OD",
            "Tab Metformin 500 mg BD",
        ],
    ),
    (
        "Numbers and abbreviations",
        [
            "Ibuprofen 400 mg 1-1-1",
            "Clarithromycin 500 mg 1-0-1",
        ],
    ),
]


def create_test_image(lines: list[str], path: str):
    """Create a simple white-background image with black text."""
    from PIL import Image, ImageDraw, ImageFont

    # Use a basic font — Pillow's default if no TTF available
    width, height = 800, 60 + 40 * len(lines)
    img = Image.new("RGB", (width, height), color="white")
    draw = ImageDraw.Draw(img)

    try:
        # Try to load a common monospace font for clearer OCR
        font = ImageFont.truetype("cour.ttf", 28)
    except (OSError, IOError):
        try:
            font = ImageFont.truetype("arial.ttf", 28)
        except (OSError, IOError):
            font = ImageFont.load_default()

    y = 20
    for line in lines:
        draw.text((30, y), line, fill="black", font=font)
        y += 40

    img.save(path, "PNG")


def run_ocr_test():
    try:
        import pytesseract
    except ImportError:
        print("ERROR: pytesseract not installed")
        return 1

    # Check if Tesseract binary is available
    try:
        version = pytesseract.get_tesseract_version()
        print(f"Tesseract version: {version}")
    except Exception as e:
        print(f"ERROR: Tesseract binary not found: {e}")
        print("OCR_ENGINE should be set to 'none' — typed entry only.")
        print("\nOCR DECISION: Tesseract unavailable. Use typed entry + confirmation screen.")
        return 2

    print(f"\n{'='*60}")
    print("OCR FEASIBILITY TEST — 5 synthetic images")
    print(f"{'='*60}\n")

    results = []

    with tempfile.TemporaryDirectory() as tmpdir:
        for i, (desc, lines) in enumerate(TEST_CASES, 1):
            img_path = os.path.join(tmpdir, f"test_{i}.png")
            create_test_image(lines, img_path)

            print(f"TEST {i}: {desc}")
            print(f"  Expected lines: {lines}")

            try:
                from PIL import Image
                img = Image.open(img_path)
                raw = pytesseract.image_to_string(img)
                ocr_lines = [l.strip() for l in raw.strip().split("\n") if l.strip()]
                print(f"  OCR output:     {ocr_lines}")

                # Rough accuracy: what fraction of expected lines have a close match
                matched = 0
                for expected in lines:
                    exp_lower = expected.lower().replace(" ", "")
                    for ocr_line in ocr_lines:
                        ocr_lower = ocr_line.lower().replace(" ", "")
                        # Check if most chars match (> 70% overlap)
                        common = sum(1 for c in exp_lower if c in ocr_lower)
                        if common / max(len(exp_lower), 1) > 0.70:
                            matched += 1
                            break

                accuracy = matched / len(lines) * 100
                results.append((desc, accuracy, ocr_lines))
                print(f"  Line match:     {matched}/{len(lines)} ({accuracy:.0f}%)")

            except Exception as e:
                print(f"  ERROR: {e}")
                results.append((desc, 0, []))

            print()

    # Summary
    print(f"{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    avg = sum(r[1] for r in results) / len(results) if results else 0
    for desc, acc, _ in results:
        status = "OK" if acc >= 70 else "POOR"
        print(f"  [{status}] {desc}: {acc:.0f}%")
    print(f"\n  Average line match: {avg:.0f}%")

    if avg >= 70:
        print("\nOCR DECISION: Tesseract works for printed text. Keep OCR_ENGINE=tesseract.")
        print("Handwritten text will need the confirmation screen.")
        return 0
    else:
        print("\nOCR DECISION: Tesseract accuracy too low for reliable use.")
        print("Keep Tesseract for printed text but rely heavily on the confirmation screen.")
        return 0  # Not a fatal error — confirmation screen handles it


if __name__ == "__main__":
    sys.exit(run_ocr_test())
