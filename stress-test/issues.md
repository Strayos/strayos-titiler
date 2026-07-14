# Issues

| # | Issue | File | Lines | Details |
|---|-------|------|-------|---------|
| 1 | **World bounds as magic numbers** | `concurrent_tile_requester.py` | 105–108 | `±20037508.34` repeated — should be module-level constants |
| 2 | **No CLI / argparse** | `concurrent_tile_requester.py` | 239–360 | `main()` takes only Python args, so the script cannot be reused without editing source |
| 3 | **No TypedDict for results** | `concurrent_tile_requester.py` | 180–201 | Results are plain `dict[str, Any]` — no type safety on fields |
| 4 | **No `__all__`** | `concurrent_tile_requester.py` | — | Module exposes all functions publicly |
| 5 | **`tile_counts` computed eagerly** | `concurrent_tile_requester.py` | 116–141 | Dict built alongside combinations but only used for display |
| 6 | **URL encoding** | `concurrent_tile_requester.py` | 50, 74, 170 | `raster_path` interpolated into query strings without `urllib.parse.quote` |
| 7 | **Absolute output path** | `concurrent_tile_requester.py` | 327 | `output_dir / output_file` silently writes outside `output/` if an absolute path is passed |
