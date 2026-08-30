# Adapter le CV Canva à une offre

`radar apply` produit deux fichiers dans le dossier de candidature :

- **`cv-adapte.md`** — lisible : le paragraphe de profil réécrit et l'ordre des
  rubriques de compétences pour cette offre.
- **`cv-canva.json`** — le même contenu, découpé exactement comme les zones de
  texte du CV Canva, prêt à être appliqué.

Seules deux choses changent : **l'ordre des rubriques** de compétences et le
**paragraphe de profil**. Les expériences, les chiffres et les dates ne sont
jamais touchés — ce sont des faits.

## Relever la cartographie de votre CV

À renseigner une fois dans `config/profile.yaml`, section `documents.canva_cv` :

| Clé | Ce que c'est |
|---|---|
| `design_id` | L'identifiant du design (commence par `DA`) |
| `page` | Le `locator_id` de la page (commence par `PB`) |
| `element_profil` | L'élément texte du paragraphe PROFIL |
| `element_competences` | L'élément texte du bloc COMPÉTENCES |

## État de l'automatisation : limite constatée

**L'application automatique dans Canva ne fonctionne pas correctement**, et c'est
une limite de l'API, pas du projet.

L'opération `find_and_replace_text` **ne préserve pas la mise en forme des zones**.
Le bloc COMPÉTENCES alterne des intitulés en gras et des valeurs en normal ;
après un simple remplacement, la valeur fusionne avec l'intitulé qui la précède
et hérite du gras :

```
avant : "Data & Programmation : "        (gras)
        "Python · SQL · MongoDB · R"     (normal)

après : "Data & Programmation : Tableau · Power BI · Excel"   (tout en gras)
```

Testé isolément, sur un remplacement unique : le comportement est systématique.
Le paragraphe de profil subit le même sort — il ressort intégralement en gras.

Un CV dont toute la section compétences passe en gras n'est pas envoyable. Les
modifications ont donc été annulées, jamais enregistrées.

### Ce qui marche aujourd'hui

Ouvrir `cv-adapte.md`, dupliquer le CV dans Canva, et coller les deux blocs à la
main. Le collage dans les zones existantes conserve leur mise en forme. Compter
une minute par candidature — le travail de réflexion, lui, est déjà fait.

### La piste propre

Canva dispose d'un mécanisme prévu pour ça : les **champs d'autofill**, qui
remplacent du contenu en conservant le style de chaque champ. Le CV n'en déclare
aucun aujourd'hui (`get-design-dataset` rend un objet vide). Les définir une fois
sur le design — un champ pour le profil, un par rubrique — rendrait l'application
automatique et sans perte de mise en forme.
