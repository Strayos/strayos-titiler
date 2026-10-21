# Strayos TiTiler Notes

This file documents the Strayos-specific TiTiler implementation and its production operations.

The repository uses GitHub to sync changes from the original TiTiler project and Bitbucket for Strayos deployment.

## Building Wheel Files

This application is normally deployed as a container. When another application or a function deployment requires a wheel, run the tests and then build it with `build_whl_file.sh`.

## Local Development

* Start the Strayos TiTiler stack with `docker-compose-strayos-dev.yml` when you want to run code from this project instead of an installed package. Development Nginx uses `dockerfiles/nginx-strayos-dev.conf` and serves HTTP on `http://localhost` without requiring the production TLS certificates.
* Production deploys use `docker-compose-strayos-deploy.yml`, with the image supplied through `TITILER_IMAGE` and defaulting to `strayos1/strayos-titiler:app`.
* The standalone ODP deployment at `https://titilerodp.strayos.com` uses `docker-compose-strayos-deploy-odp.yml`. It runs three backends on a dedicated 4-vCPU, 16-GiB VM; see **Azure Infrastructure: ODP** below.
* Staging at `https://titilerstaging.strayos.com` uses `docker-compose-strayos-deploy-staging.yml`. Its pipeline deploys `:staging-<full-commit-sha>` images, separate from production's `:app`; `:staging` remains a convenience/bootstrap alias. Its three-worker stack mirrors ODP; see **Azure Infrastructure: Staging** below.

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
container memory limit is 8 GiB by default. Edge Nginx has a separate 128 MiB
shared-memory response-cache key index.

## Stress Testing

* Activate the repository virtual environment before running a test: `source .venv/bin/activate`.
* Raster inputs and their TiTiler parameters are defined in `stress-test/rasters.json`; raster names must be unique because they are used in artifact filenames.
* Run peak load with `python stress-test/main.py --peak-load`. Add `--local` to any mode to target the development stack at `http://localhost`; start it first with `docker compose -f docker-compose-strayos-dev.yml up -d`.
* Output paths are relative to `stress-test/`. Runs save API responses and tile timing data, while peak-load artifacts are written to `stress-test/output_peak_load/`. Progress and final summaries include successful tile counts and transferred data; failures produce a nonzero exit code.

## CI/CD Pipeline

### Branch behavior

- `main` builds and pushes the `:app` image, then deploys the existing production VM and the standalone ODP VM in parallel.
- `staging` builds and pushes `:staging-${BITBUCKET_COMMIT}`, updates the `:staging` convenience alias, then deploys that exact commit-tagged image to only the staging VM. It never publishes `:app` or runs the production/ODP deployment steps.
- `add-titiler-staging` is the implementation/review branch for this setup, intended to merge into `main`. It is not the deployment selector and has no automatic pipeline.
- Other branches have no configured pipeline.
- Pipelines use a clone depth of `1`.

### Selecting a commit for staging

The repository's default branch is named `main`, not `master`. Merge `add-titiler-staging` into `main` through the normal review process. Keep `staging` as a movable deployment branch rather than developing the setup directly on it.

After the merge, move the remote `staging` branch to the commit you want to test. A **remote branch update** triggers Bitbucket to build the source at that commit and deploy its `:staging-<full-commit-sha>` image. A local reset alone does not deploy anything. This can be done without resetting a local working tree:

```bash
git fetch bitbucket
TARGET_COMMIT="replace-with-full-commit-sha"
git show "${TARGET_COMMIT}:bitbucket-pipelines.yml"
git push --force-with-lease bitbucket "${TARGET_COMMIT}:refs/heads/staging"
```

- The target commit must contain this staging pipeline and its Compose/Nginx/monitoring files. Commits predating this setup cannot deploy simply by moving `staging`, because Bitbucket reads its pipeline configuration from the selected commit.
- The branch policy must permit creating/updating `staging`, including non-fast-forward updates for rollbacks. Use `--force-with-lease`, not an unconditional force push; review any lease rejection before retrying.
- Cancel or wait for earlier staging pipelines before moving the branch again. The deployment concurrency group serializes deploy steps but does not cancel older builds or queued deployments. Commit-specific image tags prevent one build from pulling a different commit's mutable alias.
- Pushing an unchanged branch tip is a no-op and does not create a new pipeline. Rerun that commit's staging pipeline in Bitbucket when redeployment is needed.
- Updating only `staging` never triggers the `main` production/ODP pipeline. Merging into `main` still triggers its existing production and ODP deployment behavior.

### Deploy step

- Uses `atlassian/default-image:4`. Production uses `concurrency-group: titiler-app-deploy`; ODP uses `concurrency-group: titiler-odp-deploy`; staging uses `concurrency-group: titiler-staging-deploy` to serialize deployments to each VM.
- On `main`, the two deployment steps run in a `parallel` group after image publication succeeds. `fail-fast: false` lets the other deployment finish when one fails; a failure still fails the overall pipeline. On `staging`, only the staging deploy runs after its image publication succeeds. Each target's concurrency group prevents overlapping deploys to that VM.
- Production uses the existing Bitbucket `Production` deployment environment. ODP and staging run as regular pipeline steps with repository/workspace variables and their own concurrency groups; neither requires an additional Bitbucket deployment environment.
- Authenticates to Azure with the service-principal variables listed below.
- Temporarily permits SSH from the runner's public IPv4 address, syncs the repository with `rsync`, deploys the services, and removes the NSG rule during cleanup.
- Authenticates Docker Hub separately on each target VM as its SSH user before syncing or pulling images. The token is sent through SSH stdin with shell tracing disabled.

### Required pipeline variables

| Variable | Scope | Purpose |
| --- | --- | --- |
| `DOCKER_HUB_USER` | Workspace | Docker Hub username/namespace used for the image repository. |
| `DOCKER_HUB_TOKEN` | Workspace | Docker Hub token used for image publication and authenticated pulls on each deployment VM. |
| `DEPLOY_AZURE_ID` | Workspace | Azure service-principal client/application ID. |
| `DEPLOY_AZURE_PASS` | Workspace | Azure service-principal secret. |
| `AZURE_TENANT_ID` | Workspace | Azure tenant ID for service-principal login. |
| `AZURE_SUBSCRIPTION_ID` | Workspace | Azure subscription selected before NSG changes. |
| `SSH_KEY_B64` | Repository | Shared base64-encoded private SSH key used for production, ODP, and staging VM access. |
| `TITILER_SERVER` | Repository | Public host or IP for the production VM. |
| `TITILER_SSH_USER` | Repository | SSH user for the production VM. |
| `ODP_TITILER_SERVER` | Repository | Public host or IP for the standalone ODP VM. |
| `ODP_TITILER_SSH_USER` | Repository | SSH user for the ODP VM. |

Configure `ODP_TITILER_SERVER` and `ODP_TITILER_SSH_USER` as repository variables before running the main-branch pipeline. ODP reuses the repository's `SSH_KEY_B64` and the workspace Docker Hub and Azure login variables; the service principal needs permission to manage rules on the ODP NSG. Keep the shared SSH key variable secured.

Staging reuses `SSH_KEY_B64` and the existing workspace Docker Hub and Azure variables. Its verified SSH host/user and Azure infrastructure have embedded defaults, so no new pipeline variables or Bitbucket deployment environment are required. Bitbucket supplies `BITBUCKET_COMMIT` automatically. The Azure service principal must be able to manage rules on `TiTiler-Staging-nsg` in `TiTiler`; the Docker Hub token must be allowed to publish `:staging` and `:staging-<full-commit-sha>` tags in the existing image repository.

### Optional ODP infrastructure overrides

Like production, ODP has embedded Azure infrastructure defaults. These values were verified with Azure CLI against `Titiler-Oman` in `TITILER-OMAN`; the following Bitbucket variables are optional overrides rather than deployment requirements:

| Variable | Embedded default | Purpose |
| --- | --- | --- |
| `TITILER_ODP_RESOURCE_GROUP` | `TITILER-OMAN` | Azure resource group containing the ODP NSG. |
| `TITILER_ODP_NSG_NAME` | `Titiler-Oman-nsg` | NIC-level NSG controlling SSH access to the ODP VM. |
| `TITILER_ODP_PRIVATE_IP` | `172.16.0.4` | VM private IP used as the SSH NSG rule destination. |

### Optional staging overrides

| Variable | Embedded default | Purpose |
| --- | --- | --- |
| `STAGING_TITILER_SERVER` | `titilerstaging.strayos.com` | Staging SSH host. |
| `STAGING_TITILER_SSH_USER` | `ubuntu` | Staging SSH user. |
| `TITILER_STAGING_RESOURCE_GROUP` | `TiTiler` | Azure resource group containing the staging NSG. |
| `TITILER_STAGING_NSG_NAME` | `TiTiler-Staging-nsg` | NIC-level NSG controlling staging SSH access. |
| `TITILER_STAGING_PRIVATE_IP` | `172.16.0.4` | Staging VM private IP used as the SSH NSG rule destination. |

### Deployment behavior

On `main`, the pipeline pushes `${DOCKER_HUB_USER}/strayos-titiler:app` once, then starts the two deployment steps in parallel. On `staging`, it builds and publishes `${DOCKER_HUB_USER}/strayos-titiler:staging-${BITBUCKET_COMMIT}`, updates the `:staging` alias for build caching/manual convenience, and deploys only `docker-compose-strayos-deploy-staging.yml`, selecting three workers and the staging SSH/NSG settings. The shared deployment commands default to `:app`; only the staging step exports `DEPLOY_IMAGE_TAG=staging-${BITBUCKET_COMMIT}`. Deployments use the commit-specific tag, not whichever image most recently overwrote the `:staging` alias.

After SSH is ready, each step logs into Docker Hub on its target VM using `DOCKER_HUB_USER` and `DOCKER_HUB_TOKEN`, then syncs the repository to `~/strayos-titiler` without overwriting that VM's `.env`. The remote login uses `--password-stdin` over SSH with runner shell tracing disabled, and Docker stores the login for the target SSH user. Prometheus, Loki, and Alloy are recreated to reload bind-mounted configuration. Within each VM, backends are replaced sequentially (six on production, three each on ODP and staging), and Nginx is recreated last so its upstreams resolve and its file bind mount follows the inode replaced by `rsync`. All targets reuse the same YAML-anchored Azure/SSH deployment commands. See `bitbucket-pipelines.yml` for the exact sequence.

The initial ODP pull failed because the new `ubuntu` account had no Docker Hub login. Production's `azureuser` already had working registry credentials. A login performed only inside the image-build runner does not authenticate a VM; every deploy step establishes its own remote login. An absent or rejected token stops the deployment before repository sync or container changes, while the EXIT trap removes the temporary SSH NSG rule.

Main-branch parallel steps are enabled through YAML and require two available workspace concurrent-step slots; no extra Bitbucket enable switch or ODP deployment environment is needed. Staging has sequential build/deploy steps and needs one slot at a time. Workspace concurrency limits apply across all running pipelines. Build minutes are the sum of both parallel steps' execution time, even though their elapsed time overlaps. See Bitbucket's [parallel step options](https://support.atlassian.com/bitbucket-cloud/docs/parallel-step-options/).

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

Each backend is an independent process with its own Rasterio/GDAL state and cache; memory is not shared between workers. Development, production, ODP, and staging limit each backend container to 4 GiB. Backends use `/healthz` healthchecks, while Nginx uses `nginx -t`. The public health response includes installed TiTiler and geospatial library versions.

Production, ODP, and staging support HTTP/2 for multiplexed browser tile requests, fall back to HTTP/1.1, and redirect port 80 to HTTPS. Local development remains HTTP-only.

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

Production, development, ODP, and staging Nginx emit JSON for completed TiTiler API requests, which Alloy ships asynchronously to Loki. Events contain the requester IP, normalized endpoint, full client-facing `request_url` (`scheme://Host/path?query`), status, request time, response size, upstream details, and cache status. Full URLs include raster query strings and may contain signed credentials; they appear in the Docker access logs and Loki's 7-day store, accessible to Grafana users. URLs from before this field was deployed are not recoverable from older access events. Grafana, metrics, healthcheck, and production/ODP/staging HTTP redirect requests are excluded. Client IP and full URL remain parsed JSON fields rather than indexed Loki labels to avoid high-cardinality streams. The independent edge Nginx configuration still omits query strings and has no Grafana stack.

Dashboard traffic totals include Nginx cache hits, while Starlette metrics include only backend work. Cached delivery includes `HIT`, `STALE`, `UPDATING`, and `REVALIDATED`; backend delivery includes `MISS`, `BYPASS`, `EXPIRED`, and uncached upstream requests. Client-IP panels support up to 10,000 result series per query, retaining a safety bound above Loki's default of 500.

The **API Endpoints (Selected Time Range)** table shows all observed endpoint groups sorted by request count, without a top-10 limit. Grafana combines the instant-query series into one table with **API** and **Requests** columns. All Strayos Nginx configurations normalize known dynamic routes and retain the actual path (`$uri`, without query strings) for unmapped routes instead of logging `other_api`. The endpoint and slow-request tables recover paths from `request_url` for retained legacy `other_api` events; older events without a URL display **Unknown endpoint (legacy log)** because their original path was not recorded. The endpoint-count query drops the temporary URL field before aggregation, so different query strings do not create separate series. Endpoint queries remain subject to Loki's 10,000-series safety bound.

The **Top 20 Slowest Requests (>1s)** table ranks individual Nginx access events with client-facing `request_time` strictly greater than 1 second across the selected Grafana range. Its instant LogQL query parses and numerically filters duration *before* constructing a temporary per-event key from the nanosecond Loki timestamp and log line, unwraps duration, and applies `topk(20)` in Loki before Grafana receives the rows. Fewer than 20 qualifying requests yield fewer rows even if faster requests exist. Grafana converts the returned labeled series into table rows, sorts by duration, and hides the temporary event key. Equal-duration requests remain separate, and the panel does not depend on Grafana's log-row retrieval limit. It displays the logged completion time, normalized endpoint, full request URL (with an inspectable cell for long URLs), method, client IP, status, delivery source, and duration. Older retained events lack the full URL. The ranking covers only Loki's retained 7 days; ranges that create over 10,000 *qualifying* per-event series (or hit other query limits) still fail explicitly. `topk(20)` alone does not reduce cardinality before Loki applies the series limit. The per-event key is created at query time and is not an indexed Loki label.

Production, ODP, and staging expose Grafana only through `/grafana/`; Prometheus and Loki remain internal. Anonymous Grafana access is disabled by default. Set `GRAFANA_ADMIN_PASSWORD` and, only when needed, `GRAFANA_ANONYMOUS_ENABLED=true` in each VM's `.env`. Staging requires a nonempty admin password instead of falling back to the legacy default.

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
| Nginx cache-key index | 128 MiB shared by all cache-enabled endpoints |
| Prometheus / Loki retention | 7 days |

VM sizing, managed-disk capacity, SSH username, private IP, and NSG attachment were verified with Azure CLI. SSH confirmed Ubuntu 24.04.4 LTS and a root filesystem reported as `61G` by `df -h`; Azure exposes a 64 GiB managed OS disk.

Three workers halve the six-worker production topology for the smaller VM. Their combined hard memory limit leaves approximately 4 GiB for the OS, Nginx, Grafana, Prometheus, Loki, Alloy, node-exporter, and cAdvisor. CPU is not pinned or reserved. Cache ceilings are included within backend memory limits, not additional allocations. ODP's rotated Docker local logs have an approximately 780 MB combined ceiling, separate from cache and monitoring data.

ODP has its own Compose network, generated container names, and project-scoped `nginx-cache`, `prometheus-data`, `grafana-data`, `loki-data`, and `alloy-data` volumes. Its Nginx carries the `com.strayos.stack=odp` label; the ODP Alloy configuration selects this label and the Compose `nginx` service rather than relying on a fixed container name. The shared dashboard queries still use `service_name="nginx"` in ODP's own Loki store.

### ODP VM setup

1. Install Docker with the Compose plugin using `sudo bash scripts/install-docker-ubuntu.sh ubuntu` on the VM, or the SSH command below. The installer also ensures `rsync` is installed and adds `ubuntu` to the Docker group. The pipeline syncs the repository into `~/strayos-titiler`.
2. Point `titilerodp.strayos.com` at the VM and allow public ports 80/443. Both inbound rules were verified on Oct-21-2026. Nginx terminates TLS and supports HTTP/2.
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

## Azure Infrastructure: Staging

Staging is a standalone deployment at `https://titilerstaging.strayos.com`. It mirrors ODP's three-worker topology, GDAL settings, 4 GiB backend limits, 21 GiB response cache, 128 MiB cache-key index, 125-second tile read-idle timeout, disabled tile retries, seven-day monitoring retention, and provisioned Grafana dashboards. The `staging` branch selects the source commit and deploys its `:staging-<full-commit-sha>` image, isolated from production/ODP's `:app` tag.

| Resource | Staging configuration |
| --- | --- |
| Resource group | `TiTiler` |
| VM name | `TiTiler-Staging` |
| VM size | `Standard_D4as_v5`: 4 vCPUs, 16 GiB RAM |
| Azure region | `eastus2` |
| VM OS | Ubuntu 26.04 LTS, x86_64 |
| VM OS disk | 128 GiB Azure managed disk, `StandardSSD_LRS`; root filesystem reports `124G` |
| VM private IP | `172.16.0.4` on a separate VNet from ODP |
| NSG | `TiTiler-Staging-nsg` at NIC level; no subnet NSG |
| SSH | `ssh Titiler-Staging`, user `ubuntu`, shared production/ODP key |
| Compose file | `docker-compose-strayos-deploy-staging.yml` |
| Default Compose project | `strayos-titiler-staging` |
| TiTiler backends | `titiler-1`, `titiler-2`, `titiler-3`; one Uvicorn worker each |
| Nginx configuration | `dockerfiles/nginx-strayos-staging.conf` |
| Prometheus configuration | `dockerfiles/prometheus-staging.yml` |
| Alloy configuration | `dockerfiles/alloy/config-staging.alloy` |
| Grafana | `https://titilerstaging.strayos.com/grafana/` |
| Pipeline TiTiler image | `strayos1/strayos-titiler:staging-<full-commit-sha>` |
| Default/manual-bootstrap image | `strayos1/strayos-titiler:staging` |

Staging has its own Compose network, generated container names, and project-scoped `nginx-cache`, `prometheus-data`, `grafana-data`, `loki-data`, and `alloy-data` volumes. Its Nginx has the `com.strayos.stack=staging` label; its Alloy configuration selects only that stack's Nginx container. Loki configuration and Grafana provisioning/dashboard files are shared read-only with the other deployment definitions, but their runtime data stores are independent.

### Staging access and health checks

| Endpoint | URL / expected behavior |
| --- | --- |
| Health | `https://titilerstaging.strayos.com/healthz` returns HTTP 200 with library versions. |
| API documentation | `https://titilerstaging.strayos.com/api.html` |
| OpenAPI schema | `https://titilerstaging.strayos.com/api`; not `/openapi.json`. |
| Grafana | `https://titilerstaging.strayos.com/grafana/` requires login; the admin password is in the VM's protected `.env`. |
| HTTP | `http://titilerstaging.strayos.com/` redirects to HTTPS. |
| Metrics | Public `/metrics` returns HTTP 403; Prometheus scrapes workers over the private Compose network. |

From the local workstation:

```bash
curl --fail --http2 https://titilerstaging.strayos.com/healthz
ssh Titiler-Staging 'cd ~/strayos-titiler && docker compose -f docker-compose-strayos-deploy-staging.yml ps'
ssh Titiler-Staging 'systemctl show nginx.service -p LoadState -p ActiveState -p UnitFileState'
```

The host Nginx unit should report `LoadState=masked`, `ActiveState=inactive`, and `UnitFileState=masked`. This is intentional: the Compose Nginx container alone serves HTTPS and owns ports 80/443. Do not start a second host reverse proxy.

### Staging bootstrap and deployment

1. Install Docker with `ssh Titiler-Staging 'sudo -n bash -s -- ubuntu' < scripts/install-docker-ubuntu.sh`. On Oct-21-2026, Docker Engine **29.9.2**, Compose **5.6.0**, and Buildx **0.38.1** were installed. A fresh SSH session as `ubuntu` confirmed Docker access without `sudo`.
2. Keep DNS pointing `titilerstaging.strayos.com` to the staging VM. Its NIC-level NSG permits public TCP 443 and now also TCP 80 through the `HTTP` rule at priority `320`. SSH remains allowlisted; pipeline deploys temporarily allow only their runner's IPv4 address.
3. TLS files are `/etc/ssl/strayos/strayos.com.crt` and `/etc/ssl/strayos/star_strayos_com.key`, mounted read-only into Nginx. The trusted wildcard certificate matches ODP's live certificate, covers staging, and expires Jan-30-2027. The certificate is `root:root 0644`; its matching unencrypted private key is `root:root 0600`, corrected from the initial world-readable permissions.
4. Keep `GRAFANA_ADMIN_PASSWORD` in `~/strayos-titiler/.env`, owned by `ubuntu` with mode `0600`. Bootstrap created a random password without logging it. The Compose file refuses to render if this variable is missing or empty. Deployment sync explicitly preserves `.env`.
5. The host-installed `nginx.service` is stopped, disabled, and masked; containerized Nginx alone owns ports 80/443 and TLS. The host package and placeholder site configuration remain available for rollback, but must not run alongside the container. The cutover happened only after images, backend healthchecks, monitoring services, and the container's Nginx configuration passed. To restore the placeholder site, first stop the Compose Nginx, then run `sudo systemctl unmask nginx` followed by `sudo systemctl enable --now nginx`.
6. Merge the reviewed `add-titiler-staging` implementation branch into `main`, then move/push the remote `staging` branch to a selected commit containing this setup, as described under **Selecting a commit for staging**. The dedicated staging pipeline reuses existing secured credentials, verifies Docker Hub login on the VM, and deploys that commit's three backends sequentially before recreating Nginx. No production or ODP deploy runs on the `staging` branch. The defaults and optional overrides are listed under **CI/CD Pipeline**.

The initial bootstrap image was built directly on the staging VM from the working tree with `dockerfiles/Dockerfile` and the frozen `uv.lock`, tagged locally as `strayos1/strayos-titiler:staging`. It was not pushed to Docker Hub, and no registry credentials were copied from another VM. Until the first staging pipeline publishes its images, use the already-built local image with `docker compose -f docker-compose-strayos-deploy-staging.yml up -d --wait --pull never` rather than pulling the unpublished tag. The first pipeline run replaces this bootstrap image with a registry-published commit-tagged staging build; its Bitbucket credentials and service-principal permissions still need end-to-end verification.

For a manual deployment after a commit-specific image has been published, run from `~/strayos-titiler` on the staging VM, explicitly selecting the same commit as the synced deployment files:

```bash
COMMIT_SHA="replace-with-full-commit-sha"
export TITILER_IMAGE="strayos1/strayos-titiler:staging-${COMMIT_SHA}"
docker login --username strayos1
docker compose -f docker-compose-strayos-deploy-staging.yml config --quiet
docker compose -f docker-compose-strayos-deploy-staging.yml pull
docker compose -f docker-compose-strayos-deploy-staging.yml up -d --wait
```

Inspect or stop only staging with:

```bash
docker compose -f docker-compose-strayos-deploy-staging.yml ps
docker compose -f docker-compose-strayos-deploy-staging.yml logs
docker compose -f docker-compose-strayos-deploy-staging.yml down
```

Normal `down` retains staging's cache and monitoring volumes. Do not use `--volumes` for routine restarts. Bootstrap does not copy production/ODP `.env` files or registry credentials; the pipeline performs its own remote Docker Hub login.

Routine `docker compose restart` retains the existing containers' commit-tagged images. Before manually running `up` for workers after a pipeline deployment, set `TITILER_IMAGE` to the intended commit-specific tag; do not inadvertently switch back to the Compose file's floating/bootstrap default.

### Emergency placeholder-site rollback

Only when deliberately taking staging TiTiler offline, run these commands on the staging VM to restore the retained host Nginx placeholder site:

```bash
cd ~/strayos-titiler
docker compose -f docker-compose-strayos-deploy-staging.yml stop nginx
sudo systemctl unmask nginx.service
sudo systemctl enable --now nginx.service
```

This restores only the static placeholder, not the TiTiler API. Before returning to containerized service, stop and disable the host unit, mask it again, and then start the Compose Nginx:

```bash
sudo systemctl disable --now nginx.service
sudo systemctl mask nginx.service
docker compose -f docker-compose-strayos-deploy-staging.yml up -d --wait --pull never --no-deps nginx
```

### Initial staging verification: Oct-21-2026

- All ten staging containers started; all three TiTiler workers and containerized Nginx passed their healthchecks. Host Nginx is stopped and masked.
- Public `/healthz` returned HTTP/2 200 with the same TiTiler/geospatial versions as production and ODP. Public HTTP redirects to HTTPS, `/grafana/` redirects to its login page, and `/metrics` returns 403.
- A real Azure Blob DSM COG returned metadata, staging-host TileJSON URLs, and a valid PNG tile. Repeated tile and COG-validation requests demonstrated `X-TiTiler-Cache: MISS` followed by `HIT`.
- All five Prometheus targets were `up`: three TiTiler workers, node-exporter, and cAdvisor. Loki and Grafana health endpoints passed. Grafana authenticated with the generated admin password and provisioned both datasources and two dashboards. Alloy delivered structured staging Nginx API events into staging's Loki store.
- Production and ODP public healthchecks remained successful; neither deployment's Compose, Nginx, Prometheus, or Alloy definitions were changed.
- This was a manual bootstrap from the local working tree, not a Bitbucket pipeline run. Publish and merge the `add-titiler-staging` implementation branch into `main`, then point the remote `staging` branch at the chosen commit to verify its first automated build, registry publication, and deploy. End-to-end Bitbucket verification remains pending until that first run.

### Deployment configuration checks

With the development dependencies installed, run `python -m pytest tests/test_deployment_configs.py` from the repository virtual environment. These checks compare staging against ODP, resolve pipeline YAML anchors, validate shell syntax, and verify separate branch/image/VM selection without running cloud or deployment commands. Container tools can separately validate Nginx (`nginx -t`), Prometheus (`promtool check config`), and Alloy (`alloy validate`).

## Caching

### Nginx Response Cache

Nginx uses the persistent `nginx-cache` volume for a shared 21 GiB disk cache and a separate 128 MiB shared-memory cache-key index within each deployment. It caches tiles and successful `GET`/`HEAD` requests to `/cog/statistics` and `/cog/validate` (including COG validation responses with `"COG": false`); STAC statistics and GeoJSON `POST /cog/statistics` are not cached. All Strayos Nginx configurations use the same index size, 48-hour successful-response TTL, and cache lock for COG validation. Cache keys include the scheme, host, path, and query string, so different raster URLs or validation options remain separate. Failed HTTP responses are not cached.

The zone name `tiles` does not restrict the cache to tile endpoints. The tile, COG statistics, and COG validation locations all use `proxy_cache tiles`, sharing one key-index capacity and one disk-cache budget within that deployment. There is no separate allocation or reserved quota per endpoint; the path in the cache key keeps their entries distinct. The memory index stores keys and metadata, while response bodies are stored on disk.

Production, ODP, staging, development, and Edge Nginx include `X-TiTiler-Cache` on responses for cache-enabled requests. It exposes Nginx's `$upstream_cache_status` (for example, `MISS` when fetched from the backend, `HIT` when served from the cache, or `STALE` when serving an expired entry). Nginx omits the header when there is no cache status, such as on non-cached endpoints. This diagnostic header does not change caching behavior and can be inspected in browser developer tools; `Cache-Control` alone does not identify a cache hit.

Successful GET responses advertise a 90-minute browser lifetime through `Cache-Control`. Nginx preserves that client header but marks successful cached responses fresh for 48 hours. Entries inactive for 48 hours or displaced as the disk cache approaches 21 GiB or the key index fills may be removed earlier. The volume survives normal container recreation but not volume deletion, explicit purging, or disk loss.

### Capacity Planning: 500,000 Requests per Working Day

Size for distinct cache keys rather than total requests: repeat requests for the same key reuse an entry. The planning case is 500,000 new cacheable keys per working day, or approximately 1 million across a 48-hour window. Changes to raster URLs, rendering parameters, or query-string ordering create separate entries with the current cache key.

[Nginx documents approximately 8,000 keys per MiB](https://nginx.org/en/docs/http/ngx_http_proxy_module.html#proxy_cache_path). The configured capacities include tiles, statistics, and validation together:

| Deployment | Cache-key index | Approximate combined key capacity |
| --- | --- | --- |
| Production, ODP, staging, development, and edge | `keys_zone=tiles:128m` | 1,024,000 keys |

The 128 MiB index is sized above the roughly 650,000 entries that fill the 21 GiB disk cache at the observed production average. The previous 10 MiB zone held roughly 80,000 keys; 32 MiB would hold only about 256,000. Each zone is shared within its Nginx deployment, not allocated per TiTiler worker or per endpoint.

The capacity review on Oct-16-2026 analyzed 134,849 distinct successful tile URLs from `stress-test/output_peak_load/results_*.csv`, excluding failed responses and deduplicating repeated URLs. Those saved tiles use `scale=1`. Disk estimates add 1 KiB per entry for cache metadata and HTTP headers, round each file to 4 KiB filesystem blocks, and allow 20 MiB for cache directories. A sample of 500 live production cache files measured average metadata/header overhead of 958 bytes.

| Workload basis | 100,000 distinct entries | 500,000 distinct entries | 1 million distinct entries |
| --- | --- | --- | --- |
| Combined saved stress-test mix | 1.79 GiB | 8.9 GiB | 17.7 GiB |
| Largest average-size saved orthophoto workload | 2.21 GiB | 11.0 GiB | 22.0 GiB |
| Observed production disk cost per entry | 3.24 GiB | 16.2 GiB | 32.4 GiB |

These are workload-dependent disk estimates, not entry-count guarantees. The production snapshot contained 71,207 cache files using 2.30 GiB including directories; ODP had no cached response files. At that production average, the 21 GiB disk limit holds roughly 650,000 entries. It can accommodate the one-day planning case, but a million-entry two-day set would require about 32.4 GiB before growth headroom. Nginx evicts least-recently-used entries when disk capacity is reached, even if their 48-hour freshness TTL has not expired. Retaining that full two-day production-sized set requires additional cache storage and VM disk-capacity planning. Larger tile scales or different response mixes require remeasurement.

### GDAL Cache Behavior

`VSI_CACHE=FALSE` disables the per-file-handle byte-range cache because datasets are opened and closed per request. `GDAL_CACHEMAX=1024` is a ceiling for decoded blocks held by open datasets, not a startup allocation. Production, ODP, and staging also provide a process-wide 200,000,000-byte `/vsicurl/` cache per backend, which can reuse downloaded ranges across dataset opens until eviction or worker restart. Development does not set this cache size explicitly.

### Memory Budget

The six-worker production budget is:

```
6 backends * 1024 MiB GDAL_CACHEMAX = ~6 GiB decoded-block ceiling (not startup allocation)
6 backends * 200,000,000-byte /vsicurl/ cache = ~1.12 GiB total
No per-file VSI_CACHE overhead
Nginx response-cache key index = 128 MiB shared within this deployment
4 GiB hard memory limit per backend container
32 GiB total RAM shared by GDAL cache, Python, Nginx, OS, request buffers, Azure Blob connections
```

Cache ceilings are not reserved allocations; backend memory must also accommodate Python, native libraries, request buffers, and other allocations. The three-worker ODP budget is listed under **Azure Infrastructure: ODP**; staging uses the same budget.

## Request Routing

Nginx uses `hash $titiler_hash_key consistent`, keyed by the `url` query parameter when present and otherwise by the full request URI. Requests for one raster therefore usually reach the same backend and reuse that process's `/vsicurl/` cache.

Routing is best-effort: failed requests can spill to another backend, trading some cache duplication for availability. A hot raster may also concentrate CPU load on one worker; distributing it would improve CPU balance but reduce cache locality. Production uses six single-worker backends on eight vCPUs; ODP and staging each use three on four vCPUs. This sizing leaves nominal CPU headroom for Nginx, the OS, and supporting services; cores are not pinned or reserved.

## Sources

- TiTiler performance tuning: https://developmentseed.org/titiler/advanced/performance_tuning/
- GDAL configuration options: https://gdal.org/en/stable/user/configoptions.html
- GDAL virtual file systems: https://gdal.org/en/stable/user/virtual_file_systems.html
- Python `PYTHONWARNINGS`: https://docs.python.org/3/using/cmdline.html#envvar-PYTHONWARNINGS
