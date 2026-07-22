import asyncio
import csv
import json
import math
import time
import traceback
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlencode as _urlencode

import aiohttp
from pyproj import Transformer


async def async_retry(
    coro_factory: Any,
    max_retries: int = 2,
    base_delay: float = 1.0,
    backoff: float = 2.0,
) -> Any:
    """Retry an async callable on failure with exponential backoff."""
    last_exc = None
    for attempt in range(max_retries + 1):
        try:
            return await coro_factory()
        except Exception as e:
            last_exc = e
            if attempt < max_retries:
                delay = base_delay * (backoff**attempt)
                print(f"    Retry {attempt + 1}/{max_retries} after {delay:.1f}s: {e}")
                await asyncio.sleep(delay)
    raise last_exc  # type: ignore[misc]


class Timer:
    """Context manager for timing code blocks.

    Usage:
        with Timer() as timer:
            do_something()
        print(timer.elapsed)
    """

    def __enter__(self) -> "Timer":
        self.start: float = time.perf_counter()
        return self

    def __exit__(self, *args: object) -> None:
        self.elapsed: float = time.perf_counter() - self.start


async def fetch_validate(
    session: aiohttp.ClientSession, endpoint: str, raster_path: str
) -> dict[str, Any]:
    """Fetch the COG validate response from the titiler endpoint."""
    url = f"{endpoint}/cog/validate?url={raster_path}"
    async with session.get(url) as response:
        if response.status != 200:
            raise Exception(f"Failed to fetch validate info: {response.status}")
        return await response.json()


async def fetch_statistics(
    session: aiohttp.ClientSession, endpoint: str, raster_path: str
) -> dict[str, Any]:
    """Fetch the COG statistics response from the titiler endpoint."""
    url = f"{endpoint}/cog/statistics?url={raster_path}"
    async with session.get(url) as response:
        if response.status != 200:
            raise Exception(f"Failed to fetch statistics: {response.status}")
        return await response.json()


async def fetch_tilejson(
    session: aiohttp.ClientSession, endpoint: str, raster_path: str
) -> dict[str, Any]:
    """Fetch the tilejson document from the titiler endpoint."""
    tilejson_url = f"{endpoint}/cog/WebMercatorQuad/tilejson.json?url={raster_path}"
    async with session.get(tilejson_url) as response:
        if response.status != 200:
            raise Exception(f"Failed to fetch tilejson info: {response.status}")
        data = await response.json()
        if "maxzoom" not in data or "minzoom" not in data:
            raise Exception("No minzoom/maxzoom found in the tilejson response")
        return data


def calculate_tile_combinations(
    extent: Sequence[float],
    min_zoom: int,
    max_zoom: int,
) -> tuple[list[tuple[int, int, int]], dict[int, int]]:
    """Calculate all x, y, z combinations for a raster extent using Web Mercator Quad tiling.

    Args:
        extent: Bounding box as [min_lon, min_lat, max_lon, max_lat].
        min_zoom: Minimum zoom level (inclusive).
        max_zoom: Maximum zoom level (inclusive).

    Returns:
        Tuple of (list of (x, y, z) tile combinations, dict mapping zoom -> tile count).

    """
    combinations = []

    # Web Mercator extent (world bounds in EPSG:3857)
    # X: -20037508.34 to 20037508.34
    # Y: -20037508.34 to 20037508.34
    world_min_x = -20037508.34
    world_max_x = 20037508.34
    world_min_y = -20037508.34
    world_max_y = 20037508.34

    # Convert extent to Web Mercator (EPSG:3857)
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
    min_lon, min_lat, max_lon, max_lat = extent
    min_x, min_y = transformer.transform(min_lon, min_lat)
    max_x, max_y = transformer.transform(max_lon, max_lat)

    tile_counts = {}
    for z in range(min_zoom, max_zoom + 1):
        # At zoom level z, there are 2^z tiles in each dimension
        n_tiles = 2**z
        tile_extent = (world_max_x - world_min_x) / n_tiles  # Meters per tile

        # Convert mercator coordinates to tile coordinates
        # Tile X: (mercator_x - world_min_x) / tile_extent
        # Tile Y: (world_max_y - mercator_y) / tile_extent (Y is inverted in tile coordinates)
        min_tile_x = math.floor((min_x - world_min_x) / tile_extent)
        max_tile_x = math.floor((max_x - world_min_x) / tile_extent)
        min_tile_y = math.floor((world_max_y - max_y) / tile_extent)
        max_tile_y = math.floor((world_max_y - min_y) / tile_extent)

        # Clamp to valid tile range
        min_tile_x = max(0, min_tile_x)
        max_tile_x = min(n_tiles - 1, max_tile_x)
        min_tile_y = max(0, min_tile_y)
        max_tile_y = min(n_tiles - 1, max_tile_y)

        zoom_count = 0
        for x in range(min_tile_x, max_tile_x + 1):
            for y in range(min_tile_y, max_tile_y + 1):
                combinations.append((x, y, z))
                zoom_count += 1
        tile_counts[z] = zoom_count

    return combinations, tile_counts


async def fetch_tile(
    session: aiohttp.ClientSession,
    x: int,
    y: int,
    z: int,
    base_url: str,
    raster_path: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Fetch a single tile and record response data."""
    params = params or {}
    scale = params.get("scale", 2)
    url = f"{base_url}/cog/tiles/WebMercatorQuad/{z}/{x}/{y}?scale={scale}&url={raster_path}"
    extra_params = {k: v for k, v in params.items() if k != "scale"}
    if extra_params:
        encoded_params = _urlencode(
            {
                k: json.dumps(v) if isinstance(v, (dict, list)) else v
                for k, v in extra_params.items()
            },
            doseq=True,
        )
        url += "&" + encoded_params

    t0 = time.perf_counter()
    try:
        async with session.get(url) as response:
            status = response.status
            content_length = len(await response.read()) if status == 200 else 0
        elapsed = time.perf_counter() - t0
    except Exception:
        elapsed = time.perf_counter() - t0
        raise
    return {
        "x": x,
        "y": y,
        "z": z,
        "url": url,
        "status": status,
        "wait_time_s": round(elapsed, 4),
        "content_length": content_length,
        "success": status == 200,
        "error": None,
    }


async def fetch_all_tiles(
    session: aiohttp.ClientSession,
    combinations: list[tuple[int, int, int]],
    base_url: str,
    raster_path: str,
    max_concurrent: int = 50,
    params: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Fetch all tiles concurrently with a limit on concurrent requests."""
    semaphore = asyncio.Semaphore(max_concurrent)

    async def fetch_with_semaphore(
        session: aiohttp.ClientSession, x: int, y: int, z: int
    ) -> dict[str, Any]:
        async with semaphore:
            t0 = time.perf_counter()
            try:
                return await async_retry(
                    lambda: fetch_tile(
                        session, x, y, z, base_url, raster_path, params=params
                    )
                )
            except Exception as e:
                elapsed = time.perf_counter() - t0
                # Build URL for the error result
                params_local = params or {}
                scale = params_local.get("scale", 2)
                url = f"{base_url}/cog/tiles/WebMercatorQuad/{z}/{x}/{y}?scale={scale}&url={raster_path}"
                return {
                    "x": x,
                    "y": y,
                    "z": z,
                    "url": url,
                    "status": None,
                    "wait_time_s": round(elapsed, 4),
                    "content_length": 0,
                    "success": False,
                    "error": str(e),
                }

    results = []
    tasks = [fetch_with_semaphore(session, x, y, z) for x, y, z in combinations]
    results = await asyncio.gather(*tasks)

    return results


async def main(
    endpoint: str,
    raster_path: str,
    max_concurrent: int = 50,
    output_file: str | None = None,
    output_dir: str = "output",
    params: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:

    print("Concurrent Tile Requester")

    try:
        raster_name = Path(raster_path).name

        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30),
        ) as session:
            # Fetch tilejson, validation, and statistics responses.
            print(f"Fetching tilejson for {raster_name}")
            tilejson = await async_retry(
                lambda: fetch_tilejson(session, endpoint, raster_path)
            )
            min_zoom = tilejson["minzoom"]
            max_zoom = tilejson["maxzoom"]
            extent = tilejson["bounds"]
            print(f"Raster extent: {extent}")
            print(f"Zoom range from tilejson: {min_zoom} to {max_zoom}")

            print(f"Fetching validate for {raster_name}")
            validate = await async_retry(
                lambda: fetch_validate(session, endpoint, raster_path)
            )

            print(f"Fetching statistics for {raster_name}")
            statistics = await async_retry(
                lambda: fetch_statistics(session, endpoint, raster_path)
            )

            # Save JSON responses to output directory
            output_path = Path(__file__).resolve().parent / output_dir
            output_path.mkdir(exist_ok=True)
            tilejson_path = output_path / f"tilejson_{Path(raster_name).stem}.json"
            validate_path = output_path / f"validate_{Path(raster_name).stem}.json"
            statistics_path = output_path / f"statistics_{Path(raster_name).stem}.json"
            with open(tilejson_path, "w") as f:
                json.dump(tilejson, f, indent=2)
            with open(validate_path, "w") as f:
                json.dump(validate, f, indent=2)
            with open(statistics_path, "w") as f:
                json.dump(statistics, f, indent=2)
            print(f"TileJSON saved to {tilejson_path}")
            print(f"Validate JSON saved to {validate_path}")
            print(f"Statistics JSON saved to {statistics_path}")

            # Calculate tile combinations
            print("Calculating tile combinations")
            combinations, tile_counts = calculate_tile_combinations(
                extent, min_zoom, max_zoom
            )

            print(f"Total tile combinations: {len(combinations)}")
            print("Tile count per zoom level:")
            for z in range(min_zoom, max_zoom + 1):
                print(f"  Zoom {z:2d}: {tile_counts[z]:>8} tiles")

            # Fetch all tiles concurrently
            print(
                f"Fetching tiles for {raster_name} (max {max_concurrent} concurrent requests)"
            )
            with Timer() as timer:
                results = await fetch_all_tiles(
                    session,
                    combinations,
                    endpoint,
                    raster_path,
                    max_concurrent=max_concurrent,
                    params=params,
                )

            total_time = timer.elapsed
            print(f"Completed in {total_time:.2f} seconds")

            # Analyze results
            successful = [r for r in results if r["success"]]
            failed = [r for r in results if not r["success"]]

            print(f"Results Summary")
            print(f"Total requests: {len(results)}")
            print(
                f"Successful: {len(successful)} ({len(successful)/len(results)*100:.1f}%)"
            )
            print(f"Failed: {len(failed)} ({len(failed)/len(results)*100:.1f}%)")

            if successful:
                wait_times = [r["wait_time_s"] for r in successful]
                print(f"Wait time statistics for successful requests:")
                print(f"  Minimum: {min(wait_times):.4f}s")
                print(f"  Maximum: {max(wait_times):.4f}s")
                print(f"  Average: {sum(wait_times)/len(wait_times):.4f}s")

            # Save results to CSV
            output_path = Path(__file__).resolve().parent / output_dir
            output_path.mkdir(exist_ok=True)
            if output_file is None:
                csv_name = f"results_{Path(raster_name).stem}.csv"
                output_file = str(output_path / csv_name)
            else:
                output_file = str(output_path / output_file)
            with open(output_file, "w", newline="") as f:
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
                for r in results:
                    writer.writerow(
                        [
                            r["x"],
                            r["y"],
                            r["z"],
                            r["url"],
                            r["status"],
                            r["wait_time_s"],
                            r["content_length"],
                            r["success"],
                            r["error"],
                        ]
                    )

            print(f"Results saved to {output_file}")

            return results

    except Exception as e:
        print(f"Execution failed: {e}")
        traceback.print_exc()
