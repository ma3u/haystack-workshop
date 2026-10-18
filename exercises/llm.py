"""
Shared LLM factory for the exercises.

Provider order:
  LLM_PROVIDER=gemini  -> Google Gemini API (key: GEMINI_API_KEY, GOOGLE_API_KEY or AISTUDIO_API_KEY; model GEMINI_MODEL)
  AZURE_OPENAI_ENDPOINT set -> Azure OpenAI (AI Foundry)
  otherwise -> OpenAI (needs OPENAI_API_KEY)

Env vars (see .env.example):
  AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY, AZURE_OPENAI_API_VERSION,
  AZURE_OPENAI_DEPLOYMENT  (e.g. gpt-5-mini)
"""
import os

from dotenv import load_dotenv
from haystack.components.generators.chat import AzureOpenAIChatGenerator, OpenAIChatGenerator
from haystack.utils import Secret

load_dotenv()


def chat_generator(**kwargs):
    """Return a chat generator for the configured provider. kwargs pass through (e.g. streaming_callback)."""
    if os.getenv("LLM_PROVIDER", "").lower() == "gemini":
        from haystack_integrations.components.generators.google_genai import GoogleGenAIChatGenerator

        # Callers use OpenAI's JSON mode; Gemini's equivalent is response_mime_type.
        gen = dict(kwargs.pop("generation_kwargs", None) or {})
        if gen.pop("response_format", {}).get("type") == "json_object":
            gen["response_mime_type"] = "application/json"
        return GoogleGenAIChatGenerator(
            api_key=Secret.from_env_var(["GEMINI_API_KEY", "GOOGLE_API_KEY", "AISTUDIO_API_KEY"]),
            model=os.getenv("GEMINI_MODEL", "gemini-3.8-flash"), generation_kwargs=gen or None, **kwargs
        )
    if os.getenv("AZURE_OPENAI_ENDPOINT"):
        return AzureOpenAIChatGenerator(
            azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
            azure_deployment=os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-5-mini"),
            api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2025-01-01-preview"),
            **kwargs,
        )
    return OpenAIChatGenerator(model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"), **kwargs)
