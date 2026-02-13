@echo off
echo Starting Bastet AI Web System...
echo 1. Starting Backend (FastAPI)...
start "Bastet Backend" cmd /k "python server.py"

echo 2. Starting Frontend (React)...
cd frontend
start "Bastet Frontend" cmd /k "npm run dev"

echo System launching... Please wait for browser to open at http://localhost:5173
echo (You might need to Ctrl+Click the link in the frontend terminal if it doesn't open automatically)
pause
