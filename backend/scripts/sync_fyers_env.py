import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.fyers_credentials import save_credentials_and_sync_env

src = Path(r"C:\Users\Lakhan\Downloads\credentials.txt")
if not src.exists():
    raise SystemExit(f"Missing {src}")

content = src.read_bytes()
save_credentials_and_sync_env(content, filename="credentials.txt")
shutil.copy2(src, Path(__file__).resolve().parents[1] / "credentials.txt")
print("Fyers credentials synced to backend/.env and backend/credentials.txt")
