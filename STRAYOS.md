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
* Raster inputs and their TiTiler parameters are defined in `stress-test/rasters.json`; raster names must be unique because they are used in artifact filenames.
* Run peak load with `python stress-test/main.py --peak-load`. Add `--local` to any mode to target the development stack at `http://localhost`; start it first with `docker compose -f docker-compose-strayos-dev.yml up -d`.
* Output paths are relative to `stress-test/`. Runs save API responses and tile timing data, while peak-load artifacts are written to `stress-test/output_peak_load/`. Progress and final summaries include successful tile counts and transferred data; failures produce a nonzero exit code.

## CI/CD Pipeline

### Branch behavior

- `main` builds, pushes, and deploys the Docker image.
- Other branches have no configured pipeline.
- Pipelines use a clone depth of `1`.

### Deploy step

- Uses `atlassian/default-image:4` and `concurrency-group: titiler-app-deploy` to prevent overlapping production deploys.
- Authenticates to Azure with the service-principal variables listed below.
- Temporarily permits SSH from the runner's public IPv4 address, syncs the repository with `rsync`, deploys the services, and removes the NSG rule during cleanup.

### Required pipeline variables

| Variable | Scope | Purpose |
| --- | --- | --- |
| `DOCKER_HUB_USER` | Workspace | Docker Hub username/namespace used for the image repository. |
| `DOCKER_HUB_TOKEN` | Workspace | Docker Hub token used by `docker login`. |
| `DEPLOY_AZURE_ID` | Workspace | Azure service-principal client/application ID. |
| `DEPLOY_AZURE_PASS` | Workspace | Azure service-principal secret. |
| `AZURE_TENANT_ID` | Workspace | Azure tenant ID for service-principal login. |
| `AZURE_SUBSCRIPTION_ID` | Workspace | Azure subscription selected before NSG changes. |
| `SSH_KEY_B64` | Repository | Base64-encoded private SSH key used for VM access. |
| `TITILER_SERVER` | Repository | Public host or IP for the production VM. |
| `TITILER_SSH_USER` | Repository | SSH user for the production VM. |

### Deployment behavior

The pipeline pushes `${DOCKER_HUB_USER}/strayos-titiler:app` and syncs the repository to `~/strayos-titiler` without overwriting the VM's `.env`. Prometheus, Loki, and Alloy are recreated to reload bind-mounted configuration. The six backends are replaced sequentially to preserve availability, and Nginx is recreated last so its upstreams resolve and its file bind mount follows the inode replaced by `rsync`. See `bitbucket-pipelines.yml` for the exact sequence.

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

Addresses and Azure account identifiers are omitted because they can change; consult Azure and the Bitbucket deployment variables when needed.

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

Each backend is an independent process with its own Rasterio/GDAL state and cache; memory is not shared between workers. Development and production limit each backend container to 4 GiB. Backends use `/healthz` healthchecks, while Nginx uses `nginx -t`. The public health response includes installed TiTiler and geospatial library versions.

Production supports HTTP/2 for multiplexed browser tile requests, falls back to HTTP/1.1, and redirects port 80 to HTTPS. Local development remains HTTP-only.

### Observability and Logging

Prometheus collects backend metrics, node-exporter collects host metrics, and cAdvisor collects container metrics. Alloy reads the Nginx container log through the read-only Docker socket and sends events to Loki. Grafana presents both metrics and client-traffic logs.

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

Worst-case Docker local-log usage is approximately 1.23 GB. These rotated binary logs live under `/var/lib/docker/containers/<id>/local-logs/` and are read with `docker logs`. Loki's indexed events are stored separately in `loki-data`. Prometheus and Loki retain data for 7 days; Grafana and Alloy use `grafana-data` and `alloy-data`. Grafana provisioning files are under `dockerfiles/grafana/`.

Nginx emits compact JSON for completed TiTiler API requests, which Alloy ships asynchronously to Loki. Events contain the requester IP, normalized endpoint, status, request time, response size, upstream details, and cache status. Query strings are excluded because raster URLs may contain credentials; Grafana, metrics, healthcheck, and HTTP redirect requests are also excluded. Client IP remains a parsed JSON field rather than a Loki label to avoid high-cardinality streams.

Dashboard traffic totals include Nginx cache hits, while Starlette metrics include only backend work. Cached delivery includes `HIT`, `STALE`, `UPDATING`, and `REVALIDATED`; backend delivery includes `MISS`, `BYPASS`, `EXPIRED`, and uncached upstream requests. Client-IP panels support up to 10,000 result series per query, retaining a safety bound above Loki's default of 500.

Production exposes Grafana only through `/grafana/`; Prometheus and Loki remain internal. Anonymous Grafana access is disabled by default. Set `GRAFANA_ADMIN_PASSWORD` and, only when needed, `GRAFANA_ANONYMOUS_ENABLED=true` in the VM's `.env`.

### Production Environment Variables

Most production variables tune Rasterio/GDAL access to remote raster data rather than TiTiler features.

| Variable | Current value | Meaning |
| --- | --- | --- |
| `CPL_TMPDIR` | `/tmp` | Container-local GDAL temporary directory. |
| `GDAL_CACHEMAX` | `1024` | Decoded block-cache ceiling in MiB per backend process. |
| `GDAL_INGESTED_BYTES_AT_OPEN` | `32768` | Reads the first 32 KiB at open to reduce remote metadata requests. |
| `GDAL_DISABLE_READDIR_ON_OPEN` | `EMPTY_DIR` | Avoids remote directory listing; datasets requiring sidecar files may fail. |
| `GDAL_HTTP_MERGE_CONSECUTIVE_RANGES` | `YES` | Merges adjacent HTTP byte ranges. |
| `GDAL_HTTP_MULTIPLEX` | `YES` | Enables parallel HTTP/2 range reads when supported. |
| `GDAL_HTTP_VERSION` | `2` | Requests HTTP/2 from GDAL/libcurl. |
| `CPL_VSIL_CURL_CACHE_SIZE` | `200000000` | Process-wide downloaded-range cache, about 191 MiB per backend. |
| `PYTHONWARNINGS` | `ignore` | Suppresses warnings, including potentially useful deprecation notices. |
| `VSI_CACHE` | `FALSE` | Disables the generic per-file-handle cache; it does not disable `/vsicurl/` caching. |

## Caching

### Nginx Response Cache

Nginx uses the persistent `nginx-cache` volume for a shared 21 GiB cache. It caches tiles and `GET`/`HEAD` requests to `/cog/statistics`; STAC statistics and GeoJSON `POST /cog/statistics` are not cached. Cache keys include the scheme, host, path, and query string, so different raster URLs or options remain separate.

Successful GET responses advertise a 90-minute browser lifetime through `Cache-Control`. Nginx preserves that client header but keeps successful cached responses for 24 hours. Entries inactive for 24 hours or displaced as the cache approaches 21 GiB may be removed earlier. The volume survives normal container recreation but not volume deletion, explicit purging, or disk loss.

### GDAL Cache Behavior

`VSI_CACHE=FALSE` disables the per-file-handle byte-range cache because datasets are opened and closed per request. `GDAL_CACHEMAX=1024` is a ceiling for decoded blocks held by open datasets, not a startup allocation. Production also provides a process-wide 200,000,000-byte `/vsicurl/` cache, which can reuse downloaded ranges across dataset opens until eviction or worker restart. Development does not set this cache size explicitly.

### Memory Budget

```
6 backends * 1024 MiB GDAL_CACHEMAX = ~6 GiB decoded-block ceiling (not startup allocation)
6 backends * 200,000,000-byte /vsicurl/ cache = ~1.12 GiB total
No per-file VSI_CACHE overhead
4 GiB hard memory limit per backend container
32 GiB total RAM shared by GDAL cache, Python, Nginx, OS, request buffers, Azure Blob connections
```

Cache ceilings are not reserved allocations; backend memory must also accommodate Python, native libraries, request buffers, and other allocations.

## Request Routing

Nginx uses `hash $titiler_hash_key consistent`, keyed by the `url` query parameter when present and otherwise by the full request URI. Requests for one raster therefore usually reach the same backend and reuse that process's `/vsicurl/` cache.

Routing is best-effort: failed requests can spill to another backend, trading some cache duplication for availability. A hot raster may also concentrate CPU load on one worker; distributing it would improve CPU balance but reduce cache locality. Six single-worker backends enable this routing and leave two of the VM's eight vCPUs for Nginx, the OS, and supporting services.

## Sources

- TiTiler performance tuning: https://developmentseed.org/titiler/advanced/performance_tuning/
- GDAL configuration options: https://gdal.org/en/stable/user/configoptions.html
- GDAL virtual file systems: https://gdal.org/en/stable/user/virtual_file_systems.html
- Python `PYTHONWARNINGS`: https://docs.python.org/3/using/cmdline.html#envvar-PYTHONWARNINGS
