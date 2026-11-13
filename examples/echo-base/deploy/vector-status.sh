#!/bin/bash
# Vector's "Status Report" (wire-pod custom intent) on the echo-base Jetson (#349).
# Install: sudo install -m 755 vector-status.sh /usr/local/bin/vector-status.sh
#
# Says the hub's newest machine-health reading, so Vector says exactly what LTMS
# recorded. Falls back to reading the machine directly if the hub isn't running.
STATE=/var/lib/echo-base/hub-health.json
HEALTH=/opt/ai_si_ltm/cli_agent/hub_health.py   # standard library only: the system python3 can run it
if [ -r "$HEALTH" ] && out=$(python3 "$HEALTH" latest --state "$STATE" --speak 2>/dev/null); then
  echo "$out"
  exit 0
fi

CPU=$(vmstat 1 2 | tail -1 | awk '{print 100-$15}')
MEM_USED=$(free -m | awk 'NR==2{print $3}')
MEM_TOTAL=$(free -m | awk 'NR==2{print $2}')
DISK=$(df -h / | awk 'NR==2{print $5}')
TEMP=$(cat /sys/class/thermal/thermal_zone0/temp 2>/dev/null | awk '{printf "%.0f", $1/1000}')
OLLAMA=$(curl -s --max-time 2 http://localhost:11434/api/tags > /dev/null 2>&1 && echo "online" || echo "offline")
case "$(systemctl is-active wire-pod)" in
  active) WIREPOD=running ;; inactive) WIREPOD=stopped ;; *) WIREPOD="$(systemctl is-active wire-pod)" ;;
esac
echo "Jetson status report, read directly because the hub isn't reporting. CPU at ${CPU} percent. Memory: ${MEM_USED} of ${MEM_TOTAL} megabytes used. Disk at ${DISK} capacity. Temperature: ${TEMP} degrees Celsius. Ollama is ${OLLAMA}. Wire-pod is ${WIREPOD}."
