"""Application FastAPI : consultation, notation et candidatures depuis le navigateur."""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
)
from fastapi.templating import Jinja2Templates

from ..apply import ApplicationGenerator, construire_fiche, rendre_markdown
from ..apply.generator import slugify
from ..apply.llm import LLMWriter
from ..apply.pdf import generer_cv
from ..config import Config, Profile, load_config, load_profile
from ..models import ApplicationStatus
from ..storage import Database
from .state import CampaignState

log = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"

STATUS_LABELS = {
    "new": "a traiter",
    "drafted": "brouillon pret",
    "sent": "envoyee",
    "replied": "reponse recue",
    "interview": "entretien",
    "won": "gagnee",
    "rejected": "refusee",
    "archived": "archivee",
}


def create_app(cfg: Config | None = None, profile: Profile | None = None) -> FastAPI:
    cfg = cfg or load_config()
    profile = profile or load_profile()

    app = FastAPI(title="freelance-radar", docs_url=None, redoc_url=None)
    templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
    templates.env.filters["statut"] = lambda s: STATUS_LABELS.get(s, s)

    campaign = CampaignState()
    app.state.cfg = cfg
    app.state.profile = profile
    app.state.campaign = campaign

    def db() -> Database:
        """Une connexion par requete.

        sqlite3 refuse le partage entre threads, et FastAPI execute les routes
        synchrones dans un pool. Ouvrir la base est peu couteux : c'est plus sur
        qu'un `check_same_thread=False` qui masquerait de vraies races.
        """
        return Database(cfg.db_path)

    def page(request: Request, name: str, **ctx: Any) -> HTMLResponse:
        base = {
            "request": request,
            # Les formulaires d'action renvoient ici : on conserve ainsi les
            # filtres en cours au lieu de retomber sur la liste complete.
            "url_courante": str(request.url.replace(scheme="", netloc="")) or "/",
            "profil": profile,
            "seuil": cfg.scoring.apply_threshold,
            "campagne": campaign.snapshot(),
        }
        return templates.TemplateResponse(request, name, {**base, **ctx})

    # ------------------------------------------------------------------ #
    #  Consultation
    # ------------------------------------------------------------------ #
    @app.get("/", response_class=HTMLResponse)
    def index(request: Request, score_min: float = 0, statut: str = "",
              source: str = "", limite: int = 100, selection: bool = False,
              ecartees: bool = False) -> HTMLResponse:
        base = db()
        try:
            offres = base.list_offers(
                min_score=score_min, status=statut or None,
                source=source or None, limit=limite,
                starred_only=selection, include_discarded=ecartees,
            )
            ctx = {
                "offres": offres,
                "par_statut": base.counts_by_status(),
                "par_source": base.counts_by_source(),
                "filtres": {"score_min": score_min, "statut": statut,
                            "source": source, "limite": limite,
                            "selection": selection, "ecartees": ecartees},
                "statuts": [s.value for s in ApplicationStatus],
                "dernier_passage": base.last_run_at(),
            }
        finally:
            base.close()
        return page(request, "index.html.j2", **ctx)

    @app.get("/offre/{offer_id}", response_class=HTMLResponse)
    def detail(request: Request, offer_id: str) -> HTMLResponse:
        base = db()
        try:
            offre = base.get_offer(offer_id)
            if offre is None:
                raise HTTPException(status_code=404, detail="Offre introuvable")
            candidature = base.get_application(offre.id)
        finally:
            base.close()
        return page(request, "offre.html.j2", offre=offre, candidature=candidature,
                    poids=cfg.scoring.weights,
                    statuts=[s.value for s in ApplicationStatus])

    @app.get("/offre/{offer_id}/fiche", response_class=HTMLResponse)
    def fiche(request: Request, offer_id: str) -> HTMLResponse:
        """Reponses prêtes a coller dans le formulaire de l'employeur."""
        base = db()
        try:
            offre = base.get_offer(offer_id)
            if offre is None:
                raise HTTPException(status_code=404, detail="Offre introuvable")
        finally:
            base.close()
        fiche = ApplicationGenerator(cfg, profile).fiche_candidature(offre)
        return page(request, "fiche.html.j2", offre=offre, fiche=fiche)

    @app.get("/outils", response_class=HTMLResponse)
    def outils(request: Request) -> HTMLResponse:
        """Le favori de pre-remplissage, a installer une fois pour toutes."""
        from .bookmarklet import CHAMPS, construire, valeurs_profil

        valeurs = {c: v for c, v in valeurs_profil(profile).items() if v}
        return page(request, "outils.html.j2",
                    favori=construire(profile), valeurs=valeurs,
                    champs=[(cle, motifs) for cle, motifs in CHAMPS if valeurs.get(cle)])

    @app.get("/candidatures", response_class=HTMLResponse)
    def pipeline(request: Request) -> HTMLResponse:
        base = db()
        try:
            lignes = base.pipeline()
            par_statut = base.counts_by_status()
        finally:
            base.close()
        return page(request, "candidatures.html.j2", lignes=lignes, par_statut=par_statut)

    # ------------------------------------------------------------------ #
    #  Actions
    # ------------------------------------------------------------------ #
    @app.post("/offre/{offer_id}/statut")
    def changer_statut(offer_id: str, statut: str = Form(...)) -> RedirectResponse:
        try:
            nouveau = ApplicationStatus(statut)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Statut inconnu") from exc
        base = db()
        try:
            if not base.set_status(offer_id, nouveau):
                raise HTTPException(status_code=404, detail="Offre introuvable")
        finally:
            base.close()
        return RedirectResponse(f"/offre/{offer_id}", status_code=303)

    @app.get("/offre/{offer_id}/candidature/confirmer", response_class=HTMLResponse)
    def confirmer_llm(request: Request, offer_id: str) -> HTMLResponse:
        """Premiere validation : l'ecran qui annonce l'appel facture.

        Cette page n'emet aucune requete. Elle expose le modele et le cout, et
        n'ouvre le chemin LLM que via le POST qu'elle contient.
        """
        base = db()
        try:
            offre = base.get_offer(offer_id)
            if offre is None:
                raise HTTPException(status_code=404, detail="Offre introuvable")
        finally:
            base.close()
        sonde = LLMWriter(max_words=cfg.application.max_words, consent=True)
        blocage = (None if cfg.application.use_llm
                   else "moteur LLM desactive dans config.yaml")
        return page(request, "confirmation-llm.html.j2", offre=offre,
                    modele=sonde.model, blocage=blocage or sonde.blocked_reason())

    @app.post("/offre/{offer_id}/candidature")
    def generer_candidature(offer_id: str, moteur: str = Form("template"),
                            confirmation: str = Form("")) -> RedirectResponse:
        """Genere un BROUILLON. Aucun envoi : c'est la regle du projet.

        Le moteur LLM exige `moteur=llm` ET `confirmation=oui` : le premier
        vient du bouton, le second de l'ecran de confirmation. Un POST forge
        sans passer par cet ecran retombe sur les templates.
        """
        consent = (moteur == "llm" and confirmation == "oui"
                   and cfg.application.use_llm)
        base = db()
        try:
            offre = base.get_offer(offer_id)
            if offre is None:
                raise HTTPException(status_code=404, detail="Offre introuvable")
            generateur = ApplicationGenerator(cfg, profile)
            candidature = generateur.generate(
                offre, force_template=not consent, consent_llm=consent)
            base.save_application(candidature)
        finally:
            base.close()
        return RedirectResponse(f"/offre/{offer_id}", status_code=303)

    @app.post("/offre/{offer_id}/note")
    def enregistrer_note(offer_id: str, note: str = Form(""),
                         retour: str = Form("")) -> RedirectResponse:
        base = db()
        try:
            if not base.set_note(offer_id, note.strip()):
                raise HTTPException(status_code=404, detail="Offre introuvable")
        finally:
            base.close()
        return RedirectResponse(retour or f"/offre/{offer_id}", status_code=303)

    @app.post("/offre/{offer_id}/selection")
    def basculer_selection(offer_id: str, retour: str = Form("")) -> RedirectResponse:
        base = db()
        try:
            if base.toggle_star(offer_id) is None:
                raise HTTPException(status_code=404, detail="Offre introuvable")
        finally:
            base.close()
        return RedirectResponse(retour or f"/offre/{offer_id}", status_code=303)

    @app.post("/offre/{offer_id}/ecarter")
    def ecarter(offer_id: str, remettre: bool = Form(False),
                retour: str = Form("")) -> RedirectResponse:
        """Masque une offre sans la supprimer : sinon la campagne suivante la reinsere."""
        base = db()
        try:
            if not base.discard(offer_id, discarded=not remettre):
                raise HTTPException(status_code=404, detail="Offre introuvable")
        finally:
            base.close()
        return RedirectResponse(retour or "/", status_code=303)

    @app.post("/offre/{offer_id}/postuler")
    def marquer_postulee(offer_id: str, retour: str = Form("")) -> RedirectResponse:
        """Marque une candidature comme envoyee — apres un envoi fait a la main."""
        base = db()
        try:
            if not base.set_status(offer_id, ApplicationStatus.SENT):
                raise HTTPException(status_code=404, detail="Offre introuvable")
        finally:
            base.close()
        return RedirectResponse(retour or f"/offre/{offer_id}", status_code=303)

    # ------------------------------------------------------------------ #
    #  Campagne de veille
    # ------------------------------------------------------------------ #
    def _run_campaign() -> None:
        from ..pipeline.runner import run_campaign

        base = Database(cfg.db_path)
        try:
            resultat = run_campaign(cfg, profile, base, progress=campaign.progress)
            campaign.finish({
                "collectees": resultat.fetched,
                "retenues": resultat.kept,
                "nouvelles": resultat.new_offers,
                "par_source": resultat.per_source,
                "rejets": resultat.rejected_summary,
            })
        except Exception as exc:  # une campagne qui echoue ne doit pas tuer le serveur
            log.exception("Campagne en echec")
            campaign.finish(error=str(exc))
        finally:
            base.close()

    @app.post("/offre/{offer_id}/entretien")
    def generer_entretien(offer_id: str) -> RedirectResponse:
        """Ecrit la fiche de preparation d'entretien.

        Entierement hors ligne : aucune cle, aucun appel facture. La fiche
        rejoint le dossier de candidature, qu'elle cree au besoin.
        """
        base = db()
        try:
            offre = base.get_offer(offer_id)
            if offre is None:
                raise HTTPException(status_code=404, detail="Offre introuvable")
            fiche = construire_fiche(offre, profile, cfg)
            dossier = cfg.applications_path / (
                f"{int(offre.score):03d}-{slugify(offre.company or offre.source)}"
                f"-{slugify(offre.title)}"
            )
            dossier.mkdir(parents=True, exist_ok=True)
            (dossier / "entretien.md").write_text(rendre_markdown(fiche),
                                                  encoding="utf-8")
        finally:
            base.close()
        return RedirectResponse(f"/document/{offer_id}/entretien.md", status_code=303)

    @app.post("/lettre-generique")
    def generer_lettre_generique() -> RedirectResponse:
        """Ecrit la lettre generique, une fois pour toutes les candidatures."""
        from ..apply.lettre import ecrire_lettre_generique

        ecrire_lettre_generique(
            profile, cfg.applications_path.parent / "lettre-generique.md")
        return RedirectResponse("/outils?lettre=ok", status_code=303)

    @app.post("/offre/{offer_id}/cv")
    def regenerer_cv(offer_id: str, avec: str = Form("")) -> RedirectResponse:
        """Rejoue la seule composition du CV.

        Utile apres avoir touche a `profile.yaml` : inutile de regenerer la
        lettre et l'email, qui n'ont pas bouge. Hors ligne, rien n'est facture.

        `avec` impose des competences, separees par des virgules : elles
        passent devant quoi qu'en dise l'annonce, pour les cas ou vous savez
        qu'un outil comptera alors que le texte ne le nomme pas.
        """
        imposees = [c.strip() for c in avec.split(",") if c.strip()]
        base = db()
        try:
            offre = base.get_offer(offer_id)
            if offre is None:
                raise HTTPException(status_code=404, detail="Offre introuvable")
            dossier = cfg.applications_path / (
                f"{int(offre.score):03d}-{slugify(offre.company or offre.source)}"
                f"-{slugify(offre.title)}"
            )
            dossier.mkdir(parents=True, exist_ok=True)
            generer_cv(offre, profile, dossier / "cv.pdf", imposees=imposees)
        finally:
            base.close()
        return RedirectResponse(f"/document/{offer_id}/cv.pdf", status_code=303)

    @app.post("/campagne")
    def lancer_campagne() -> RedirectResponse:
        if campaign.start():
            threading.Thread(target=_run_campaign, daemon=True).start()
        return RedirectResponse("/", status_code=303)

    @app.get("/campagne/etat")
    def etat_campagne() -> JSONResponse:
        """Sondee par la page pendant qu'une campagne tourne."""
        return JSONResponse(campaign.snapshot())

    # ------------------------------------------------------------------ #
    #  Documents generes
    # ------------------------------------------------------------------ #
    @app.get("/document/{offer_id}/{nom}", response_class=HTMLResponse)
    def document(request: Request, offer_id: str, nom: str) -> HTMLResponse:
        """Affiche un fichier du dossier de candidature.

        Le nom demande est valide contre une liste fermee, et le chemin resolu
        est verifie comme etant sous le dossier des candidatures : sans cela,
        un `nom` du type `../../.env` sortirait de l'arborescence.
        """
        autorises = {"lettre.md", "email.md", "checklist.md", "offre.md",
                     "cv-adapte.md", "entretien.md", "cv.pdf"}
        if nom not in autorises:
            raise HTTPException(status_code=404, detail="Document inconnu")

        base = db()
        try:
            offre = base.get_offer(offer_id)
            candidature = base.get_application(offre.id) if offre else None
        finally:
            base.close()
        if offre is None or candidature is None or not candidature.file_path:
            raise HTTPException(status_code=404, detail="Candidature introuvable")

        racine = cfg.applications_path.resolve()
        chemin = (Path(candidature.file_path) / nom).resolve()
        if not chemin.is_relative_to(racine) or not chemin.exists():
            raise HTTPException(status_code=404, detail="Document introuvable")

        # Le CV est un PDF : il s'ouvre dans le lecteur du navigateur plutot
        # que dans le gabarit de lecture, qui attend du texte.
        if chemin.suffix == ".pdf":
            return FileResponse(chemin, media_type="application/pdf",
                                filename=f"CV - {profile.name}.pdf")

        return page(request, "document.html.j2", offre=offre, nom=nom,
                    contenu=chemin.read_text(encoding="utf-8"),
                    dossier=str(chemin.parent))

    return app


def run(host: str = "127.0.0.1", port: int = 8000, reload: bool = False) -> None:
    """Demarre le serveur.

    Ecoute par defaut sur 127.0.0.1 : l'interface expose votre profil, vos
    candidatures et vos coordonnees. Exposer ce serveur au reseau doit rester
    un geste volontaire, jamais un defaut.
    """
    import uvicorn

    from ..secrets import install as install_redaction

    install_redaction()
    if reload:
        # Le rechargement a chaud exige un chemin d'import, pas une instance.
        uvicorn.run("freelance_radar.web.app:create_app", host=host, port=port,
                    reload=True, factory=True, log_level="warning")
    else:
        uvicorn.run(create_app(), host=host, port=port, log_level="warning")
