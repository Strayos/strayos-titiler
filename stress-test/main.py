import argparse
import asyncio
import functools
import json
import multiprocessing
import sys
import time
from pathlib import Path

from concurrent_tile_requester import main as run_raster_tests
from plot import plot_response_times
from stitch_histograms import stitch_histograms

ENDPOINT = "https://titiler.strayos.com"
MAX_CONCURRENT = 16
RASTER_JSON = Path(__file__).resolve().parent / "rasters.json"


def load_raster_list(path: Path = RASTER_JSON) -> list[dict]:
    """Load the raster list from a JSON file."""
    with open(path) as f:
        return json.load(f)


def process_raster_wrapper(raster: dict, output_dir: str) -> None:
    """Fetch tiles for a single raster in a separate process."""
    raster_name = raster["name"]
    print(f"Processing raster: {raster_name} (type: {raster['type']})")

    results = asyncio.run(
        run_raster_tests(
            ENDPOINT,
            raster["url"],
            max_concurrent=MAX_CONCURRENT,
            output_dir=output_dir,
            params=raster.get("params", {}),
        )
    )
    if results is not None:
        plot_response_times(results, raster_name, MAX_CONCURRENT, output_dir)
    else:
        print(f"Warning: No results returned for {raster_name}, skipping plot")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Stress-test titiler by fetching tiles from multiple rasters."
    )
    mode_group = parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument(
        "--peak-load",
        action="store_true",
        help="Run with up to 10 concurrent processes.",
    )
    mode_group.add_argument(
        "--low-load",
        action="store_true",
        help="Run with at most 1 concurrent process (sequentially).",
    )
    mode_group.add_argument(
        "--test",
        action="store_true",
        help="Run only rasters with a ``test`` attribute set to true.",
    )
    args = parser.parse_args()

    max_processes = 10 if args.peak_load else 1
    output_dir = "output_peak_load" if args.peak_load else "output_low_load"
    raster_list = load_raster_list()
    if args.test:
        raster_list = [r for r in raster_list if r.get("test")][:1]
        max_processes = 1
        output_dir = "output_test"
        if not raster_list:
            print('No rasters with "test": true found in rasters.json')
            sys.exit(1)
    print(
        f"Starting raster processing for {len(raster_list)} rasters "
        f"with max {max_processes} concurrent process(es) "
        f"and max {MAX_CONCURRENT} concurrent requests per process"
    )
    script_start = time.perf_counter()

    with multiprocessing.Pool(processes=max_processes) as pool:
        pool.map(
            functools.partial(process_raster_wrapper, output_dir=output_dir),
            raster_list,
        )

    script_end = time.perf_counter()
    total_script_time = script_end - script_start
    print(f"Total script execution time: {total_script_time:.2f} seconds")

    stitch_histograms([output_dir])
