#!/bin/bash
# Auto-restart loop; token is read from the environment.
cd "$(dirname "$0")"
while true; do
  python3 bot.py >> bot.log 2>&1
  echo "$(date '+%F %T') bot exited ($?), restarting in 5s" >> bot.log
  sleep 5
done
