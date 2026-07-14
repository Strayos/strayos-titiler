"""Stitch histogram PNGs from output dirs into a 2-column mosaic."""

from pathlib import Path

from PIL import Image


def stitch_histograms(output_dirs: list[str]) -> None:
    """Find all histogram PNGs in given directories and stitch them into a 2-column mosaic.

    The mosaic is saved as ``mosaic.png`` inside each source directory.

    Parameters
    ----------
    output_dirs : list of str
        List of output directory paths to search for histogram PNGs.
    """
    histograms: list[Path] = []
    for d in output_dirs:
        histograms.extend(sorted(Path(d).glob("histogram_*.png")))

    if not histograms:
        print("No histogram PNGs found in the specified directories.")
        return

    source_dirs = sorted({p.parent for p in histograms})

    if len(histograms) == 1:
        print(f"Only one histogram found ({histograms[0]}), copying as-is.")
        img = Image.open(histograms[0])
        for d in source_dirs:
            out = d / "mosaic.png"
            img.save(out)
            print(f"Saved to {out}")
        return

    imgs = [Image.open(p) for p in histograms]
    widths = [im.width for im in imgs]
    heights = [im.height for im in imgs]
    col_width = max(widths)
    row_height = max(heights)

    n = len(imgs)
    cols = 2
    rows = (n + cols - 1) // cols

    mosaic = Image.new("RGB", (col_width * cols, row_height * rows), color="white")

    for i, im in enumerate(imgs):
        col = i % cols
        row_idx = i // cols
        x = col * col_width + (col_width - im.width) // 2
        y = row_idx * row_height + (row_height - im.height) // 2
        mosaic.paste(im, (x, y))

    for d in source_dirs:
        out = d / "mosaic.png"
        mosaic.save(out)
        print(
            f"Mosaic saved to {out} ({mosaic.width}x{mosaic.height}) "
            f"from {n} histograms in {rows}x{cols} grid."
        )
