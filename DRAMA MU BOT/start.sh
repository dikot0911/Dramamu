#!/bin/bash

# Start script untuk menjalankan Bot dan FastAPI backend bersamaan
# Script ini akan menjalankan keduanya secara parallel

echo "🚀 Starting Dramamu Backend System..."

echo "✅ Current directory: $(pwd)"
echo "📝 Files in directory:"
ls -la

echo "🤖 Starting Telegram Bot in background..."
python bot.py &
BOT_PID=$!
echo "   Bot PID: $BOT_PID"

# Wait a moment for bot to initialize
sleep 2

echo "🔥 Starting FastAPI Backend..."
# Backend API runs on port from environment (Railway/Render) or defaults to 8000
BACKEND_PORT=${PORT:-8000}
echo "   Listening on port: $BACKEND_PORT"
uvicorn main:app --host 0.0.0.0 --port $BACKEND_PORT &
API_PID=$!
echo "   API PID: $API_PID"

echo ""
echo "✅ Both services started successfully!"
echo "🤖 Telegram Bot: Running (PID: $BOT_PID)"
echo "🔥 FastAPI Backend: http://0.0.0.0:$BACKEND_PORT (PID: $API_PID)"
echo ""
if [ -n "$RENDER_EXTERNAL_URL" ]; then
    echo "💡 Platform: Render.com"
    echo "💡 Backend URL: $RENDER_EXTERNAL_URL"
    echo "🧪 Health check: $RENDER_EXTERNAL_URL/health"
elif [ -n "$RAILWAY_PUBLIC_DOMAIN" ]; then
    echo "💡 Platform: Railway.app"
    echo "💡 Backend URL: https://$RAILWAY_PUBLIC_DOMAIN"
    echo "🧪 Health check: https://$RAILWAY_PUBLIC_DOMAIN/health"
else
    echo "💡 Platform: Local/Development"
    echo "💡 Backend running on port: $BACKEND_PORT"
    echo "🧪 Health check: http://localhost:$BACKEND_PORT/health"
fi
echo ""
echo "Waiting for processes..."

# Wait for both processes
wait $BOT_PID $API_PID
