"""Fabrication du dossier de candidature a partir d'une offre + du profil.

Deux moteurs interchangeables :
  * `template` : Jinja2, deterministe, fonctionne hors ligne ;
  * `llm`      : Claude redige l'accroche et le corps a partir du meme contexte.

Dans les deux cas la sortie est un dossier de BROUILLONS a relire. Rien n'est
envoye : aucune fonction de ce module n'ouvre de session mail ou de formulaire.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..config import Config, Profile, project_root
from ..models import Application, ApplicationStatus, JobOffer, RemotePolicy
from ..pipeline.enrich import detect_role_family
from .cv import adapter_cv, plan_canva
from .llm import LLMWriter

log = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"

_REMOTE_LABELS = {
    RemotePolicy.FULL_REMOTE: "full remote",
    RemotePolicy.HYBRID: "hybride",
    RemotePolicy.ONSITE: "sur site",
    RemotePolicy.UNKNOWN: "à préciser",
}

_ROLE_ANGLES = {
    "data_engineer": "l'industrialisation de pipelines fiables et testés",
    "analytics_engineer": "la modélisation analytique et la qualité des données exposées",
    "data_analyst": "la mise à disposition d'analyses actionnables pour le métier",
    "bi_engineer": "la refonte de rapports lents en produits BI adoptés",
    "data_scientist": "la mise en production de modèles utiles au métier",
    "data_architect": "la conception d'architectures data soutenables",
    "data_manager": "le cadrage et le pilotage d'un chantier data",
    "data_generic": "la construction de chaînes de données exploitables",
}


def slugify(text: str, max_len: int = 45) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", text.lower()).strip("-")
    return slug[:max_len].strip("-") or "offre"


class ApplicationGenerator:
    def __init__(self, cfg: Config, profile: Profile):
        self.cfg = cfg
        self.profile = profile
        self.env = Environment(
            loader=FileSystemLoader(str(TEMPLATE_DIR)),
            autoescape=select_autoescape(enabled_extensions=("html",)),
            trim_blocks=True,
            lstrip_blocks=True,
        )
        self.writer = LLMWriter(max_words=cfg.application.max_words)

    # ------------------------------------------------------------------ #
    #  Contexte partage entre les deux moteurs
    # ------------------------------------------------------------------ #
    def _proposed_rate(self, offer: JobOffer) -> int:
        """TJM a proposer, deduit de l'annonce et du profil.

        Deux strategies, reglees par `constraints.rate_strategy` :
          * `align` (defaut) : si l'annonce affiche un TJM superieur a l'objectif,
            on s'aligne dessus. Le client a lui-meme fixe ce budget ; proposer
            moins ne rend pas la candidature plus competitive, cela laisse
            simplement de l'argent sur la table.
          * `target` : ne jamais depasser l'objectif du profil.
        En dessous, le plancher declare fait toujours foi.
        """
        target = self.profile.rate_target
        floor = self.profile.rate_floor or int(target * 0.85)
        strategy = str(self.profile.constraints.get("rate_strategy", "align")).lower()

        advertised = offer.daily_rate_max or offer.daily_rate_min
        if not advertised:
            return target
        if strategy == "align" and advertised >= target:
            return advertised
        return max(floor, min(target, advertised))

    def _availability(self) -> str:
        raw = self.profile.constraints.get("available_from")
        if not raw:
            return "sous 2 semaines"
        try:
            when = datetime.fromisoformat(str(raw)).date()
        except ValueError:
            return str(raw)
        today = datetime.now(timezone.utc).date()
        return "immédiatement" if when <= today else f"à partir du {when.strftime('%d/%m/%Y')}"

    def _context(self, offer: JobOffer) -> dict[str, Any]:
        detail = offer.score_detail or {}
        matched = detail.get("_matched_skills") or [
            s for s in offer.skills if s in self.profile.all_skills
        ]
        gaps = detail.get("_missing_skills") or []
        role = detect_role_family(offer.title, offer.description)

        return {
            "offer": offer,
            "profile": self.profile,
            "references": self.profile.references,
            "matched_skills": matched,
            "gaps": gaps,
            "role_family": role,
            "role_angle": _ROLE_ANGLES.get(role, _ROLE_ANGLES["data_generic"]),
            "remote_label": _REMOTE_LABELS.get(offer.remote, "à préciser"),
            "proposed_rate": self._proposed_rate(offer),
            "availability": self._availability(),
        }

    # ------------------------------------------------------------------ #
    #  Moteur "template"
    # ------------------------------------------------------------------ #
    def _template_hook(self, ctx: dict[str, Any]) -> str:
        offer, matched = ctx["offer"], ctx["matched_skills"]
        company = offer.company or "votre client"
        pitch = " ".join(str(self.profile.positioning.get("pitch", "")).split())
        stack = ", ".join(matched[:4]) if matched else "votre stack data"
        # Le pitch du profil mentionne deja l'anciennete : on ne la repete pas.
        return (
            f'Votre mission "{offer.title}" chez {company} porte sur '
            f"{ctx['role_angle']}, avec {stack} au centre du dispositif. {pitch}"
        )

    def _template_closing(self, ctx: dict[str, Any]) -> str:
        gaps = ctx["gaps"]
        if gaps:
            return (
                f"Sur {', '.join(gaps[:2])}, je n'ai pas la profondeur d'un spécialiste, "
                "mais l'environnement m'est familier et la montée en compétence est "
                "rapide sur ce type d'outillage."
            )
        return (
            "Je peux démarrer le cadrage dès la première semaine et livrer un premier "
            "incrément utile sous quinze jours."
        )

    def _render_templates(self, ctx: dict[str, Any]) -> dict[str, Any]:
        offer = ctx["offer"]
        ctx = {**ctx, "hook": self._template_hook(ctx), "closing": self._template_closing(ctx)}
        subject = f"Candidature freelance — {offer.title}"
        if offer.company:
            subject += f" ({offer.company})"
        ctx["subject"] = subject

        return {
            "subject": subject,
            "cover_letter": self.env.get_template("cover_letter_fr.md.j2").render(**ctx),
            "email_body": self.env.get_template("email_fr.md.j2").render(**ctx),
            "highlights": ctx["matched_skills"][:5],
            "gaps": ctx["gaps"][:5],
            "generator": "template",
        }

    # ------------------------------------------------------------------ #
    #  Moteur "llm"
    # ------------------------------------------------------------------ #
    def _build_prompt(self, ctx: dict[str, Any]) -> str:
        offer: JobOffer = ctx["offer"]
        refs = "\n".join(
            f"- {r.get('client')} ({r.get('period')}, {', '.join(r.get('stack', []))}) : "
            f"{str(r.get('achievement', '')).strip()}"
            for r in self.profile.references
        ) or "- (aucune reference renseignee dans le profil)"

        return f"""Redige une candidature freelance pour la mission ci-dessous.

## Annonce
Titre : {offer.title}
Entreprise : {offer.company or "non precisee"}
Lieu : {offer.location or "non precise"} ({ctx['remote_label']})
TJM affiche : {offer.daily_rate or "non precise"}
Duree : {offer.duration_months or "non precisee"} mois
Source : {offer.source} — {offer.url}

Description (extrait) :
\"\"\"
{offer.description[:4000]}
\"\"\"

## Profil du candidat
Nom : {self.profile.name}
Titre : {self.profile.identity.get('title', '')}
Positionnement : {str(self.profile.positioning.get('pitch', '')).strip()}
Competences expertes : {', '.join(self.profile.skills.get('expert', []))}
Competences avancees : {', '.join(self.profile.skills.get('advanced', []))}
Competences connues : {', '.join(self.profile.skills.get('familiar', []))}

References verifiables (seule matiere autorisee pour les preuves) :
{refs}

## Elements imposes
- Recoupement identifie : {', '.join(ctx['matched_skills']) or "aucun explicite"}
- Ecarts identifies : {', '.join(ctx['gaps']) or "aucun"}
- Angle a privilegier : {ctx['role_angle']}
- TJM a annoncer : {ctx['proposed_rate']} EUR HT/jour
- Disponibilite : {ctx['availability']}
- Longueur maximale du corps : {self.cfg.application.max_words} mots
- Ton : {self.cfg.application.tone}

Rends le JSON demande, en francais."""

    def _render_llm(self, ctx: dict[str, Any]) -> dict[str, Any] | None:
        data = self.writer.write(self._build_prompt(ctx))
        if not data:
            return None

        offer = ctx["offer"]
        header_ctx = {**ctx, "hook": data.get("hook", ""), "closing": ""}
        header = self.env.get_template("cover_letter_fr.md.j2").render(
            **{**header_ctx, "subject": data.get("subject", "")}
        )
        # On conserve l'en-tete factuel du template (coordonnees, TJM, lien) et
        # on remplace le corps redactionnel par la version du modele.
        head = header.split("Bonjour,")[0]
        cover = (
            f"{head}Bonjour,\n\n{data.get('hook', '')}\n\n{data.get('body', '')}\n\n"
            f"Bien cordialement,\n{self.profile.name}\n\n---\n\n"
            f"> **Score de matching :** {offer.score:.0f}/100  \n"
            f"> **Points à anticiper :** {', '.join(data.get('gaps', [])) or 'aucun'}  \n"
            f"> **Redige par :** {self.writer.model} — à relire avant envoi."
        )
        return {
            "subject": data.get("subject") or f"Candidature freelance — {offer.title}",
            "cover_letter": cover,
            "email_body": data.get("email_body", ""),
            "highlights": data.get("highlights", [])[:5],
            "gaps": data.get("gaps", [])[:5],
            "generator": "llm",
        }

    # ------------------------------------------------------------------ #
    #  Point d'entree
    # ------------------------------------------------------------------ #
    def generate(self, offer: JobOffer, *, force_template: bool = False) -> Application:
        ctx = self._context(offer)

        rendered = None
        if self.cfg.application.use_llm and not force_template:
            rendered = self._render_llm(ctx)
        if rendered is None:
            rendered = self._render_templates(ctx)

        folder = self._write_files(offer, ctx, rendered)

        return Application(
            offer_id=offer.id,
            status=ApplicationStatus.DRAFTED,
            subject=rendered["subject"],
            cover_letter=rendered["cover_letter"],
            email_body=rendered["email_body"],
            highlights=rendered["highlights"],
            gaps=rendered["gaps"],
            proposed_rate=ctx["proposed_rate"],
            generator=rendered["generator"],
            file_path=str(folder),
        )

    def _write_files(self, offer: JobOffer, ctx: dict[str, Any],
                     rendered: dict[str, Any]) -> Path:
        """Ecrit le dossier de candidature. Rend le chemin du dossier."""
        root = self.cfg.applications_path
        name = (f"{int(offer.score):03d}-{slugify(offer.company or offer.source)}"
                f"-{slugify(offer.title)}")
        folder = root / name
        folder.mkdir(parents=True, exist_ok=True)

        (folder / "lettre.md").write_text(rendered["cover_letter"], encoding="utf-8")
        # Le template n'ecrit pas l'objet : il est ajoute ici, une seule fois.
        (folder / "email.md").write_text(
            f"Objet : {rendered['subject']}\n\n"
            f"{rendered['email_body'].strip()}\n",
            encoding="utf-8",
        )
        (folder / "offre.md").write_text(self._offer_sheet(offer), encoding="utf-8")
        (folder / "checklist.md").write_text(self._checklist(offer, ctx, rendered),
                                             encoding="utf-8")
        self._ecrire_cv_adapte(folder, offer)
        (folder / "offre.json").write_text(
            json.dumps(offer.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return folder

    def _ecrire_cv_adapte(self, folder: Path, offer: JobOffer) -> None:
        """Ecrit l'adaptation du CV : une note lisible et un plan applicable.

        Le plan Canva n'est produit que si le profil renseigne `documents.canva_cv`.
        Rien n'est envoye a Canva ici : ce module ne fait que calculer.
        """
        adaptation = adapter_cv(offer, self.profile)
        lignes_md = [
            f"# CV adapté — {offer.title}",
            "",
            "## Paragraphe de profil",
            "",
            adaptation.profil.texte(),
            "",
            "## Ordre des rubriques de compétences",
            "",
        ]
        for i, r in enumerate(adaptation.rubriques, 1):
            marque = " *(position fixe)*" if r.epinglee else ""
            lignes_md.append(f"{i}. **{r.label}**{marque} — {r.texte()}")
        lignes_md += [
            "",
            "---",
            "",
            "Seuls l'ordre des rubriques et le paragraphe de profil changent.",
            "Les expériences, les chiffres et les dates sont laissés intacts.",
        ]
        (folder / "cv-adapte.md").write_text(
            "\n".join(lignes_md) + "\n", encoding="utf-8"
        )

        plan = plan_canva(offer, self.profile, adaptation)
        if plan:
            (folder / "cv-canva.json").write_text(
                json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8"
            )

    @staticmethod
    def _offer_sheet(offer: JobOffer) -> str:
        return "\n".join([
            f"# {offer.title}",
            "",
            f"- **Entreprise :** {offer.company or 'non précisée'}",
            f"- **Source :** {offer.source}",
            f"- **Lien :** {offer.url}",
            f"- **Lieu :** {offer.location or 'non précisé'}",
            f"- **Télétravail :** {offer.remote.value}",
            f"- **Contrat :** {offer.contract.value}",
            f"- **TJM :** {offer.daily_rate or 'non précisé'}",
            f"- **Durée :** {offer.duration_months or 'non précisée'} mois",
            f"- **Publiée le :** {offer.published_at.date() if offer.published_at else 'inconnu'}",
            f"- **Compétences détectées :** {', '.join(offer.skills) or 'aucune'}",
            f"- **Score :** {offer.score:.0f}/100",
            "",
            "---",
            "",
            "## Annonce",
            "",
            offer.description or "(description vide)",
        ])

    def _checklist(self, offer: JobOffer, ctx: dict[str, Any],
                   rendered: dict[str, Any]) -> str:
        docs = self.profile.documents or {}
        cv = docs.get("cv_pdf") or "(cv_pdf non renseigne dans profile.yaml)"
        cv_path = project_root() / cv if cv and not str(cv).startswith("(") else None
        cv_state = ("présent" if cv_path and cv_path.exists()
                    else "ABSENT — à ajouter avant envoi")
        gaps = rendered["gaps"]
        return "\n".join([
            f"# Checklist avant envoi — {offer.title}",
            "",
            "## À vérifier",
            "- [ ] Nom de l'interlocuteur et de l'entreprise corrects",
            "- [ ] Intitulé exact de la mission repris tel quel",
            f"- [ ] TJM annoncé cohérent : **{ctx['proposed_rate']} EUR HT/j**"
            + (f" (offre : {offer.daily_rate} EUR)" if offer.daily_rate
               else " (offre : non précisé)"),
            f"- [ ] Disponibilité exacte : **{ctx['availability']}**",
            f"- [ ] CV joint — `{cv}` : {cv_state}",
            "- [ ] Relecture orthographe et longueur",
            "- [ ] Aucune affirmation non vérifiable dans la lettre",
            "",
            "## Points à préparer pour l'entretien",
            *([f"- {g}" for g in gaps] or ["- (aucun écart identifié)"]),
            "",
            "## Envoi",
            f"- Lien de candidature : {offer.url}",
            "- L'envoi est manuel : cet outil ne soumet aucun formulaire et n'envoie aucun mail.",
            "",
            f"Une fois envoyée : `radar track {offer.id} --status sent`",
        ])
