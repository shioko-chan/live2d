"""Prepare one component with an explicit uniform scale and canvas anchor."""

import argparse
import hashlib
from io import BytesIO
import json
from pathlib import Path

from PIL import Image
from live2d_pipeline.component_preparation import prepare_uniform
from live2d_pipeline.store import digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--crop", type=int, nargs=4, required=True, metavar=("LEFT", "TOP", "RIGHT", "BOTTOM"))
    dimensions = parser.add_mutually_exclusive_group(required=True)
    dimensions.add_argument("--width", type=int)
    dimensions.add_argument("--height", type=int)
    parser.add_argument("--canvas-anchor", type=float, nargs=2, required=True)
    parser.add_argument("--source-anchor", type=float, nargs=2, default=(.5, .5))
    args = parser.parse_args()
    receipt_path = args.output.with_suffix(".json")
    if args.output.suffix.lower() != ".png":
        parser.error("Output must be PNG")
    if args.output.exists() or receipt_path.exists():
        parser.error("Output or receipt already exists; choose a fresh path")
    source_bytes = args.source.read_bytes()
    with Image.open(BytesIO(source_bytes)) as source:
        output, receipt = prepare_uniform(source, args.crop, args.canvas_anchor,
                                          width=args.width, height=args.height, source_anchor=args.source_anchor)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation protects existing project artifacts.
    with args.output.open("xb") as stream:
        output.save(stream, format="PNG")
    receipt.update({"source": str(args.source.resolve()), "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
                    "output": str(args.output.resolve()), "output_sha256": digest(args.output)})
    with receipt_path.open("x") as stream:
        json.dump(receipt, stream, indent=2)
        stream.write("\n")
    print(json.dumps({"image": str(args.output), "receipt": str(receipt_path), "placement": receipt["placement"]}))


if __name__ == "__main__":
    main()
