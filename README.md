# freelance-radar

Veille automatisée d'**offres freelance Data** (France + remote) et génération de
**brouillons de candidature** adaptés à votre profil.

L'outil fait trois choses, dans cet ordre :

1. **Collecte** les annonces sur plusieurs sources (APIs publiques + scraping poli).
2. **Trie et note** chaque offre selon vos compétences, votre TJM et vos contraintes.
3. **Rédige un dossier de candidature** pour les offres qui passent le seuil.

> **Rien n'est envoyé automatiquement.** L'outil produit des fichiers à relire.
> Aucun formulaire n'est soumis, aucun mail n'est expédié : c'est un choix de
> conception, pas une limitation temporaire. Une candidature envoyée en votre nom
> sans relecture est un risque pour votre réputation, et l'envoi automatisé viole
> les conditions d'utilisation de la plupart des job boards.

---

## Installation

```bash
python -m venv .venv && .venv\Scripts\python -m pip install -e ".[web]"
```

Puis :

```bash
radar init
```

`radar init` crée `config/profile.yaml` (copie du modèle), `.env` et la base SQLite.

**Complétez ensuite `config/profile.yaml`** : compétences, TJM visé, disponibilité,
références. C'est ce fichier qui pilote le scoring *et* le contenu des lettres —
un profil vide donne des candidatures génériques et un classement sans valeur.

### Options

```bash
pip install -e ".[llm]"   # rédaction des lettres par Claude (sinon : templates)
pip install -e ".[dev]"   # pytest + ruff
```

---

## Interface web

Une interface locale permet de tout faire sans terminal : parcourir et filtrer les
offres, lire le détail d'un score, générer un brouillon, changer un statut et
lancer une campagne.

**Le plus simple** : double-cliquez **`radar-web.bat`** à la racine du projet.
Il démarre l'interface et ouvre votre navigateur tout seul. Pour arrêter,
fermez la fenêtre noire.

En ligne de commande, l'équivalent est :

```bash
.\radar.bat web
```

Puis ouvrez **http://127.0.0.1:8000**. `Ctrl+C` arrête le serveur.

Le serveur écoute sur `127.0.0.1` : il n'est accessible que depuis votre machine.
C'est délibéré — l'interface expose votre profil, vos coordonnées et vos
candidatures. `--host 0.0.0.0` l'ouvre au réseau local, avec un avertissement
explicite au démarrage ; ne le faites qu'en connaissance de cause.

**Ce qu'elle permet :**

- **Annoter** chaque offre (interlocuteur, relance prévue, ce qui vous a plu).
- **Sélectionner** les offres intéressantes (★) et filtrer sur cette sélection.
- **Écarter** ce qui ne vous concerne pas — l'offre disparaît des listes.
- **Générer un brouillon** de candidature, et marquer « j'ai postulé » après envoi.
- **Suivre** vos candidatures dans un tableau dédié, avec accès aux documents.
- **Remplir le formulaire de l'employeur** de deux façons :
  - une **fiche** par offre, chaque champ avec un bouton de copie ;
  - un **favori de pré-remplissage** (onglet *Outils*) que vous glissez dans
    votre barre de favoris. Sur la page de candidature, un clic dépose vos
    informations dans les champs reconnus.

Le favori ne soumet jamais le formulaire, n'écrase aucun champ déjà rempli et
ignore les champs de mot de passe. Vos informations sont contenues dans le
favori lui-même — aucun appel réseau, rien ne remonte au serveur local. En
contrepartie, régénérez-le depuis l'onglet *Outils* après avoir modifié votre
profil.

Écarter **masque** l'offre, ça ne la supprime pas de la base. C'est délibéré : une
suppression réelle serait annulée à la campagne suivante, qui réinsérerait la même
annonce. La ligne reste donc comme mémoire de votre décision. De même, une campagne
n'écrase jamais vos notes ni votre sélection.

L'interface pilote exactement le même moteur que la CLI : `pipeline.runner` pour
les campagnes, `apply.generator` pour les brouillons, la même base SQLite. Rien
n'y est réimplémenté, et la règle du projet y tient aussi — **elle génère des
brouillons, elle n'envoie rien**.

Une campagne dure plusieurs minutes : elle tourne en tâche de fond et la page
affiche l'avancement, puis se recharge à la fin. Un seul run à la fois, pour ne
pas solliciter les mêmes sites deux fois en parallèle.

Installation des dépendances web (déjà faites si vous avez suivi le README) :

```bash
pip install -e ".[web]"
```

## Ouvrir l'outil

`freelance-radar` est un outil en ligne de commande : il n'y a pas de fenêtre à
lancer. Ouvrez un terminal **dans le dossier du projet** — dans l'Explorateur
Windows, clic droit sur le dossier → « Ouvrir dans le Terminal ».

Le raccourci `radar.bat` évite d'activer l'environnement virtuel :

```bash
.\radar.bat list --min-score 65
```

Sans argument, il affiche la liste des commandes. L'équivalent sans raccourci est
`.venv\Scripts\python -m freelance_radar.cli list --min-score 65`.

Pour une vue visuelle plutôt qu'un terminal, générez le rapport et ouvrez-le dans
votre navigateur :

```bash
.\radar.bat report -f html
```

Le fichier atterrit dans `output/rapport.html` — un double-clic suffit ensuite, et
chaque titre de mission renvoie à l'annonce d'origine.

## Utilisation

```bash
radar sources                          # quelles sources sont actives et prêtes
radar scrape                           # lance une campagne complète
radar scrape -s freework --explain     # une seule source, avec le détail des rejets
radar list --min-score 60              # les offres retenues, triées
radar show 8099059b                    # une offre + l'explication de son score
radar apply --all --min-score 65       # génère les brouillons des meilleures offres
radar apply 8099059b                   # génère pour une offre précise
radar track                            # tableau de suivi des candidatures
radar track 8099059b --status sent     # marque une candidature comme envoyée
radar report -f html                   # rapport HTML (aussi : csv, json)
radar stats                            # état de la base + historique des campagnes
```

Les identifiants d'offre sont acceptés sous forme de préfixe : `radar show 8099` suffit.

### Cycle de vie d'une candidature

```
new  →  drafted  →  sent  →  replied  →  interview  →  won
                                                    ↘  rejected
```

`radar apply` passe l'offre en `drafted`. Les transitions suivantes sont manuelles
(`radar track <id> --status ...`) : elles reflètent des faits que l'outil ne peut pas
observer.

---

## Structure du projet

```
freelance-radar/
├── config/
│   ├── config.yaml            # mots-clés, filtres, sources, pondérations du score
│   ├── profile.example.yaml   # modèle de profil (versionné)
│   └── profile.yaml           # VOTRE profil — gitignoré, contient vos données
├── src/freelance_radar/
│   ├── cli.py                 # commandes Typer (seule couche qui parle à l'humain)
│   ├── config.py              # chargement config.yaml + profile.yaml + .env
│   ├── models.py              # JobOffer, Application, statuts — le vocabulaire commun
│   ├── scrapers/
│   │   ├── base.py            # contrat commun + registre (@register)
│   │   ├── http.py            # SEUL point de sortie réseau : robots.txt, délais, cache
│   │   ├── jsonld.py          # extraction schema.org JobPosting (sources HTML)
│   │   ├── freework.py        # missions freelance FR  (HTML + JSON-LD)
│   │   ├── freelanceinfo.py   # missions freelance FR  (JSON-LD de liste)
│   │   ├── lesjeudis.py       # IT France             (HTML + JSON-LD)
│   │   ├── remote_boards.py   # Jobicy, Himalayas, Arbeitnow, Working Nomads
│   │   ├── remotive.py        # remote worldwide       (API publique)
│   │   ├── remoteok.py        # remote worldwide       (API publique)
│   │   ├── adzuna.py          # agrégateur             (API, clé requise, désactivé)
│   │   └── francetravail.py   # France Travail         (API officielle, clés requises)
│   ├── pipeline/
│   │   ├── normalize.py       # HTML→texte, TJM, durée, contrat, télétravail, mojibake
│   │   ├── enrich.py          # taxonomie de 66 compétences + noyau « data »
│   │   ├── filters.py         # pertinence, exclusions, déduplication
│   │   ├── score.py           # 5 signaux pondérés → note /100
│   │   └── runner.py          # orchestration d'une campagne
│   ├── apply/
│   │   ├── generator.py       # fabrique le dossier de candidature
│   │   ├── candidature.py     # fiche de champs pour les formulaires employeur
│   │   ├── cv.py              # compose la section compétences selon l'offre
│   │   └── llm.py             # rédaction par Claude (optionnelle, avec repli)
│   ├── storage/db.py          # SQLite : offres, candidatures, campagnes
│   ├── report/html.py         # exports HTML / CSV / JSON
│   └── templates/             # lettre, email, rapport (Jinja2)
│   ├── web/                   # interface web locale (FastAPI + Jinja2)
│   │   ├── app.py             # routes ; appelle le même moteur que la CLI
│   │   ├── bookmarklet.py     # favori de pré-remplissage de formulaire
│   │   ├── state.py           # suivi de la campagne lancée en tâche de fond
│   │   └── templates/         # pages HTML
├── radar.bat                  # raccourci CLI (sans activer le venv)
├── radar-web.bat              # double-clic : interface + navigateur
├── docs/cv-canva.md           # adapter le CV Canva : mode d'emploi et limites
├── output/applications/       # les brouillons générés
├── data/radar.db              # base SQLite
└── tests/                     # 196 tests, sans accès réseau
```

### Le flux, en une ligne

```
sources → normalisation → filtres + dédup → enrichissement → scoring → SQLite → candidature
```

Chaque étape est un module pur et testable. Les scrapers ne font qu'extraire ;
toute l'intelligence (nettoyage, filtres, notation) vit dans `pipeline/`, ce qui
permet d'ajouter une source sans toucher au reste.

---

## Ajouter une source

Trois choses : hériter de `BaseScraper`, décorer avec `@register`, activer dans
`config.yaml`.

```python
# src/freelance_radar/scrapers/ma_source.py
from .base import BaseScraper, register

@register
class MaSourceScraper(BaseScraper):
    name = "ma_source"
    label = "Ma Source"

    def fetch(self, keywords):
        html = self.client.get("https://exemple.fr/missions?q=data")
        # ... produire des JobOffer bruts ; le pipeline s'occupe du reste
        yield offre
```

Puis l'importer dans `scrapers/__init__.py` et ajouter dans `config.yaml` :

```yaml
  ma_source:
    enabled: true
    kind: html
```

Pour une source HTML, essayez d'abord `jsonld.find_job_posting()` : la plupart des
job boards exposent un bloc `schema.org/JobPosting` pour le référencement Google.
C'est nettement plus stable que des sélecteurs CSS, qui cassent à chaque refonte.

---

## Sources

| Source | Type | Clé | Couverture |
|---|---|---|---|
| **Adzuna** | API officielle | oui | Agrégateur multi-boards France — **le plus gros contributeur** |
| **Free-Work** | HTML + JSON-LD | non | Missions freelance IT en France |
| **Freelance-Informatique** | HTML + JSON-LD | non | Missions freelance IT France, toutes régions |
| **Mindquest** | Sitemap + JSON-LD | non | Missions freelance IT/finance, très Île-de-France |
| **France Travail** | API officielle | oui | Toute la France, y compris hors métropoles |
| **Jobicy** | API publique | non | Remote Europe/monde, filtrable par industrie |
| **Himalayas** | API publique | non | Remote worldwide |
| **Arbeitnow** | API publique | non | Remote Europe (volume important) |
| **Working Nomads** | API publique | non | Remote worldwide |
| **Remotive** | API publique | non | Remote worldwide |
| **Remote OK** | API publique | non | Remote worldwide |

Dix sources actives sans aucune configuration, hormis France Travail qui demande
des clés gratuites (voir plus bas).

Adzuna nécessite des clés gratuites ([inscription](https://developer.adzuna.com)) :
`ADZUNA_APP_ID` et `ADZUNA_APP_KEY` dans `.env`.

**LesJeudis** est livrée mais désactivée (~10 offres par campagne pour une vingtaine
de requêtes, essentiellement du salariat). Passer son `enabled` à `true` la réactive.

### Identifiants et journaux

Certaines API imposent leurs clés en **paramètres d'URL** (Adzuna), et le client
HTTP journalise l'URL complète : en mode `-v`, les clés s'affichaient en clair.
Un filtre de masquage est posé à la racine du logging (`secrets.py`) — il couvre
aussi les messages de httpx et d'uvicorn, et masque `app_key`, `client_secret`,
les jetons `Bearer` et les clés Anthropic.

En pratique, sur un profil Data français, **Free-Work fournit l'essentiel du
volume utile** ; les sources remote anglophones remontent surtout du CDI et du
`data entry`, que les filtres écartent.

### Activer France Travail

1. Créez un compte sur [francetravail.io](https://francetravail.io), puis une
   **application**.
2. **Abonnez cette application à l'API « Offres d'emploi v2 »** — c'est l'étape
   qu'on oublie : sans abonnement, le portail délivre bien un `client_id` mais
   l'authentification échoue avec `invalid_client`, ce qui ressemble à tort à un
   mauvais secret. Le message d'erreur de l'outil le rappelle.
3. Reportez les identifiants dans `.env` :

```
FRANCE_TRAVAIL_CLIENT_ID=votre_client_id
FRANCE_TRAVAIL_CLIENT_SECRET=votre_client_secret
```

4. Vérifiez : `radar sources` doit afficher `prêt = oui` sur la ligne France Travail.

Tant que les clés manquent, la source est simplement ignorée avec un
avertissement explicite — les autres sources continuent normalement.

**Réglages** (`config.yaml`, section `sources.francetravail`) :

| Clé | Défaut | Rôle |
|---|---|---|
| `type_contrat` | `LIB` | Code du référentiel. `LIB` = profession libérale, le seul qui corresponde vraiment au freelance. `""` pour tout voir, `"LIB,CDD"` pour élargir. |
| `queries` | `[data, donnees, decisionnel]` | Un terme **par requête** : l'API combine les mots-clés par un ET, donc `data,donnees` ne rend rien. `donnees` remonte presque le double de `data` — le catalogue est en français. |
| `departement` | absent | Restreint à un département (`"75"`) |
| `max_results` | `150` | Plafond imposé par l'API sur une pagination |

**À quoi s'attendre :** le catalogue France Travail est très majoritairement
salarié. Avec `type_contrat: LIB` le volume sera faible — quelques offres par
semaine — mais géographiquement complémentaire de Free-Work, qui est très
centré Île-de-France. Si vous préférez tout voir et laisser le pipeline trancher,
mettez `type_contrat: ""` : les CDI seront rejetés au filtrage, visible dans
`radar scrape --explain`.

Contraintes de l'API respectées par le client : 4 requêtes/seconde maximum,
`range` plafonné à 150 résultats, et `publieeDepuis` restreint aux valeurs
1, 3, 7, 14 et 31 — votre `max_age_days` est automatiquement arrondi à la valeur
autorisée supérieure.

### Mindquest

Pas de recherche exploitable sans JavaScript, mais un sitemap dédié aux missions
(`sitemap-missions.xml`) et un `JobPosting` JSON-LD sur chaque fiche. Le scraper
lit le sitemap, **filtre les URL sur leur slug** — qui porte l'intitulé du poste —
puis ne charge que les fiches retenues : sur 128 missions, une douzaine sont Data,
donc ce tri évite 90 % des téléchargements.

| Clé | Défaut | Rôle |
|---|---|---|
| `max_missions` | `40` | Plafond de fiches chargées, après filtrage sur le slug |

Deux particularités de la source, toutes deux gérées :

- `datePosted` est la date de **création** de la mission, pas de rafraîchissement :
  des annonces toujours actives affichent 2024, et `max_age_days` les rejetait
  toutes. Le `<lastmod>` du sitemap sert donc de date de publication.
- `baseSalary` est inexploitable (une fiche annonce 50 000 €/jour, en réalité un
  salaire annuel mal étiqueté) et `employmentType` vaut `FULL_TIME` sur une place
  de marché freelance. Le TJM se lit dans la description, le contrat est forcé.

### Ce que l'outil ne scrape pas

Malt, LinkedIn, Comet et Welcome to the Jungle ne sont pas intégrés : leurs
conditions d'utilisation interdisent l'extraction automatisée et leurs
protections anti-bot le confirment. We Work Remotely renvoie un `403` à tout
client automatisé — la source a donc été retirée plutôt que contournée.

### Politesse réseau

Tout le trafic passe par `scrapers/http.py`, qui applique systématiquement :

- **robots.txt** vérifié avant chaque domaine — **pour les sources HTML uniquement** ;
- **délai minimum** entre deux requêtes vers un même hôte (1,5 s par défaut) ;
- **cache disque** (60 min) — relancer une campagne ne re-sollicite pas les sites ;
- **retries** avec back-off exponentiel sur 429 et 5xx ;
- **User-Agent identifiable**, configurable via `RADAR_USER_AGENT`.

**Pourquoi robots.txt ne s'applique pas aux API.** robots.txt régit l'exploration
d'un site web ; il ne régit pas l'appel d'une API documentée qu'on est autorisé à
consommer. Les hôtes d'API servent d'ailleurs souvent un `Disallow: /` global —
`api.francetravail.io` le fait — parce qu'ils n'ont rien à faire indexer : appliquer
cette directive à un client sous contrat, à qui le portail vient de délivrer des
clés, est une erreur de catégorie. Chaque scraper porte donc un attribut
`respects_robots` : `True` pour Free-Work (scraping HTML réel), `False` pour les
sources `kind: api`, avec la justification en commentaire dans le code.

---

## Le scoring

Cinq signaux, pondérés dans `config.yaml`, ramenés sur 100 :

| Signal | Poids | Ce qu'il mesure |
|---|---|---|
| `skills_match` | 45 | Recouvrement offre/profil, pondéré par votre niveau déclaré (expert 1.0, avancé 0.75, familier 0.45) |
| `daily_rate` | 20 | TJM affiché vs votre objectif ; 0 sous votre plancher |
| `remote` | 15 | Compatibilité avec votre contrainte de présentiel |
| `freshness` | 10 | Décroissance linéaire jusqu'à `max_age_days` |
| `duration` | 10 | Durée de mission vs votre préférence |

Une information absente vaut **0,5** (neutre), jamais 0 : la plupart des annonces
n'affichent ni TJM ni durée, et les pénaliser reviendrait à ne classer que les
annonces bavardes.

`radar show <id>` affiche le détail signal par signal, plus les compétences
communes et les écarts.

### Termes de recherche vs termes de filtrage

Deux réglages distincts, et les confondre coûte cher :

- **`search.queries`** — ce qu'on **demande** aux moteurs des sources. À calquer
  sur votre profil : un radar qui n'interroge que `data` rate les annonces
  intitulées « Consultant Power BI » ou « Chef de projet décisionnel ».
- **`search.keywords_any`** — ce qu'on **garde** au retour. Volontairement plus
  large, pour ne pas jeter une bonne annonce au libellé inattendu.

Chaque source hérite de `search.queries` et peut le surcharger quand son
vocabulaire diffère (Remotive est anglophone, Remote OK filtre par slug interne).
Plusieurs API combinent les mots-clés par un **ET** : on envoie donc un terme par
requête, jamais une liste concaténée.

### Anti-bruit

Le mot « data » apparaît dans quantité d'annonces qui n'ont rien à voir. Trois
garde-fous :

1. **Le titre fait foi.** Un mot-clé trouvé uniquement dans la description ne
   suffit pas — il faut alors au moins 3 compétences techniques reconnues
   (`min_skills_without_title_match`).
2. **Liste d'exclusion** : `data entry`, `stage`, `alternance`, `data center`…
3. **Filtre géographique en deux temps** : `locations_exclude` évalué *avant* la
   liste blanche, car une annonce « Remote — LATAM only » contient le mot
   « remote » et passerait sinon.

4. **Compétences « data » vs compétences partagées.** Python, Java, Docker, CI/CD,
   AWS et SQL sont communs au data *et* au développement logiciel. Les compter
   dans la règle 1 laissait remonter des postes de dev dès que « data »
   apparaissait quelque part dans le texte. Seules les compétences de
   `CORE_DATA_SKILLS` (dbt, Airflow, Power BI, Data Modeling, MLOps…) ouvrent
   cette porte de secours.
5. **Métiers techniques voisins** exclus par le titre : `software engineer`,
   `qa engineer`, `full stack`, `frontend`, `backend`, `devops`, `site reliability`.

**Attention aux formats de lieu.** Chaque source écrit les localisations à sa
façon : Free-Work rend `Montreuil, Île-de-France, FR`, France Travail rend
`92 - Nanterre` — sans jamais nommer la région. Une liste blanche qui ne contient
que des noms de villes et de régions rejette donc silencieusement des missions
franciliennes valides. D'où les codes de département dans `locations`. Si vous
ajoutez une source, vérifiez ce format en premier : c'est le piège le plus
coûteux, parce qu'il ne produit aucune erreur, juste des offres manquantes.

Lancez `radar scrape --explain` pour voir le décompte des rejets par motif.

---

## La génération de candidature

`radar apply` crée un dossier par offre dans `output/applications/` :

```
065-lehibou-data-engineer-produits-data-infrastructure/
├── lettre.md      # lettre de motivation, en-tête factuel + corps rédigé
├── email.md       # version courte pour un envoi par mail (objet inclus)
├── offre.md       # l'annonce complète, pour la relecture hors ligne
├── offre.json     # les données brutes de l'offre
├── cv-adapte.md   # profil réécrit + compétences composées pour cette offre
└── checklist.md   # à vérifier avant envoi + points à préparer en entretien
```

Deux moteurs de rédaction :

- **`template`** (par défaut) — Jinja2, déterministe, hors ligne. Reprend votre
  pitch, vos deux références les plus pertinentes et l'angle correspondant à la
  famille de poste détectée (data engineer, analytics engineer, BI, data science…).
- **`llm`** — utilisé automatiquement si `ANTHROPIC_API_KEY` est défini et le SDK
  installé. Claude rédige l'accroche et le corps **à partir des seules références
  de votre profil**, avec consigne explicite de ne rien inventer et de lister dans
  `gaps` les compétences demandées qui vous manquent. En cas d'échec réseau, de
  clé absente ou de réponse inexploitable, le repli sur les templates est
  automatique — la commande ne casse jamais.

`radar apply --template` force le moteur déterministe.

Le TJM proposé est calculé, pas copié, selon `constraints.rate_strategy` :

- **`align`** (défaut) — si l'annonce affiche un TJM supérieur à votre objectif,
  on s'aligne dessus. Le client a lui-même fixé ce budget ; proposer moins ne rend
  pas la candidature plus compétitive, cela laisse de l'argent sur la table.
- **`target`** — ne jamais dépasser votre objectif.

Dans les deux cas, **jamais sous votre plancher déclaré**.

---

## Automatiser la veille

`scripts/veille.ps1` enchaîne campagne + rapport et peut être planifié :

```powershell
.\scripts\veille.ps1
```

Pour une exécution quotidienne à 8 h via le Planificateur de tâches Windows :

```powershell
schtasks /create /tn "freelance-radar" /tr "powershell -File C:\chemin\vers\scripts\veille.ps1" /sc daily /st 08:00
```

Le script ne génère **pas** les candidatures : la sélection reste un geste manuel.

---

## Développement

```bash
pytest              # 196 tests, aucun accès réseau
ruff check src tests
```

Les tests couvrent en priorité les endroits où ça casse en vrai : parsing du TJM
et des durées (faux positifs sur les codes postaux et les années d'expérience),
mojibake des sources mal encodées, déduplication inter-sources, round-trip SQLite
et bornes du TJM proposé.

### Points d'attention connus

- **`freework.py` dépend du HTML d'un tiers.** Le JSON-LD est stable, mais si
  Free-Work le retire, le repli `_parse_html` utilise des sélecteurs larges qui
  demanderont une mise à jour.
- **La taxonomie de compétences est explicite** (`pipeline/enrich.py`), pas
  apprise : ajoutez-y le vocabulaire de votre spécialité, c'est le levier le plus
  direct sur la qualité du scoring.
- **`config/profile.yaml` contient vos données personnelles** et est gitignoré.
  Ne le commitez pas.

---

## Licence et usage

Outil de veille personnelle. Chaque source reste soumise à ses propres conditions
d'utilisation : Remote OK, par exemple, demande que le lien vers l'annonce
d'origine soit conservé — ce que fait le rapport HTML. Respectez les volumes
raisonnables : les réglages par défaut de `http:` sont là pour ça.
