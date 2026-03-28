import requests
import json
import os
import re
from dotenv import load_dotenv
load_dotenv()

def call_chat_api(prompt):
    url = f"{os.getenv('OPENAI_API_BASE_URL')}v1/chat/completions"
    headers = {
        "accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {os.getenv('OPENAI_API_KEY')}"
    }
    payload = {
        "model": os.getenv('MODEL'),
        "messages": [{"role": "user", "content": prompt}],
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
    with open(os.path.join(os.path.dirname(__file__), "RUBRIC.md"), "r") as f:
        rubric_content = f.read()
    
    with open(os.path.join(os.path.dirname(__file__), "parsed.html"), "r") as f:
        html_content = f.read()

    combined_prompt = f"""
Please act as an expert evaluator. I will provide you with a rubric for evaluating the effectiveness of a Persuader, followed by an HTML discussion thread. We assume the person who made the initial post is the Persuader trying to do the convincing.

RUBRIC:
{rubric_content}

DISCUSSION THREAD:
{html_content}

Using the rubric, please grade the Persuader's performance and provide your detailed assessment and final score. The response should be a complete HTML file. Embed the final score as pure JSON text inside a <div id="score-summary"> element. Do not use markdown backticks around the json inside the div, and do not use a script tag to generate the JSON. The JSON should be directly inside the div and structured like:
{{ "argument_strength_logic": number, "empathy_tone": number, "adaptability_responsiveness": number, "progress_towards_goal": number, "overall_score": number }}
"""

    result = call_chat_api(combined_prompt)
    
    if "choices" in result and len(result["choices"]) > 0:
        content = result["choices"][0]["message"]["content"]
        
        # Remove Markdown HTML code block wrappers if they exist
        content = re.sub(r'^```html\s*', '', content, flags=re.MULTILINE|re.IGNORECASE)
        content = re.sub(r'^```\s*$', '', content, flags=re.MULTILINE)
        content = content.strip()
        
        print(content)
        
        score_html_path = os.path.join(os.path.dirname(__file__), "score.html")
        score_json_path = os.path.join(os.path.dirname(__file__), "score.json")
        
        # Save HTML
        with open(score_html_path, "w") as f:
            f.write(content)
            
        # Extract JSON using regex: find the first { ... } block inside the summary div if possible,
        # or globally if not found.
        json_match = re.search(r'<div[^>]*id=["\']score-summary["\'][^>]*>.*?(\{.*\}).*?</div>', content, re.DOTALL | re.IGNORECASE)
        if not json_match:
            json_match = re.search(r'<div[^>]*class=["\']score-summary["\'][^>]*>.*?(\{.*\}).*?</div>', content, re.DOTALL | re.IGNORECASE)
            
        if not json_match:
            # Fallback: just find the first JSON-like block {} anywhere
            json_match = re.search(r'(\{[\s\S]*?\})', content)
            
        if json_match:
            json_str = json_match.group(1).strip()
            
            with open(score_json_path, "w") as f:
                f.write(json_str)
            print(f"\nSaved {score_html_path} and {score_json_path} successfully.")
        else:
            print(f"\nSaved {score_html_path} but no JSON found to save to score.json.")
    else:
        print(json.dumps(result, indent=2))
except Exception as e:
    print(f"Error calling API: {e}")