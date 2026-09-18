import os
from dotenv import dotenv_values

def audit_providers():
    env_file = ".env"
    vals = dotenv_values(env_file) if os.path.exists(env_file) else {}
    
    groq_key = vals.get("GROQ_API_KEY", "").strip()
    gemini_key = vals.get("GEMINI_API_KEY", "").strip()
    
    groq_ok = bool(groq_key and not groq_key.startswith("your_") and len(groq_key) > 10)
    gemini_ok = bool(gemini_key and not gemini_key.startswith("your_") and len(gemini_key) > 10)
    
    print("  Groq Provider:         " + ("[+] CONFIGURED" if groq_ok else "[-] NOT CONFIGURED"))
    print("  Google Gemini Provider: " + ("[+] CONFIGURED" if gemini_ok else "[-] NOT CONFIGURED"))
    print("")
    
    if groq_ok and gemini_ok:
        print("  [OK] Dual-provider architecture ready (AUTO failover active).")
    elif groq_ok:
        print("  ---------------------------------------------------------------")
        print("  WARNING: Gemini API key not configured.")
        print("  Groq provider will remain available (DEGRADED MODE).")
        print("  ---------------------------------------------------------------")
    elif gemini_ok:
        print("  ---------------------------------------------------------------")
        print("  WARNING: Groq API key not configured.")
        print("  Gemini provider will remain available (DEGRADED MODE).")
        print("  ---------------------------------------------------------------")
    else:
        print("  ---------------------------------------------------------------")
        print("  WARNING: Neither Groq nor Gemini keys are configured in .env.")
        print("  Please add at least one key to enable autonomous AI testing.")
        print("  ---------------------------------------------------------------")

if __name__ == "__main__":
    audit_providers()
