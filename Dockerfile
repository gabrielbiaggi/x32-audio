FROM python:3.10-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code and config
COPY src/ src/
COPY config/ config/

# Set Python path
ENV PYTHONPATH=/app

# Default command (can be overridden by K8s)
CMD ["python", "src/brain_core.py", "--broker", "mosquitto", "--config", "/app/config/x32_map.json"]
