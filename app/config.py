"""
Configuration settings for the GridWise application.
Reads from environment variables and optional .env file.
"""

import os
from dotenv import load_dotenv

# Load local .env if present
load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "7860"))
