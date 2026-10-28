"""
STEP 3 - Your first call to the LLM (the "brain") through the Groq API.

Before running this you need:
  1. pip install groq python-dotenv
  2. A file named  .env  in this folder containing one line:
         GROQ_API_KEY=your_key_here
     (no quotes, no spaces around the = sign)

What it does:
  - Makes sure .gitignore exists and protects .env (your GitHub repo is PUBLIC,
    so a leaked key would be visible to everyone).
  - Loads your key from .env.
  - Sends one small question to the model and prints the reply.
  - Prints how many tokens were used (free plans have daily token limits).

How to run:
    python 03_first_llm_call.py
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# Model name can change over time. If you get a "model not found" error,
# open https://console.groq.com/docs/models and copy a current model id here.
MODEL = "openai/gpt-oss-120b"

GITIGNORE_LINES = [".env", ".venv/", "data/", "outputs/", "__pycache__/", "*.pyc"]


def protect_secrets() -> None:
    """Add .env (and other local-only folders) to .gitignore without deleting anything."""
    path = ROOT / ".gitignore"
    existing = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    missing = [line for line in GITIGNORE_LINES if line not in existing]
    if missing:
        with path.open("a", encoding="utf-8") as f:
            if existing and existing[-1] != "":
                f.write("\n")
            f.write("\n".join(missing) + "\n")
        print(f"[OK] .gitignore updated (added: {', '.join(missing)})")
    else:
        print("[OK] .gitignore already protects .env")


def main() -> int:
    protect_secrets()

    try:
        from dotenv import load_dotenv
        from groq import Groq
    except ImportError:
        print("\n[FAIL] Missing packages. Run:  pip install groq python-dotenv")
        return 1

    env_path = ROOT / ".env"
    if not env_path.exists():
        print(f"\n[FAIL] No .env file found at {env_path}")
        print("Create it with one line:  GROQ_API_KEY=your_key_here")
        return 1
    load_dotenv(env_path)

    key = os.getenv("GROQ_API_KEY", "").strip()
    if not key:
        print("\n[FAIL] .env exists but GROQ_API_KEY is empty or missing.")
        return 1
    print(f"[OK] API key loaded (starts with '{key[:4]}...', length {len(key)})")

    client = Groq(api_key=key)
    print(f"\nAsking {MODEL} a question...\n")
    try:
        reply = client.chat.completions.create(
            model=MODEL,
            temperature=0,  # 0 = steady, repeatable answers (best for analysis)
            messages=[
                {
                    "role": "system",
                    "content": "You are a careful financial analyst. Answer in two short sentences.",
                },
                {
                    "role": "user",
                    "content": "In one line each: what is revenue, and what is gross margin?",
                },
            ],
        )
    except Exception as e:
        print(f"[FAIL] The API call failed:\n  {type(e).__name__}: {e}")
        print("\nCommon causes: wrong key (401), model name changed (404), "
              "too many requests (429), no internet.")
        return 1

    print("MODEL REPLY")
    print("-" * 60)
    print(reply.choices[0].message.content)
    print("-" * 60)
    u = reply.usage
    if u:
        print(f"Tokens used: {u.prompt_tokens} in + {u.completion_tokens} out = {u.total_tokens}")
    print("\nSuccess. Next: tell me this worked and paste the reply, and I will give you Step 4.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
