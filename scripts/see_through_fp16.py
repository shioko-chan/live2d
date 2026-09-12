"""Pinned See-through adapter: FP16 on Turing and release each model between stages.
Only invoked explicitly by l2d run, never imported by doctor or CPU tests.
"""

import argparse
import gc
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--srcp", required=True)
    parser.add_argument("--save_dir", required=True)
    parser.add_argument("--repo_id_layerdiff", required=True)
    parser.add_argument("--repo_id_depth", required=True)
    parser.add_argument("--resolution", type=int, default=1024)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--save_to_psd", action="store_true")
    parser.add_argument("--tblr_split", action="store_true")
    args = parser.parse_args()
    sys.path.insert(0, str(args.source_root))
    sys.path.insert(0, str(args.source_root / "common"))
    # inference_utils is patched with a verified diff during source preparation.
    import torch
    from utils import inference_utils as inference
    from utils.torch_utils import seed_everything

    seed_everything(args.seed)
    inference.apply_layerdiff(
        args.srcp, args.repo_id_layerdiff, save_dir=args.save_dir, seed=args.seed, resolution=args.resolution
    )
    inference.layerdiff_pipeline = None
    gc.collect()
    torch.cuda.empty_cache()
    inference.apply_marigold(
        args.srcp, args.repo_id_depth, save_dir=args.save_dir, seed=args.seed, resolution=768
    )
    inference.marigold_pipeline = None
    gc.collect()
    torch.cuda.empty_cache()
    inference.further_extr(
        str(Path(args.save_dir) / Path(args.srcp).stem),
        rotate=False,
        save_to_psd=args.save_to_psd,
        tblr_split=args.tblr_split,
    )


if __name__ == "__main__":
    main()
