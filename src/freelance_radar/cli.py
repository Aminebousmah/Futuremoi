"""Interface en ligne de commande.

    radar init      preparer le projet (profil, .env, base)
    radar sources   etat des sources
    radar scrape    lancer une campagne de veille
    radar list      consulter les offres retenues
    radar show      detail d'une offre + explication du score
    radar apply     generer un brouillon de candidature
    radar track     suivre le pipeline de candidatures
    radar report    exporter (HTML / CSV / JSON)
"""

from __future__ import annotations

import contextlib
import logging
import shutil
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.panel import Panel
from rich.table import Table

from .config import Config, Profile, load_config, load_profile, profile_is_customized, project_root
from .models import ApplicationStatus, JobOffer
from .scrapers import available_scrapers
from .secrets import install as install_redaction
from .storage import Database

# Les consoles Windows heritees sont en cp1252 : un caractere non mappable
# venant d'une annonce ferait planter l'affichage. On degrade au lieu de
# lever, sans imposer d'encodage au terminal.
for _stream in (sys.stdout, sys.stderr):
    # flux redirige ou non reconfigurable : on laisse tel quel
    with contextlib.suppress(AttributeError, ValueError):
        _stream.reconfigure(errors="replace")

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Veille d'offres freelance Data et generation de candidatures (brouillons).",
)
console = Console()

_STATUS_STYLE = {
    "new": "white",
    "drafted": "yellow",
    "sent": "cyan",
    "replied": "blue",
    "interview": "magenta",
    "won": "bold green",
    "rejected": "dim red",
    "archived": "dim",
}


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.WARNING,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=console, show_path=False, rich_tracebacks=True)],
    )
    # Adzuna exige ses cles en parametres d'URL, et httpx journalise l'URL
    # complete : sans ce filtre, `-v` les afficherait en clair.
    install_redaction()


def _load(verbose: bool = False) -> tuple[Config, Profile, Database]:
    _setup_logging(verbose)
    cfg = load_config()
    profile = load_profile()
    if not profile_is_customized():
        console.print(
            "[yellow]Profil d'exemple utilise.[/] Lancez [bold]radar init[/] puis "
            "completez [bold]config/profile.yaml[/] pour un scoring pertinent.\n"
        )
    return cfg, profile, Database(cfg.db_path)


def _score_style(score: float, threshold: float) -> str:
    if score >= threshold:
        return "bold green"
    if score >= threshold - 15:
        return "yellow"
    return "dim"


def _offers_table(offers: list[JobOffer], threshold: float, title: str) -> Table:
    table = Table(title=title, header_style="bold", expand=True)
    table.add_column("id", style="dim", no_wrap=True)
    table.add_column("sc", justify="right", no_wrap=True)
    table.add_column("mission", overflow="fold")
    table.add_column("entreprise", overflow="fold")
    table.add_column("lieu", overflow="fold")
    table.add_column("TJM", justify="right", no_wrap=True)
    table.add_column("src", no_wrap=True)
    table.add_column("statut", no_wrap=True)

    for o in offers:
        table.add_row(
            o.id[:8],
            f"[{_score_style(o.score, threshold)}]{o.score:.0f}[/]",
            o.title[:60],
            (o.company or "-")[:24],
            (o.location or "-")[:22],
            f"{o.daily_rate}" if o.daily_rate else "-",
            o.source[:10],
            f"[{_STATUS_STYLE.get(o.status.value, 'white')}]{o.status.value}[/]",
        )
    return table


# --------------------------------------------------------------------------- #
#  init / sources
# --------------------------------------------------------------------------- #
@app.command()
def init() -> None:
    """Prepare le projet : profil, .env et base de donnees."""
    root = project_root()
    created: list[str] = []

    profile = root / "config" / "profile.yaml"
    if not profile.exists():
        shutil.copy(root / "config" / "profile.example.yaml", profile)
        created.append("config/profile.yaml")

    env_file = root / ".env"
    if not env_file.exists() and (root / ".env.example").exists():
        shutil.copy(root / ".env.example", env_file)
        created.append(".env")

    cfg = load_config()
    Database(cfg.db_path).close()
    created.append(str(cfg.db_path.relative_to(root)))

    console.print(Panel.fit(
        "\n".join([
            "[bold green]Projet pret.[/]",
            "",
            *[f"  cree : {c}" for c in created],
            "",
            "Etapes suivantes :",
            "  1. Completez [bold]config/profile.yaml[/] (competences, TJM, references).",
            "  2. Ajustez [bold]config/config.yaml[/] (mots-cles, filtres, sources).",
            "  3. Lancez [bold]radar scrape[/] puis [bold]radar list[/].",
        ]),
        title="radar init",
    ))


@app.command()
def sources() -> None:
    """Liste les sources disponibles et leur etat de configuration."""
    cfg = load_config()
    from .scrapers import PoliteClient

    table = Table(title="Sources", header_style="bold", expand=True)
    for col in ("source", "type", "active", "pret", "detail"):
        table.add_column(col)

    with PoliteClient(cfg) as client:
        for name, cls in sorted(available_scrapers().items()):
            source_cfg = cfg.sources.get(name, {})
            enabled = bool(source_cfg.get("enabled"))
            scraper = cls(cfg, client, source_cfg)
            ready = scraper.is_configured()
            table.add_row(
                cls.label,
                str(source_cfg.get("kind", "?")),
                "[green]oui[/]" if enabled else "[dim]non[/]",
                "[green]oui[/]" if ready else "[yellow]non[/]",
                "" if ready else scraper.missing_requirement(),
            )
    console.print(table)


# --------------------------------------------------------------------------- #
#  scrape
# --------------------------------------------------------------------------- #
@app.command()
def scrape(
    source: Optional[list[str]] = typer.Option(
        None, "--source", "-s", help="Limiter a certaines sources (repetable)."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Ne rien ecrire en base."),
    explain: bool = typer.Option(False, "--explain", help="Detail des rejets de filtrage."),
    tout: bool = typer.Option(
        False, "--tout",
        help="Reexaminer tout le catalogue, sans se limiter aux nouveautes."),
    top: int = typer.Option(15, "--top", "-n", help="Nombre d'offres affichees."),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Lance une campagne : collecte, normalisation, filtrage, scoring."""
    from .pipeline.runner import run_campaign

    cfg, profile, db = _load(verbose)

    with console.status("[bold]Campagne en cours...") as status:
        def progress(step: str, detail: str) -> None:
            status.update(f"[bold]{step}[/] {detail}")

        result = run_campaign(cfg, profile, db, sources=source,
                              dry_run=dry_run, tout=tout, progress=progress)

    if not result.fetched:
        console.print("[yellow]Aucune offre collectee.[/] Verifiez `radar sources`.")
        raise typer.Exit(code=1)

    summary = Table.grid(padding=(0, 2))
    if result.publiees_depuis:
        summary.add_row("nouveautes depuis",
                        f"[bold]{result.publiees_depuis.strftime('%d/%m/%Y %H:%M')}[/]")
    elif cfg.filters.only_since_last_run:
        summary.add_row("perimetre", "[dim]tout le catalogue (premier passage)[/]")
    summary.add_row("collectees", f"[bold]{result.fetched}[/]")
    summary.add_row("retenues", f"[bold green]{result.kept}[/]")
    summary.add_row("nouvelles", f"[bold]{result.new_offers}[/]")
    summary.add_row("deja connues", f"{result.updated_offers}")
    for name, count in result.per_source.items():
        summary.add_row(f"  via {name}", f"[dim]{count}[/]")
    console.print(Panel(summary, title="Campagne", expand=False))

    if explain and result.filter_report:
        rejects = Table(title="Rejets", header_style="bold")
        rejects.add_column("motif")
        rejects.add_column("n", justify="right")
        for reason, n in result.filter_report.rejected.most_common():
            rejects.add_row(reason, str(n))
        if result.filter_report.duplicates:
            rejects.add_row("doublons", str(result.filter_report.duplicates))
        console.print(rejects)

    shortlist = [o for o in result.offers if o.score >= cfg.scoring.apply_threshold]
    console.print(_offers_table(result.offers[:top], cfg.scoring.apply_threshold,
                                f"Top {min(top, len(result.offers))} offres"))
    if shortlist:
        console.print(
            f"\n[bold green]{len(shortlist)}[/] offres au-dessus du seuil "
            f"({cfg.scoring.apply_threshold:.0f}). Generer les brouillons :\n"
            f"  [bold]radar apply --all --min-score {cfg.scoring.apply_threshold:.0f}[/]"
        )
    if dry_run:
        console.print("[yellow]--dry-run : rien n'a ete enregistre.[/]")
    db.close()


# --------------------------------------------------------------------------- #
#  list / show
# --------------------------------------------------------------------------- #
@app.command(name="list")
def list_offers(
    min_score: float = typer.Option(0, "--min-score", "-m"),
    status: Optional[str] = typer.Option(None, "--status", help="new, drafted, sent, ..."),
    source: Optional[str] = typer.Option(None, "--source", "-s"),
    limit: int = typer.Option(30, "--limit", "-n"),
    new_only: bool = typer.Option(False, "--new", help="Seulement les offres non traitees."),
) -> None:
    """Affiche les offres enregistrees, triees par score."""
    cfg, _, db = _load()
    offers = db.list_offers(min_score=min_score, status=status, source=source,
                            limit=limit, new_only=new_only)
    if not offers:
        console.print("[yellow]Aucune offre ne correspond.[/] Lancez `radar scrape`.")
        raise typer.Exit()
    console.print(_offers_table(offers, cfg.scoring.apply_threshold,
                                f"{len(offers)} offres"))
    db.close()


@app.command()
def show(offer_id: str = typer.Argument(..., help="Identifiant (prefixe accepte).")) -> None:
    """Detail d'une offre et explication de son score."""
    cfg, _, db = _load()
    offer = db.get_offer(offer_id)
    if not offer:
        console.print(f"[red]Offre introuvable :[/] {offer_id}")
        raise typer.Exit(code=1)

    header = Table.grid(padding=(0, 2))
    header.add_row("entreprise", offer.company or "-")
    header.add_row("source", f"{offer.source} — {offer.url}")
    header.add_row("lieu", f"{offer.location or '-'} ({offer.remote.value})")
    header.add_row("contrat", offer.contract.value)
    header.add_row("TJM", f"{offer.daily_rate} EUR" if offer.daily_rate else "non precise")
    header.add_row("duree", f"{offer.duration_months} mois" if offer.duration_months else "-")
    header.add_row("publiee", str(offer.published_at.date()) if offer.published_at else "-")
    header.add_row("competences", ", ".join(offer.skills) or "-")
    header.add_row("statut", offer.status.value)
    console.print(Panel(header, title=f"[bold]{offer.title}[/] — {offer.score:.0f}/100"))

    detail = offer.score_detail or {}
    if detail:
        breakdown = Table(title="Detail du score", header_style="bold")
        breakdown.add_column("signal")
        breakdown.add_column("valeur", justify="right")
        breakdown.add_column("poids", justify="right")
        for signal, weight in cfg.scoring.weights.items():
            value = detail.get(signal)
            if isinstance(value, (int, float)):
                breakdown.add_row(signal, f"{value:.2f}", f"{weight:.0f}")
            else:
                # Un signal muet est retire du calcul : le dire evite de lire
                # un score eleve comme une certitude.
                breakdown.add_row(f"[dim]{signal}[/]", "[dim]n/c[/]",
                                  f"[dim]{weight:.0f}[/]")
        console.print(breakdown)

        couvert = detail.get("_poids_couvert")
        if isinstance(couvert, (int, float)) and couvert < 1.0:
            console.print(
                f"[yellow]Score etabli sur {couvert:.0%} du poids total[/] — "
                f"non renseigne : {', '.join(detail.get('_signaux_ignores') or [])}\n"
            )

        matched = detail.get("_matched_skills") or []
        gaps = detail.get("_missing_skills") or []
        if matched:
            console.print(f"[green]Recoupement :[/] {', '.join(matched)}")
        if gaps:
            console.print(f"[yellow]Ecarts :[/] {', '.join(gaps)}")

    console.print(Panel(offer.description[:2500] or "(description vide)",
                        title="Annonce", border_style="dim"))
    db.close()


# --------------------------------------------------------------------------- #
#  apply
# --------------------------------------------------------------------------- #
@app.command()
def apply(
    offer_id: Optional[str] = typer.Argument(None, help="Offre a traiter."),
    all_offers: bool = typer.Option(False, "--all", help="Traiter toutes les offres eligibles."),
    min_score: Optional[float] = typer.Option(None, "--min-score", "-m"),
    limit: int = typer.Option(10, "--limit", "-n", help="Plafond en mode --all."),
    template_only: bool = typer.Option(
        False, "--template", help="Forcer les templates (sans LLM)."),
    use_llm: bool = typer.Option(
        False, "--llm",
        help="Autoriser un appel FACTURE a l'API Anthropic pour cette commande."),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Confirmer l'appel LLM sans invite."),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Genere un dossier de candidature (brouillon) pour une ou plusieurs offres."""
    from .apply import ApplicationGenerator

    cfg, profile, db = _load(verbose)
    threshold = min_score if min_score is not None else cfg.scoring.apply_threshold

    if all_offers:
        targets = [o for o in db.list_offers(min_score=threshold, limit=limit * 3,
                                             new_only=True)][:limit]
    elif offer_id:
        offer = db.get_offer(offer_id)
        if not offer:
            console.print(f"[red]Offre introuvable :[/] {offer_id}")
            raise typer.Exit(code=1)
        targets = [offer]
    else:
        console.print("[red]Precisez un identifiant d'offre ou utilisez --all.[/]")
        raise typer.Exit(code=1)

    if not targets:
        console.print(f"[yellow]Aucune offre nouvelle au-dessus de {threshold:.0f}.[/]")
        raise typer.Exit()

    generator = ApplicationGenerator(cfg, profile)

    # Deuxieme validation : --llm ouvre la porte, la confirmation la franchit.
    # Sans elle, la commande retombe sur les templates plutot que d'echouer :
    # un dossier de candidature reste produit dans tous les cas.
    consent = use_llm and not template_only and cfg.application.use_llm
    if consent:
        generator.writer.consent = True
        blocage = generator.writer.blocked_reason()
        if blocage:
            console.print(f"[yellow]LLM indisponible ({blocage}) : templates.[/]")
            consent = False
        elif not yes:
            console.print(Panel.fit(
                "\n".join([
                    "[bold yellow]Appel facture a l'API Anthropic[/]",
                    f"Modele : [bold]{generator.writer.model}[/]",
                    f"Offres : [bold]{len(targets)}[/] (un appel par offre)",
                ]),
                title="Confirmation",
            ))
            consent = typer.confirm("Lancer les appels ?", default=False)
        generator.writer.consent = consent

    engine = "LLM" if consent else "templates"
    console.print(f"Moteur de redaction : [bold]{engine}[/]\n")

    for offer in targets:
        with console.status(f"Redaction — {offer.title[:50]}..."):
            application = generator.generate(
                offer, force_template=template_only, consent_llm=consent)
            db.save_application(application)
        console.print(
            f"[green]OK[/] [{offer.score:.0f}] {offer.title[:55]}\n"
            f"     {application.file_path}"
        )

    console.print(Panel.fit(
        "\n".join([
            f"[bold]{len(targets)}[/] brouillon(s) genere(s) dans "
            f"[bold]{cfg.applications_path.relative_to(project_root())}[/].",
            "",
            "Chaque dossier contient : cv.pdf, lettre.md, email.md, offre.md, checklist.md.",
            "[yellow]Relisez avant envoi : rien n'est envoye automatiquement.[/]",
            "",
            "Apres envoi : [bold]radar track <id> --status sent[/]",
        ]),
        title="Candidatures",
    ))
    db.close()


# --------------------------------------------------------------------------- #
#  track / report
# --------------------------------------------------------------------------- #
@app.command()
def entretien(
    offer_id: str = typer.Argument(..., help="Offre a preparer (prefixe accepte)."),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Genere la fiche de preparation d'entretien d'une offre.

    Entierement hors ligne : les questions se deduisent de ce que l'annonce
    reclame, aucun appel a un modele n'est necessaire.
    """
    from .apply import construire_fiche, rendre_markdown
    from .apply.generator import slugify

    cfg, profile, db = _load(verbose)
    offre = db.get_offer(offer_id)
    if not offre:
        console.print(f"[red]Offre introuvable :[/] {offer_id}")
        raise typer.Exit(code=1)

    fiche = construire_fiche(offre, profile, cfg)

    # La fiche rejoint le dossier de candidature quand il existe, sinon elle
    # le cree : on garde tout ce qui concerne une offre au meme endroit.
    dossier = cfg.applications_path / (
        f"{int(offre.score):03d}-{slugify(offre.company or offre.source)}"
        f"-{slugify(offre.title)}"
    )
    dossier.mkdir(parents=True, exist_ok=True)
    chemin = dossier / "entretien.md"
    chemin.write_text(rendre_markdown(fiche), encoding="utf-8")

    sujets = ", ".join(nom for nom, _ in fiche.sujets_cles) or "aucun sujet identifie"
    lignes = [
        f"Sujets cles : [bold]{sujets}[/]",
        f"Questions techniques : [bold]{len(fiche.questions_techniques)}[/]",
        f"Points a reviser : [bold]{len(fiche.revisions)}[/]",
    ]
    if fiche.questions_ecarts:
        lignes.append(f"[yellow]Ecarts a preparer : {len(fiche.questions_ecarts)}[/]")
    lignes += ["", f"Fiche : [bold]{chemin}[/]"]
    console.print(Panel.fit("\n".join(lignes), title=f"Entretien — {offre.title[:45]}"))


@app.command()
def track(
    offer_id: Optional[str] = typer.Argument(None),
    status: Optional[str] = typer.Option(
        None, "--status",
        help="new, drafted, sent, replied, interview, won, rejected, archived"),
) -> None:
    """Affiche le pipeline de candidatures, ou met a jour le statut d'une offre."""
    _, _, db = _load()

    if offer_id and status:
        try:
            new_status = ApplicationStatus(status)
        except ValueError:
            valid = ", ".join(s.value for s in ApplicationStatus)
            console.print(f"[red]Statut invalide.[/] Valeurs possibles : {valid}")
            raise typer.Exit(code=1) from None
        offer = db.get_offer(offer_id)
        if not offer:
            console.print(f"[red]Offre introuvable :[/] {offer_id}")
            raise typer.Exit(code=1)
        db.set_status(offer.id, new_status)
        console.print(f"[green]OK[/] {offer.title[:60]} -> [bold]{new_status.value}[/]")
        db.close()
        return

    pipeline = db.pipeline()
    if not pipeline:
        console.print("[yellow]Aucune candidature.[/] Lancez `radar apply --all`.")
        raise typer.Exit()

    table = Table(title="Pipeline de candidatures", header_style="bold", expand=True)
    for col, kw in (("id", {"style": "dim", "no_wrap": True}), ("sc", {"justify": "right"}),
                    ("mission", {"overflow": "fold"}), ("entreprise", {}),
                    ("TJM propose", {"justify": "right"}), ("statut", {}), ("creee le", {})):
        table.add_column(col, **kw)

    for offer, application in pipeline:
        table.add_row(
            offer.id[:8],
            f"{offer.score:.0f}",
            offer.title[:52],
            (offer.company or "-")[:22],
            f"{application.proposed_rate}" if application.proposed_rate else "-",
            f"[{_STATUS_STYLE.get(offer.status.value, 'white')}]{offer.status.value}[/]",
            application.created_at.strftime("%d/%m"),
        )
    console.print(table)

    counts = db.counts_by_status()
    line = "  ".join(f"[{_STATUS_STYLE.get(k, 'white')}]{k}[/]={v}" for k, v in counts.items())
    console.print(f"\n{line}")
    db.close()


@app.command()
def report(
    fmt: str = typer.Option("html", "--format", "-f", help="html, csv ou json."),
    min_score: float = typer.Option(0, "--min-score", "-m"),
    limit: int = typer.Option(200, "--limit", "-n"),
    output: Optional[Path] = typer.Option(None, "--output", "-o"),
) -> None:
    """Exporte les offres vers un rapport HTML, un CSV ou un JSON."""
    from .report import export_csv, export_html, export_json

    cfg, _, db = _load()
    offers = db.list_offers(min_score=min_score, limit=limit)
    if not offers:
        console.print("[yellow]Rien a exporter.[/]")
        raise typer.Exit()

    default_name = {"html": "rapport.html", "csv": "offres.csv", "json": "offres.json"}
    if fmt not in default_name:
        console.print("[red]Format invalide.[/] Utilisez html, csv ou json.")
        raise typer.Exit(code=1)
    out = output or (project_root() / "output" / default_name[fmt])

    if fmt == "html":
        path = export_html(offers, db, cfg, out)
    elif fmt == "csv":
        path = export_csv(offers, out)
    else:
        path = export_json(offers, out)

    console.print(f"[green]Export ecrit :[/] {path}  ({len(offers)} offres)")
    db.close()


@app.command()
def stats() -> None:
    """Statistiques de la base et historique des campagnes."""
    _, _, db = _load()

    by_status = db.counts_by_status()
    by_source = db.counts_by_source()
    grid = Table.grid(padding=(0, 3))
    grid.add_row("[bold]Par statut[/]", "[bold]Par source[/]")
    grid.add_row(
        "\n".join(f"{k:<10} {v:>4}" for k, v in by_status.items()) or "-",
        "\n".join(f"{k:<14} {v:>4}" for k, v in by_source.items()) or "-",
    )
    console.print(Panel(grid, title=f"{sum(by_status.values())} offres en base"))

    runs = db.last_runs(8)
    if runs:
        table = Table(title="Dernieres campagnes", header_style="bold")
        for col in ("date", "sources", "collectees", "retenues", "nouvelles", "rejets"):
            table.add_column(col, overflow="fold")
        for run in runs:
            table.add_row(
                (run["started_at"] or "")[:16].replace("T", " "),
                run["sources"] or "-",
                str(run["fetched"]), str(run["kept"]), str(run["new_offers"]),
                (run["detail"] or "")[:60],
            )
        console.print(table)
    db.close()


@app.command()
def web(
    port: int = typer.Option(8000, "--port", "-p"),
    host: str = typer.Option("127.0.0.1", "--host",
                             help="Ne changez ceci qu'en connaissance de cause."),
    reload: bool = typer.Option(False, "--reload", help="Rechargement a chaud (dev)."),
) -> None:
    """Lance l'interface web locale."""
    try:
        from .web.app import run
    except ImportError:
        console.print(
            "[red]Dependances web absentes.[/] Installez-les avec :\n"
            '  [bold]pip install -e ".[web]"[/]'
        )
        raise typer.Exit(code=1) from None

    cfg = load_config()
    if not profile_is_customized():
        console.print("[yellow]Profil d'exemple utilise.[/] Completez config/profile.yaml.\n")

    # Ecoute locale par defaut : l'interface expose le profil, les
    # coordonnees et les candidatures. L'ouvrir au reseau doit etre explicite.
    if host != "127.0.0.1":
        console.print(
            "[yellow]Attention :[/] le serveur va ecouter sur "
            f"[bold]{host}[/], donc au-dela de cette machine. L'interface expose "
            "votre profil, vos coordonnees et vos candidatures.\n"
        )

    console.print(Panel.fit(
        "\n".join([
            f"Interface disponible sur [bold]http://{host}:{port}[/]",
            "",
            f"Base : {cfg.db_path}",
            "Arreter le serveur : Ctrl+C",
        ]),
        title="radar web",
    ))
    run(host=host, port=port, reload=reload)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
