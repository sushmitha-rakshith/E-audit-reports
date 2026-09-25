#!/bin/bash

set -e

PROJECT_DIR="/home/fastpace_rpa/Documents/Eauditreports"
LOG_DIR="${PROJECT_DIR}/logs"
LOG_FILE="${LOG_DIR}/pcd38_$(date +%Y%m%d_%H%M%S).log"

mkdir -p "$LOG_DIR"

STATUS=1
finish() {
    echo "===== END $(date) (exit $STATUS) =====" >> "$LOG_FILE"
}
trap finish EXIT

echo "===== START $(date) =====" >> "$LOG_FILE"

export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

cd "$PROJECT_DIR" || {
    echo "Failed to cd into project dir" >> "$LOG_FILE"
    exit 1
}

source "${PROJECT_DIR}/.venv/bin/activate" || {
    echo "Failed to activate venv" >> "$LOG_FILE"
    exit 1
}

echo "Python: $(which python)" >> "$LOG_FILE"
echo "PWD: $(pwd)" >> "$LOG_FILE"

set +e
"${PROJECT_DIR}/.venv/bin/python" pcd_38.py >> "$LOG_FILE" 2>&1
STATUS=$?
set -e

if [ $STATUS -ne 0 ]; then
    echo "pcd_38.py exited with status $STATUS" >> "$LOG_FILE"
fi

exit $STATUS
