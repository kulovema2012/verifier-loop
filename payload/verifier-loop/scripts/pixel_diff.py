#!/usr/bin/env python3
"""Pixel-diff two images, or two directories of same-named images.

Prints one JSON object (last line of stdout) in the verifier-loop harness format:
    {"score": <mean % differing pixels>, "cases": {name: pct, ...}, "artifacts": <dir>}

so a directory comparison can be used directly as a harness.

Examples:
    pixel_diff.py expected.png actual.png --heatmap out/diff.png
    pixel_diff.py renders/expected renders/actual --out verify/out --tolerance 24
    pixel_diff.py a.png b.png --ignore 0,0,1920,40      # mask a header bar
    pixel_diff.py a/ b/ --mask masks/                    # per-case masks: masks/<name>.png, white = ignore

A pixel counts as different when any RGB channel differs by more than --tolerance
(0-255). Transparent areas are composited onto white first. If the images differ in
size, both are placed on the larger canvas and the non-overlapping area counts as
different, so size bugs cannot hide behind resizing.

Requires Pillow and numpy.
"""
import argparse
import json
import sys
from pathlib import Path

try:
    import numpy as np
    from PIL import Image, ImageFilter
except ImportError:
    sys.exit("pixel_diff.py needs Pillow and numpy: pip install pillow numpy")

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}


def load_rgb(path, blur=0.0):
    img = Image.open(path)
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
        img = Image.alpha_composite(bg, img)
    img = img.convert("RGB")
    if blur > 0:
        img = img.filter(ImageFilter.GaussianBlur(blur))
    return np.asarray(img, dtype=np.int16)


def pad(arr, h, w):
    """Place arr on an h x w canvas; returns (canvas, valid_mask)."""
    canvas = np.zeros((h, w, 3), dtype=np.int16)
    valid = np.zeros((h, w), dtype=bool)
    ah, aw = arr.shape[:2]
    canvas[:ah, :aw] = arr
    valid[:ah, :aw] = True
    return canvas, valid


def parse_box(text):
    parts = [int(p) for p in text.split(",")]
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("--ignore expects x,y,w,h")
    return parts


def diff_pair(a_path, b_path, tolerance, boxes, mask_path, heatmap_path, blur=0.0):
    a = load_rgb(a_path, blur)
    b = load_rgb(b_path, blur)
    h = max(a.shape[0], b.shape[0])
    w = max(a.shape[1], b.shape[1])
    a, va = pad(a, h, w)
    b, vb = pad(b, h, w)

    channel_delta = np.abs(a - b).max(axis=2)
    diff = channel_delta > tolerance
    diff |= va != vb  # area covered by only one image

    ignore = np.zeros((h, w), dtype=bool)
    for x, y, bw, bh in boxes:
        ignore[max(y, 0):y + bh, max(x, 0):x + bw] = True
    if mask_path is not None and Path(mask_path).exists():
        m = np.asarray(Image.open(mask_path).convert("L"))
        mh, mw = min(m.shape[0], h), min(m.shape[1], w)
        ignore[:mh, :mw] |= m[:mh, :mw] > 127

    counted = ~ignore
    total = int(counted.sum())
    bad = int((diff & counted).sum())
    pct = 100.0 * bad / total if total else 0.0

    if heatmap_path is not None:
        heatmap_path = Path(heatmap_path)
        heatmap_path.parent.mkdir(parents=True, exist_ok=True)
        # Faded grayscale of the expected image, red where wrong, blue-tinted where ignored.
        gray = a.mean(axis=2)
        base = (255 - (255 - gray) * 0.3).astype(np.uint8)
        out = np.stack([base, base, base], axis=2)
        out[ignore] = (out[ignore] * np.array([0.8, 0.85, 1.0])).astype(np.uint8)
        out[diff & counted] = (255, 0, 0)
        Image.fromarray(out).save(heatmap_path)

    return {"pct": round(pct, 4), "diff_pixels": bad, "counted_pixels": total,
            "size_a": [int(va.any(axis=0).sum()), int(va.any(axis=1).sum())],
            "size_b": [int(vb.any(axis=0).sum()), int(vb.any(axis=1).sum())]}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("expected", help="expected image or directory")
    ap.add_argument("actual", help="actual image or directory")
    ap.add_argument("--tolerance", type=int, default=0,
                    help="max per-channel delta (0-255) still treated as equal; 16-32 absorbs anti-aliasing")
    ap.add_argument("--ignore", type=parse_box, action="append", default=[], metavar="x,y,w,h",
                    help="region to ignore (repeatable)")
    ap.add_argument("--mask", help="mask image (file mode) or directory of <name>.png masks (dir mode); white = ignore")
    ap.add_argument("--heatmap", help="file mode: where to write the diff heatmap PNG")
    ap.add_argument("--out", default="pixel-diff-out", help="dir mode: where to write heatmaps (default: %(default)s)")
    ap.add_argument("--aggregate", choices=["mean", "max", "p90"], default="mean",
                    help="how per-case percentages combine into score (default: %(default)s)")
    ap.add_argument("--blur", type=float, default=0.0,
                    help="Gaussian blur radius applied to both images first: ~6 compares tone/layout/lighting "
                         "and ignores texture (useful when fine content can't match, e.g. regenerated photos); ~1 "
                         "softens anti-aliasing for a detail pass")
    ap.add_argument("--verbose", action="store_true", help="include pixel counts and sizes per case")
    args = ap.parse_args()

    exp, act = Path(args.expected), Path(args.actual)
    details = {}

    if exp.is_dir() != act.is_dir():
        sys.exit("expected and actual must both be files or both be directories")

    if exp.is_file():
        name = exp.stem
        details[name] = diff_pair(exp, act, args.tolerance, args.ignore, args.mask, args.heatmap, args.blur)
        artifacts = str(Path(args.heatmap).parent) if args.heatmap else None
    else:
        out = Path(args.out)
        names = sorted(p.name for p in exp.iterdir() if p.suffix.lower() in IMAGE_EXTS)
        if not names:
            sys.exit(f"no images found in {exp}")
        mask_dir = Path(args.mask) if args.mask else None
        for n in names:
            stem = Path(n).stem
            other = act / n
            if not other.exists():
                details[stem] = {"pct": 100.0, "error": f"missing in {act}"}
                continue
            mask = mask_dir / f"{stem}.png" if mask_dir else None
            try:
                details[stem] = diff_pair(exp / n, other, args.tolerance, args.ignore, mask, out / f"{stem}.diff.png", args.blur)
            except Exception as e:  # unreadable/corrupt output is a failed case, not a crash
                details[stem] = {"pct": 100.0, "error": str(e)}
        extra = sorted(p.stem for p in act.iterdir() if p.suffix.lower() in IMAGE_EXTS and not (exp / p.name).exists())
        if extra:
            print(f"note: {len(extra)} image(s) only in {act}, ignored: {', '.join(extra[:5])}", file=sys.stderr)
        artifacts = str(out)

    pcts = [d["pct"] for d in details.values()]
    if args.aggregate == "mean":
        score = sum(pcts) / len(pcts)
    elif args.aggregate == "max":
        score = max(pcts)
    else:
        score = float(np.percentile(pcts, 90))

    result = {"score": round(score, 4), "cases": {k: v["pct"] for k, v in details.items()}, "artifacts": artifacts}
    if args.verbose or any("error" in d for d in details.values()):
        result["details"] = details
    print(json.dumps(result))


if __name__ == "__main__":
    main()
