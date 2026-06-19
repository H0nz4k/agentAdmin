#!/usr/bin/env bash
source "$(dirname "$0")/../lib/common.sh"
require_command docker
printf '=== CONTAINERS ===\n'
docker ps -a --no-trunc --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'
printf '\n=== RESOURCE USAGE ===\n'
docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}\t{{.NetIO}}\t{{.BlockIO}}'
printf '\n=== STORAGE ===\n'
docker system df -v
