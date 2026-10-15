# Strayos TiTiler Notes

This file documents the Strayos-specific TiTiler implementation and its production operations.

The repository uses GitHub to sync changes from the original TiTiler project and Bitbucket for Strayos deployment.

## Building Wheel Files

This application is normally deployed as a container. When another application or a function deployment requires a wheel, run the tests and then build it with `build_whl_file.sh`.

## Local Development

* Start the Strayos TiTiler stack with `docker-compose-strayos-dev.yml` when you want to run code from this project instead of an installed package. Development Nginx uses `dockerfiles/nginx-strayos-dev.conf` and serves HTTP on `http://localhost` without requiring the production TLS certificates.
* Production deploys use `docker-compose-strayos-deploy.yml`, with the image supplied through `TITILER_IMAGE` and defaulting to `strayos1/strayos-titiler:app`.
* The standalone ODP deployment at `https://titilerodp.strayos.com` uses `docker-compose-strayos-deploy-odp.yml`. It runs three backends on a dedicated 4-vCPU, 16-GiB VM; see **Azure Infrastructure: ODP** below.

## On-Prem Edge Deployment

`docker-compose-strayos-edge.yml` is an independent on-prem deployment with two TiTiler containers and one Uvicorn worker per container. It is not used by the Bitbucket pipeline and does not replace or extend either Strayos development or production compose file. Its Nginx configuration is edge-only so changing the edge worker count cannot alter the six-worker production stack.

The edge stack serves HTTP and is intended to run behind an on-prem TLS reverse proxy when HTTPS is required. Edge Nginx joins the external `strayos-edge` network under the `titiler` alias so separately managed control-node Compose projects can reach it at `http://titiler:80`. The TiTiler workers remain on a private network. MinIO runs on a separate physical host and is reached through its fixed LAN endpoint, not through Docker networking.

| Variable | Default | Purpose |
| --- | --- | --- |
| `TITILER_IMAGE` | `strayos1/strayos-titiler:app` | TiTiler image from Docker Hub, an internal registry, or a locally loaded tag. |
| `TITILER_DATA_PATH` | `.` | Host directory mounted read-only at `/data` in both TiTiler workers. |
| `TITILER_WORKER_MEMORY_LIMIT` | `4g` | Hard memory limit for each TiTiler container. |
| `TITILER_MINIO_ENDPOINT` | Required | Storage-node MinIO endpoint; Hanka uses `minio.hanka.internal:9000` without a URL scheme. |
| `TITILER_MINIO_HTTPS` | `YES` | HTTPS is required for the Hanka storage-node connection. |
| `TITILER_MINIO_CA_BUNDLE` | `/etc/ssl/certs/hanka-minio-ca.pem` | CA bundle path inside the TiTiler workers. |
| `TITILER_MINIO_REGION` | `us-east-1` | S3 region supplied to GDAL. |
| `TITILER_MINIO_CONFIG_DIR` | Required | Protected host directory containing `titiler-access-key` and `titiler-secret-key`. |
The Nginx image is pinned to a multi-architecture manifest digest. The TiTiler
worker image is supplied through `TITILER_IMAGE`.

Edge workers fail before Uvicorn starts unless MinIO HTTPS is enabled and the
AWS, libcurl, and GDAL CA variables use the mounted Hanka CA bundle. TLS
certificate hostname validation remains a runtime check against the configured
MinIO endpoint.

Create the shared control-node network once. Other control-node Compose projects
must reuse its exact name:

```bash
docker network inspect strayos-edge >/dev/null 2>&1 || docker network create strayos-edge
```

TiTiler reads private objects with the dedicated read-only identity provisioned by `strayos-at-edge/minio-object-storage`. Set its endpoint, HTTPS, CA bundle, CA file, and protected credential directory before rendering or starting the stack:

```bash
export TITILER_MINIO_ENDPOINT=minio.hanka.internal:9000
export TITILER_MINIO_HTTPS=YES
export TITILER_MINIO_CA_BUNDLE=/etc/ssl/certs/hanka-minio-ca.pem
export TITILER_MINIO_CA_FILE=/opt/strayos/config/minio/ca.pem
export TITILER_MINIO_CONFIG_DIR=/opt/strayos/config/minio
```

Use a fixed Compose project name so the containers and named volumes stay isolated and predictable:

```bash
docker compose -p strayos-titiler-edge -f docker-compose-strayos-edge.yml config
docker compose -p strayos-titiler-edge -f docker-compose-strayos-edge.yml pull
docker compose -p strayos-titiler-edge -f docker-compose-strayos-edge.yml up -d --wait
```

A local MinIO COG can be addressed through GDAL's S3 virtual filesystem, for example `s3://uploads/path/to/raster.tif`. The TiTiler identity can list and read objects in `uploads` and `output` but cannot create, replace, or delete them.

The same-workstation integration test used the canonical `dsm_cog.tif` with fresh isolated MinIO storage. A valid PNG preview passed through Edge Nginx over HTTPS using the mounted Hanka CA, and the dedicated identity listed/read `uploads` and `output` while writes, deletes, other-bucket access, bucket creation, and admin operations were denied. This does not validate the final storage-node LAN address, outage recovery, or site acceptance.

Inspect or stop only this stack with:

```bash
docker compose -p strayos-titiler-edge -f docker-compose-strayos-edge.yml ps
docker compose -p strayos-titiler-edge -f docker-compose-strayos-edge.yml logs
docker compose -p strayos-titiler-edge -f docker-compose-strayos-edge.yml down
```

Do not add `--volumes` to the normal `down` command; the named volume retains
the edge Nginx cache. The two workers have a combined 2 GiB `GDAL_CACHEMAX`
ceiling and approximately 382 MiB of process-wide `/vsicurl/` cache, in
addition to Python, native-library, and request memory. Their combined
container memory limit is 8 GiB by default.

## Stress Testing

* Activate the repository virtual environment before running a test: `source .venv/bin/activate`.
* Raster inputs and their TiTiler parameters are defined in `stress-test/rasters.json`; raster names must be unique because they are used in artifact filenames.
* Run peak load with `python stress-test/main.py --peak-load`. Add `--local` to any mode to target the development stack at `http://localhost`; start it first with `docker compose -f docker-compose-strayos-dev.yml up -d`.
* Output paths are relative to `stress-test/`. Runs save API responses and tile timing data, while peak-load artifacts are written to `stress-test/output_peak_load/`. Progress and final summaries include successful tile counts and transferred data; failures produce a nonzero exit code.

## CI/CD Pipeline

### Branch behavior

- `main` builds and pushes the Docker image, then deploys the existing production VM and the standalone ODP VM in parallel.
- Other branches have no configured pipeline.
- Pipelines use a clone depth of `1`.

### Deploy step

- Uses `atlassian/default-image:4`. Production uses `concurrency-group: titiler-app-deploy`; ODP uses `concurrency-group: titiler-odp-deploy` to serialize deployments to each VM.
- The two deployment steps run in a `parallel` group after image publication succeeds. `fail-fast: false` lets the other deployment finish when one fails; a failure still fails the overall pipeline. Each target's concurrency group continues to prevent overlapping deploys to that VM.
- Production uses the existing Bitbucket `Production` deployment environment. ODP runs as a regular pipeline step with repository/workspace variables and its own concurrency group; it requires no additional Bitbucket deployment environment.
- Authenticates to Azure with the service-principal variables listed below.
- Temporarily permits SSH from the runner's public IPv4 address, syncs the repository with `rsync`, deploys the services, and removes the NSG rule during cleanup.
- Authenticates Docker Hub separately on each target VM as its SSH user before syncing or pulling images. The token is sent through SSH stdin with shell tracing disabled.

### Required pipeline variables

| Variable | Scope | Purpose |
| --- | --- | --- |
| `DOCKER_HUB_USER` | Workspace | Docker Hub username/namespace used for the image repository. |
| `DOCKER_HUB_TOKEN` | Workspace | Docker Hub token used for image publication and authenticated pulls on both deployment VMs. |
| `DEPLOY_AZURE_ID` | Workspace | Azure service-principal client/application ID. |
| `DEPLOY_AZURE_PASS` | Workspace | Azure service-principal secret. |
| `AZURE_TENANT_ID` | Workspace | Azure tenant ID for service-principal login. |
| `AZURE_SUBSCRIPTION_ID` | Workspace | Azure subscription selected before NSG changes. |
| `SSH_KEY_B64` | Repository | Shared base64-encoded private SSH key used for both production and ODP VM access. |
| `TITILER_SERVER` | Repository | Public host or IP for the production VM. |
| `TITILER_SSH_USER` | Repository | SSH user for the production VM. |
| `ODP_TITILER_SERVER` | Repository | Public host or IP for the standalone ODP VM. |
| `ODP_TITILER_SSH_USER` | Repository | SSH user for the ODP VM. |

Configure `ODP_TITILER_SERVER` and `ODP_TITILER_SSH_USER` as repository variables before running the main-branch pipeline. ODP reuses the repository's `SSH_KEY_B64` and the workspace Docker Hub and Azure login variables; the service principal needs permission to manage rules on the ODP NSG. Keep the shared SSH key variable secured.

### Optional ODP infrastructure overrides

Like production, ODP has embedded Azure infrastructure defaults. These values were verified with Azure CLI against `Titiler-Oman` in `TITILER-OMAN`; the following Bitbucket variables are optional overrides rather than deployment requirements:

| Variable | Embedded default | Purpose |
| --- | --- | --- |
| `TITILER_ODP_RESOURCE_GROUP` | `TITILER-OMAN` | Azure resource group containing the ODP NSG. |
| `TITILER_ODP_NSG_NAME` | `Titiler-Oman-nsg` | NIC-level NSG controlling SSH access to the ODP VM. |
| `TITILER_ODP_PRIVATE_IP` | `172.16.0.4` | VM private IP used as the SSH NSG rule destination. |

### Deployment behavior

The pipeline pushes `${DOCKER_HUB_USER}/strayos-titiler:app` once, then starts the two deployment steps in parallel. After SSH is ready, each step logs into Docker Hub on its target VM using `DOCKER_HUB_USER` and `DOCKER_HUB_TOKEN`, then syncs the repository to `~/strayos-titiler` without overwriting that VM's `.env`. The remote login uses `--password-stdin` over SSH with runner shell tracing disabled, and Docker stores the login for the target SSH user. Prometheus, Loki, and Alloy are recreated to reload bind-mounted configuration. Within each VM, backends are replaced sequentially (six on production, three on ODP), and Nginx is recreated last so its upstreams resolve and its file bind mount follows the inode replaced by `rsync`. The two deployment steps reuse the same YAML-anchored Azure/SSH deployment commands, with ODP explicitly selecting its own Compose file, worker count, and connection/NSG variables. See `bitbucket-pipelines.yml` for the exact sequence.

The initial ODP pull failed because the new `ubuntu` account had no Docker Hub login. Production's `azureuser` already had working registry credentials. A login performed only inside the image-build runner does not authenticate either VM; both deploy steps now establish their own remote login. An absent or rejected token stops the deployment before repository sync or container changes, while the EXIT trap removes the temporary SSH NSG rule.

Parallel steps are enabled through YAML and require two available workspace concurrent-step slots; no extra Bitbucket enable switch or ODP deployment environment is needed. Workspace concurrency limits apply across all running pipelines. Build minutes are the sum of both steps' execution time, even though their elapsed time overlaps. See Bitbucket's [parallel step options](https://support.atlassian.com/bitbucket-cloud/docs/parallel-step-options/).

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

Each backend is an independent process with its own Rasterio/GDAL state and cache; memory is not shared between workers. Development, production, and ODP limit each backend container to 4 GiB. Backends use `/healthz` healthchecks, while Nginx uses `nginx -t`. The public health response includes installed TiTiler and geospatial library versions.

Production and ODP support HTTP/2 for multiplexed browser tile requests, fall back to HTTP/1.1, and redirect port 80 to HTTPS. Local development remains HTTP-only.

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

Production, development, and ODP Nginx emit JSON for completed TiTiler API requests, which Alloy ships asynchronously to Loki. Events contain the requester IP, normalized endpoint, full client-facing `request_url` (`scheme://Host/path?query`), status, request time, response size, upstream details, and cache status. Full URLs include raster query strings and may contain signed credentials; they appear in the Docker access logs and Loki's 7-day store, accessible to Grafana users. URLs from before this field was deployed are not recoverable from older access events. Grafana, metrics, healthcheck, and production/ODP HTTP redirect requests are excluded. Client IP and full URL remain parsed JSON fields rather than indexed Loki labels to avoid high-cardinality streams. The independent edge Nginx configuration still omits query strings and has no Grafana stack.

Dashboard traffic totals include Nginx cache hits, while Starlette metrics include only backend work. Cached delivery includes `HIT`, `STALE`, `UPDATING`, and `REVALIDATED`; backend delivery includes `MISS`, `BYPASS`, `EXPIRED`, and uncached upstream requests. Client-IP panels support up to 10,000 result series per query, retaining a safety bound above Loki's default of 500.

The **API Endpoints (Selected Time Range)** table shows all observed endpoint groups sorted by request count, without a top-10 limit. Grafana combines the instant-query series into one table with **API** and **Requests** columns. All Strayos Nginx configurations normalize known dynamic routes and retain the actual path (`$uri`, without query strings) for unmapped routes instead of logging `other_api`. The endpoint and slow-request tables recover paths from `request_url` for retained legacy `other_api` events; older events without a URL display **Unknown endpoint (legacy log)** because their original path was not recorded. The endpoint-count query drops the temporary URL field before aggregation, so different query strings do not create separate series. Endpoint queries remain subject to Loki's 10,000-series safety bound.

The **Top 20 Slowest Requests (>1s)** table ranks individual Nginx access events with client-facing `request_time` strictly greater than 1 second across the selected Grafana range. Its instant LogQL query parses and numerically filters duration *before* constructing a temporary per-event key from the nanosecond Loki timestamp and log line, unwraps duration, and applies `topk(20)` in Loki before Grafana receives the rows. Fewer than 20 qualifying requests yield fewer rows even if faster requests exist. Grafana converts the returned labeled series into table rows, sorts by duration, and hides the temporary event key. Equal-duration requests remain separate, and the panel does not depend on Grafana's log-row retrieval limit. It displays the logged completion time, normalized endpoint, full request URL (with an inspectable cell for long URLs), method, client IP, status, delivery source, and duration. Older retained events lack the full URL. The ranking covers only Loki's retained 7 days; ranges that create over 10,000 *qualifying* per-event series (or hit other query limits) still fail explicitly. `topk(20)` alone does not reduce cardinality before Loki applies the series limit. The per-event key is created at query time and is not an indexed Loki label.

Production and ODP expose Grafana only through `/grafana/`; Prometheus and Loki remain internal. Anonymous Grafana access is disabled by default. Set `GRAFANA_ADMIN_PASSWORD` and, only when needed, `GRAFANA_ANONYMOUS_ENABLED=true` in each VM's `.env`.

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

## Azure Infrastructure: ODP

ODP is a standalone deployment reached explicitly at `https://titilerodp.strayos.com`. It uses the same TiTiler image, remote raster access, GDAL tuning, dashboards, Loki configuration, retention, and response-cache behavior as production.

| Resource | ODP configuration |
| --- | --- |
| Resource group | `TITILER-OMAN` |
| VM name | `Titiler-Oman` |
| VM size | `Standard_D4as_v5` |
| Azure region | `uaenorth` |
| Compose file | `docker-compose-strayos-deploy-odp.yml` |
| Default Compose project | `strayos-titiler-odp` |
| VM CPU | 4 vCPUs |
| VM RAM | 16 GiB |
| VM OS | Ubuntu 24.04.4 LTS, x86_64 |
| VM OS disk | 64 GiB Azure managed disk, `Premium_LRS` |
| VM private IP | `172.16.0.4` |
| NSG | `Titiler-Oman-nsg` at NIC level; no subnet NSG |
| SSH user | `ubuntu`, supplied through `ODP_TITILER_SSH_USER` |
| TiTiler backends | `titiler-1`, `titiler-2`, `titiler-3`; one Uvicorn worker each |
| Backend memory limit | 4 GiB each, 12 GiB combined |
| Combined decoded GDAL block-cache ceiling | 3 GiB; not a startup allocation |
| Combined process-wide `/vsicurl/` cache ceiling | 600,000,000 bytes, approximately 572 MiB |
| Nginx configuration | `dockerfiles/nginx-strayos-odp.conf` |
| Tile upstream behavior | 125-second Nginx read-idle timeout; upstream retries disabled |
| Prometheus configuration | `dockerfiles/prometheus-odp.yml`, scraping exactly three backends plus node-exporter and cAdvisor |
| Alloy configuration | `dockerfiles/alloy/config-odp.alloy` |
| Grafana | `https://titilerodp.strayos.com/grafana/` |
| Nginx disk cache | 21 GiB, 48-hour successful-response TTL |
| Prometheus / Loki retention | 7 days |

VM sizing, managed-disk capacity, SSH username, private IP, and NSG attachment were verified with Azure CLI. SSH confirmed Ubuntu 24.04.4 LTS and a root filesystem reported as `61G` by `df -h`; Azure exposes a 64 GiB managed OS disk.

Three workers halve the six-worker production topology for the smaller VM. Their combined hard memory limit leaves approximately 4 GiB for the OS, Nginx, Grafana, Prometheus, Loki, Alloy, node-exporter, and cAdvisor. CPU is not pinned or reserved. Cache ceilings are included within backend memory limits, not additional allocations. ODP's rotated Docker local logs have an approximately 780 MB combined ceiling, separate from cache and monitoring data.

ODP has its own Compose network, generated container names, and project-scoped `nginx-cache`, `prometheus-data`, `grafana-data`, `loki-data`, and `alloy-data` volumes. Its Nginx carries the `com.strayos.stack=odp` label; the ODP Alloy configuration selects this label and the Compose `nginx` service rather than relying on a fixed container name. The shared dashboard queries still use `service_name="nginx"` in ODP's own Loki store.

### ODP VM setup

1. Install Docker with the Compose plugin using `sudo bash scripts/install-docker-ubuntu.sh ubuntu` on the VM, or the SSH command below. The installer also ensures `rsync` is installed and adds `ubuntu` to the Docker group. The pipeline syncs the repository into `~/strayos-titiler`.
2. Point `titilerodp.strayos.com` at the new VM and allow public ports 80/443. The verified NSG currently allows public 443; add an inbound TCP 80 rule for the HTTP-to-HTTPS redirect. Nginx terminates TLS and supports HTTP/2.
3. Install the certificate and key at `/etc/ssl/strayos/strayos.com.crt` and `/etc/ssl/strayos/star_strayos_com.key`. The certificate must cover `titilerodp.strayos.com`; the directory is mounted read-only into Nginx.
4. Set `GRAFANA_ADMIN_PASSWORD` in `~/strayos-titiler/.env`. `TITILER_IMAGE` defaults to `strayos1/strayos-titiler:app`; the pipeline explicitly selects the image it publishes.
5. Configure the repository variables `ODP_TITILER_SERVER` and `ODP_TITILER_SSH_USER` from **CI/CD Pipeline** above, reusing `SSH_KEY_B64`. Resource group, NSG, and private IP use the embedded defaults unless their optional overrides are set. The pipeline temporarily allows runner SSH to the ODP private IP and removes that rule on exit.

### Docker bootstrap

From the local repository, use the `Titiler-ODP` SSH configuration entry to install Docker Engine, Buildx, and the Compose plugin from Docker's official Ubuntu repository:

```bash
ssh Titiler-ODP 'sudo -n bash -s -- ubuntu' < scripts/install-docker-ubuntu.sh
```

The installer enables Docker and containerd at boot and runs `hello-world`. Open a new SSH connection to use the updated Docker-group membership:

```bash
ssh -S none Titiler-ODP 'docker version && docker compose version && docker run --rm hello-world'
```

ODP bootstrap was verified on Oct-15-2026 with Docker Engine **29.9.1**, Compose **5.6.0**, and Buildx **0.38.0**. Docker and containerd were active and enabled; a fresh SSH session as `ubuntu` ran a container without `sudo`, and the installed Compose plugin validated `docker-compose-strayos-deploy-odp.yml`.

### Initial stack start

For a manual initial start from the repository directory on the VM:

```bash
docker login --username strayos1
docker compose -f docker-compose-strayos-deploy-odp.yml config
docker compose -f docker-compose-strayos-deploy-odp.yml pull
docker compose -f docker-compose-strayos-deploy-odp.yml up -d --wait
```

Use the Docker Hub account with access to the configured image for a manual login. Automated deployment supplies the existing workspace credentials and performs this login on the VM itself.

Inspect or stop the ODP stack with:

```bash
docker compose -f docker-compose-strayos-deploy-odp.yml ps
docker compose -f docker-compose-strayos-deploy-odp.yml logs
docker compose -f docker-compose-strayos-deploy-odp.yml down
```

Normal `down` retains ODP's cache and monitoring volumes. Main-branch deployments update production and ODP in parallel, replacing each VM's workers sequentially and recreating its Nginx last.

## Caching

### Nginx Response Cache

Nginx uses the persistent `nginx-cache` volume for a shared 21 GiB cache within each deployment. It caches tiles and successful `GET`/`HEAD` requests to `/cog/statistics` and `/cog/validate` (including COG validation responses with `"COG": false`); STAC statistics and GeoJSON `POST /cog/statistics` are not cached. All Strayos Nginx configurations use the same 48-hour successful-response TTL and cache lock for COG validation. Cache keys include the scheme, host, path, and query string, so different raster URLs or validation options remain separate. Failed HTTP responses are not cached.

Production, ODP, development, and Edge Nginx include `X-TiTiler-Cache` on responses for cache-enabled requests. It exposes Nginx's `$upstream_cache_status` (for example, `MISS` when fetched from the backend, `HIT` when served from the cache, or `STALE` when serving an expired entry). Nginx omits the header when there is no cache status, such as on non-cached endpoints. This diagnostic header does not change caching behavior and can be inspected in browser developer tools; `Cache-Control` alone does not identify a cache hit.

Successful GET responses advertise a 90-minute browser lifetime through `Cache-Control`. Nginx preserves that client header but keeps successful cached responses for 48 hours. Entries inactive for 48 hours or displaced as the cache approaches 21 GiB may be removed earlier. The volume survives normal container recreation but not volume deletion, explicit purging, or disk loss.

### GDAL Cache Behavior

`VSI_CACHE=FALSE` disables the per-file-handle byte-range cache because datasets are opened and closed per request. `GDAL_CACHEMAX=1024` is a ceiling for decoded blocks held by open datasets, not a startup allocation. Production and ODP also provide a process-wide 200,000,000-byte `/vsicurl/` cache per backend, which can reuse downloaded ranges across dataset opens until eviction or worker restart. Development does not set this cache size explicitly.

### Memory Budget

The six-worker production budget is:

```
6 backends * 1024 MiB GDAL_CACHEMAX = ~6 GiB decoded-block ceiling (not startup allocation)
6 backends * 200,000,000-byte /vsicurl/ cache = ~1.12 GiB total
No per-file VSI_CACHE overhead
4 GiB hard memory limit per backend container
32 GiB total RAM shared by GDAL cache, Python, Nginx, OS, request buffers, Azure Blob connections
```

Cache ceilings are not reserved allocations; backend memory must also accommodate Python, native libraries, request buffers, and other allocations. The three-worker ODP budget is listed under **Azure Infrastructure: ODP**.

## Request Routing

Nginx uses `hash $titiler_hash_key consistent`, keyed by the `url` query parameter when present and otherwise by the full request URI. Requests for one raster therefore usually reach the same backend and reuse that process's `/vsicurl/` cache.

Routing is best-effort: failed requests can spill to another backend, trading some cache duplication for availability. A hot raster may also concentrate CPU load on one worker; distributing it would improve CPU balance but reduce cache locality. Production uses six single-worker backends on eight vCPUs; ODP uses three on four vCPUs. This sizing leaves nominal CPU headroom for Nginx, the OS, and supporting services; cores are not pinned or reserved.

## Sources

- TiTiler performance tuning: https://developmentseed.org/titiler/advanced/performance_tuning/
- GDAL configuration options: https://gdal.org/en/stable/user/configoptions.html
- GDAL virtual file systems: https://gdal.org/en/stable/user/virtual_file_systems.html
- Python `PYTHONWARNINGS`: https://docs.python.org/3/using/cmdline.html#envvar-PYTHONWARNINGS
