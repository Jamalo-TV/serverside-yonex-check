"""
AI Size Analyzer Module
Supports Gemini and Qwen for analyzing product size descriptions.
"""

import os
import json
import time
import re
from openai import OpenAI

# Try to import Gemini, but make it optional
try:
    from google import genai
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False
    print("⚠️ Google GenAI not installed. Gemini will not be available.")

# API Keys (DO NOT hardcode secrets; set env vars instead)
# Gemini: set GEMINI_API_KEY
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
# Qwen (DashScope OpenAI-compatible): set DASHSCOPE_API_KEY (preferred) or QWEN_API_KEY
QWEN_API_KEY = (os.getenv("DASHSCOPE_API_KEY") or os.getenv("QWEN_API_KEY") or "").strip()

# Model configurations
AI_PROVIDERS = {
    "gemini": {
        "models": {
            "2": "gemini-2.0-flash",
            "2.5": "gemini-2.5-flash-lite",
            "3": "gemini-3-flash-preview",
        },
        "default": "gemini-3-flash-preview"
    },
    "qwen": {
        "models": {
            "default": "qwen-flash",
            "turbo": "qwen-turbo",
            "plus": "qwen-plus",
            "max": "qwen3-max",
            "flash": "qwen-flash",
        },
        "default": "qwen-flash",
        "thinking_models": ["qwen-flash"]  # Models that require streaming with thinking
    }
}

# Current configuration
_current_provider = "gemini"
_current_model = AI_PROVIDERS["gemini"]["default"]


def set_ai_provider(provider: str, model_key: str = None):
    """Set the AI provider and optionally a specific model."""
    global _current_provider, _current_model
    
    provider = provider.lower()
    if provider not in AI_PROVIDERS:
        raise ValueError(f"Unknown provider: {provider}. Available: {list(AI_PROVIDERS.keys())}")
    
    _current_provider = provider
    
    if model_key:
        models = AI_PROVIDERS[provider]["models"]
        if model_key in models:
            _current_model = models[model_key]
        else:
            # Assume it's a direct model name
            _current_model = model_key
    else:
        _current_model = AI_PROVIDERS[provider]["default"]
    
    print(f"🤖 AI Provider: {_current_provider.upper()}, Model: {_current_model}")


def get_current_config():
    """Get current AI configuration."""
    return {"provider": _current_provider, "model": _current_model}


def _build_size_prompt(description: str) -> str:
    """Build the prompt for size analysis."""
    return f"""You are given a product size description written in German.
Task: extract all sizes from the text (shoe sizes, clothing sizes, kids sizes) and determine which are available and which are sold out, based on any annotations in the text (e.g., "(=nur ausverkauft in 46)", "(=komplett)", "(=ausverkauft)").

Rules (apply in order):
1. Extract sizes as individual strings. Recognize:
- Numeric sizes (e.g., "36", "36.5", "39,5", "130", "140").
- Normalize decimals written with ',' to '.' (e.g., "39,5" -> "39.5").
- Clothing sizes such as "XS", "S", "M", "L", "XL", "XXL", "XXXL", "2XL", "3XL", etc.
2. Recognize separators: ',', '/', ';', whitespace, '–', '-', 'bis' and split accordingly. 
- Expand numeric ranges like "36-38", "36–38", or "36 bis 38" -> ["36","37","38"].
- Expand clothing ranges like "S-XXL" into the sequence ["S","M","L","XL","XXL"] using the standard size order XS < S < M < L < XL < XXL < XXXL < 2XL < 3XL.
3. Remove duplicates and sort:
- Numeric sizes: sort numerically ascending ("36","36.5","37","130","140").
- Clothing sizes: sort in logical order XS < S < M < L < XL < XXL < XXXL < 2XL < 3XL (etc.).
- Mixed lists: place numeric sizes first, then clothing sizes.
4. Interpret annotations:
- If the text contains "komplett" (case-insensitive), treat it as **all listed sizes are available**: put all extracted sizes into "available" and set "sold_out":[] — unless explicit sold-out sizes are listed.
- If the text contains "nur ausverkauft in X", "ausverkauft in X", or similar, mark those specific sizes as sold_out and the remaining extracted sizes as available.
- If the text contains "ausverkauft" without specific sizes (e.g. "(=ausverkauft)"), treat it as **all listed sizes are sold out**: available=[], sold_out=[all sizes].
- If both "komplett" and explicit sold-out sizes appear, explicit sold-out sizes take precedence.
5. Output EXACTLY one line of valid JSON. No line breaks, no indentation, no extra text. The format must be:
{{"available":["...","..."],"sold_out":["...","..."]}}

Examples:

Input: "Grössen 39.5, 40, 40.5, 41, 42, 43, 44, 44.5, 45, 45.5, 47   (=nur ausverkauft in 46)"
Output: {{"available":["39.5","40","40.5","41","42","43","44","44.5","45","45.5","47"],"sold_out":["46"]}}

Input: "in den Grössen 36/37/37.5/38/39/39.5/40/40.5/41/42   (=komplett)"
Output: {{"available":["36","37","37.5","38","39","39.5","40","40.5","41","42"],"sold_out":[]}}

Input: "Kindergrössen 120, 130, 140   (=ausverkauft)"
Output: {{"available":[],"sold_out":["120","130","140"]}}

Input: "in den Grössen S, M, L, XL, XXL   (=nur ausverkauft in L)"
Output: {{"available":["S","M","XL","XXL"],"sold_out":["L"]}}

Input: "78% Polyester, 22% Modal (Cellulose) S-XXL (ausverkauft in XL)"
Output: {{"available":["S","M","L","XXL"],"sold_out":["XL"]}}

Now process the following input and return only the JSON in this compact one-line format:
[{description}]"""


def _send_to_gemini(prompt: str) -> dict:
    """Send prompt to Gemini API."""
    if not GEMINI_AVAILABLE:
        return {"available": [], "sold_out": [], "error": "Gemini not available (google-genai not installed)"}
    
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        
        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = client.models.generate_content(
                    model=_current_model, contents=prompt
                )
                return _parse_json_response(response.text)
                
            except Exception as api_error:
                error_str = str(api_error)
                print(f"⚠️ Gemini API Error: {error_str[:300]}...") if len(error_str) > 300 else print(f"⚠️ Gemini API Error: {error_str}")
                
                # Check for rate limit / retry delay
                if 'retryDelay' in error_str or 'RESOURCE_EXHAUSTED' in error_str or '429' in error_str:
                    delay_match = re.search(r"retryDelay[^0-9]*?(\d+)", error_str, re.IGNORECASE)
                    if delay_match:
                        delay_seconds = int(delay_match.group(1))
                        print(f"📊 Parsed delay from API: {delay_seconds}s")
                        delay_seconds = delay_seconds + 5  # Add buffer
                    else:
                        delay_seconds = 60
                        print(f"📊 Could not parse delay, using default: {delay_seconds}s")
                    
                    delay_seconds = max(delay_seconds, 15)
                    
                    if attempt < max_retries - 1:
                        print(f"⏳ Rate limited. Waiting {delay_seconds}s before retry (attempt {attempt + 1}/{max_retries})...")
                        time.sleep(delay_seconds)
                    else:
                        print(f"⏳ Rate limited. Max retries ({max_retries}) exhausted.")
                        return {"available": [], "sold_out": [], "error": f"Rate limited after {max_retries} attempts"}
                else:
                    raise
        
        return {"available": [], "sold_out": [], "error": "Max retries exceeded"}
        
    except Exception as e:
        print(f"⚠️ Error communicating with Gemini API: {e}")
        return {"available": [], "sold_out": [], "error": str(e)}


def _send_to_qwen(prompt: str) -> dict:
    """Send prompt to Qwen API via Alibaba DashScope."""
    if not QWEN_API_KEY:
        return {"available": [], "sold_out": [], "error": "DASHSCOPE_API_KEY environment variable not set"}
    
    try:
        import httpx
        http_client = httpx.Client(proxy=None)
        client = OpenAI(
            base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
            api_key=QWEN_API_KEY,
            http_client=http_client,
        )
        
        # Check if this is a thinking model (like qwen-flash)
        thinking_models = AI_PROVIDERS["qwen"].get("thinking_models", [])
        is_thinking_model = _current_model in thinking_models
        
        max_retries = 3
        for attempt in range(max_retries):
            try:
                if is_thinking_model:
                    # Use streaming with thinking for flash model
                    completion = client.chat.completions.create(
                        model=_current_model,
                        messages=[{"role": "user", "content": prompt}],
                        extra_body={"enable_thinking": True},
                        stream=True
                    )
                    
                    # Collect the response from stream
                    response_text = ""
                    for chunk in completion:
                        delta = chunk.choices[0].delta
                        if hasattr(delta, "content") and delta.content:
                            response_text += delta.content
                    
                    return _parse_json_response(response_text)
                else:
                    # Standard non-streaming request
                    completion = client.chat.completions.create(
                        model=_current_model,
                        messages=[{"role": "user", "content": prompt}],
                    )
                    
                    response_text = completion.choices[0].message.content
                    return _parse_json_response(response_text)
                
            except Exception as api_error:
                error_str = str(api_error)
                print(f"⚠️ Qwen API Error: {error_str[:300]}...") if len(error_str) > 300 else print(f"⚠️ Qwen API Error: {error_str}")
                
                # Check for rate limit
                if '429' in error_str or 'rate' in error_str.lower():
                    delay_seconds = 30
                    if attempt < max_retries - 1:
                        print(f"⏳ Rate limited. Waiting {delay_seconds}s before retry (attempt {attempt + 1}/{max_retries})...")
                        time.sleep(delay_seconds)
                    else:
                        print(f"⏳ Rate limited. Max retries ({max_retries}) exhausted.")
                        return {"available": [], "sold_out": [], "error": f"Rate limited after {max_retries} attempts"}
                else:
                    raise
        
        return {"available": [], "sold_out": [], "error": "Max retries exceeded"}
        
    except Exception as e:
        print(f"⚠️ Error communicating with Qwen API: {e}")
        return {"available": [], "sold_out": [], "error": str(e)}


def _parse_json_response(response_text: str) -> dict:
    """Parse JSON from AI response."""
    response_text = response_text.strip()
    
    # Remove markdown code blocks if present
    if response_text.startswith("```"):
        lines = response_text.split("\n")
        # Remove first and last line (```json and ```)
        lines = [l for l in lines if not l.startswith("```")]
        response_text = "\n".join(lines).strip()
    
    # Extract JSON from response
    if '{' in response_text and '}' in response_text:
        json_start = response_text.find('{')
        json_end = response_text.rfind('}') + 1
        json_text = response_text[json_start:json_end]
        
        try:
            size_info = json.loads(json_text)
            return size_info
        except json.JSONDecodeError as e:
            return {"available": [], "sold_out": [], "error": f"JSON parse error: {e}"}
    else:
        return {"available": [], "sold_out": [], "error": "Invalid JSON response"}


def analyze_sizes(description: str) -> dict:
    """
    Analyze product sizes from description using the configured AI provider.
    
    Args:
        description: Product description text (in German)
    
    Returns:
        dict with 'available' and 'sold_out' lists
    """
    if not description:
        return {"available": [], "sold_out": [], "error": "No description provided"}
    
    prompt = _build_size_prompt(description)
    
    if _current_provider == "gemini":
        return _send_to_gemini(prompt)
    elif _current_provider == "qwen":
        return _send_to_qwen(prompt)
    else:
        return {"available": [], "sold_out": [], "error": f"Unknown provider: {_current_provider}"}


# For backwards compatibility
def send_to_gemini(description: str) -> dict:
    """Legacy function - use analyze_sizes() instead."""
    return analyze_sizes(description)


if __name__ == "__main__":
    # Test the module
    import argparse
    
    parser = argparse.ArgumentParser(description="Test AI Size Analyzer")
    parser.add_argument("--provider", choices=["gemini", "qwen"], default="gemini", help="AI provider to use")
    parser.add_argument("--model", help="Specific model to use")
    parser.add_argument("--test", help="Test description to analyze")
    
    args = parser.parse_args()
    
    set_ai_provider(args.provider, args.model)
    
    test_description = args.test or "Grössen 39.5, 40, 40.5, 41, 42, 43, 44, 44.5, 45, 45.5, 47 (=nur ausverkauft in 46)"
    
    print(f"\n📝 Testing with: {test_description}")
    result = analyze_sizes(test_description)
    print(f"✅ Result: {json.dumps(result, indent=2)}")
