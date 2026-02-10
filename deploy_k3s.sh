#!/bin/bash

# Deploy script for K3s
# Assumes kubectl is configured and pointing to the cluster

echo "Deploying X32 Automation to K3s..."

# 1. Build Docker Image (if running on the node itself)
# Or user needs to push to a registry. We'll label it 'x32-brain:latest'
# If using K3ss built-in containerd:
# sudo k3s ctr images import x32-brain.tar
echo "Building Docker Image..."
docker build -t x32-brain:latest .

# Export if needed (optional step for transferring to K3s node if developing remotely)
# docker save x32-brain:latest > x32-brain.tar

# 2. Apply Namespace
kubectl apply -f k8s/namespace.yaml

# 3. Apply ConfigMap
kubectl apply -f k8s/configmap.yaml

# 4. Apply Mosquitto
kubectl apply -f k8s/mosquitto.yaml

# 5. Apply Brain
kubectl apply -f k8s/brain.yaml

echo "Deployment applied. Check status with: kubectl get pods -n x32-automation"
