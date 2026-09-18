"""Test Groq model capabilities."""
from openai import OpenAI
import os
from dotenv import load_dotenv

load_dotenv('.env')

client = OpenAI(api_key=os.getenv('GROQ_API_KEY'), base_url='https://api.groq.com/openai/v1')
client2 = OpenAI(api_key=os.getenv('GROQ_API_KEY_2'), base_url='https://api.groq.com/openai/v1')

schema = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "action": {"type": "string", "enum": ["click","type","scroll","press_key","back","wait","finish","fail","dismiss_modal"]},
        "element_id": {"type": ["string", "null"]},
        "text": {"type": ["string", "null"]},
        "key": {"type": ["string", "null"]},
        "scroll_y": {"type": ["integer", "null"]},
        "rationale": {"type": "string"},
        "expected_result": {"type": "string"},
        "confidence": {"type": "number"},
        "goal_complete": {"type": "boolean"},
        "stuck": {"type": "boolean"}
    },
    "required": ["action","element_id","text","key","scroll_y","rationale","expected_result","confidence","goal_complete","stuck"]
}

print("Testing qwen/qwen3.8-27b with JSON schema...")
try:
    resp = client.chat.completions.create(
        model='qwen/qwen3.8-27b',
        messages=[
            {"role": "system", "content": "You control a browser. Output JSON only."},
            {"role": "user", "content": 'Goal: explore navigation. UI: [{"id":"e0","tag":"a","role":"link","text":"Home"}]. Choose an action.'}
        ],
        max_tokens=200,
        temperature=0.1,
        response_format={"type": "json_schema", "json_schema": {"name": "ui_action", "strict": True, "schema": schema}},
    )
    print("SUCCESS (key1):", resp.choices[0].message.content)
except Exception as e:
    print("FAILED (key1):", str(e)[:200])

print("\nTesting key2...")
try:
    resp2 = client2.chat.completions.create(
        model='qwen/qwen3.8-27b',
        messages=[
            {"role": "system", "content": "You control a browser. Output JSON only."},
            {"role": "user", "content": 'Goal: explore navigation. UI: [{"id":"e0","tag":"a","role":"link","text":"Home"}]. Choose an action.'}
        ],
        max_tokens=200,
        temperature=0.1,
        response_format={"type": "json_schema", "json_schema": {"name": "ui_action", "strict": True, "schema": schema}},
    )
    print("SUCCESS (key2):", resp2.choices[0].message.content)
except Exception as e:
    print("FAILED (key2):", str(e)[:200])

# Test vision
print("\nTesting vision support with qwen...")
try:
    resp3 = client.chat.completions.create(
        model='qwen/qwen3.8-27b',
        messages=[
            {"role": "user", "content": [
                {"type": "text", "text": "What do you see in this 1x1 image?"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="}}
            ]}
        ],
        max_tokens=50,
    )
    print("Vision SUPPORTED:", resp3.choices[0].message.content)
except Exception as e:
    print("Vision NOT supported:", str(e)[:200])
