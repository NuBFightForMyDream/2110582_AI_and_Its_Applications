"""Optional, user-enabled Gemini explanation. Never selects numerical outcomes."""
import json
import os

from pydantic import BaseModel, Field

from .models import AnalysisResult


class Explanation(BaseModel):
    text: str = Field(min_length=1, max_length=1600)
    evidence_ids: list[str]


def explain(result: AnalysisResult, instructions: str, timeout_seconds: float) -> str:
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_NOT_CONFIGURED")
    if timeout_seconds <= 1:
        raise TimeoutError("Insufficient time for an optional explanation")
    from google import genai
    from google.genai import types
    evidence = {row.experiment_id for row in result.candidates}
    if result.baseline:
        evidence.add(result.baseline.experiment_id)
    payload = result.model_dump(exclude={"events", "gemini_explanation"})
    client = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=int(timeout_seconds * 1000), retry_options=types.HttpRetryOptions(attempts=1)))
    try:
        response = client.models.generate_content(
            model=os.getenv("GEMINI_MODEL", "gemini-flash-latest"),
            contents=instructions + "\nExplain the supplied validated result in plain English. "
                     "Keep it under 120 words. Cite supplied experiment IDs only.\n" + json.dumps(payload),
            config=types.GenerateContentConfig(response_mime_type="application/json", response_schema=Explanation,
                                               max_output_tokens=800, temperature=0.1),
        )
        result.model_tokens += getattr(response.usage_metadata,"total_token_count",0) or 0
        parsed = Explanation.model_validate_json(response.text)
        if not set(parsed.evidence_ids).issubset(evidence):
            raise ValueError("UNSUPPORTED_EXPLANATION_REFERENCE")
        return parsed.text
    finally:
        client.close()
