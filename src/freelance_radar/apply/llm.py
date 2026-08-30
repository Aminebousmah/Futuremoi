"""Redaction assistee par Claude (optionnelle).

Sans ANTHROPIC_API_KEY, `LLMWriter.available()` rend False et le generateur
bascule sur les templates Jinja2 : l'outil reste pleinement fonctionnel hors
ligne. Le SDK `anthropic` est une dependance optionnelle (extra `llm`).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from ..config import env

log = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-opus-5"

# Schema de sortie : garantit un JSON exploitable sans post-traitement fragile.
RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "subject": {
            "type": "string",
            "description": "Objet d'email, moins de 80 caracteres, sans formule creuse.",
        },
        "hook": {
            "type": "string",
            "description": "Paragraphe d'accroche (2-4 phrases) reliant le besoin au parcours.",
        },
        "body": {
            "type": "string",
            "description": "Corps de la lettre en Markdown, preuves chiffrees en priorite.",
        },
        "email_body": {
            "type": "string",
            "description": "Version email courte (moins de 150 mots), ton direct.",
        },
        "highlights": {
            "type": "array",
            "items": {"type": "string"},
            "description": "3 a 5 arguments retenus, formules pour etre relus vite.",
        },
        "gaps": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Competences demandees et absentes du profil, a preparer.",
        },
    },
    "required": ["subject", "hook", "body", "email_body", "highlights", "gaps"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """Tu rediges des candidatures de freelance pour des missions Data.

Regles de redaction :
- Ecris en francais, a la premiere personne, ton professionnel et direct.
- Appuie chaque affirmation sur une reference reelle du profil fourni. N'invente
  jamais une experience, un client, un chiffre ou une certification.
- Si une competence demandee manque au profil, ne la revendique pas : liste-la
  dans `gaps`.
- Pas de superlatifs creux ("passionne", "dynamique", "leader"), pas de
  paraphrase de l'annonce : montre la comprehension du besoin en une phrase,
  puis la preuve.
- Reste sous la limite de mots demandee.
"""


class LLMWriter:
    """Enveloppe minimale autour du SDK Anthropic."""

    def __init__(self, model: str | None = None, max_words: int = 320):
        self.model = model or env("ANTHROPIC_MODEL", DEFAULT_MODEL)
        self.max_words = max_words
        self._client = None

    # ------------------------------------------------------------------ #
    def available(self) -> bool:
        if not env("ANTHROPIC_API_KEY"):
            return False
        try:
            import anthropic  # noqa: F401
        except ImportError:
            log.info("SDK anthropic absent : `pip install anthropic` pour activer le LLM.")
            return False
        return True

    def _get_client(self):
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic()
        return self._client

    # ------------------------------------------------------------------ #
    def write(self, prompt: str) -> dict[str, Any] | None:
        """Rend le dict conforme a RESPONSE_SCHEMA, ou None si l'appel echoue."""
        if not self.available():
            return None

        client = self._get_client()
        params: dict[str, Any] = {
            "model": self.model,
            # Sortie volontairement courte : une lettre, pas un rapport.
            "max_tokens": 8000,
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": prompt}],
            "thinking": {"type": "adaptive"},
            "output_config": {"format": {"type": "json_schema", "schema": RESPONSE_SCHEMA}},
        }

        try:
            response = self._create(client, params)
        except Exception as exc:
            log.warning("Appel LLM en echec (%s) : repli sur les templates.", exc)
            return None

        if getattr(response, "stop_reason", None) == "refusal":
            log.warning("Le modele a refuse de repondre : repli sur les templates.")
            return None

        text = next((b.text for b in response.content if getattr(b, "type", "") == "text"), "")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            log.warning("Reponse LLM non exploitable : repli sur les templates.")
            return None

    @staticmethod
    def _create(client, params: dict[str, Any]):
        """Appel avec repli automatique du chemin beta vers le chemin standard.

        Le repli serveur (`fallbacks="default"`) evite qu'un refus de classifieur
        ne bloque la generation ; il n'existe que sur l'endpoint beta et selon la
        version du SDK, d'ou le repli sur `messages.create` en cas de rejet.
        """
        try:
            return client.beta.messages.create(
                **params,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except TypeError:
            pass
        except Exception as exc:
            if "beta" not in str(exc).lower() and "fallback" not in str(exc).lower():
                raise
        return client.messages.create(**params)
