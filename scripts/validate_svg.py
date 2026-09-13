"""Regenerate optimized copies and compare overview pixels using the test extras.

uv run --extra test python scripts/validate_svg.py artifacts/svg-before --output artifacts/svg-validation
The measurements use resvg, not a claim about a particular interactive viewer.
"""

import argparse
import json
from io import BytesIO
from pathlib import Path
from time import perf_counter

import resvg_py
from PIL import Image, ImageChops, ImageStat

from archer.render.svg import optimize_svg


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("raw_directory", type=Path)
    parser.add_argument("--output", type=Path, default=Path("artifacts/svg-validation"))
    parser.add_argument("--optimization", choices=["raw", "medium", "fast"], default="medium")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    report = []
    for raw_path in sorted(args.raw_directory.glob("*.svg")):
        original = raw_path.read_bytes()
        start = perf_counter()
        optimized, stats = optimize_svg(original, level=args.optimization)
        postprocess_seconds = perf_counter() - start
        (args.output / raw_path.name).write_bytes(optimized)
        images, seconds = [], []
        for data in (original, optimized):
            start = perf_counter()
            png = resvg_py.svg_to_bytes(
                svg_string=data.decode(),
                width=1600,
                font_family="DejaVu Sans",
                style_sheet='text { font-family: "DejaVu Sans" !important; }',
            )
            seconds.append(round(perf_counter() - start, 4))
            images.append(Image.open(BytesIO(png)).convert("RGB"))
        difference = ImageChops.difference(*images)
        item = {
            "file": raw_path.name,
            "optimization": stats,
            "postprocess_seconds": round(postprocess_seconds, 4),
            "raster_seconds_before_after": seconds,
            "mean_channel_difference_0_255": ImageStat.Stat(difference).mean,
            "max_channel_difference_0_255": [high for _, high in difference.getextrema()],
            "bytes_before_after": [len(original), len(optimized)],
        }
        report.append(item)
        print(json.dumps(item), flush=True)
    (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
