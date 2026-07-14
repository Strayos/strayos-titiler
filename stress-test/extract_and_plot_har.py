"""Extract tile requests from a HAR file to CSV and plot histogram."""

import argparse
import csv
import json
import re
from pathlib import Path

from plot import plot_response_times


def extract_tile_coords(url: str) -> tuple[int, int, int] | None:
    """Extract (x, y, z) tile coordinates from a tile URL."""
    for pattern in (r"/tiles/(\d+)/(\d+)/(\d+)", r"/WebMercatorQuad/(\d+)/(\d+)/(\d+)"):
        m = re.search(pattern, url)
        if m:
            return int(m.group(1)), int(m.group(2)), int(m.group(3))
    return None


def parse_har(har_path: str) -> list[dict]:
    """Parse HAR file and return list of tile request dicts."""
    with open(har_path) as f:
        har = json.load(f)

    rows = []
    for entry in har["log"]["entries"]:
        url = entry["request"]["url"]
        if "/tiles/" not in url:
            continue

        coords = extract_tile_coords(url)
        if coords is None:
            continue
        z, x, y = coords

        status = entry["response"]["status"]
        timings = entry.get("timings", {})
        wait_ms = timings.get("wait", 0)
        wait_s = round(wait_ms / 1000, 4)
        content_length = entry["response"].get("bodySize", 0)
        if content_length == -1:
            content_length = entry["response"].get("content", {}).get("size", 0)
        if content_length is None:
            content_length = 0

        rows.append(
            {
                "x": x,
                "y": y,
                "z": z,
                "url": url,
                "status": status,
                "wait_time_s": wait_s,
                "content_length": content_length,
                "success": status == 200,
                "error": "",
            }
        )
    return rows


def write_csv(rows: list[dict], output_path: str) -> None:
    """Write results to CSV."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "x",
                "y",
                "z",
                "url",
                "status",
                "wait_time_s",
                "content_length",
                "success",
                "error",
            ]
        )
        for row in rows:
            writer.writerow(
                [
                    row["x"],
                    row["y"],
                    row["z"],
                    row["url"],
                    row["status"],
                    row["wait_time_s"],
                    row["content_length"],
                    row["success"],
                    row["error"],
                ]
            )


def main(har_path: str, output_path: str, output_dir: str, plot: bool) -> None:
    """Parse HAR, optionally write CSV and plot histogram."""
    rows = parse_har(har_path)

    if output_path:
        write_csv(rows, output_path)
        print(f"Extracted {len(rows)} tile requests -> {output_path}")

    if plot:
        if not rows:
            print("No tile requests found, skipping plot")
            return
        raster_name = Path(har_path).stem
        non_zero = [r for r in rows if r["wait_time_s"] > 0]
        print(f"Non-zero wait entries for plot: {len(non_zero)}/{len(rows)}")
        if not non_zero:
            print("All entries have 0.0s wait time, skipping plot")
            return
        plot_response_times(
            non_zero, raster_name, max_concurrent=0, output_dir=output_dir
        )
        print(f"Histogram saved to {output_dir}/{raster_name}.png")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Extract tile requests from HAR file to CSV and/or plot histogram."
    )
    parser.add_argument("har_file", help="Path to the HAR file")
    parser.add_argument(
        "-o", "--output", help="Output CSV file path (omit to skip CSV)"
    )
    parser.add_argument(
        "-d",
        "--output-dir",
        default="output_har",
        help="Output directory for histogram",
    )
    parser.add_argument(
        "-p", "--plot", action="store_true", help="Plot histogram after extraction"
    )
    args = parser.parse_args()

    if not args.output and not args.plot:
        parser.error("Provide at least one of --output or --plot")

    main(args.har_file, args.output, args.output_dir, args.plot)
