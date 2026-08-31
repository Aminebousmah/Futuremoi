"""Fiche de preparation d'entretien, deduite de l'offre et du profil.

Produite hors ligne, sans appel a un modele : les questions posees en
entretien Data sont stables et se deduisent de ce que l'annonce reclame. Ce
qui change d'une offre a l'autre, c'est *quelles* competences sont sur la
table -- et c'est precisement ce que `cv.classer_competences()` sait dire.

La fiche repond a trois questions, dans cet ordre :

  1. Qu'est-ce qu'on va me demander ? (questions probables, par origine)
  2. Qu'est-ce que je dois reviser ? (points techniques, cibles)
  3. Qu'est-ce que je dois demander, moi ? (cadrage, et protection)

Deux partis pris :

  * Les ecarts declares dans `score_detail["_missing_skills"]` passent en
    premier. Ce sont les questions qui font perdre une mission quand elles
    sont improvisees, et la fiche ne suggere jamais de les masquer.
  * Les sujets adjacents comptent autant que les sujets cites. Une annonce
    "Power BI" ne dit pas "DAX", mais l'entretien le demandera : `ADJACENCES`
    porte ces implications que le texte de l'offre laisse tacites.

Les cles de `REVISIONS` reprennent a l'identique les intitules de
`profile.cv.competences`, accents compris : une cle mal orthographiee ne
declencherait jamais, sans erreur visible. `test_entretien.py` le verrouille.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..config import Config, Profile
from ..models import JobOffer
from .cv import classer_competences

# --------------------------------------------------------------------------- #
#  Base de revision
# --------------------------------------------------------------------------- #
# Chaque entree tient en quelques points concrets : une liste de vingt items
# ne se revise pas la veille d'un entretien.
REVISIONS: dict[str, list[str]] = {
    "SQL": [
        "Fonctions de fenêtrage : ROW_NUMBER, RANK, LAG/LEAD, et ce que "
        "PARTITION BY fait de plus que GROUP BY",
        "Anti-jointures : NOT IN casse dès que la sous-requête rend un NULL ; "
        "savoir pourquoi NOT EXISTS est plus sûr",
        "Lire un plan d'exécution : index utilisé ou non, scan contre seek, "
        "où une jointure devient coûteuse",
        "CTE et CTE récursives : lisibilité contre coût réel",
        "WHERE contre HAVING : à quel moment du traitement chacun s'applique",
    ],
    "Python": [
        "pandas : merge contre join, groupby/agg, et pourquoi un apply ligne "
        "à ligne s'effondre sur du volume",
        "Types et mémoire : category, downcast, lecture par morceaux",
        "Structurer un script en module testable plutôt qu'un notebook",
        "Gestion des erreurs et journalisation dans un traitement planifié",
    ],
    "DAX": [
        "Contexte de ligne contre contexte de filtre : la question qui revient "
        "le plus souvent en entretien Power BI",
        "CALCULATE et la transition de contexte : savoir l'expliquer sur un cas",
        "Mesure contre colonne calculée : où est stocké le résultat, et le coût",
        "Time intelligence : SAMEPERIODLASTYEAR, DATEADD, et la table de dates "
        "marquée comme telle",
        "Itérateurs (SUMX, AVERAGEX) : quand ils sont indispensables",
    ],
    "Power BI": [
        "Modèle en étoile contre flocon : pourquoi Power BI préfère l'étoile",
        "Relations : cardinalité, sens du filtre, et les risques du bidirectionnel",
        "Import contre DirectQuery contre mode composite : le compromis",
        "Optimiser un rapport lent : réduire les colonnes, les visuels, le modèle",
        "Row-level security : mise en œuvre et test",
    ],
    "Power Query (langage M)": [
        "Query folding : savoir dire si une étape est repliée vers la source, "
        "et pourquoi c'est décisif sur la performance",
        "Paramètres et fonctions personnalisées pour ne pas dupliquer les requêtes",
        "Typage explicite des colonnes et gestion des erreurs de conversion",
    ],
    "Excel (Power Query, TCD, VBA)": [
        "Power Query dans Excel : les mêmes étapes que dans Power BI",
        "Tableaux croisés dynamiques : champs calculés, groupement, segments",
        "Savoir dire à partir de quel moment Excel n'est plus le bon outil",
    ],
    "Tableau": [
        "LOD expressions (FIXED, INCLUDE, EXCLUDE) : le pendant de CALCULATE",
        "Extraits contre connexion directe",
        "Ordre des opérations Tableau : filtres, dimensions, mesures",
    ],
    "Modèle en étoile (Kimball)": [
        "Fixer le grain de la table de faits AVANT tout le reste : c'est la "
        "première question d'un entretien de modélisation",
        "Dimensions à évolution lente : type 1 contre type 2, et le coût du type 2",
        "Dimensions conformes : pourquoi elles rendent les faits comparables",
        "Faits additifs, semi-additifs, non additifs",
    ],
    "Modèle sémantique": [
        "Ce qu'un modèle sémantique apporte au métier : vocabulaire commun, "
        "règles de calcul centralisées",
        "Où placer une règle de gestion : dans l'ETL, le modèle, ou la mesure",
    ],
    "Snowflake": [
        "Séparation stockage / calcul, et ce que cela change sur la facture",
        "Virtual warehouse : dimensionnement, suspension automatique",
        "Clustering et micro-partitions : quand cela vaut le coût",
        "Time travel et zero-copy cloning",
    ],
    "BigQuery": [
        "Partitionnement et clustering : l'effet direct sur le coût d'une requête",
        "Facturation à l'octet lu : les réflexes pour ne pas exploser un budget",
        "Tables externes et vues matérialisées",
    ],
    "PostgreSQL": [
        "Index B-tree contre GIN : à quoi chacun sert",
        "EXPLAIN ANALYZE : lire le coût réel plutôt que le coût estimé",
        "VACUUM et le gonflement des tables",
    ],
    "dbt": [
        "Matérialisations : view, table, incremental — et le critère de choix",
        "Tests intégrés (unique, not_null, relationships) et tests sur mesure",
        "Sources, refs, et le graphe de lignage qui en découle",
        "Stratégie incrémentale : clé unique, fenêtre de rattrapage",
    ],
    "Airflow": [
        "Idempotence d'une tâche : rejouer un DAG ne doit rien casser",
        "Scheduling et backfill : la différence entre date d'exécution et "
        "date logique",
        "Dépendances, capteurs, et les pièges d'un DAG trop bavard",
    ],
    "Spark / PySpark": [
        "Transformations paresseuses contre actions",
        "Le shuffle : ce qui le déclenche et pourquoi il coûte cher",
        "Partitionnement, repartition et coalesce",
        "Broadcast join : quand il sauve une jointure",
    ],
    "Databricks": [
        "Delta Lake : transactions ACID sur un lac de données",
        "Architecture médaillon (bronze / argent / or)",
        "Cluster : dimensionnement et coût",
    ],
    "Azure Data Factory": [
        "Pipelines, activités, jeux de données, services liés",
        "Integration runtime : quand il faut un runtime auto-hébergé",
        "Déclencheurs et gestion des reprises",
    ],
    "Azure": [
        "Les briques data : Data Factory, Synapse, Data Lake Storage",
        "Identités et accès : quel principal accède à quoi",
    ],
    "AWS": [
        "Les briques data : S3, Glue, Redshift, Athena",
        "IAM : rôle contre utilisateur, principe du moindre privilège",
    ],
    "Git": [
        "Branches, rebase contre merge, et résoudre un conflit sans paniquer",
        "Ce qu'on ne commite jamais : secrets, données, fichiers générés",
    ],
    "CI/CD": [
        "Ce qui doit tourner à chaque commit : tests, lint, build",
        "Déployer un modèle de données : comment on revient en arrière si ça casse",
    ],
    "Docker": [
        "Image contre conteneur, et ce qu'apporte un Dockerfile à un projet data",
    ],
    "Scikit-learn": [
        "Pipeline et ColumnTransformer : éviter la fuite de données",
        "Validation croisée, et pourquoi un simple train/test ne suffit pas",
        "Choisir la métrique selon le problème : précision, rappel, F1, AUC, "
        "RMSE, MAE",
    ],
    "Statistiques appliquées": [
        "Test d'hypothèse : ce que la p-value dit, et surtout ce qu'elle ne dit pas",
        "Corrélation n'est pas causalité : savoir le formuler sur un exemple",
        "Intervalle de confiance et taille d'échantillon",
    ],
    "Tests A/B": [
        "Puissance statistique et durée du test décidées AVANT de lancer",
        "Les pièges : arrêt anticipé, tests multiples, effet de nouveauté",
    ],
    "Séries temporelles": [
        "Saisonnalité, tendance, stationnarité",
        "Validation temporelle : jamais de découpage aléatoire train/test",
        "Baselines (naïve, moyenne mobile) avant tout modèle compliqué",
    ],
    "MLOps": [
        "Suivi des expériences et versionnage des modèles",
        "Dérive des données et dérive du modèle : comment on la détecte",
        "Réentraînement : déclenché par le calendrier ou par la performance",
    ],
    "RAG (retrieval augmented generation)": [
        "Découpage des documents et stratégie d'indexation",
        "Évaluer une réponse : pertinence des passages, fidélité au contexte",
        "Ce que le RAG ne répare pas : une base documentaire de mauvaise qualité",
    ],
    "LLM (GPT, Claude)": [
        "Où un LLM apporte vraiment, et où une règle métier suffit",
        "Coût et latence : ce que cela change dans une architecture",
        "Confidentialité des données envoyées à un service tiers",
    ],
    "Gouvernance des données": [
        "Propriétaire de la donnée, définition partagée, cycle de vie",
        "Catalogue et lignage : à quoi ils servent concrètement au quotidien",
    ],
    "Qualité des données": [
        "Les dimensions : complétude, unicité, fraîcheur, cohérence, validité",
        "Où placer les contrôles : à l'ingestion, pas au moment du rapport",
    ],
    "RGPD": [
        "Donnée personnelle contre donnée sensible",
        "Minimisation, durée de conservation, base légale",
        "Pseudonymisation contre anonymisation : la différence juridique",
    ],
    "Analyse métier": [
        "Traduire une demande floue en indicateur mesurable",
        "Savoir dire non à un indicateur qui ne se calcule pas proprement",
    ],
    "KPI et tableaux de bord": [
        "Ce qui fait un bon indicateur : actionnable, défini, comparable",
        "Un tableau de bord qui ne déclenche aucune décision ne sert à rien : "
        "savoir l'argumenter",
    ],
    "Suivi budgétaire": [
        "Budget, réalisé, engagé, écart : le vocabulaire du contrôle de gestion",
        "Rapprochement et explication des écarts",
    ],
    "Prévision / forecast": [
        "Baseline avant modèle, et mesure de l'erreur (MAPE, biais)",
        "Ce qu'on fait quand le métier conteste la prévision",
    ],
    "Agile / Scrum": [
        "Le rôle d'un freelance dans une équipe agile déjà constituée",
        "Estimer sans s'engager sur ce qui ne dépend pas de soi",
    ],
    "Cadrage du besoin": [
        "Les questions à poser avant d'écrire la moindre requête",
        "Distinguer besoin exprimé et besoin réel",
    ],
}

# Ce qu'une annonce ne dit pas mais que l'entretien demandera. Une offre
# "Power BI" ne mentionne presque jamais DAX ; elle sera pourtant testee
# dessus. Ces implications tacites sont ajoutees aux sujets a reviser.
ADJACENCES: dict[str, list[str]] = {
    "Power BI": ["DAX", "Power Query (langage M)", "Modèle en étoile (Kimball)"],
    "Tableau": ["SQL"],
    "Excel (Power Query, TCD, VBA)": ["Power Query (langage M)"],
    "DAX": ["Modèle en étoile (Kimball)"],
    "dbt": ["SQL", "Modèle en étoile (Kimball)"],
    "Airflow": ["Python"],
    "Snowflake": ["SQL"],
    "BigQuery": ["SQL"],
    "PostgreSQL": ["SQL"],
    "Databricks": ["Spark / PySpark"],
    "Spark / PySpark": ["Python"],
    "Scikit-learn": ["Python", "Statistiques appliquées"],
    "MLOps": ["Docker", "CI/CD"],
    "Séries temporelles": ["Statistiques appliquées"],
    "RAG (retrieval augmented generation)": ["LLM (GPT, Claude)", "Python"],
    "KPI et tableaux de bord": ["Analyse métier"],
    "Prévision / forecast": ["Statistiques appliquées"],
    "Qualité des données": ["SQL"],
}

# Questions techniques que l'on entend reellement, par competence.
QUESTIONS_PAR_COMPETENCE: dict[str, list[str]] = {
    "SQL": [
        "Écrivez une requête qui rend, par client, sa dernière commande.",
        "Quelle différence entre WHERE et HAVING ?",
        "Cette requête est lente : comment vous vous y prenez ?",
    ],
    "DAX": [
        "Expliquez le contexte de filtre à quelqu'un qui débute.",
        "À quoi sert CALCULATE ?",
        "Mesure ou colonne calculée ? Sur quel critère vous choisissez ?",
    ],
    "Power BI": [
        "Comment structurez-vous un modèle Power BI sur un nouveau projet ?",
        "Un rapport met 30 secondes à s'ouvrir. Par où vous commencez ?",
        "Comment gérez-vous les droits par utilisateur ?",
    ],
    "Power Query (langage M)": [
        "Qu'est-ce que le query folding, et comment savez-vous qu'il est actif ?",
    ],
    "Python": [
        "Comment traitez-vous un fichier trop gros pour tenir en mémoire ?",
        "Comment testez-vous un traitement de données ?",
    ],
    "Modèle en étoile (Kimball)": [
        "Comment définissez-vous le grain d'une table de faits ?",
        "Comment gérez-vous un changement d'attribut sur une dimension ?",
    ],
    "dbt": [
        "Quand passez-vous un modèle en incrémental ?",
        "Comment testez-vous vos transformations ?",
    ],
    "Airflow": [
        "Que se passe-t-il si une tâche est rejouée deux fois ?",
        "Comment gérez-vous un rattrapage sur un mois d'historique ?",
    ],
    "Spark / PySpark": [
        "Qu'est-ce qui déclenche un shuffle, et pourquoi c'est un problème ?",
    ],
    "Snowflake": [
        "Comment maîtrisez-vous le coût sur Snowflake ?",
    ],
    "BigQuery": [
        "Comment réduisez-vous le coût d'une requête BigQuery ?",
    ],
    "Scikit-learn": [
        "Comment évitez-vous la fuite de données dans un pipeline ?",
        "Quelle métrique choisissez-vous, et pourquoi celle-là ?",
    ],
    "Qualité des données": [
        "Comment détectez-vous qu'une donnée est fausse avant le métier ?",
    ],
    "Analyse métier": [
        "Le métier demande un indicateur qui n'a pas de sens. Vous faites quoi ?",
    ],
    "KPI et tableaux de bord": [
        "Comment savez-vous qu'un tableau de bord est utile ?",
    ],
    "Excel (Power Query, TCD, VBA)": [
        "À partir de quand Excel n'est plus le bon outil ?",
    ],
    "Tableau": [
        "Quand utilisez-vous une expression LOD ?",
    ],
}

# Questions de posture : elles tombent presque toujours, quel que soit le poste.
QUESTIONS_POSTURE: list[str] = [
    "Présentez-vous en deux minutes, en partant de ce qui vous rapproche de "
    "cette mission.",
    "Racontez un projet dont vous êtes fier, et votre part exacte dedans.",
    "Une fois où vous vous êtes trompé : qu'avez-vous fait ensuite ?",
    "Comment travaillez-vous avec un métier qui ne sait pas formuler son besoin ?",
    "Comment vous organisez-vous quand plusieurs demandes arrivent en même temps ?",
    "Qu'est-ce qui vous a intéressé dans cette mission en particulier ?",
]

# Questions propres au freelance : elles decident souvent de la suite.
QUESTIONS_FREELANCE: list[str] = [
    "Quel est votre TJM ?",
    "Quelle est votre disponibilité, et sur quel rythme ?",
    "Êtes-vous en cours de mission ? Jusqu'à quand ?",
    "Combien de jours sur site par semaine acceptez-vous ?",
    "Avez-vous déjà travaillé dans ce secteur ?",
    "Vous vous engagez sur quelle durée ?",
]

# Questions a poser : elles cadrent la mission et protegent le freelance.
A_POSER: list[str] = [
    "Qui sera mon interlocuteur au quotidien, et qui valide les livrables ?",
    "Quel est l'existant : outils en place, dette technique, documentation ?",
    "Qu'est-ce qui a été tenté avant moi, et pourquoi ça n'a pas suffi ?",
    "Comment saurez-vous, dans trois mois, que la mission est réussie ?",
    "Combien de personnes travaillent sur la donnée aujourd'hui ?",
    "Quel est le processus de décision quand un arbitrage technique est nécessaire ?",
    "Quelles sont les prochaines étapes du recrutement, et sous quel délai ?",
]


@dataclass
class Question:
    """Une question probable, et ce qui la rend probable."""

    texte: str
    origine: str = ""


@dataclass
class PointRevision:
    sujet: str
    points: list[str]
    raison: str = ""


@dataclass
class FicheEntretien:
    offer: JobOffer
    sujets_cles: list[tuple[str, float]] = field(default_factory=list)
    sujets_adjacents: list[str] = field(default_factory=list)
    questions_techniques: list[Question] = field(default_factory=list)
    questions_ecarts: list[Question] = field(default_factory=list)
    revisions: list[PointRevision] = field(default_factory=list)
    preuves: list[dict] = field(default_factory=list)
    vigilance: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
#  Construction
# --------------------------------------------------------------------------- #
SUJETS_RETENUS = 6        # au-dela, la veille d'un entretien, on ne revise plus
REVISIONS_RETENUES = 6


def sujets_cles(offer: JobOffer, profile: Profile) -> list[tuple[str, float]]:
    """Les competences que l'offre reclame vraiment, les mieux notees d'abord.

    On ne garde que les scores strictement positifs : une competence que
    l'annonce ne mentionne pas n'a pas a occuper une fiche de revision.
    """
    classees = classer_competences(offer, profile)
    return [(competence, score) for _, competence, score in classees if score > 0]


def adjacents(sujets: list[str]) -> list[str]:
    """Sujets impliques par ceux que l'annonce cite, sans doublon."""
    sortie: list[str] = []
    for sujet in sujets:
        for implique in ADJACENCES.get(sujet, []):
            if implique not in sujets and implique not in sortie:
                sortie.append(implique)
    return sortie


def _ecarts(offer: JobOffer) -> list[str]:
    """Competences demandees que le profil ne declare pas."""
    detail = offer.score_detail or {}
    return [str(s) for s in (detail.get("_missing_skills") or [])]


def _preuves(offer: JobOffer, profile: Profile, sujets: list[str]) -> list[dict]:
    """References du profil qui recoupent les sujets de l'offre.

    Une reference qui partage un outil avec l'annonce est une histoire prete a
    raconter ; c'est ce qu'un entretien technique attend a chaque question.
    """
    cibles = {s.lower() for s in sujets}
    retenues = []
    for ref in profile.references:
        stack = [str(t) for t in (ref.get("stack") or [])]
        communs = [t for t in stack
                   if any(c in t.lower() or t.lower() in c for c in cibles)]
        if communs:
            retenues.append({**ref, "communs": communs})
    return retenues


def _vigilance(offer: JobOffer, profile: Profile, cfg: Config,
               ecarts: list[str]) -> list[str]:
    """Les points ou l'entretien peut deraper, formules comme des rappels."""
    points: list[str] = []

    cible = profile.rate_target
    if cible:
        affiche = (f" L'annonce affiche {offer.daily_rate} €."
                   if offer.daily_rate else " L'annonce n'affiche aucun TJM.")
        points.append(
            f"TJM : annoncer {cible} €, plancher {profile.rate_floor} €.{affiche} "
            "Laisser le budget se dire en premier quand l'occasion se présente."
        )

    if ecarts:
        liste = ", ".join(ecarts[:5])
        points.append(
            f"Écarts assumés : {liste}. Dire ce qui est connu, ce qui ne l'est "
            "pas, et sur quoi s'appuyer pour monter vite. Une compétence "
            "surjouée se voit au premier cas pratique."
        )

    if offer.duration_months:
        points.append(
            f"Durée annoncée : {offer.duration_months:g} mois. Vérifier les "
            "conditions de renouvellement et le préavis."
        )

    points.append(
        "Cet outil n'envoie rien : vérifier que la candidature est bien partie "
        "avant de préparer l'entretien."
    )
    return points


def construire_fiche(offer: JobOffer, profile: Profile,
                     cfg: Config) -> FicheEntretien:
    """Assemble la fiche a partir de l'offre, du profil et de la config."""
    classes = sujets_cles(offer, profile)[:SUJETS_RETENUS]
    tetes = [nom for nom, _ in classes]
    implicites = adjacents(tetes)
    ecarts = _ecarts(offer)

    questions: list[Question] = []
    for nom in tetes + implicites:
        for texte in QUESTIONS_PAR_COMPETENCE.get(nom, []):
            questions.append(Question(texte=texte, origine=nom))

    questions_ecarts = [
        Question(texte=f"Quel est votre niveau sur {ecart} ?",
                 origine=f"{ecart} est demandé et absent du profil")
        for ecart in ecarts[:5]
    ]

    # Ordre de revision : les ecarts d'abord (c'est la qu'un entretien se
    # perd), puis ce que l'annonce cite, puis ce qu'elle implique.
    revisions: list[PointRevision] = []
    vus: set[str] = set()
    for sujets, raison in ((ecarts, "demandé, et absent du profil déclaré"),
                           (tetes, "cité par l'annonce"),
                           (implicites, "implicite : sera demandé malgré le silence de l'annonce")):
        for nom in sujets:
            points = REVISIONS.get(nom)
            if points and nom not in vus:
                vus.add(nom)
                revisions.append(PointRevision(sujet=nom, points=points, raison=raison))

    return FicheEntretien(
        offer=offer,
        sujets_cles=classes,
        sujets_adjacents=implicites,
        questions_techniques=questions,
        questions_ecarts=questions_ecarts,
        revisions=revisions[:REVISIONS_RETENUES],
        preuves=_preuves(offer, profile, tetes),
        vigilance=_vigilance(offer, profile, cfg, ecarts),
    )


# --------------------------------------------------------------------------- #
#  Rendu
# --------------------------------------------------------------------------- #
def rendre_markdown(fiche: FicheEntretien) -> str:
    """Rend la fiche en Markdown, dans l'ordre ou on la lit avant un entretien."""
    o = fiche.offer
    lignes: list[str] = [
        f"# Préparation d'entretien — {o.title}",
        "",
        f"**{o.company or 'Entreprise non précisée'}** · "
        f"{o.location or 'lieu non précisé'} · score {o.score:.0f}/100",
        "",
        f"[Voir l'annonce]({o.url})",
        "",
    ]

    if fiche.sujets_cles:
        lignes += ["## Ce que l'offre met sur la table", ""]
        lignes += [f"- **{nom}** _(pertinence {score:.1f})_"
                   for nom, score in fiche.sujets_cles]
        if fiche.sujets_adjacents:
            lignes += [
                "",
                "Sujets que l'annonce ne cite pas, mais qui seront demandés :",
                "",
            ]
            lignes += [f"- {nom}" for nom in fiche.sujets_adjacents]
        lignes.append("")

    if fiche.questions_ecarts:
        lignes += [
            "## À préparer en priorité — les écarts",
            "",
            "Ces compétences sont demandées et absentes de votre profil déclaré.",
            "Une réponse honnête et préparée passe ; une réponse improvisée se voit.",
            "",
        ]
        for q in fiche.questions_ecarts:
            lignes += [f"- {q.texte}", f"  _{q.origine}_"]
        lignes.append("")

    if fiche.questions_techniques:
        lignes += ["## Questions techniques probables", ""]
        courant = ""
        for q in fiche.questions_techniques:
            if q.origine != courant:
                if courant:
                    lignes.append("")
                courant = q.origine
                lignes.append(f"**{courant}**")
            lignes.append(f"- {q.texte}")
        lignes.append("")

    if fiche.revisions:
        lignes += ["## Points à réviser", ""]
        for rev in fiche.revisions:
            lignes += [f"### {rev.sujet}", f"_{rev.raison}_", ""]
            lignes += [f"- {point}" for point in rev.points]
            lignes.append("")

    if fiche.preuves:
        lignes += [
            "## Vos preuves",
            "",
            "Une référence qui partage un outil avec l'annonce est une histoire "
            "prête à raconter.",
            "Format : situation, ce que vous avez fait, résultat.",
            "",
        ]
        for ref in fiche.preuves:
            entete = f"- **{ref.get('client', 'Client')}** — {ref.get('role', '')}"
            if ref.get("period"):
                entete += f" _{ref['period']}_"
            lignes.append(entete)
            if ref.get("achievement"):
                # Les réalisations sont saisies sur plusieurs lignes dans le
                # profil : on les remet à plat pour ne pas casser la puce.
                plat = " ".join(str(ref["achievement"]).split())
                lignes.append(f"  - {plat}")
            lignes.append(f"  - En commun avec l'offre : {', '.join(ref['communs'])}")
        lignes.append("")

    lignes += ["## Questions de posture", ""]
    lignes += [f"- {q}" for q in QUESTIONS_POSTURE]
    lignes += ["", "## Questions freelance", ""]
    lignes += [f"- {q}" for q in QUESTIONS_FREELANCE]
    lignes += ["", "## À poser vous-même", "",
               "Ces questions cadrent la mission — et vous protègent.", ""]
    lignes += [f"- {q}" for q in A_POSER]

    if fiche.vigilance:
        lignes += ["", "## Points de vigilance", ""]
        lignes += [f"- {p}" for p in fiche.vigilance]

    lignes.append("")
    return "\n".join(lignes)
