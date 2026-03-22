import requests
from config import load_config


def get_ticker():
    config = load_config()
    base_url = config["base_url"]

    resp = requests.get(f"{base_url}/api/market/ticker", timeout=10)
    resp.raise_for_status()
    return resp.json()