"""Génération du CV en PDF, sans dépendance à Canva.

Pourquoi ce module existe
-------------------------
L'adaptation du CV passait par Canva : copie du design, remplacements au mot
près, export. Ce chemin fonctionne mais il n'est pas automatisable — il
s'appuie sur le connecteur MCP, donc sur une session interactive. L'API
publique Canva Connect, elle, ne sait pas faire de find-and-replace sur un
design existant (seulement remplir des *brand templates*) et suppose un
abonnement payant.

D'où ce générateur : la maquette est reproduite ici, les données viennent de
`profile.yaml`, et le PDF sort en une fonction. Canva reste utile pour
retoucher le modèle de référence à la main ; il n'est plus dans la chaîne.

Choix techniques imposés par la machine
---------------------------------------
`fpdf2` est du pur Python. WeasyPrint a été essayé d'abord : il s'installe,
puis Windows bloque sa DLL compilée (`bezierTools.cp312-win_amd64.pyd`) au
titre du contrôle d'application — la même protection qui empêchait déjà
`radar.exe`. Tout moteur à extension C ou à binaire embarqué (Playwright)
échouerait pour la même raison.

Fidélité à la maquette
----------------------
Les dimensions viennent du design Canva (page 794 x 1123 px, soit A4 à
96 dpi). `_mm()` convertit. La mise en page est en flux plutôt qu'en
positions absolues : ajouter une puce décale la suite au lieu de chevaucher.

Les polices Canva (`YAFdJpCEKCQ`) ne sont pas redistribuables ; Arial, présente
sur toute machine Windows, en est proche et rend un PDF au texte parfaitement
extractible — mesuré meilleur que l'export Canva, qui positionne chaque glyphe
individuellement et fait lire « P o w e r  B I » à certains parseurs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from fpdf import FPDF

from ..config import Profile
from ..models import JobOffer
from .cv import AdaptationCV, adapter_cv

# --------------------------------------------------------------------------- #
#  Gabarit
# --------------------------------------------------------------------------- #
PAGE_PX = 794.0          # largeur de la page Canva, en pixels
PAGE_MM = 210.0          # A4


def _mm(px: float) -> float:
    """Convertit une mesure du design Canva en millimètres."""
    return px * PAGE_MM / PAGE_PX


def _pt(px: float) -> float:
    """Convertit une taille de police Canva (px à 96 dpi) en points."""
    return px * 0.75


MARGE = _mm(79.37)                    # 21 mm, la marge du design
LARGEUR_UTILE = _mm(634.96)           # 168 mm
ENCRE = (0x44, 0x44, 0x44)            # #444444, la couleur du CV
TRAIT = (0xA8, 0xA7, 0xA7)            # #706e6e à 36 % sur blanc

PHOTO_DIAMETRE = _mm(140.554)         # 37,2 mm
POLICE = "CVSans"
POLICE_TITRE = "CVSerif"      # le nom, en serif comme la maquette

# Tailles reprises du design.
T_NOM = _pt(33.3335)
T_SOUS_TITRE = _pt(16.0)
T_CONTACT = _pt(10.0)
T_SECTION = _pt(13.3334)
T_POSTE = _pt(10.6668)
T_CORPS = _pt(10.0)

# Interlignes : le design utilise 1.33 pour le corps, 1.27 pour les titres.
H_CORPS = _mm(10.0 * 1.33)
H_POSTE = _mm(10.6668 * 1.27)

CHEMINS_POLICES = (
    Path("C:/Windows/Fonts"),
    Path("/usr/share/fonts/truetype/msttcorefonts"),
    Path("/usr/share/fonts/truetype/liberation"),
)
FICHIERS = {
    "": ("arial.ttf", "LiberationSans-Regular.ttf"),
    "B": ("arialbd.ttf", "LiberationSans-Bold.ttf"),
    "I": ("ariali.ttf", "LiberationSans-Italic.ttf"),
}
# Le nom du CV est composé dans un serif léger. Constantia s'en approche ;
# à défaut, on retombe sur la sans, la mise en page ne bouge pas.
FICHIERS_TITRE = {
    "": ("constan.ttf", "cambria.ttc", "LiberationSerif-Regular.ttf"),
}


class PolicesIntrouvables(RuntimeError):
    """Aucune police compatible sur la machine."""


def _trouver_police(candidats: tuple[str, ...]) -> Path:
    for dossier in CHEMINS_POLICES:
        for nom in candidats:
            chemin = dossier / nom
            if chemin.exists():
                return chemin
    raise PolicesIntrouvables(
        f"aucune de {candidats} trouvée dans {[str(d) for d in CHEMINS_POLICES]}"
    )


@dataclass
class Bloc:
    """Un segment de texte et son style, pour le rendu en ligne."""

    texte: str
    gras: bool = False


_GRAS_RE = re.compile(r"\*\*(.+?)\*\*")


def decouper_gras(texte: str) -> list[Bloc]:
    """Découpe `un **mot** en gras` en segments stylés.

    Le balisage vient de `profile.yaml`, où il marque ce que le CV met en
    avant : outils et résultats chiffrés.
    """
    blocs: list[Bloc] = []
    position = 0
    for m in _GRAS_RE.finditer(texte):
        if m.start() > position:
            blocs.append(Bloc(texte[position:m.start()]))
        blocs.append(Bloc(m.group(1), gras=True))
        position = m.end()
    if position < len(texte):
        blocs.append(Bloc(texte[position:]))
    return blocs or [Bloc(texte)]


def echapper_markdown(texte: str) -> str:
    """Neutralise les marqueurs que fpdf2 interpréterait à tort.

    Un intitulé comme « Excel (Power Query, TCD, VBA) » ne pose pas de
    problème, mais un tiret double ou un souligné dans un nom d'outil serait
    lu comme du style. On les échappe plutôt que de désactiver le markdown,
    dont on a besoin pour le gras.
    """
    return texte.replace("__", "\\_\\_").replace("--", "\\-\\-")


class CVPdf(FPDF):
    """La maquette, en flux vertical."""

    def __init__(self) -> None:
        super().__init__(format="A4", unit="mm")
        self.set_margins(MARGE, _mm(79.37), MARGE)
        self.set_auto_page_break(True, margin=_mm(60))
        self.set_text_color(*ENCRE)
        for style, candidats in FICHIERS.items():
            self.add_font(POLICE, style, str(_trouver_police(candidats)))
        try:
            for style, candidats in FICHIERS_TITRE.items():
                self.add_font(POLICE_TITRE, style, str(_trouver_police(candidats)))
            self.serif = True
        except PolicesIntrouvables:
            self.serif = False

    # -- primitives ---------------------------------------------------- #
    def paragraphe(self, texte: str, *, taille: float = T_CORPS, style: str = "",
                   hauteur: float = H_CORPS, align: str = "L",
                   markdown: bool = False) -> None:
        self.set_font(POLICE, style, taille)
        self.multi_cell(0, hauteur, texte, align=align, markdown=markdown,
                        new_x="LMARGIN", new_y="NEXT")

    def ligne_stylee(self, blocs: list[Bloc], *, taille: float = T_CORPS,
                     hauteur: float = H_CORPS) -> None:
        """Rend une suite de segments gras/normaux avec retour à la ligne."""
        markdown = "".join(
            f"**{echapper_markdown(b.texte)}**" if b.gras else echapper_markdown(b.texte)
            for b in blocs
        )
        self.paragraphe(markdown, taille=taille, hauteur=hauteur, align="J",
                        markdown=True)

    def filet(self) -> None:
        """Le trait de séparation entre sections."""
        self.ln(_mm(6))
        self.set_draw_color(*TRAIT)
        self.set_line_width(0.2)
        y = self.get_y()
        self.line(MARGE, y, MARGE + _mm(631.28), y)
        self.ln(_mm(8))

    def titre_section(self, texte: str) -> None:
        self.set_font(POLICE, "B", T_SECTION)
        self.set_char_spacing(0.3)          # letterSpacing 0.1 du design
        self.multi_cell(0, _mm(13.3334 * 1.27), texte.upper(),
                        new_x="LMARGIN", new_y="NEXT")
        self.set_char_spacing(0)
        self.ln(_mm(4))

    def puce(self, texte: str) -> None:
        """Une puce, avec son retrait et son gras en ligne."""
        gauche = self.l_margin
        self.set_font(POLICE, "", T_CORPS)
        self.set_x(gauche)
        self.cell(_mm(12), H_CORPS, "•")
        self.set_left_margin(gauche + _mm(12))
        self.set_x(gauche + _mm(12))
        self.ligne_stylee(decouper_gras(texte))
        self.set_left_margin(gauche)
        self.set_x(gauche)


# --------------------------------------------------------------------------- #
#  Sections
# --------------------------------------------------------------------------- #
def _entete(pdf: CVPdf, profile: Profile, photo: Path | None) -> None:
    ident = profile.identity or {}
    haut = pdf.get_y()

    if photo and photo.exists():
        pdf.image(str(photo), x=MARGE, y=haut,
                  w=PHOTO_DIAMETRE, h=PHOTO_DIAMETRE)

    # Le bloc texte est centré sur la largeur restante, à droite de la photo.
    gauche = MARGE + PHOTO_DIAMETRE + _mm(15)
    largeur = MARGE + LARGEUR_UTILE - gauche
    # La maquette compose le nom en capitales espacées : c'est ce qui donne
    # son allure au CV, plus que la police elle-même.
    pdf.set_xy(gauche, haut + _mm(26))
    pdf.set_font(POLICE_TITRE if pdf.serif else POLICE, "", T_NOM)
    pdf.set_char_spacing(1.2)
    pdf.cell(largeur, _mm(40), ident.get("full_name", "").upper(), align="C",
             new_x="LMARGIN", new_y="NEXT")
    pdf.set_char_spacing(0)

    pdf.set_xy(gauche, haut + _mm(67))
    pdf.set_font(POLICE, "I", T_SOUS_TITRE)
    pdf.set_char_spacing(0.6)
    pdf.cell(largeur, _mm(19), ident.get("title", "").upper(), align="C",
             new_x="LMARGIN", new_y="NEXT")
    pdf.set_char_spacing(0)

    contact = " · ".join(p for p in (
        ident.get("email", ""),
        ident.get("phone", ""),
        (ident.get("linkedin") or "").replace("https://www.linkedin.com/", ""),
        (ident.get("website") or "").replace("https://", ""),
    ) if p)
    pdf.set_xy(gauche, haut + _mm(88))
    pdf.set_font(POLICE, "", T_CONTACT)
    pdf.cell(largeur, _mm(14), contact, align="C", new_x="LMARGIN", new_y="NEXT")

    pdf.set_y(haut + PHOTO_DIAMETRE)


def _profil(pdf: CVPdf, adaptation: AdaptationCV, profile: Profile) -> None:
    pdf.titre_section("Profil")
    p = adaptation.profil
    if p is None:
        pdf.paragraphe((profile.positioning or {}).get("pitch", ""), align="J")
        return
    pdf.ligne_stylee([
        Bloc(p.initiale), Bloc(p.tete, gras=True), Bloc(p.liaison),
        Bloc(p.accent, gras=True), Bloc(p.fin),
    ])


def _experiences(pdf: CVPdf, profile: Profile) -> None:
    experiences = (profile.cv or {}).get("parcours", {}).get("experiences") or []
    if not experiences:
        return
    pdf.titre_section("Expériences professionnelles")
    for i, exp in enumerate(experiences):
        if i:
            pdf.ln(_mm(8))
        pdf.set_font(POLICE, "B", T_POSTE)
        pdf.set_char_spacing(0.25)
        pdf.multi_cell(0, H_POSTE, exp.get("poste", "").upper(),
                       new_x="LMARGIN", new_y="NEXT")

        y = pdf.get_y()
        pdf.set_font(POLICE, "", T_POSTE)
        pdf.cell(LARGEUR_UTILE / 2, H_POSTE, exp.get("client", "").upper())
        pdf.set_char_spacing(0)
        pdf.set_xy(MARGE + LARGEUR_UTILE / 2, y)
        pdf.set_font(POLICE, "I", T_POSTE)
        pdf.set_text_color(0x70, 0x6E, 0x6E)
        droite = " ".join(x for x in (exp.get("periode"), exp.get("lieu")) if x)
        pdf.cell(LARGEUR_UTILE / 2, H_POSTE, droite, align="R",
                 new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(*ENCRE)
        pdf.ln(_mm(3))
        for texte in exp.get("puces") or []:
            pdf.puce(texte)


def _formation(pdf: CVPdf, profile: Profile) -> None:
    lignes = (profile.cv or {}).get("parcours", {}).get("formation") or []
    if not lignes:
        return
    pdf.filet()
    pdf.titre_section("Formation")
    pdf.set_font(POLICE, "", T_POSTE)
    pdf.set_char_spacing(0.25)
    for f in lignes:
        texte = " | ".join(x for x in (f.get("intitule"), f.get("etablissement"),
                                       f.get("lieu"), f.get("annee")) if x)
        pdf.multi_cell(0, _mm(10.6668 * 1.27) + _mm(2), texte.upper(),
                       new_x="LMARGIN", new_y="NEXT")
    pdf.set_char_spacing(0)


def _projets(pdf: CVPdf, profile: Profile) -> None:
    projets = (profile.cv or {}).get("parcours", {}).get("projets") or []
    if not projets:
        return
    pdf.filet()
    pdf.titre_section("Projets")
    for i, projet in enumerate(projets):
        if i:
            pdf.ln(_mm(8))
        pdf.set_char_spacing(0.25)
        pdf.set_font(POLICE, "B", T_POSTE)
        pdf.multi_cell(0, H_POSTE, projet.get("nom", "").upper(),
                       new_x="LMARGIN", new_y="NEXT")
        pdf.set_font(POLICE, "", T_POSTE)
        pdf.multi_cell(0, H_POSTE, projet.get("sous_titre", "").upper(),
                       new_x="LMARGIN", new_y="NEXT")
        pdf.set_char_spacing(0)
        pdf.ln(_mm(3))
        for texte in projet.get("puces") or []:
            pdf.puce(texte)


def _competences(pdf: CVPdf, adaptation: AdaptationCV) -> None:
    if not adaptation.rubriques:
        return
    pdf.filet()
    pdf.titre_section("Compétences")
    for rubrique in adaptation.rubriques:
        pdf.ligne_stylee([
            Bloc(f"{rubrique.label} : ", gras=True),
            Bloc(" · ".join(rubrique.outils)),
        ])


# --------------------------------------------------------------------------- #
#  Point d'entrée
# --------------------------------------------------------------------------- #
def generer_cv(offer: JobOffer, profile: Profile, destination: Path,
               photo: Path | None = None) -> Path:
    """Écrit le CV adapté à `offer` et rend le chemin du PDF.

    Le contenu adapté (paragraphe de profil et rubriques de compétences) vient
    de `cv.adapter_cv` : ce module ne décide de rien, il met en page.
    """
    adaptation = adapter_cv(offer, profile)
    if photo is None:
        chemin = (profile.documents or {}).get("photo")
        photo = Path(chemin) if chemin else None

    pdf = CVPdf()
    pdf.add_page()
    _entete(pdf, profile, photo)
    pdf.filet()
    _profil(pdf, adaptation, profile)
    pdf.filet()
    _experiences(pdf, profile)
    _formation(pdf, profile)
    _projets(pdf, profile)
    _competences(pdf, adaptation)

    destination.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(destination))
    return destination
