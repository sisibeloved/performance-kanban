#!/bin/bash
# Start Streamlit server
cd /opt/ZCode/performance-kanban
pkill -f 'streamlit run' 2>/dev/null
sleep 1
~/.local/bin/streamlit run perf_kanban.py sample_data/ \
    --server.headless true \
    --server.port 8501 \
    --server.address 0.0.0.0 \
    </dev/null \
    >>/tmp/streamlit.log 2>&1 &
disown -a
sleep 3
echo "=== Process ==="
pgrep -af streamlit
echo "=== Port ==="
ss -tlnp | grep 8501
echo "=== Internal curl ==="
curl -s -o /dev/null -w "HTTP %{http_code}\n" http://localhost:8501/
