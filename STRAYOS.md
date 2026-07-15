# Strayos TiTiler Notes

This file is supposed to serve as a working documentation for the strayos specific implementation of titiler

There are two upstream origins to this project.
* A Main upstream which is github used to sync changes from the original repo to our fork
* a bitbucket upstream for us to push our changes for deployment

## Tile sizes

We assume that the raster's for which we fetch tiles for has a default block size of 512

## Building Wheel Files

Incase of doing a function app deployment or using in another app/service follow these steps

* Make any changes necessary changes
* build a wheel file to be using `build_whl_file.sh`
* check run tests.

This app is not primarily used via a whl file installation

## Local Development

* Start the Strayos TiTiler stack with `docker-compose-strayos-dev.yml` when you want to run code from this project instead of an installed package. Development Nginx uses `dockerfiles/nginx-strayos-dev.conf` and serves HTTP on `http://localhost` without requiring the production TLS certificates.
* Production deploys use `docker-compose-strayos-deploy.yml`, with the image supplied through `TITILER_IMAGE` and defaulting to `strayos1/strayos-titiler:app`.

## Stress Testing

* Activate the repository virtual environment before running a test: `source .venv/bin/activate`.
* Run peak load from any working directory with `python stress-test/main.py --peak-load` (adjust the script path when outside the repository root).
* Relative output directories are resolved against `stress-test/`. Peak-load artifacts, including the stitched histogram mosaic, are written to `stress-test/output_peak_load/` regardless of the caller's working directory.

## CI/CD Pipeline

### Branch behavior

- `main`: builds and pushes the Docker image, then exposes a manual production deploy step.
- Other branches: builds the Docker image only.
- The clone depth is `1`, so pipeline steps operate on a shallow clone of the current branch.

### Deploy step

- Uses `atlassian/default-image:4`.
- Uses `concurrency-group: titiler-production-deploy` so production deploys do not overlap.
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

## Deployment Flow

The production deployment runs against the Azure VM and deploys the `app` image built from the Bitbucket main branch:

```text
Bitbucket main branch
  -> build ${DOCKER_HUB_USER}/strayos-titiler:app
  -> push ${DOCKER_HUB_USER}/strayos-titiler:app
  -> create temporary NSG SSH allow rule
  -> rsync repository to ~/strayos-titiler on the VM
  -> export TITILER_IMAGE=${DOCKER_HUB_USER}/strayos-titiler:app
  -> docker compose -f docker-compose-strayos-deploy.yml pull
  -> docker compose -f docker-compose-strayos-deploy.yml up -d --wait --force-recreate prometheus
  -> docker compose -f docker-compose-strayos-deploy.yml up -d --wait loki alloy
  -> docker compose -f docker-compose-strayos-deploy.yml up -d --wait node-exporter cadvisor grafana
  -> for each backend from titiler-1 to titiler-6: docker compose -f docker-compose-strayos-deploy.yml up -d --wait <service>
  -> docker compose -f docker-compose-strayos-deploy.yml up -d --wait nginx
  -> docker compose -f docker-compose-strayos-deploy.yml exec nginx nginx -s reload
  -> delete temporary NSG rule
```

The deploy step force-recreates Prometheus, then starts Loki and Alloy before the remaining monitoring services and Grafana. Prometheus reads its bind-mounted configuration only at process startup, so the forced recreation ensures new scrape jobs are loaded instead of leaving the process on an older in-memory configuration. The deploy then starts workers sequentially with a short delay between services. This avoids replacing all six TiTiler backends at once. Nginx is started after the backends and Grafana exist so its upstream service names resolve cleanly. The deploy rsync excludes `.env`, so VM-local compose settings can persist across deployments.

## Azure Infrastructure: Production

| Resource | Value |
| --- | --- |
| Subscription | `1c2680bc-ad06-4e7a-a43f-f69099c8c2fe` (`Microsoft Azure Sponsorship`) |
| Resource group | `TiTiler` |
| VM name | `app-titiler` |
| VM size | `Standard_D8as_v5` |
| VM CPU | 8 vCPUs |
| VM RAM | 32 GiB |
| VM OS disk | 61 GB |
| VM public IP | `20.81.154.120` |
| VM private IP | `10.9.0.4` |
| VNet/subnet | `app-titiler-vnet` / `default` |
| NIC | `app-titiler266_z1` |
| NSG | `app-titiler-nsg` at NIC level (Azure NAT happens before NSG evaluation, so rules target the VM private IP) |
| NSG rule name | `titiler_${BITBUCKET_BRANCH}_deploy` (`/` and `\` replaced with `_`) |
| NSG destination port | `22` (SSH) |
| NSG rule priority | `250` (auto-incremented by the deploy script until a free slot is found) |
| SSH user | `azureuser` |
| SSH private key (manual) | `~/.ssh/app-titiler_key.pem` |
| SSH private key (pipeline) | `SSH_KEY_B64` (base64-encoded, decoded on the fly) |
| Pipeline SSH vars | `TITILER_SERVER` (public IP), `TITILER_SSH_USER`, `SSH_KEY_B64` |
| Service principal | `9e4d68a5-0c04-4094-aaea-1ac75ee9f35a` |

Existing SSH allow rules include `SSH_whitelist`. The deploy script deletes the temporary rule it creates with `az network nsg rule delete --no-wait` during cleanup.

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
  -> titiler-1:8000  # container: titiler-worker-1, 1 Uvicorn worker
  -> titiler-2:8000  # container: titiler-worker-2, 1 Uvicorn worker
  -> titiler-3:8000  # container: titiler-worker-3, 1 Uvicorn worker
  -> titiler-4:8000  # container: titiler-worker-4, 1 Uvicorn worker
  -> titiler-5:8000  # container: titiler-worker-5, 1 Uvicorn worker
  -> titiler-6:8000  # container: titiler-worker-6, 1 Uvicorn worker
```

Each TiTiler backend is a separate process with its own Python interpreter, Rasterio/GDAL state, and GDAL block cache. There is no shared memory cache across workers.

Each backend has a healthcheck that curls `http://localhost:8000/healthz` every 30s with a 10s timeout, 3 retries, and a 10s start period. Nginx has a lightweight `nginx -t` healthcheck with the same timing.

### Logging

All services use Docker's `local` logging driver with file rotation:

| Service | `max-size` | `max-file` | Max per container |
| ------- | ---------- | ---------- | ---------------- |
| titiler-1..6 | 50m | 3 | 150 MB |
| nginx | 50m | 3 | 150 MB |
| prometheus, node-exporter, cAdvisor, Grafana, Loki, Alloy | 10m | 3 | 30 MB each |

Total worst-case Docker local-log usage is approximately 1.23 GB across all production services. Logs are stored at `/var/lib/docker/containers/<id>/local-logs/` in binary format (view with `docker logs`). Loki's indexed event data is separate from these rotated container logs and is retained for 7 days in the `loki-data` named volume.

Prometheus stores its TSDB in `prometheus-data` with a 7-day sample retention period. Grafana stores its application data in `grafana-data`. Loki stores chunks and indexes in `loki-data` with a 7-day event retention period, and Alloy stores Docker log positions in `alloy-data` so a collector restart can resume without intentionally replaying the complete available log. Provisioned datasources and dashboards come from `dockerfiles/grafana/`.

Nginx emits compact JSON for completed HTTPS TiTiler API requests. The log deliberately excludes query strings because the `url` parameter can contain signed raster credentials. It also excludes `/grafana/`, `/metrics`, `/healthz`, and port-80 redirects. The event records a normalized endpoint, client-visible status, total Nginx request time, response size, upstream details, and cache delivery state. Cache keys, validity, routing, and stale-response behavior are unchanged by this instrumentation.

The request path performs no synchronous call to Loki: Nginx writes its normal container access log, and Alloy tails it asynchronously through Docker. The structured line is roughly a few hundred bytes per request. Loki retention is time-based rather than size-based, so `loki-data` disk growth must still be monitored as traffic changes even with the 7-day window.

Nginx routes requests using a consistent hash:

```nginx
hash $titiler_hash_key consistent;
```

- For normal COG tile requests, `$titiler_hash_key` is the `url` query parameter. Requests for the same raster URL should usually go to the same TiTiler backend, improving cache locality.
- Requests without a `url` query parameter fall back to hashing the full request URI.
- This is not hard pinning. If the selected upstream has an error or connection reset, Nginx may route a request to another backend.
- This was tested before and 86% to 99.7% of requests for each raster was found to be sticky

### Environment Variables

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

## Caching

### Nginx Response Cache

Nginx uses the `nginx-cache` named volume for a shared 21 GiB response cache. It caches tile responses and `GET`/`HEAD` requests to `/cog/statistics`; STAC statistics and GeoJSON `POST /cog/statistics` responses are not cached. The complete scheme, host, path, and query string form the cache key, so statistics requests with different COG URLs or options are separate entries.

TiTiler sends `Cache-Control: public, max-age=5400` on successful GET responses, telling browsers and other downstream clients to cache a response for 5,400 seconds (90 minutes). The Nginx tile and COG statistics cache locations ignore that upstream header only when calculating their internal cache lifetime and use `proxy_cache_valid 200 12h` instead. Nginx still passes the original `Cache-Control` header to clients. Consequently, a browser caches a response for 90 minutes while Nginx can reuse the stored response for 12 hours without contacting TiTiler.

The cache path also has `inactive=12h`. This is an eviction rule separate from the 12-hour freshness period: an entry that is not accessed for 12 hours can be deleted. Accessing an entry resets its inactivity timer, while the freshness period is measured from when the response was stored or last refreshed. Nginx may evict entries earlier when the combined tile and statistics cache approaches 21 GiB.

The named volume persists across normal Nginx container restarts and recreations. Removing the `nginx-cache` volume, explicitly purging the cache, or losing the VM disk removes the cached responses.

### VSI Cache Disabled

`VSI_CACHE=FALSE` disables GDAL's in-memory byte-range cache for `/vsicurl/` and `/vsis3/`. It was previously `TRUE` with `VSI_CACHE_SIZE=268435456` (256 MiB), but provided no benefit in this architecture:

- **Within a single request**: Each tile request opens the COG, reads metadata once (`rasterio.open()`), then reads tile data once (`dataset.read()`). No byte range is read twice.
- **Across requests**: Each request opens a fresh dataset and closes it when done, destroying the file handle and its VSI cache. The next request starts with an empty cache.

The VSI cache was allocated but never served a hit — dead memory overhead. Removing it frees up to 256 MiB per open handle.

### How `GDAL_CACHEMAX` differs

`GDAL_CACHEMAX=4096` caches **decoded raster blocks** at the process level, not per-file-handle. This persists across requests within the same worker:

```text
Request 1: rasterio.open() -> read metadata -> read tile blocks A, B -> close
Request 2: rasterio.open() -> read metadata -> read tile blocks B, C -> close
```

Without VSI cache: raw bytes are re-fetched from Azure each time (fine — GDAL only fetches the ranges it needs).  
With GDAL block cache: decoded blocks A and B from Request 1 may still be cached when Request 2 needs block B again, avoiding re-decode.

The GDAL block cache is the cache that matters. The VSI byte-range cache was a redundant layer.

### Memory Budget

```
6 backends * 4096 MiB GDAL_CACHEMAX = ~24 GiB ceiling (not startup allocation)
No VSI overhead
32 GiB total RAM shared by GDAL cache, Python, Nginx, OS, request buffers, Azure Blob connections
```

## Request Routing

### Cache Locality

Consistent hashing routes the same raster URL to the same backend:

```text
ortho_cog.tif tile 1 -> titiler-worker-4
ortho_cog.tif tile 2 -> titiler-worker-4
ortho_cog.tif tile 3 -> titiler-worker-4
```

This keeps each raster's decoded blocks in one backend's `GDAL_CACHEMAX`, avoiding duplication across workers.

### Hot Raster Tradeoff

If one raster receives most traffic, its backend can become CPU-bound. Cache locality improves, but CPU spreading is reduced. If this becomes a bottleneck, the hash key could include part of the tile coordinate — at the cost of cache locality.

### Upstream Spillover

Consistent hashing is best-effort, not hard pinning. If the selected backend returns a connection reset, timeout, or other upstream error, Nginx routes to another backend. This causes some cache duplication but keeps the service available.

### Worker Count Rationale

Six single-worker backends (not `uvicorn --workers 6`) reserve 2 of the 8 vCPUs for the OS, Nginx, and overhead, while keeping each backend addressable by Nginx for per-URL routing.

### Monitoring and Traffic Observability

The stack keeps two complementary observability paths:

- Prometheus, node-exporter, and cAdvisor provide backend, VM, and container metrics. Removing them would break backend health and the infrastructure dashboard.
- Alloy and Loki provide exact Nginx request events. Removing them would break the client-facing traffic, delivery-source, status, latency, and endpoint panels.
- Grafana presents both data sources. These services overlap in presentation, not in what they measure.

| Service | Internal port | Public access |
| ------- | ------------- | ------------- |
| Prometheus | `9090` | None; Docker network only. |
| node-exporter | `9100` | None; Docker network only. |
| cAdvisor | `8080` | None; Docker network only. |
| Loki | `3100` | None; Docker network only. |
| Grafana | `3000` | `https://titiler.strayos.com/grafana/` through nginx. |

- **Prometheus** scrapes metrics from the TiTiler backends using `dockerfiles/prometheus.yml`.
- **Alloy** discovers only the `nginx` container through the read-only Docker socket and forwards its logs to Loki. Its persisted positions live in `alloy-data`.
- **Loki** stores the Nginx client-traffic events for 7 days using the single-binary filesystem configuration in `dockerfiles/loki/loki-config.yml`.
- **Grafana** is pre-provisioned with Prometheus and Loki datasources and the `TiTiler API Traffic & Performance` and `TiTiler Infrastructure & Containers` dashboards from `dockerfiles/grafana/`.
- `API Requests Served`, `API Requests/sec`, `Requests By Status Code`, `Request Latency (Nginx)`, and `Top Endpoints` are computed from Nginx events in Loki. They represent client-visible HTTPS TiTiler API responses and include both cached and backend-served responses.
- `Cached Requests Served` includes Nginx `HIT`, `STALE`, `UPDATING`, and `REVALIDATED` responses.
- `Backend Requests Served` includes responses obtained from an upstream on `MISS`, `BYPASS`, or `EXPIRED`, plus uncached API locations that contacted an upstream.
- The dashboard intentionally shows dedicated delivery totals only for cached and backend-served responses. Rare responses generated directly by Nginx remain included in `API Requests Served` and the status-code panel but do not have a separate summary panel.
- Nginx normalizes high-cardinality tile coordinates into endpoint templates before logging. Unknown paths are grouped as `other_api`; raw request URIs are not used as Loki labels.
- Starlette metrics remain available as backend diagnostics. They measure work that reached TiTiler and can be lower than Nginx request totals when Nginx serves cache hits.
- `node-exporter` covers VM CPU, memory, and filesystem metrics.
- `cAdvisor` covers container CPU, memory, network, and filesystem usage.
- Prometheus is force-recreated during deployment because it does not automatically reload changes to the bind-mounted `prometheus.yml` file.
- Production does not publish host ports `3000`, `3100`, or `9090`; users reach Grafana only through nginx.
- Grafana is configured for sub-path serving with `GF_SERVER_ROOT_URL=https://titiler.strayos.com/grafana/` and `GF_SERVER_SERVE_FROM_SUB_PATH=true`.
- Anonymous access is disabled by default. Set `GRAFANA_ANONYMOUS_ENABLED=true` in `~/strayos-titiler/.env` for a public view-only dashboard, and set `GRAFANA_ADMIN_PASSWORD` there to avoid the default fallback password.

#### Local observability validation (2026-07-14)

- The development compose stack uses `dockerfiles/nginx-strayos-dev.conf`, publishes only port 80, and does not mount or require production TLS certificates. Production continues to use `dockerfiles/nginx-strayos.conf` with HTTPS.
- A cold tile request logged `delivery_source=backend` and `cache_status=MISS`; the identical follow-up logged `delivery_source=cache` and `cache_status=HIT`.
- Loki returned an exact total of 3 for an initial sequence of two tile requests and one validation request, split into 1 cached and 2 backend-served responses. Status, latency, and normalized endpoint queries all returned successfully.
- A 500-request cached-tile smoke test at concurrency 32 returned 500 HTTP 200 responses in 1.488 seconds (336.1 requests/second from the local container client). The Loki tile total increased exactly from 2 to 502. The run itself split into 32 backend and 468 cached responses because its `nginx` test hostname created a cold cache key for the first concurrent wave.
- This confirms event accounting and ingestion under a short burst. It is not an enabled-versus-disabled production performance benchmark, so production CPU, disk growth, and latency should still be watched after rollout.

## Checking Routing Stickiness

Collect logs on the VM:

```bash
mkdir -p titiler-log-dump

docker compose -f docker-compose-strayos-deploy.yml ps > titiler-log-dump/compose-ps.txt 2>&1

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

Redact signed tokens from URLs before sharing logs. For stronger proof, add an Nginx log format that includes `$upstream_addr`, `$arg_url`, and `$titiler_hash_key`.

## Sources

- TiTiler performance tuning: https://developmentseed.org/titiler/advanced/performance_tuning/
- GDAL configuration options: https://gdal.org/en/stable/user/configoptions.html
- GDAL virtual file systems: https://gdal.org/en/stable/user/virtual_file_systems.html
- Python `PYTHONWARNINGS`: https://docs.python.org/3/using/cmdline.html#envvar-PYTHONWARNINGS
