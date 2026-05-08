mkdir -p titiler-log-dump

docker compose -f docker-compose-strayos.yml ps > titiler-log-dump/compose-ps.txt 2>&1

docker inspect -f '{{.Name}} {{range.NetworkSettings.Networks}}{{.IPAddress}}{{end}}' \
  nginx titiler-worker-1 titiler-worker-2 titiler-worker-3 titiler-worker-4 \
  > titiler-log-dump/container-ips.txt 2>&1

docker logs --timestamps nginx > titiler-log-dump/nginx.log 2>&1
docker logs --timestamps titiler-worker-1 > titiler-log-dump/titiler-worker-1.log 2>&1
docker logs --timestamps titiler-worker-2 > titiler-log-dump/titiler-worker-2.log 2>&1
docker logs --timestamps titiler-worker-3 > titiler-log-dump/titiler-worker-3.log 2>&1
docker logs --timestamps titiler-worker-4 > titiler-log-dump/titiler-worker-4.log 2>&1

tar -czf titiler-log-dump.tar.gz titiler-log-dump