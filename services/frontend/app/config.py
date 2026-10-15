import os

API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")
USE_MOCKS = os.getenv("USE_MOCKS", "true").lower() == "true"
HOST = os.getenv("FRONTEND_HOST", "0.0.0.0")
PORT = int(os.getenv("FRONTEND_PORT", "8080"))
