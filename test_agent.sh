#!/bin/bash

# Test script for kospex-agent Enter key functionality
echo "Testing kospex-agent with Enter key functionality..."
echo "This script will start the agent for 10 seconds, then send an Enter key press."
echo

cd /Users/peterfreiberg/dev/github.com/kospex/kospex
source .venv/bin/activate

# Run kospex-agent in background with a longer interval
timeout 10s bash -c 'echo -e "\n" | kospex-agent start --interval 30 --verbose' &

# Wait a moment for the agent to start
sleep 2

echo "Agent should be running now. It will be automatically terminated after 10 seconds."
echo "Check the output above to see if Enter key detection works."

wait
echo "Test completed."
