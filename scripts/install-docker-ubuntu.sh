#!/usr/bin/env bash
# Install Docker Engine and the Compose plugin on an Ubuntu deployment VM.
set -euo pipefail

deploy_user="${1:-ubuntu}"

if [ "$(id -u)" -ne 0 ]; then
  printf 'Run with sudo: sudo bash %s %s\n' "$0" "$deploy_user" >&2
  exit 1
fi

. /etc/os-release
if [ "$ID" != ubuntu ]; then
  printf 'This installer requires Ubuntu.\n' >&2
  exit 1
fi
getent passwd "$deploy_user" >/dev/null

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl

install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc

tee /etc/apt/sources.list.d/docker.sources >/dev/null <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: ${UBUNTU_CODENAME:-$VERSION_CODENAME}
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF

apt-get update
# rsync is needed on the VM as well as on the Bitbucket runner.
apt-get install -y docker-ce docker-ce-cli containerd.io \
  docker-buildx-plugin docker-compose-plugin rsync

systemctl enable --now docker.service containerd.service
usermod -aG docker "$deploy_user"

docker --version
docker compose version
docker run --rm hello-world
printf 'Open a new SSH session for %s to use Docker without sudo.\n' "$deploy_user"
