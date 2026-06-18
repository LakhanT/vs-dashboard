"""CLI: one-shot Fyers browser login (saves backend/token.json)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.fyers_auth import get_fyers_client

if __name__ == "__main__":
    client = get_fyers_client()
    print(client.get_profile())
