"""Bounded GOES throughput experiment over one day of full-disk MCMIPF scans.

Downloads one UTC day of GOES-16 8-band scans repeatedly into temporary local
directories (deleted after each variant) to measure effective read throughput
across backend, range-read block size, and worker count. Nothing is written to
the durable archive, the HF bucket, or /mnt/p. Fingerprints must match across
variants so the comparison is about speed, not content.
"""
import argparse
from dataclasses import replace

from ecore_weather import benchmark, goes


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--day", default="2022-09-07", help="UTC day to fetch")
    p.add_argument("--bands", nargs="+", type=int, default=[8, 9, 10, 11, 13, 14, 15, 16])
    p.add_argument("--scans-per-hour", type=int, default=6, help="0 keeps every available scan")
    p.add_argument("--repeats", type=int, default=2)
    p.add_argument("--output", default="results/throughput")
    args = p.parse_args()
    selection = goes.discover(args.day, f"{args.day}T23:59:59", bands=tuple(args.bands),
                              satellite=16, product="ABI-L2-MCMIPF",
                              scans_per_hour=args.scans_per_hour or None)
    # Pin to whole-day hours so variants share one inventory.
    selection = replace(selection, assets=[a for a in selection.assets if a.time[:10] == args.day])
    print("assets:", len(selection.assets), flush=True)
    variants = [
        {"backend": "s3fs", "block_size": 256 * 1024, "workers": 8},
        {"backend": "obstore", "block_size": 256 * 1024, "workers": 8},
        {"backend": "obstore", "block_size": 1024 * 1024, "workers": 8},
        {"backend": "obstore", "block_size": 1024 * 1024, "workers": 16},
        {"backend": "obstore", "block_size": 4 * 1024 * 1024, "workers": 16},
    ]
    table = benchmark.goes_throughput(selection, args.output, variants, repeats=args.repeats)
    print(table.groupby(["backend", "block_size", "workers"])[["wall_s", "effective_MBps"]].mean())


if __name__ == "__main__":
    main()
