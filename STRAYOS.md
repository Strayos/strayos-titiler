# Strayos TiTiler Notes

This file documents the Strayos-specific TiTiler implementation and its production operations.

The repository uses GitHub to sync changes from the original TiTiler project and Bitbucket for Strayos deployment.

## Building Wheel Files

This application is normally deployed as a container. When another application or a function deployment requires a wheel, run the tests and then build it with `build_whl_file.sh`.

## Local Development

* Start the Strayos TiTiler stack with `docker-compose-strayos-dev.yml` when you want to run code from this project instead of an installed package. Development Nginx uses `dockerfiles/nginx-strayos-dev.conf` and serves HTTP on `http://localhost` without requiring the production TLS certificates.
* Production deploys use `docker-compose-strayos-deploy.yml`, with the image supplied through `TITILER_IMAGE` and defaulting to `strayos1/strayos-titiler:app`.

## Stress Testing

* Activate the repository virtual environment before running a test: `source .venv/bin/activate`.
* Run peak load from any working directory with `python stress-test/main.py --peak-load` (adjust the script path when outside the repository root).
* Relative output directories are resolved against `stress-test/`. Each raster run stores the TileJSON, validation, and COG statistics responses alongside its tile timing CSV. Peak-load artifacts, including the stitched histogram mosaic, are written to `stress-test/output_peak_load/` regardless of the caller's working directory.

## CI/CD Pipeline

### Branch behavior

- `main`: builds and pushes the Docker image, then runs the production deploy step.
- No pipeline is currently configured for other branches.
- The clone depth is `1`, so pipeline steps operate on a shallow clone of the current branch.

### Deploy step

- Uses `atlassian/default-image:4`.
- Uses `concurrency-group: titiler-app-deploy` so production deploys do not overlap.
- Logs in to Azure with the service-principal variables `DEPLOY_AZURE_ID`, `DEPLOY_AZURE_PASS`, `AZURE_TENANT_ID`, and `AZURE_SUBSCRIPTION_ID`.
- Gets the Bitbucket runner public IP with `curl -s4 icanhazip.com`. The `-4` flag is required because the NSG rule targets an IPv4 destination.
- Temporarily opens SSH from the runner IP, syncs the repo to the VM with `rsync`, starts the six TiTiler services one at a time, then reloads Nginx.

### Required pipeline variables:

| Variable | Scope | Purpose |
| --- | --- | --- |
| `DOCKER_HUB_USER` | Workspace | Docker Hub username/namespace used for the image repository. |
| `DOCKER_HUB_TOKEN` | Workspace | Docker Hub token used by `docker login`. |
| `DEPLOY_AZURE_ID` | Workspace | Azure service-principal client/application ID. |
| `DEPLOY_AZURE_PASS` | Workspace | Azure service-principal secret. |
| `AZURE_TENANT_ID` | Workspace | Azure tenant ID for service-principal login. |
| `AZURE_SUBSCRIPTION_ID` | Workspace | Azure subscription selected before NSG changes. |
| `SSH_KEY_B64` | Repository | Base64-encoded private SSH key used for VM access. Decoded on the fly for usage |
| `TITILER_SERVER` | Repository | Public host or IP for the production VM. |
| `TITILER_SSH_USER` | Repository | SSH user for the production VM. |

### Deployment behavior

The pipeline builds and pushes `${DOCKER_HUB_USER}/strayos-titiler:app`, temporarily allows SSH from the runner, and rsyncs the repository to `~/strayos-titiler` without overwriting the VM's `.env`. It then pulls the image and starts the observability services, six TiTiler backends, and Nginx. The temporary NSG rule is deleted during cleanup.

Prometheus is force-recreated so it reads its bind-mounted configuration. Backends are replaced sequentially to avoid stopping all six at once. Nginx is recreated last so its upstream names resolve and its bind mount follows the configuration inode replaced by rsync. `bitbucket-pipelines.yml` is the source of truth for the exact command sequence.

## Azure Infrastructure: Production

| Resource | Value |
| --- | --- |
| Resource group | `TiTiler` |
| VM name | `app-titiler` |
| VM size | `Standard_D8as_v5` |
| VM CPU | 8 vCPUs |
| VM RAM | 32 GiB |
| VM OS disk | 61 GB |
| NSG | `app-titiler-nsg` at NIC level (Azure NAT happens before NSG evaluation, so rules target the VM private IP) |
| NSG rule name | `titiler_${BITBUCKET_BRANCH}_deploy` (`/` and `\` replaced with `_`) |
| NSG destination port | `22` (SSH) |
| NSG rule priority | `250` (auto-incremented by the deploy script until a free slot is found) |
| SSH user | `azureuser` |

Current addresses and Azure account identifiers are intentionally omitted because they can change. Consult Azure and the Bitbucket deployment variables when they are needed.

### Runtime Topology

`docker-compose-strayos-deploy.yml` runs six TiTiler backend services plus the routing and observability services:

```text
public web :80/:443
  -> nginx
      /grafana/ -> grafana:3000
      /         -> titiler_backends
      structured API access events -> Docker local log
grafana
  -> prometheus:9090
  -> loki:3100
prometheus
  -> titiler-1..6:8000/metrics
node-exporter
  -> VM host metrics
cadvisor
  -> Docker container metrics
alloy
  -> reads the nginx container log through the read-only Docker socket
  -> loki:3100
loki
  -> 7-day client-traffic event store in the loki-data volume
titiler_backends
  -> titiler-1..6:8000  # one Uvicorn worker per container
```

Each TiTiler backend is a separate process with its own Python interpreter, Rasterio/GDAL state, and GDAL block cache. There is no shared memory cache across workers. Both development and production limit each TiTiler container to 4 GiB of memory.

Each backend has a healthcheck that curls `http://localhost:8000/healthz` every 30s with a 10s timeout, 3 retries, and a 10s start period. Nginx has a lightweight `nginx -t` healthcheck with the same timing.

The public `https://titiler.strayos.com/healthz` response includes installed version information for TiTiler, rio-tiler, Rasterio, GDAL, PROJ, and GEOS under the `versions` object.

The production TLS listener negotiates HTTP/2 with compatible clients, allowing concurrent browser tile requests to share one multiplexed connection. Clients without HTTP/2 support fall back to HTTP/1.1. The port-80 listener continues to redirect to HTTPS, and the local development stack remains HTTP-only.

### Observability and Logging

Prometheus collects backend metrics, node-exporter collects VM metrics, and cAdvisor collects container metrics. Alloy asynchronously reads only the Nginx container log through the read-only Docker socket and sends its events to Loki. Grafana presents the Prometheus infrastructure data and Loki client-traffic data.

| Service | Internal port | Production access |
| ------- | ------------- | ----------------- |
| Prometheus | `9090` | Docker network only |
| node-exporter | `9100` | Docker network only |
| cAdvisor | `8080` | Docker network only |
| Loki | `3100` | Docker network only |
| Grafana | `3000` | `https://titiler.strayos.com/grafana/` through Nginx |

All services use Docker's `local` logging driver with file rotation:

| Service | `max-size` | `max-file` | Max per container |
| ------- | ---------- | ---------- | ---------------- |
| titiler-1..6 | 50m | 3 | 150 MB |
| nginx | 50m | 3 | 150 MB |
| prometheus, node-exporter, cAdvisor, Grafana, Loki, Alloy | 10m | 3 | 30 MB each |

Total worst-case Docker local-log usage is approximately 1.23 GB across all production services. Logs are stored at `/var/lib/docker/containers/<id>/local-logs/` in binary format (view with `docker logs`). Loki's indexed event data is separate from these rotated container logs and is retained for 7 days in the `loki-data` named volume.

Prometheus stores its TSDB in `prometheus-data` with 7-day retention. Grafana uses `grafana-data`, Loki uses `loki-data` with 7-day retention, and Alloy stores its read positions in `alloy-data`. Datasources and the `TiTiler API Traffic & Performance` and `TiTiler Infrastructure & Containers` dashboards are provisioned from `dockerfiles/grafana/`.

Nginx emits compact JSON for completed HTTPS TiTiler API requests. The log deliberately excludes query strings because the `url` parameter can contain signed raster credentials. It also excludes `/grafana/`, `/metrics`, `/healthz`, and port-80 redirects. The event records the direct requester IP from Nginx's `$remote_addr`, a normalized endpoint, client-visible status, total Nginx request time, response size, upstream details, and cache delivery state. The IP remains a JSON log field and is not promoted to a Loki label, avoiding a high-cardinality stream for every client. Cache keys, validity, routing, and stale-response behavior are unchanged by this instrumentation.

The request path makes no synchronous call to Loki. Loki retention is time-based rather than size-based, so disk growth must still be monitored as traffic changes. Dashboard request totals come from Nginx and include cache hits; Starlette metrics count only work that reached a backend. `Cached Requests Served` includes `HIT`, `STALE`, `UPDATING`, and `REVALIDATED`; `Backend Requests Served` includes `MISS`, `BYPASS`, `EXPIRED`, and uncached requests that contacted an upstream.

Production does not publish the Prometheus, Loki, or Grafana host ports. Grafana serves from `/grafana/`; anonymous access is disabled by default. VM-local `.env` values can set `GRAFANA_ANONYMOUS_ENABLED=true` and should set `GRAFANA_ADMIN_PASSWORD` instead of relying on the compose fallback.

### Production Environment Variables

Most configured variables are GDAL settings consumed through Rasterio/GDAL while TiTiler reads Cloud-Optimized GeoTIFFs and other remote raster data. They are not `TITILER_API_*` settings for enabling or disabling TiTiler application features.

| Variable | Current value | Meaning |
| --- | --- | --- |
| `CPL_TMPDIR` | `/tmp` | Points GDAL/CPL temporary-file operations at container-local `/tmp`. |
| `GDAL_CACHEMAX` | `1024` | Sets GDAL's decoded raster block cache ceiling to 1024 MiB per TiTiler backend process. Blocks belong to open datasets and are released when those datasets close. |
| `GDAL_INGESTED_BYTES_AT_OPEN` | `32768` | Makes GDAL read the first 32 KiB of a remote file when opening it, which can reduce extra metadata range requests for COGs. |
| `GDAL_DISABLE_READDIR_ON_OPEN` | `EMPTY_DIR` | Prevents GDAL from listing the containing directory or bucket by pretending only the requested file exists. This reduces remote `LIST` calls, but can break datasets that need sidecar files such as external `.ovr` overviews. |
| `GDAL_HTTP_MERGE_CONSECUTIVE_RANGES` | `YES` | Lets GDAL merge adjacent byte ranges into one HTTP request. |
| `GDAL_HTTP_MULTIPLEX` | `YES` | Allows HTTP/2 multiplexing for parallel range reads when the server and libcurl support it. |
| `GDAL_HTTP_VERSION` | `2` | Tells GDAL/libcurl to attempt HTTP/2 for HTTP/HTTPS requests. |
| `CPL_VSIL_CURL_CACHE_SIZE` | `200000000` | Sets GDAL's process-global `/vsicurl/` downloaded-range LRU cache to 200,000,000 bytes (about 191 MiB) per backend. Unlike `VSI_CACHE`, this cache can reuse ranges after a file handle closes and reopens. |
| `PYTHONWARNINGS` | `ignore` | Suppresses Python warnings. This keeps logs quiet, but can hide useful dependency or deprecation warnings. |
| `VSI_CACHE` | `FALSE` | Disables GDAL's generic per-file-handle VSI cache. Its expected benefit is limited because each request opens and closes its dataset; this does not disable the separate process-global `/vsicurl/` cache. |

## Caching

### Nginx Response Cache

Nginx uses the `nginx-cache` named volume for a shared 21 GiB response cache. It caches tile responses and `GET`/`HEAD` requests to `/cog/statistics`; STAC statistics and GeoJSON `POST /cog/statistics` responses are not cached. The complete scheme, host, path, and query string form the cache key, so statistics requests with different COG URLs or options are separate entries.

TiTiler sends `Cache-Control: public, max-age=5400` on successful GET responses, telling browsers and other downstream clients to cache a response for 5,400 seconds (90 minutes). The Nginx tile and COG statistics cache locations ignore that upstream header only when calculating their internal cache lifetime and use `proxy_cache_valid 200 24h` instead. Nginx still passes the original `Cache-Control` header to clients. Consequently, a browser caches a response for 90 minutes while Nginx can reuse the stored response for 24 hours without contacting TiTiler.

The cache path also has `inactive=24h`. This is an eviction rule separate from the 24-hour freshness period: an entry that is not accessed for 24 hours can be deleted. Accessing an entry resets its inactivity timer, while the freshness period is measured from when the response was stored or last refreshed. Nginx may evict entries earlier when the combined tile and statistics cache approaches 21 GiB.

The named volume persists across normal Nginx container restarts and recreations. Removing the `nginx-cache` volume, explicitly purging the cache, or losing the VM disk removes the cached responses.

### GDAL Cache Behavior

`VSI_CACHE=FALSE` disables the generic per-file-handle byte-range cache. TiTiler opens and closes a dataset for each request, so that cache cannot provide cross-request reuse. `GDAL_CACHEMAX=1024` instead limits decoded blocks for datasets that are currently open; it is a ceiling, not a startup allocation, and those blocks are released when their dataset closes.

Production explicitly sets `CPL_VSIL_CURL_CACHE_SIZE=200000000` for GDAL's separate process-global `/vsicurl/` downloaded-range cache. This cache can reuse ranges after a file handle closes until entries are evicted or the worker exits. Development does not explicitly set its size.

### Memory Budget

```
6 backends * 1024 MiB GDAL_CACHEMAX = ~6 GiB decoded-block ceiling (not startup allocation)
6 backends * 200,000,000-byte /vsicurl/ cache = ~1.12 GiB total
No per-file VSI_CACHE overhead
4 GiB hard memory limit per backend container
32 GiB total RAM shared by GDAL cache, Python, Nginx, OS, request buffers, Azure Blob connections
```

The development compose file uses the same 4 GiB per-backend memory limit and `GDAL_CACHEMAX=1024` setting as production, leaving container memory available for Python, native libraries, request buffers, and downloaded data outside GDAL's decoded-block cache.

## Request Routing

Nginx uses `hash $titiler_hash_key consistent`. The key is the `url` query parameter when present and otherwise the full request URI. Requests for one raster therefore usually reach the same backend, concentrating reusable production `/vsicurl/` ranges in one process.

This routing is best-effort. Nginx can spill a request to another backend after an upstream failure, improving availability at the cost of some cache duplication. A very hot raster can also make one backend CPU-bound; spreading such a raster across workers would improve CPU distribution but reduce cache locality.

Six independently addressable, single-worker containers make per-raster routing possible and leave two of the VM's eight vCPUs for Nginx, the OS, and supporting services.

## Sources

- TiTiler performance tuning: https://developmentseed.org/titiler/advanced/performance_tuning/
- GDAL configuration options: https://gdal.org/en/stable/user/configoptions.html
- GDAL virtual file systems: https://gdal.org/en/stable/user/virtual_file_systems.html
- Python `PYTHONWARNINGS`: https://docs.python.org/3/using/cmdline.html#envvar-PYTHONWARNINGS
