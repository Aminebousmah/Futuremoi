"""France Travail (ex-Pole Emploi) : API Offres d'emploi v2.

Inscription : https://francetravail.io -> creer une application, s'abonner a
"Offres d'emploi v2", puis renseigner FRANCE_TRAVAIL_CLIENT_ID / _SECRET dans .env.

Interet de cette source : c'est la seule qui couvre la France entiere, bien
au-dela de l'Ile-de-France. En contrepartie, le volume freelance y est faible
(l'essentiel du catalogue est salarie), d'ou le filtre `type_contrat` par defaut
sur LIB (profession liberale).

Limites de l'API respectees ici :
  - 4 requetes/seconde par application (notre delai par defaut est bien au-dessus) ;
  - `range` plafonne a 150 resultats par appel ;
  - `publieeDepuis` n'accepte que les valeurs 1, 3, 7, 14 et 31.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator

from ..config import env
from ..models import ContractType, JobOffer
from ..pipeline.normalize import parse_datetime, strip_html
from .base import BaseScraper, register

log = logging.getLogger(__name__)

TOKEN_URL = "https://entreprise.francetravail.fr/connexion/oauth2/access_token"
SEARCH_URL = "https://api.francetravail.io/partenaire/offresdemploi/v2/offres/search"
DEFAULT_SCOPE = "api_offresdemploiv2 o2dsoffre"

# Seules valeurs acceptees par le parametre `publieeDepuis` : toute autre
# valeur (30, par exemple) fait echouer la requete en 400.
PUBLIEE_DEPUIS_ALLOWED = (1, 3, 7, 14, 31)

PAGE_SIZE = 50
MAX_RESULTS = 150

# Codes `typeContrat` du referentiel France Travail.
# MIS = mission d'interim : c'est du salariat temporaire, pas du freelance.
_CONTRACT_MAP = {
    "LIB": ContractType.FREELANCE,   # profession liberale
    "CCE": ContractType.FREELANCE,   # profession commerciale (independant)
    "FRA": ContractType.FREELANCE,   # franchise
    "REP": ContractType.FREELANCE,   # reprise d'entreprise
    "CDI": ContractType.CDI,
    "DDI": ContractType.CDI,         # CDI interimaire
    "DIN": ContractType.CDI,         # CDI intermittent
    "CDD": ContractType.CDD,
    "MIS": ContractType.CDD,
    "SAI": ContractType.CDD,         # saisonnier
    "TTI": ContractType.CDD,
}


def snap_publiee_depuis(max_age_days: int | None) -> int:
    """Ramene une anciennete quelconque a la valeur autorisee la plus proche.

    On arrondit vers le haut pour ne pas retrecir la fenetre demandee : un
    `max_age_days` de 30 devient 31, pas 14.
    """
    if not max_age_days or max_age_days <= 0:
        return PUBLIEE_DEPUIS_ALLOWED[-1]
    for allowed in PUBLIEE_DEPUIS_ALLOWED:
        if max_age_days <= allowed:
            return allowed
    return PUBLIEE_DEPUIS_ALLOWED[-1]


@register
class FranceTravailScraper(BaseScraper):
    name = "francetravail"
    label = "France Travail (API v2)"
    homepage = "https://candidat.francetravail.fr"
    # api.francetravail.io sert un `Disallow: /` global : il vise les
    # crawlers, pas les clients API a qui le portail delivre des cles.
    respects_robots = False

    def is_configured(self) -> bool:
        return bool(env("FRANCE_TRAVAIL_CLIENT_ID") and env("FRANCE_TRAVAIL_CLIENT_SECRET"))

    def missing_requirement(self) -> str:
        return "FRANCE_TRAVAIL_CLIENT_ID / _SECRET absents du .env"

    # ------------------------------------------------------------------ #
    #  Authentification
    # ------------------------------------------------------------------ #
    def _token(self) -> str:
        scope = self._cfg("scope") or DEFAULT_SCOPE
        try:
            payload = self.client.post_form(
                f"{TOKEN_URL}?realm=%2Fpartenaire",
                data={
                    "grant_type": "client_credentials",
                    "client_id": env("FRANCE_TRAVAIL_CLIENT_ID"),
                    "client_secret": env("FRANCE_TRAVAIL_CLIENT_SECRET"),
                    "scope": scope,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        except Exception as exc:
            # `invalid_client` ne veut pas dire "mauvais secret" : c'est le plus
            # souvent une application non abonnee a l'API cote portail.
            if "invalid_client" in str(exc):
                raise RuntimeError(
                    "France Travail refuse l'application (invalid_client). Verifiez sur "
                    "francetravail.io que votre application est bien ABONNEE a "
                    "'Offres d'emploi v2', puis que le client_id/secret est le bon."
                ) from exc
            raise
        return payload["access_token"]

    # ------------------------------------------------------------------ #
    #  Recherche
    # ------------------------------------------------------------------ #
    def fetch(self, keywords: list[str]) -> Iterator[JobOffer]:
        token = self._token()
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

        # `motsCles` combine les termes par un ET, pas par un OU : envoyer
        # "data,donnees" ne rend rien du tout. On interroge donc un terme a la
        # fois et la deduplication du pipeline fusionne les resultats.
        requetes = self.queries()
        base_params: dict[str, object] = {
            "publieeDepuis": snap_publiee_depuis(self.cfg.filters.max_age_days),
        }
        # Par defaut LIB : le catalogue France Travail est surtout salarie, et
        # sans ce filtre la source noie les resultats sous des CDI que le
        # pipeline rejettera de toute facon. Mettre `type_contrat: ""` pour tout voir.
        type_contrat = self._cfg("type_contrat", "LIB")
        if type_contrat:
            base_params["typeContrat"] = type_contrat
        departement = self._cfg("departement")
        if departement:
            base_params["departement"] = departement

        max_results = min(int(self._cfg("max_results", MAX_RESULTS)), MAX_RESULTS)
        for requete in requetes:
            for start in range(0, max_results, PAGE_SIZE):
                end = min(start + PAGE_SIZE - 1, max_results - 1)
                results = self._search(headers, {
                    **base_params, "motsCles": requete, "range": f"{start}-{end}",
                })
                if not results:
                    break
                for raw in results:
                    offer = self._parse(raw)
                    if offer:
                        yield offer

    def _search(self, headers: dict[str, str], params: dict[str, object]) -> list[dict]:
        """Un appel de recherche. Rend [] pour signaler la fin de la pagination."""
        try:
            body = self.get(SEARCH_URL, params=params, headers=headers)
        except Exception as exc:
            # L'API rejette en 400 un `publieeDepuis` hors de sa liste fermee.
            # Plutot que d'echouer, on retente sans le filtre d'anciennete :
            # le pipeline appliquera le sien de toute facon.
            if "400" in str(exc) and "publieeDepuis" in params:
                log.warning("France Travail : publieeDepuis refuse, nouvelle tentative sans.")
                retry = {k: v for k, v in params.items() if k != "publieeDepuis"}
                try:
                    body = self.get(SEARCH_URL, params=retry, headers=headers)
                except Exception as retry_exc:
                    log.warning("France Travail : recherche en echec (%s)", retry_exc)
                    return []
            else:
                log.warning("France Travail : recherche en echec (%s)", exc)
                return []

        # 204 No Content en fin de pagination : corps vide, pas du JSON.
        if not body.strip():
            return []
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            log.warning("France Travail : reponse illisible, arret de la pagination.")
            return []
        return payload.get("resultats", []) if isinstance(payload, dict) else []

    # ------------------------------------------------------------------ #
    #  Conversion
    # ------------------------------------------------------------------ #
    def _parse(self, raw: dict) -> JobOffer | None:
        title = raw.get("intitule")
        if not title:
            return None

        lieu = raw.get("lieuTravail") or {}
        entreprise = raw.get("entreprise") or {}
        origine = raw.get("origineOffre") or {}

        if raw.get("alternance") is True:
            contract = ContractType.ALTERNANCE
        else:
            contract = _CONTRACT_MAP.get(
                str(raw.get("typeContrat", "")).upper(), ContractType.UNKNOWN
            )

        # L'API expose une URL partenaire quand l'offre vient d'un autre site ;
        # a defaut on reconstruit le lien vers la fiche France Travail.
        url = origine.get("urlOrigine") or (
            f"https://candidat.francetravail.fr/offres/recherche/detail/{raw.get('id', '')}"
        )

        # Les libelles (contrat, experience, duree) sont annexes a la description :
        # ils alimentent le parsing de duree et de contrat du pipeline, qui ne lit
        # que le texte. Les mettre dans `raw_html` serait inutile : ce champ n'est
        # exploite que lorsque la description est vide.
        contexte = " · ".join(
            str(raw[k]) for k in
            ("typeContratLibelle", "experienceLibelle", "qualificationLibelle",
             "dureeTravailLibelleConverti")
            if raw.get(k)
        )
        description = strip_html(raw.get("description", ""))
        if contexte:
            description = f"{description}\n\n{contexte}".strip()

        return JobOffer(
            source=self.name,
            source_id=str(raw.get("id", "")),
            url=url,
            title=strip_html(title),
            company=entreprise.get("nom", ""),
            description=description,
            location=lieu.get("libelle", ""),
            contract=contract,
            published_at=parse_datetime(raw.get("dateCreation")),
            skills=[c.get("libelle", "") for c in (raw.get("competences") or [])
                    if c.get("libelle")][:25],
        )
