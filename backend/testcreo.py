
import os
import time
import requests
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.environ["CREAO_HUB_KEY"]
AGENT_ID = os.environ["CREAO_HUB_APP_ID"]

BASE_URL = "https://agent.creao.ai/api/v1"

headers = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json",
}

# Start an agent run
response = requests.post(
    f"{BASE_URL}/runs",
    headers=headers,
    json={
        "appId": AGENT_ID,
        "inputs": {
            "prompt": "Analyze this test simulation data."
        }
    },
    timeout=30,
)

response.raise_for_status()

run_id = response.json()["id"]
print("Agent started:", run_id)

# Check the run until it finishes
for _ in range(60):
    response = requests.get(
        f"{BASE_URL}/runs/{run_id}",
        headers=headers,
        timeout=30,
    )
    response.raise_for_status()

    data = response.json()
    status = data["status"]
    print("Status:", status)

    if status in ("completed", "failed", "cancelled"):
        print("Result:", data.get("result"))
        print("Error:", data.get("error"))
        break

    time.sleep(5)
else:
    print("Still processing. Run ID:", run_id)
