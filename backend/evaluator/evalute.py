import requests
import json
import os
from dotenv import load_dotenv
load_dotenv()

def call_chat_api():
    url = f"{os.getenv('OPENAI_API_BASE_URL')}v1/chat/completions"
    headers = {
        "accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {os.getenv('OPENAI_API_KEY')}"
    }
    payload = {
        "model": os.getenv('MODEL'),
        "messages": [{"role": "user", "content": "hello world"}],
        "stream": False
    }
    
    response = requests.post(url, headers=headers, json=payload)
    response.raise_for_status()  # Raises an exception for bad status codes
    
    # Try parsing streaming chunk if it's returning one anyway
    if response.text.startswith("data:"):
        print("Received streaming response. Parsing first valid chunk...")
        for line in response.text.split("\\n"):
            if line.startswith("data: ") and line.strip() != "data: [DONE]":
                try:
                    return json.loads(line[6:])
                except json.JSONDecodeError:
                    continue
        return {"error": "Could not parse stream"}
    return response.json()

# Usage
try:
    result = call_chat_api()
    print(json.dumps(result, indent=2))
except Exception as e:
    print(f"Error calling API: {e}")