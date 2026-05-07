"""Make the ai/ module importable without docker.

Adds ../../ai to sys.path. Tests that need the DB or Ollama skip if those
services aren't reachable on localhost.
"""
import os
import sys
from pathlib import Path

AI_DIR = Path(__file__).resolve().parents[2] / "ai"
sys.path.insert(0, str(AI_DIR))

# Provide harmless defaults so `import db` doesn't KeyError on environment.
os.environ.setdefault("PGHOST", "localhost")
os.environ.setdefault("PGPORT", "5432")
os.environ.setdefault("PGUSER", "postgres")
os.environ.setdefault("PGPASSWORD", "postgres")
os.environ.setdefault("PGDATABASE", "usaspending")
os.environ.setdefault("OLLAMA_HOST", "http://localhost:11434")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-not-used")
