import math
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
from matplotlib.lines import Line2D


def plot_response_times(
    results: list[dict[str, Any]],
    raster_name: str,
    max_concurrent: int,
    output_dir: str = "output",
) -> None:
    """Plot a histogram of response times from tile fetch results."""
    if not results:
        print(f"No results to plot for {raster_name}")
        return

    wait_times = [r["wait_time_s"] for r in results if r["status"] == 200]
    if not wait_times:
        print(f"No successful requests to plot for {raster_name}")
        return

    total_requests = len(wait_times)
    total_load_time = sum(wait_times)
    min_time = min(wait_times)
    max_time = max(wait_times)
    avg_time = sum(wait_times) / total_requests

    sorted_times = sorted(wait_times)
    p33 = sorted_times[int(total_requests * 0.33)]
    p66 = sorted_times[int(total_requests * 0.66)]
    p99 = sorted_times[int(total_requests * 0.99)]

    plt.figure(figsize=(16, 9))
    plt.hist(wait_times, bins=50, edgecolor="black", alpha=0.7)

    for percentile, value, color, ls in [
        (33, p33, "orange", "--"),
        (66, p66, "green", "--"),
        (99, p99, "red", "-"),
    ]:
        plt.axvline(
            value,
            color=color,
            linestyle=ls,
            linewidth=1.5,
            label=f"{percentile}th percentile ({value:.2f}s)",
        )

    legend_handles, legend_labels = plt.gca().get_legend_handles_labels()
    legend_handles.extend(
        [
            Line2D(
                [0],
                [0],
                color="none",
                marker="",
                label=f"Total Requests: {total_requests}",
            ),
            Line2D(
                [0],
                [0],
                color="none",
                marker="",
                label=f"Total Load Time: {total_load_time:.2f}s",
            ),
            Line2D([0], [0], color="none", marker="", label=f"Min: {min_time:.2f}s"),
            Line2D([0], [0], color="none", marker="", label=f"Avg: {avg_time:.2f}s"),
            Line2D([0], [0], color="none", marker="", label=f"Max: {max_time:.2f}s"),
        ]
    )
    legend_labels.extend(
        [
            f"Total Requests: {total_requests}",
            f"Total Load Time: {total_load_time:.2f}s",
            f"Min: {min_time:.2f}s",
            f"Avg: {avg_time:.2f}s",
            f"Max: {max_time:.2f}s",
        ]
    )
    plt.legend(
        handles=legend_handles,
        labels=legend_labels,
        loc="upper right",
        fontsize=8,
        framealpha=0.9,
    )

    max_tick = math.ceil(max_time)
    plt.xticks([i * 0.5 for i in range(int(max_tick / 0.5) + 1)])
    plt.xlabel("Response Time (seconds)")
    plt.ylabel("Number of Requests")
    plt.title(
        f"Tile Request Response Times - {raster_name} (max {max_concurrent} concurrent)"
    )

    output_dir = Path(__file__).resolve().parent / output_dir
    output_dir.mkdir(exist_ok=True)
    output_path = output_dir / f"{raster_name}.png"
    plt.tight_layout()
    plt.savefig(output_path, dpi=500)
    plt.close()
    print(f"Histogram saved to {output_path}")
