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

from ..config import Config, Profile
from ..models import Application, ApplicationStatus, JobOffer, RemotePolicy
from ..pipeline.enrich import detect_role_family
from .candidature import Fiche, construire_fiche
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
        # consent reste a False : il est pose generation par generation.
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
    def generate(self, offer: JobOffer, *, force_template: bool = False,
                 consent_llm: bool = False,
                 imposees: list[str] | None = None) -> Application:
        """Produit le dossier de candidature.

        `consent_llm` est le seul chemin vers une requete facturee. Il n'a pas
        de valeur par defaut heritee de la configuration : `use_llm` autorise
        le moteur, `consent_llm` autorise l'appel. Les deux sont necessaires.
        """
        ctx = self._context(offer)

        rendered = None
        self.writer.consent = consent_llm
        if self.cfg.application.use_llm and consent_llm and not force_template:
            rendered = self._render_llm(ctx)
        if rendered is None:
            rendered = self._render_templates(ctx)

        folder = self._write_files(offer, ctx, rendered, imposees)

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

    def fiche_candidature(self, offer: JobOffer) -> Fiche:
        """Reponses prêtes a coller dans un formulaire d'employeur.

        Reutilise le meme contexte que la lettre : accroche et message ne sont
        pas reecrits ici, ce qui garantit qu'une candidature dit partout la
        meme chose.
        """
        ctx = self._context(offer)
        rendu = self._render_templates(ctx)
        message = " ".join(
            ligne for ligne in rendu["email_body"].splitlines()
            if ligne.strip() and not ligne.startswith(("-", "Bien cordialement"))
        ).strip()
        return construire_fiche(
            offer, self.profile,
            tjm_propose=ctx["proposed_rate"],
            disponibilite=ctx["availability"],
            accroche=self._template_hook(ctx),
            message=message,
        )

    @staticmethod
    def _dossier(offer: JobOffer) -> str:
        """Nom du dossier de candidature. Un seul endroit le decide."""
        return (f"{int(offer.score):03d}-{slugify(offer.company or offer.source)}"
                f"-{slugify(offer.title)}")

    def _write_files(self, offer: JobOffer, ctx: dict[str, Any],
                     rendered: dict[str, Any],
                     imposees: list[str] | None = None) -> Path:
        """Ecrit le dossier de candidature. Rend le chemin du dossier."""
        folder = self.cfg.applications_path / self._dossier(offer)
        folder.mkdir(parents=True, exist_ok=True)

        (folder / "lettre.md").write_text(rendered["cover_letter"], encoding="utf-8")
        # Le template n'ecrit pas l'objet : il est ajoute ici, une seule fois.
        (folder / "email.md").write_text(
            f"Objet : {rendered['subject']}\n\n"
            f"{rendered['email_body'].strip()}\n",
            encoding="utf-8",
        )
        (folder / "offre.md").write_text(self._offer_sheet(offer), encoding="utf-8")
        # Le CV avant la checklist : celle-ci verifie sa presence, et l'annoncer
        # absent alors qu'il vient d'etre ecrit ferait douter du reste.
        self._ecrire_cv_adapte(folder, offer, imposees)
        (folder / "checklist.md").write_text(self._checklist(offer, ctx, rendered),
                                             encoding="utf-8")
        (folder / "offre.json").write_text(
            json.dumps(offer.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return folder

    def _ecrire_cv_adapte(self, folder: Path, offer: JobOffer,
                          imposees: list[str] | None = None) -> None:
        """Ecrit l'adaptation du CV : une note lisible et un plan applicable.

        Le plan Canva n'est produit que si le profil renseigne `documents.canva_cv`.
        Rien n'est envoye a Canva ici : ce module ne fait que calculer.
        """
        adaptation = adapter_cv(offer, self.profile, imposees)
        lignes_md = [
            f"# CV adapté — {offer.title}",
            "",
            "## Paragraphe de profil",
            "",
            adaptation.profil.texte(),
            "",
            "## Compétences, composées pour cette offre",
            "",
        ]
        for i, r in enumerate(adaptation.rubriques, 1):
            marque = " *(position fixe)*" if r.epinglee else ""
            lignes_md.append(f"{i}. **{r.label}**{marque} — {r.texte()}")
        from .parcours import composer_experiences, composer_projets, resume_adaptation

        experiences = composer_experiences(offer, self.profile, imposees)
        projets = composer_projets(offer, self.profile, imposees)
        mouvements = resume_adaptation(experiences, projets)
        if mouvements:
            lignes_md += ["", "## Parcours : ce qui a été mis en avant", ""]
            lignes_md += mouvements
            lignes_md += [
                "",
                "Les puces sont **réordonnées**, jamais réécrites. Une compétence",
                "exigée que votre parcours ne porte pas reste un écart : la fiche",
                "d'entretien la nomme au lieu de la maquiller.",
            ]

        lignes_md += [
            "",
            "---",
            "",
            "Les rubriques ci-dessus sont **composées à partir de cette offre** :",
            "leur intitulé, leur contenu et leur ordre changent d'une mission à",
            "l'autre. Les outils proviennent uniquement de votre inventaire",
            "(`config/profile.yaml`, section `cv.outils`) — rien n'est inventé.",
            "",
            "Vos expériences, vos chiffres et vos dates ne sont jamais touchés.",
        ]
        (folder / "cv-adapte.md").write_text(
            "\n".join(lignes_md) + "\n", encoding="utf-8"
        )

        plan = plan_canva(offer, self.profile, adaptation)
        if plan:
            (folder / "cv-canva.json").write_text(
                json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8"
            )

        # Le PDF est le livrable ; le .md reste la note de relecture. Une
        # police manquante ne doit pas faire echouer toute la generation :
        # les autres pieces du dossier sont deja ecrites.
        try:
            from .pdf import generer_cv

            generer_cv(offer, self.profile, folder / "cv.pdf",
                       imposees=imposees)
        except Exception as exc:
            log.warning("CV PDF non genere (%s) : la note cv-adapte.md reste "
                        "disponible.", exc)

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
        gaps = rendered["gaps"]

        # Le CV est genere dans ce meme dossier : la checklist pointe dessus,
        # plus vers le PDF Canva d'origine ni vers un collage manuel.
        cv_genere = (self.cfg.applications_path / self._dossier(offer)
                     / "cv.pdf").exists()
        etat_cv = ("`cv.pdf` de ce dossier" if cv_genere
                   else "ABSENT — relancer `radar apply`, ou joindre votre CV a la main")

        # Un demarrage immediat et une disponibilite lointaine se voient tout
        # de suite cote client : autant le savoir avant d'ecrire.
        texte = f"{offer.title} {offer.description}".lower()
        urgent = any(m in texte for m in ("asap", "des que possible",
                                          "immediat", "immédiat", "urgent"))
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
            f"- [ ] CV joint : {etat_cv}",
            "- [ ] Lieu réel de la mission : l'annonce peut donner l'adresse du "
            "recruteur, pas celle du client",
            *(["- [ ] **Démarrage immédiat demandé** alors que vous êtes "
               f"disponible {ctx['availability']} — le dire dès le premier "
               "message plutôt qu'au troisième échange"] if urgent else []),
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
