"""LLM providers for Loan-Saathi: Groq first, Gemini as fallback, our deterministic parser last.

The LLM only extracts quoted answers and phrases replies; it never decides (see converse.py).
"""
from .base import InvalidJSON, LLMError, Provider, parse_json
from .gemini import GeminiProvider
from .groq import GroqProvider
from .router import LLMUnavailable, ResponseCache, Router

__all__ = ["GeminiProvider", "GroqProvider", "InvalidJSON", "LLMError", "LLMUnavailable", "Provider",
           "ResponseCache", "Router", "parse_json"]
