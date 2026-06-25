import os
import sys

# Ensure src/ is importable and force MOCK mode for deterministic tests.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

# Force deterministic MOCK mode for tests (set BEFORE config/.env import; dotenv
# uses override=False so these empties win and no real API is called).
for _k in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "GROQ_API_KEY"):
    os.environ[_k] = ""
