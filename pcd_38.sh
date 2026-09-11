#!/bin/bash

set -e

LOG_FILE="/home/fastpace_rpa/Documents/Eauditreports/logs/run_log_$(date +%Y%m%d_%H%M%S).log"

echo "===== START $(date) =====" >> "$LOG_FILE" 2>&1

# Explicit PATH (cron does NOT load your shell config)
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

# Move to project directory
cd "/home/fastpace_rpa/Documents/Eauditreports" || {
    echo "Failed to cd into project dir" >> "$LOG_FILE"
    exit 1
}

# Activate virtual environment
source "/home/fastpace_rpa/Documents/Eauditreports/.venv/bin/activate" || {
    echo "Failed to activate venv" >> "$LOG_FILE"
    exit 1
}

# Debug info
echo "Python: $(which python)" >> "$LOG_FILE"
echo "PWD: $(pwd)" >> "$LOG_FILE"

# Run script
"/home/fastpace_rpa/Documents/Eauditreports/.venv/bin/python" pcd_38.py >> "$LOG_FILE" 2>&1

echo "===== END $(date) =====" >> "$LOG_FILE" 2>&1