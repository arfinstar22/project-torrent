#!/usr/bin/env bash
# Kelola bot: start | stop | restart | status | logs
#
# Contoh:
#   ./server.sh start
#   ./server.sh status
#   ./server.sh logs 50
set -u

DIR="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="$DIR/bot.pid"
LOG="$DIR/bot.log"
PATTERN="[b]ot\.py"

cd "$DIR" || exit 1

is_running() {
    local pid
    pid=$(cat "$PID_FILE" 2>/dev/null) || return 1
    [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null
}

stop_bot() {
    # SIGTERM saja tidak cukup: Pyrogram menangkapnya dan session SQLite
    # bisa tetap terkunci, jadi proses dipaksa berhenti kalau perlu.
    pkill -f "$PATTERN" 2>/dev/null
    for _ in $(seq 1 20); do
        pgrep -f "$PATTERN" > /dev/null || return 0
        sleep 0.5
    done
    pkill -9 -f "$PATTERN" 2>/dev/null
    sleep 1
}

start_bot() {
    if is_running; then
        echo "Sudah jalan (pid $(cat "$PID_FILE"))."
        return 0
    fi

    stop_bot
    local attempt
    for attempt in 1 2; do
        setsid nohup .venv/bin/python bot.py > "$LOG" 2>&1 < /dev/null &
        echo $! > "$PID_FILE"
        sleep 8
        if is_running; then
            echo "Jalan (pid $(cat "$PID_FILE"))."
            return 0
        fi
        # Session SQLite kadang masih terkunci sesaat setelah proses lama mati.
        grep -q "database is locked" "$LOG" || break
        sleep 2
    done

    echo "Gagal start. 20 baris terakhir log:"
    tail -20 "$LOG"
    return 1
}

case "${1:-start}" in
    start)
        start_bot
        ;;
    stop)
        stop_bot
        echo "Berhenti."
        ;;
    restart)
        stop_bot
        start_bot
        ;;
    status)
        if is_running; then
            echo "Jalan (pid $(cat "$PID_FILE"))."
        else
            echo "Mati."
            exit 1
        fi
        ;;
    logs)
        tail -n "${2:-30}" -f "$LOG"
        ;;
    *)
        echo "Pakai: $0 {start|stop|restart|status|logs [jumlah baris]}"
        exit 1
        ;;
esac
