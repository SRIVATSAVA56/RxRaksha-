"""LLM fallback resolver for unresolved prescription lines with strict safety labeling.

Rules:
1. The LLM normalizes the line -> retry deterministic KB lookup first.
2. Only if still unresolved -> LLM interaction data stored as separate llm_findings tagged UNVERIFIED.
3. LLM findings are NEVER merged into report.findings and NEVER change the numeric score.
"""
from __future__ import annotations
import json
import logging
import os
import re
import httpx

logger = logging.getLogger("rxraksha.llm_resolver")

RESOLVER_PROMPT = """You are a pharmaceutical data resolver inside a prescription
safety system. A deterministic knowledge base FAILED to resolve the lines below.

UNRESOLVED LINES:
{unresolved_lines}

ALREADY-RESOLVED INGREDIENTS IN THIS PRESCRIPTION:
{resolved_ingredients}

TASK, in strict order:
1. Extract the generic ingredient name (lowercase INN), e.g. "aspirin".
2. Parse dosing abbreviations: OD = once daily, BD = twice daily,
   TDS = three times daily, ON = at night (once daily), PRN = as needed.
3. Compute total daily dose in mg (strength x frequency).
4. Check the ingredient against the ALREADY-RESOLVED list and report
   clinically significant interactions using Google Search to verify
   against authoritative sources (DailyMed, FDA label, BNF, DrugBank).

STRICT RULES:
- Severity vocabulary: ONLY ["contraindicated","major","moderate","minor","unknown"].
- If you cannot verify a value, set it to null. NEVER guess.
- Do NOT report interactions between two UNRESOLVED lines and themselves.
- Output VALID JSON only, matching this exact schema. No markdown, no commentary.

SCHEMA:
{{
  "resolved": [
    {{
      "entered_line": "string",
      "ingredient": "string or null",
      "strength_mg": number or null,
      "daily_mg": number or null,
      "confidence": "high" or "low",
      "sources": ["domain names actually used"],
      "interactions": [
        {{"with": "resolved ingredient", "severity": "major", "note": "one line mechanism"}}
      ]
    }}
  ]
}}"""

# In-memory cache per entered_line so identical lines are not resolved twice
_CACHE: dict[str, dict] = {}


def resolve_unknowns(unresolved_lines: list[str], resolved_ingredients: list[str]) -> dict:
    """Fallback resolver for lines the KB could not match.
    Returns LLM data tagged as UNVERIFIED — caller must NOT feed this
    silently into the deterministic score."""
    if not unresolved_lines:
        return {"resolved": []}

    # Check cache for any already-resolved lines
    needed_lines = [l for l in unresolved_lines if l.strip() not in _CACHE]
    cached_results = [_CACHE[l.strip()] for l in unresolved_lines if l.strip() in _CACHE]

    if not needed_lines:
        return {"resolved": cached_results}

    prompt = RESOLVER_PROMPT.format(
        unresolved_lines="\n".join(f"- {l}" for l in needed_lines),
        resolved_ingredients=", ".join(resolved_ingredients) if resolved_ingredients else "none",
    )

    data = None
    gemini_key = os.environ.get("GEMINI_API_KEY", "").strip()
    llm_key = os.environ.get("LLM_API_KEY", "").strip()

    # 1. Try google.genai if GEMINI_API_KEY is present
    if gemini_key:
        try:
            from google import genai
            from google.genai import types

            client = genai.Client(api_key=gemini_key)
            try:
                # Try with search grounding first
                resp = client.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        tools=[types.Tool(google_search=types.GoogleSearch())],
                        temperature=0.0,
                        response_mime_type="application/json",
                    ),
                )
            except Exception as search_err:
                logger.info("Search grounding unavailable, falling back to base model: %s", search_err)
                resp = client.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        temperature=0.0,
                        response_mime_type="application/json",
                    ),
                )
            data = json.loads(resp.text)
        except Exception as e:
            logger.warning("Gemini resolver call failed: %s", e)

    # 2. Try OpenRouter / LLM_API_KEY if not resolved yet
    if not data and llm_key:
        try:
            model = os.environ.get("LLM_MODEL", "openai/gpt-4o-mini").strip() or "openai/gpt-4o-mini"
            url = os.environ.get("LLM_URL", "https://openrouter.ai/api/v1/chat/completions").strip()
            headers = {
                "Authorization": f"Bearer {llm_key}",
                "X-Title": "RxRaksha",
                "Content-Type": "application/json",
            }
            payload = {
                "model": model,
                "temperature": 0.0,
                "messages": [
                    {"role": "user", "content": prompt}
                ],
                "response_format": {"type": "json_object"}
            }
            with httpx.Client(timeout=25.0) as http_client:
                r = http_client.post(url, json=payload, headers=headers)
                r.raise_for_status()
                res_json = r.json()
                raw_text = res_json["choices"][0]["message"]["content"].strip()
                # Clean markdown backticks if returned
                if raw_text.startswith("```"):
                    raw_text = re.sub(r"^```[a-z]*\n?", "", raw_text)
                    raw_text = re.sub(r"\n?```$", "", raw_text)
                data = json.loads(raw_text)
        except Exception as e:
            logger.warning("OpenRouter/LLM resolver call failed: %s", e)

    if not data or not isinstance(data, dict):
        # Fallback empty structure if API unavailable
        data = {"resolved": []}

    # Validation safety net — never trust the LLM blindly
    ALLOWED = {"contraindicated", "major", "moderate", "minor", "unknown"}
    resolved_set = {r.lower().strip() for r in resolved_ingredients}

    for item in data.get("resolved", []):
        item["status"] = "AI-RESOLVED — UNVERIFIED"
        item["source"] = "llm-web-search"
        ing = item.get("ingredient")
        if ing:
            cleaned = ing.strip().lower()
            # Allow alphabetic characters, spaces, and hyphens (e.g. acetylsalicylic acid)
            if all(c.isalpha() or c in ' -' for c in cleaned) and len(cleaned) >= 2:
                item["ingredient"] = cleaned
            else:
                item["ingredient"] = None

        valid_ixs = []
        for ix in item.get("interactions", []):
            sev = ix.get("severity", "unknown").lower()
            if sev not in ALLOWED:
                ix["severity"] = "unknown"
            else:
                ix["severity"] = sev

            with_drug = (ix.get("with") or "").lower().strip()
            if with_drug and (with_drug in resolved_set or any(with_drug in r for r in resolved_set)):
                valid_ixs.append(ix)

        item["interactions"] = valid_ixs
        # Cache resolution
        line_key = item.get("entered_line", "").strip()
        if line_key:
            _CACHE[line_key] = item
            logger.info("Cached LLM resolution for '%s': ingredient=%s", line_key, item.get("ingredient"))

    # Merge cached and new
    all_resolved = cached_results + data.get("resolved", [])
    return {"resolved": all_resolved}
