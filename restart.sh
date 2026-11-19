#!/usr/bin/env bash
# Nama lama; sama dengan ./server.sh restart.
exec "$(cd "$(dirname "$0")" && pwd)/server.sh" restart
