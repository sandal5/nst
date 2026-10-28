#!/usr/bin/env python3
"""OCR stage: band-wise tesseract (jpn+eng) over the pilot PNGs.
Produces ocr_raw/<page>.txt (plain text) and ocr_lines/<page>.json
(lines with coordinates, for parsing + depth estimation)."""
import json, os, subprocess, sys, tempfile
from PIL import Image

Image.MAX_IMAGE_PIXELS = None
HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, "ocr_raw")
LINES = os.path.join(HERE, "ocr_lines")
BAND = 3000
SCALE = 2

def ocr_band(img, psm):
    """Return list of line dicts from one band image (already upscaled)."""
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        img.save(f.name)
        path = f.name
    try:
        out = subprocess.run(["tesseract", path, "stdout", "-l", "jpn+eng",
                              "--psm", str(psm), "tsv"],
                             capture_output=True, text=True, timeout=900)
        rows = []
        lines = out.stdout.splitlines()
        if not lines:
            return rows
        hdr = lines[0].split("\t")
        idx = {k: i for i, k in enumerate(hdr)}
        words = {}
        for ln in lines[1:]:
            parts = ln.split("\t")
            if len(parts) < 12:
                continue
            try:
                conf = float(parts[idx["conf"]])
            except ValueError:
                continue
            text = parts[idx["text"]]
            if conf < 0 or not text.strip():
                continue
            key = (parts[idx["block_num"]], parts[idx["par_num"]], parts[idx["line_num"]])
            w = {"x": int(parts[idx["left"]]), "y": int(parts[idx["top"]]),
                 "w": int(parts[idx["width"]]), "h": int(parts[idx["height"]]),
                 "conf": conf, "text": text}
            words.setdefault(key, []).append(w)
        for key, ws in words.items():
            ws.sort(key=lambda w: w["x"])
            rows.append({"x": ws[0]["x"], "y": ws[0]["y"],
                         "text": "".join(w["text"] for w in ws),
                         "words": ws,
                         "conf": sum(w["conf"] for w in ws) / len(ws)})
        rows.sort(key=lambda r: (r["y"], r["x"]))
        return rows
    finally:
        os.unlink(path)

def ocr_page(png, psm=6):
    im = Image.open(png).convert("RGB")
    W, H = im.size
    all_lines = []
    band_texts = []
    y = 0
    while y < H:
        h = min(BAND, H - y)
        band = im.crop((0, y, W, y + h)).resize((W * SCALE, h * SCALE), Image.LANCZOS)
        rows = ocr_band(band, psm)
        for r in rows:
            r["y"] = r["y"] / SCALE + y
            r["x"] = r["x"] / SCALE
            for w in r["words"]:
                w["x"] = w["x"] / SCALE
                w["y"] = w["y"] / SCALE + y
            all_lines.append(r)
        band_texts.append("\n".join(r["text"] for r in rows))
        print(f"  band y={y} h={h}: {len(rows)} lines", flush=True)
        y += h
    return all_lines, "\n".join(band_texts)

def main():
    os.makedirs(RAW, exist_ok=True)
    os.makedirs(LINES, exist_ok=True)
    targets = sys.argv[1:] or ["dir2_p160", "dir2_p001", "dir2_p002"]
    psm = int(os.environ.get("OCR_PSM", "6"))
    for name in targets:
        png = os.path.join(HERE, f"{name}.png")
        print(f"OCR {name} (psm={psm})", flush=True)
        lines, text = ocr_page(png, psm)
        with open(os.path.join(RAW, f"{name}.txt"), "w", encoding="utf-8") as f:
            f.write(text)
        with open(os.path.join(LINES, f"{name}.json"), "w", encoding="utf-8") as f:
            json.dump(lines, f, ensure_ascii=False)
        print(f"  total lines: {len(lines)}", flush=True)
    print("OCR DONE")

if __name__ == "__main__":
    main()
