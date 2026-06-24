#!/usr/bin/env bash
set -euo pipefail

echo "Legacy MLB fixed-time schedules should be removed from template.yaml during the next infrastructure patch."
echo "Runtime protection is active: MLB pull Lambda refuses non-HOT inputs."
