"""Thin wrapper around structured OpenAI completions.

Single integration point with the OpenAI SDK. All structured-LLM calls in the
pipeline route through ``LLMGateway.parse_structured`` so they get the same
default model + seed and so tests can swap the backing client.
"""

import json
from typing import Dict, List, Optional

from openai import OpenAI

from rearrange.src.services.models import MiscRefinementResponse


# Pinned to match Step 1 (`bfs_v4.py`, `classify_v4.py`) — using the unversioned
# alias would let OpenAI swap underlying weights mid-experiment and break the
# determinism we get from `seed=DEFAULT_LLM_SEED`.
DEFAULT_LLM_MODEL = "gpt-5-mini-2025-08-07"
DEFAULT_LLM_SEED = 42


def _llm_parse(
    client: OpenAI,
    model: str,
    system_prompt: str,
    user_payload: object,
    response_model,
    seed: int = DEFAULT_LLM_SEED,
):
    completion = client.beta.chat.completions.parse(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(user_payload, indent=2)},
        ],
        response_format=response_model,
        seed=seed,
    )
    return completion.choices[0].message.parsed


class LLMGateway:
    """Thin wrapper around structured OpenAI completions.

    Single place for ``client.beta.chat.completions.parse`` calls so the pipeline
    has one knob for model selection, retries, and test stubbing.
    """

    def __init__(self, client: Optional[OpenAI] = None) -> None:
        self._client = client or OpenAI()

    @property
    def client(self) -> OpenAI:
        return self._client

    def parse_structured(
        self,
        model: str = DEFAULT_LLM_MODEL,
        system_prompt: str = "",
        user_payload: object = None,
        response_model=None,
        seed: int = DEFAULT_LLM_SEED,
    ):
        return _llm_parse(
            self._client, model, system_prompt, user_payload, response_model, seed=seed
        )

    def refine_miscellaneous_groups(
        self, misc_payload: List[Dict[str, str]], seed: int = DEFAULT_LLM_SEED
    ) -> MiscRefinementResponse:
        completion = self._client.beta.chat.completions.parse(
            model=DEFAULT_LLM_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are organizing course files. These files were originally dumped into a 'Lecture Miscellaneous' folder. "
                        "Your job is to further categorize them into specific logical groups (e.g., 'Review', 'Tutorials', 'Administrivia', 'Project Files', etc.).\n"
                        "Group items by their overarching subject or file type.\n"
                        "Provide a new group name and a brief description for each item.\n"
                        "Don't create more than 5 new groups. If items are very diverse, it's okay to keep some in Miscellaneous, but try to find logical clusters where possible.\n"
                    ),
                },
                {"role": "user", "content": json.dumps(misc_payload, indent=2)},
            ],
            response_format=MiscRefinementResponse,
            seed=seed,
        )
        return completion.choices[0].message.parsed
