"""Redaction assistee par Claude (optionnelle, et jamais implicite).

Deux verrous, dans cet ordre :

1. **Consentement explicite.** Presenter une cle API n'autorise rien. Aucune
   requete n'est emise si `consent=False` : c'est le defaut, et il faut un
   geste delibere pour le lever (`radar apply --llm`, ou la double validation
   de l'interface web). Une cle presente dans `.env` sert a beaucoup de
   choses ; elle ne doit jamais suffire a declencher une facturation.
2. **Modele economique.** Le defaut est Haiku, pas Opus : la redaction d'une
   lettre ne justifie pas le modele le plus cher. `ANTHROPIC_MODEL` permet de
   changer d'avis ponctuellement.

Sans consentement -- ou sans cle, ou sans le SDK -- le generateur bascule sur
les templates Jinja2 : l'outil reste pleinement fonctionnel hors ligne. Le SDK
`anthropic` est une dependance optionnelle (extra `llm`).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from ..config import env

log = logging.getLogger(__name__)

# Haiku : une lettre de motivation est une tache de redaction courte et
# cadree par un schema. Opus coute environ vingt fois plus pour un gain nul
# ici. Surchargeable par ANTHROPIC_MODEL si un cas le justifie.
DEFAULT_MODEL = "claude-haiku-4-5-20251001"

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

    def __init__(self, model: str | None = None, max_words: int = 320,
                 consent: bool = False):
        self.model = model or env("ANTHROPIC_MODEL", DEFAULT_MODEL)
        self.max_words = max_words
        # Doit etre pose a chaque appel par l'appelant qui a recueilli l'accord.
        # Aucune valeur de configuration ne peut le mettre a True durablement :
        # c'est ce qui distingue un consentement d'un reglage oublie.
        self.consent = consent
        self._client = None

    # ------------------------------------------------------------------ #
    def available(self) -> bool:
        """Vrai seulement si l'appel est autorise ici et maintenant."""
        if not self.consent:
            return False
        if not env("ANTHROPIC_API_KEY"):
            return False
        try:
            import anthropic  # noqa: F401
        except ImportError:
            log.info("SDK anthropic absent : `pip install anthropic` pour activer le LLM.")
            return False
        return True

    def blocked_reason(self) -> str | None:
        """Explique pourquoi le LLM ne sera pas utilise, pour l'afficher a l'ecran."""
        if not self.consent:
            return "appel non autorise (aucun consentement pour cette generation)"
        if not env("ANTHROPIC_API_KEY"):
            return "ANTHROPIC_API_KEY absente de .env"
        try:
            import anthropic  # noqa: F401
        except ImportError:
            return "SDK anthropic non installe (pip install anthropic)"
        return None

    def _get_client(self):
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic()
        return self._client

    # ------------------------------------------------------------------ #
    def write(self, prompt: str) -> dict[str, Any] | None:
        """Rend le dict conforme a RESPONSE_SCHEMA, ou None si l'appel echoue."""
        # Double verrou : `available()` couvre deja le consentement, mais le
        # test est reecrit ici pour qu'aucune refonte de `available()` ne
        # puisse ouvrir un chemin d'appel par inadvertance.
        if not self.consent:
            log.info("Appel LLM refuse : consentement non donne.")
            return None
        if not self.available():
            return None

        log.warning("Appel facture a l'API Anthropic (modele %s), avec accord explicite.",
                    self.model)
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
