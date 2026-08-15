"""
Embeddings via Amazon Bedrock.

Titan Text Embeddings v2. Credentials come from the environment, which on EC2
means the instance role and in Phase 3 will mean IRSA. Either way no static
keys ever appear in this code.
"""

import json
import os

import boto3

MODEL_ID = "amazon.titan-embed-text-v2:0"
DIMENSIONS = 1024
REGION = os.getenv("AWS_REGION", "us-east-1")

_client = None


def _bedrock():
    global _client
    if _client is None:
        _client = boto3.client("bedrock-runtime", region_name=REGION)
    return _client


def embed(text: str) -> list[float]:
    """Embed a single piece of text."""
    response = _bedrock().invoke_model(
        modelId=MODEL_ID,
        body=json.dumps({"inputText": text, "dimensions": DIMENSIONS}),
    )
    return json.loads(response["body"].read())["embedding"]


def embed_many(texts: list[str]) -> list[list[float]]:
    """Titan has no batch endpoint, so this is a loop. Fine at lab scale."""
    return [embed(t) for t in texts]
