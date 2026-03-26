#!/bin/bash
echo "=== Installing Python dependencies ==="
pip install -r requirements.txt

echo "=== Installing Node.js dependencies ==="
cd baileys-server
npm install
cd ..

echo "=== Build complete ==="
