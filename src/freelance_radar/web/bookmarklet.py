"""Fabrique le favori qui pre-remplit un formulaire de candidature.

Le principe : un favori `javascript:` que vous cliquez sur la page de
l'employeur. Il parcourt les champs, reconnait ceux qu'il sait remplir, et y
depose vos informations. Rien ne quitte votre navigateur — vos donnees sont
dans le favori lui-meme, jamais envoyees nulle part — et il ne soumet rien :
le bouton d'envoi reste votre geste.

Le rapprochement se fait par sous-chaines, pas par expressions regulieres :
l'ordre des cles suffit a lever les ambiguites ("nom complet" est teste avant
"prenom", teste avant "nom"), et le code reste lisible.
"""

from __future__ import annotations

import json
from urllib.parse import quote

from ..config import Profile

# Ordre significatif : le premier motif qui correspond gagne, ce qui evite
# qu'un champ "Prenom" soit rempli par la regle "nom".
CHAMPS: list[tuple[str, list[str]]] = [
    ("nom_complet", ["nom complet", "full name", "fullname", "nom et prenom",
                     "votre nom", "your name"]),
    ("prenom", ["prenom", "first name", "firstname", "given name", "fname"]),
    ("nom", ["nom de famille", "last name", "lastname", "surname",
             "family name", "lname", "nom"]),
    ("email", ["email", "e-mail", "mail", "courriel"]),
    ("telephone", ["telephone", "phone", "portable", "mobile", "numero", "tel"]),
    ("linkedin", ["linkedin", "linked in"]),
    ("github", ["github", "git hub"]),
    ("site", ["portfolio", "site web", "website", "site personnel",
              "personal site", "url"]),
    ("ville", ["ville", "city", "localisation", "location", "adresse"]),
    ("titre", ["intitule", "poste actuel", "job title", "current title",
               "headline", "titre"]),
    ("statut", ["statut", "status juridique", "forme juridique"]),
    ("siret", ["siret", "siren"]),
    ("tjm", ["tjm", "taux journalier", "pretention", "salaire", "daily rate",
             "rate", "remuneration"]),
    ("disponibilite", ["disponibilite", "availability", "date de debut",
                       "start date", "disponible"]),
    ("annees_experience", ["annees d experience", "years of experience",
                           "experience (annees)", "nombre d annees"]),
    ("mobilite", ["mobilite", "mobility", "zone geographique"]),
]


def valeurs_profil(profile: Profile) -> dict[str, str]:
    """Les informations stables du profil, celles qui ne changent pas d'une offre a l'autre."""
    ident = profile.identity or {}
    contraintes = profile.constraints or {}
    complet = str(ident.get("full_name", "")).strip()
    morceaux = complet.split()
    prenom = morceaux[0] if morceaux else ""
    nom = " ".join(morceaux[1:]) if len(morceaux) > 1 else ""

    return {
        "nom_complet": complet,
        "prenom": prenom,
        "nom": nom,
        "email": str(ident.get("email", "")),
        "telephone": str(ident.get("phone", "")),
        "linkedin": str(ident.get("linkedin", "")),
        "github": str(ident.get("github", "")),
        "site": str(ident.get("website", "")),
        "ville": str(ident.get("city", "")),
        "titre": str(ident.get("title", "")),
        "statut": profile.statut_juridique,
        "siret": profile.siret,
        "tjm": (f"{profile.rate_target} EUR HT/jour" if profile.rate_target else ""),
        "disponibilite": str(contraintes.get("available_from", "")),
        "annees_experience": str(profile.positioning.get("years_experience", "")),
        "mobilite": ", ".join(str(m) for m in (contraintes.get("mobility") or [])),
    }


# Script depose dans le favori. Volontairement sans expression reguliere :
# les sous-chaines et l'ordre des cles suffisent, et le code reste relisible.
_SCRIPT = """
(function () {
  var CHAMPS = __CHAMPS__;
  var VALEURS = __VALEURS__;

  function sansAccent(s) {
    return (s || "").toLowerCase()
      .normalize("NFD").replace(/[\u0300-\u036f]/g, "")
      .replace(/[^a-z0-9]+/g, " ").trim();
  }

  // Tout ce qui peut nommer un champ : ses attributs, son libelle, et le texte
  // juste au-dessus quand le formulaire n'utilise pas de <label>.
  function description(champ) {
    var bouts = [champ.name, champ.id, champ.placeholder,
                 champ.getAttribute("aria-label"), champ.getAttribute("autocomplete")];
    if (champ.id) {
      var lab = document.querySelector('label[for="' + champ.id + '"]');
      if (lab) { bouts.push(lab.textContent); }
    }
    var parent = champ.closest("label");
    if (parent) { bouts.push(parent.textContent); }
    var bloc = champ.closest("div, p, li, fieldset");
    if (bloc) { bouts.push(bloc.textContent.slice(0, 120)); }
    return sansAccent(bouts.join(" "));
  }

  function deposer(champ, valeur) {
    var proto = champ.tagName === "TEXTAREA"
      ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
    var setter = Object.getOwnPropertyDescriptor(proto, "value").set;
    setter.call(champ, valeur);
    // Les formulaires React/Vue ignorent une affectation directe : il faut
    // emettre les evenements qu'un vrai clavier produirait.
    champ.dispatchEvent(new Event("input", { bubbles: true }));
    champ.dispatchEvent(new Event("change", { bubbles: true }));
    champ.style.outline = "2px solid #2f6f4f";
    champ.style.outlineOffset = "1px";
  }

  var ignores = ["password", "hidden", "submit", "button", "file", "checkbox",
                 "radio", "image", "reset"];
  var remplis = 0, vus = 0;

  document.querySelectorAll("input, textarea").forEach(function (champ) {
    var type = (champ.type || "text").toLowerCase();
    if (ignores.indexOf(type) !== -1 || champ.disabled || champ.readOnly) { return; }
    if (champ.offsetParent === null && champ.type !== "hidden") { return; }
    vus++;
    if (champ.value && champ.value.trim()) { return; }   // on n'ecrase rien

    var texte = description(champ);
    for (var i = 0; i < CHAMPS.length; i++) {
      var cle = CHAMPS[i][0], motifs = CHAMPS[i][1];
      if (!VALEURS[cle]) { continue; }
      for (var j = 0; j < motifs.length; j++) {
        if (texte.indexOf(motifs[j]) !== -1) {
          deposer(champ, VALEURS[cle]);
          remplis++;
          return;
        }
      }
    }
  });

  var note = document.createElement("div");
  note.textContent = remplis
    ? "freelance-radar : " + remplis + " champ(s) rempli(s) sur " + vus +
      " — relisez avant d'envoyer."
    : "freelance-radar : aucun champ reconnu sur cette page.";
  note.style.cssText = "position:fixed;z-index:2147483647;left:50%;bottom:24px;" +
    "transform:translateX(-50%);background:#1f2328;color:#fff;padding:.7rem 1.1rem;" +
    "border-radius:10px;font:14px system-ui,sans-serif;box-shadow:0 4px 16px rgba(0,0,0,.3)";
  document.body.appendChild(note);
  setTimeout(function () { note.remove(); }, 6000);
})();
"""


def construire(profile: Profile) -> str:
    """Rend le favori complet, prêt a etre glisse dans la barre de favoris."""
    valeurs = {k: v for k, v in valeurs_profil(profile).items() if v}
    script = (_SCRIPT
              .replace("__CHAMPS__", json.dumps(CHAMPS, ensure_ascii=False))
              .replace("__VALEURS__", json.dumps(valeurs, ensure_ascii=False)))
    # Les sauts de ligne sont CONSERVES. Les joindre par des espaces
    # transformerait chaque commentaire de fin de ligne en baillon : tout ce
    # qui suit sur la ligne fusionnee se retrouve commente, et le favori ne
    # s'execute plus. L'encodage d'URL rend les retours a la ligne inoffensifs.
    return "javascript:" + quote(script.strip(), safe="")
