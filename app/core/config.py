import os

def _get_api_id() -> int:
    val = os.getenv("TELEGRAM_API_ID", "")
    if not val:
        raise RuntimeError("TELEGRAM_API_ID env variable is not set")
    try:
        return int(val)
    except ValueError:
        raise RuntimeError(f"TELEGRAM_API_ID must be an integer, got: {val!r}")

API_ID: int = _get_api_id()
API_HASH: str = os.getenv("TELEGRAM_API_HASH", "")

if not API_HASH:
    raise RuntimeError("TELEGRAM_API_HASH env variable is not set")