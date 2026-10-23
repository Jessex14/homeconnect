import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .providers import ProviderUnavailable, RentAnswerProvider


class OpenAIRentAnswerProvider(RentAnswerProvider):
    """OpenAI Responses API adapter; inactive unless selected and keyed server-side."""

    endpoint = "https://api.openai.com/v1/responses"

    def answer(self, *, question, context, instructions):
        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise ProviderUnavailable("Rent Q&A needs an OpenAI API key configured on the server.")

        model = os.environ.get("OPENAI_MODEL", "gpt-6-luna").strip()
        try:
            timeout = min(max(int(os.environ.get("OPENAI_TIMEOUT_SECONDS", "20")), 3), 30)
        except ValueError as exc:
            raise ProviderUnavailable("The OpenAI timeout configuration is invalid.") from exc

        payload = {
            "model": model,
            "store": False,
            "max_output_tokens": 600,
            "reasoning": {"effort": "none"},
            "instructions": (
                instructions
                + " Return JSON with exactly two keys: answer (string) and source_ids (array of supplied record IDs)."
            ),
            "input": json.dumps({"question": question, "context": context}, ensure_ascii=False, separators=(",", ":")),
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "rent_answer",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {
                            "answer": {"type": "string"},
                            "source_ids": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["answer", "source_ids"],
                        "additionalProperties": False,
                    },
                },
            },
        }
        request = Request(
            self.endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                api_result = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            exc.close()
            raise ProviderUnavailable("OpenAI could not answer this rent question.") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise ProviderUnavailable("Rent Q&A is temporarily unavailable.") from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProviderUnavailable("OpenAI returned an unreadable response.") from exc

        text_blocks = []
        for item in api_result.get("output", []):
            if item.get("type") != "message":
                continue
            for block in item.get("content", []):
                if block.get("type") == "refusal":
                    raise ProviderUnavailable("The assistant could not answer that question.")
                if block.get("type") == "output_text" and isinstance(block.get("text"), str):
                    text_blocks.append(block["text"])
        if not text_blocks:
            raise ProviderUnavailable("OpenAI returned no answer text.")
        try:
            result = json.loads("".join(text_blocks))
        except json.JSONDecodeError as exc:
            raise ProviderUnavailable("OpenAI returned an invalid answer format.") from exc
        if not isinstance(result, dict) or set(result) != {"answer", "source_ids"}:
            raise ProviderUnavailable("OpenAI returned an invalid answer format.")
        return result

