# Strayos TiTiler Notes

This file is the single place for Strayos-specific TiTiler deployment notes.

## Tile sizes

- assume that the raster in question has a default block size of 512

## Building

- Make any changes necessary and build a wheel file to be used for integration into other projects. The `build_whl_file.sh` script can be used to build the wheel file.
- Start the TiTiler server using `docker-compose-strayos.yml` when you want to run code from this project instead of an installed package.

## VM Info

The Strayos TiTiler deployment is tuned for the Azure `Standard_D8as_v5` VM size:

```text
Azure size: Standard_D8as_v5
CPU:        8 vCPUs
RAM:        32 GiB
```

The compose file runs six TiTiler backend services, each with one Uvicorn worker. This gives the VM one TiTiler process per CPU core while still allowing Nginx to route requests by raster URL.

The current cache settings are chosen for this VM size:

```text
GDAL_CACHEMAX=4096        # 4096 MiB per backend process
VSI_CACHE=FALSE           # disabled (see "VSI Cache Disabled" section below)
```

## Runtime Topology

`docker-compose-strayos.yml` runs six TiTiler backend services:

```text
nginx
  -> titiler-1:8000  # container: titiler-worker-1, 1 Uvicorn worker
  -> titiler-2:8000  # container: titiler-worker-2, 1 Uvicorn worker
  -> titiler-3:8000  # container: titiler-worker-3, 1 Uvicorn worker
  -> titiler-4:8000  # container: titiler-worker-4, 1 Uvicorn worker
  -> titiler-5:8000  # container: titiler-worker-5, 1 Uvicorn worker
  -> titiler-6:8000  # container: titiler-worker-6, 1 Uvicorn worker
```

Each TiTiler backend is a separate process with its own Python interpreter, Rasterio/GDAL state, and GDAL block cache. There is no shared memory cache across workers.

Nginx routes requests using a consistent hash:

```nginx
hash $titiler_hash_key consistent;
```

For normal COG tile requests, `$titiler_hash_key` is the `url` query parameter. Requests for the same raster URL should usually go to the same TiTiler backend, improving cache locality. Requests without a `url` query parameter fall back to hashing the full request URI.

This is not hard pinning. If the selected upstream has an error or connection reset, Nginx may route a request to another backend. In the June 12, 2026 test logs, raster routing was mostly sticky, with dominant workers receiving about 86% to 99.7% of requests for each raster.

## Environment Variables

Most configured variables are GDAL settings consumed through Rasterio/GDAL while TiTiler reads Cloud-Optimized GeoTIFFs and other remote raster data. They are not `TITILER_API_*` settings for enabling or disabling TiTiler application features.

| Variable | Current value | Meaning |
| --- | --- | --- |
| `CPL_TMPDIR` | `/tmp` | Points GDAL/CPL temporary-file operations at container-local `/tmp`. |
| `GDAL_CACHEMAX` | `4096` | Sets GDAL's decoded raster block cache ceiling to 4096 MiB per TiTiler backend process. |
| `GDAL_INGESTED_BYTES_AT_OPEN` | `32768` | Makes GDAL read the first 32 KiB of a remote file when opening it, which can reduce extra metadata range requests for COGs. |
| `GDAL_DISABLE_READDIR_ON_OPEN` | `EMPTY_DIR` | Prevents GDAL from listing the containing directory or bucket by pretending only the requested file exists. This reduces remote `LIST` calls, but can break datasets that need sidecar files such as external `.ovr` overviews. |
| `GDAL_HTTP_MERGE_CONSECUTIVE_RANGES` | `YES` | Lets GDAL merge adjacent byte ranges into one HTTP request. |
| `GDAL_HTTP_MULTIPLEX` | `YES` | Allows HTTP/2 multiplexing for parallel range reads when the server and libcurl support it. |
| `GDAL_HTTP_VERSION` | `2` | Tells GDAL/libcurl to attempt HTTP/2 for HTTP/HTTPS requests. |
| `PYTHONWARNINGS` | `ignore` | Suppresses Python warnings. This keeps logs quiet, but can hide useful dependency or deprecation warnings. |
| `VSI_CACHE` | `FALSE` | Disables GDAL's VSI cache. Disabled because in this architecture each request opens and closes the dataset once, so no byte range is ever read twice within a file handle's lifetime. |

## VSI Cache Disabled

`VSI_CACHE=FALSE` disables GDAL's in-memory byte-range cache for `/vsicurl/` and `/vsis3/`. This was previously `TRUE` with `VSI_CACHE_SIZE=268435456` (256 MiB), but was changed because the VSI cache provided no benefit in this architecture.

### Why the VSI cache was ineffective

The VSI cache caches raw downloaded byte ranges per open GDAL file handle. It only helps when the same byte range is requested more than once while the handle is open. In this TiTiler deployment:

1. **Within a single request**: Each tile request opens the COG, reads metadata once during `rasterio.open()`, then reads the needed tile data blocks once during `dataset.read()`. No byte range is ever read twice.

2. **Across requests**: Each HTTP request opens a fresh dataset (`with self.reader(...) as src_dst:`) and closes it when done. Closing the dataset destroys the file handle and its VSI cache. The next request gets a new empty cache.

So the VSI cache was allocated but never served a single hit — it was dead memory overhead. Removing it frees up to 256 MiB per open handle that would otherwise sit unused.

### How `GDAL_CACHEMAX` is different

`GDAL_CACHEMAX` (set to 4096 MiB) caches **decoded raster blocks** at the process level, not per-file-handle. This cache persists across requests within the same worker:

```text
Request 1: rasterio.open() -> read metadata -> read tile blocks A, B -> close
Request 2: rasterio.open() -> read metadata -> read tile blocks B, C -> close
```

Without VSI cache: raw metadata bytes and tile blocks are re-fetched from Azure each time. This is fine because GDAL only fetches the specific byte ranges it needs.

With GDAL block cache: decoded blocks A and B (from Request 1) may still be in the block cache when Request 2 needs block B again, avoiding re-decode.

The GDAL block cache is the cache that matters for this architecture. The VSI byte-range cache was a redundant layer.

### Memory impact

With VSI_CACHE disabled:

```text
6 backend processes * 4096 MiB = about 24 GiB GDAL block-cache ceiling
```

No VSI cache memory overhead. All 32 GiB of VM RAM is available for the GDAL block cache, Python, Nginx, the OS, request buffers, and Azure Blob Storage connections.

## Case-Based Behavior

### 6 Workers on a 32 GB VM

The current layout is appropriate for an 8 CPU, 32 GB VM: six backend services, each with one Uvicorn worker. This keeps one process per CPU core (with two cores reserved for the OS, Nginx, and overhead) while allowing Nginx to route by raster URL. Running one service with `uvicorn --workers 6` would hide those workers behind one socket, so Nginx could not route requests by raster URL.

### Same Raster Requested Repeatedly

Requests for the same `url` should usually go to the same backend. That improves locality for `GDAL_CACHEMAX`.

Example:

```text
ortho_cog.tif tile 1 -> titiler-worker-4
ortho_cog.tif tile 2 -> titiler-worker-4
ortho_cog.tif tile 3 -> titiler-worker-4
```

This is better than spreading the same raster across all workers, because each worker has its own GDAL block cache.

### 10 Different Rasters

Memory ceiling with `GDAL_CACHEMAX=4096`:

```text
6 backend processes * 4096 MiB = about 24 GiB GDAL block-cache ceiling
```

Actual memory can be much lower because this is a ceiling, not a startup allocation.

### 20 Different Rasters

Same ceiling: about 24 GiB max for the GDAL block cache. No VSI cache overhead.

### One Hot Raster

Consistent hashing can make one backend hot if one raster receives most traffic:

```text
very popular raster -> one TiTiler backend gets most of that raster's traffic
```

This improves cache locality but may reduce CPU spreading for that one raster. If this becomes a bottleneck, the hash key could include part of the tile coordinate, but that trades away cache locality.

### Upstream Errors and Spillover

Nginx consistent hashing is not a strict guarantee. If the selected backend has a connection reset, timeout, or other upstream failure, Nginx can send a request to another backend. This causes some cache duplication, but keeps the service more available.

## Checking Routing Stickiness

To collect logs on the VM:

```bash
mkdir -p titiler-log-dump

docker compose -f docker-compose-strayos.yml ps > titiler-log-dump/compose-ps.txt 2>&1

docker inspect -f '{{.Name}} {{range.NetworkSettings.Networks}}{{.IPAddress}}{{end}}' \
  nginx titiler-worker-1 titiler-worker-2 titiler-worker-3 titiler-worker-4 titiler-worker-5 titiler-worker-6 \
  > titiler-log-dump/container-ips.txt 2>&1

docker logs --timestamps nginx > titiler-log-dump/nginx.log 2>&1
docker logs --timestamps titiler-worker-1 > titiler-log-dump/titiler-worker-1.log 2>&1
docker logs --timestamps titiler-worker-2 > titiler-log-dump/titiler-worker-2.log 2>&1
docker logs --timestamps titiler-worker-3 > titiler-log-dump/titiler-worker-3.log 2>&1
docker logs --timestamps titiler-worker-4 > titiler-log-dump/titiler-worker-4.log 2>&1
docker logs --timestamps titiler-worker-5 > titiler-log-dump/titiler-worker-5.log 2>&1
docker logs --timestamps titiler-worker-6 > titiler-log-dump/titiler-worker-6.log 2>&1

tar -czf titiler-log-dump.tar.gz titiler-log-dump
```

If URLs contain signed tokens, redact secrets before sharing logs.

For stronger proof, add an Nginx access log format that includes `$upstream_addr`, `$arg_url`, and `$titiler_hash_key`.

## Sources

- TiTiler performance tuning: https://developmentseed.org/titiler/advanced/performance_tuning/
- GDAL configuration options: https://gdal.org/en/stable/user/configoptions.html
- GDAL virtual file systems: https://gdal.org/en/stable/user/virtual_file_systems.html
- Python `PYTHONWARNINGS`: https://docs.python.org/3/using/cmdline.html#envvar-PYTHONWARNINGS
