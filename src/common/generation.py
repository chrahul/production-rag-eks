"""
Answer generation via Bedrock.

This is where the language model finally enters. Phase 0 returned raw chunks.
This turns them into an answer.

Two things about the model choice are worth knowing.

The model ID is an inference profile, not a bare model ID. Newer Bedrock models
require one. A profile routes across regions within a geography for capacity,
which is why the ID is prefixed with a geography rather than a region.

The profile used here is prefixed apac, not global. Global profiles can route a
request anywhere in the world. For a platform whose argument is that
confidential documents stay inside a boundary, that matters more than having the
newest model.
"""

from __future__ import annotations

import json
import os

import boto3

MODEL_ID = os.getenv("BEDROCK_MODEL_ID", "apac.anthropic.claude-3-5-sonnet-20241022-v2:0")
REGION = os.getenv("AWS_REGION", "ap-south-1")
MAX_TOKENS = 800

_client = None


def _bedrock():
    global _client
    if _client is None:
        _client = boto3.client("bedrock-runtime", region_name=REGION)
    return _client


SYSTEM_PROMPT = """You are an assistant for an internal engineering knowledge platform.

Answer the question using only the context provided below. The context has
already been filtered to what this person is permitted to see, so treat it as
the complete set of information available to you.

If the context does not contain enough to answer, say so plainly. Do not fill
gaps from general knowledge. Do not speculate about information that might exist
elsewhere, and never refer to documents you cannot see.

Cite the document title when you use it. Be concise."""


def generate(question: str, chunks: list[dict]) -> str:
    """Turn retrieved chunks into an answer.

    chunks is a list of dicts with 'doc_title' and 'text'.
    """
    if not chunks:
        return (
            "I do not have any information available to answer that question."
        )

    context = "\n\n".join(
        f"[{c['doc_title']}]\n{c['text']}" for c in chunks
    )

    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": MAX_TOKENS,
        "system": SYSTEM_PROMPT,
        "messages": [
            {
                "role": "user",
                "content": f"Context:\n\n{context}\n\nQuestion: {question}",
            }
        ],
    }

    response = _bedrock().invoke_model(modelId=MODEL_ID, body=json.dumps(body))
    payload = json.loads(response["body"].read())
    return payload["content"][0]["text"]
