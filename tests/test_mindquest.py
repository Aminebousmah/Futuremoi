"""Mindquest : lecture du sitemap et pieges de la source.

Les tests portent sur les fonctions pures du scraper ; le reseau n'est jamais
sollicite. Le fragment de sitemap reproduit la structure reelle, y compris ses
deux entrees non-missions (page de liste et filtre a categorie).
"""

from __future__ import annotations

from freelance_radar.scrapers.mindquest import _intitule, lire_sitemap

BASE = "https://mindquest.io/fr/missions-freelance-offres-emploi-it-finance"

SITEMAP = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>{BASE}</loc>
    <lastmod>2026-08-31T14:16:00.995Z</lastmod>
  </url>
  <url>
    <loc>{BASE}?categories=Finance</loc>
    <lastmod>2026-08-31T14:16:00.995Z</lastmod>
  </url>
  <url>
    <loc>{BASE}/79770/business-analyst-it-senior-hf-13</loc>
    <lastmod>2026-03-11T09:00:00.000Z</lastmod>
  </url>
  <url>
    <loc>{BASE}/94162/database-administrator-expert-hf-78</loc>
    <lastmod>2026-08-27T15:14:00.597Z</lastmod>
  </url>
  <url>
    <loc>{BASE}/93001/data-engineer-dataiku-hf-79</loc>
  </url>
</urlset>
"""


class TestLectureSitemap:
    def test_ne_retient_que_les_fiches_de_mission(self):
        """La page de liste et le filtre a categorie n'ont pas de segment /id/."""
        urls = [u for u, _ in lire_sitemap(SITEMAP)]
        assert len(urls) == 3
        assert all("/79770/" in u or "/94162/" in u or "/93001/" in u for u in urls)

    def test_rend_le_lastmod(self):
        entrees = dict(lire_sitemap(SITEMAP))
        assert entrees[f"{BASE}/94162/database-administrator-expert-hf-78"] == (
            "2026-08-27T15:14:00.597Z"
        )

    def test_lastmod_absent_rend_une_chaine_vide(self):
        """Sans date, l'entree reste lisible et passe en fin de tri."""
        entrees = dict(lire_sitemap(SITEMAP))
        assert entrees[f"{BASE}/93001/data-engineer-dataiku-hf-79"] == ""

    def test_sitemap_vide(self):
        assert lire_sitemap("<urlset></urlset>") == []

    def test_tri_par_lastmod_decroissant(self):
        """Le sitemap est chronologique croissant : le scraper doit l'inverser."""
        entrees = lire_sitemap(SITEMAP)
        entrees.sort(key=lambda e: e[1], reverse=True)
        assert "/94162/" in entrees[0][0]      # aout 2026
        assert "/93001/" in entrees[-1][0]     # sans date


class TestIntitule:
    def test_slug_en_texte_lisible(self):
        url = f"{BASE}/93001/data-engineer-dataiku-hf-79"
        assert _intitule(url) == "data engineer dataiku hf 79"

    def test_ignore_la_barre_finale(self):
        url = f"{BASE}/93001/tech-lead-big-data-hf-92/"
        assert _intitule(url) == "tech lead big data hf 92"


class TestPreFiltrageParSlug:
    """Le tri sur l'intitule evite 90 % des telechargements."""

    def test_reconnait_les_missions_data(self):
        from freelance_radar.pipeline.normalize import contains_any

        mots = ["data", "bi", "business intelligence", "analytics", "etl"]
        for slug in ("data-engineer-dataiku-hf-79", "tech-lead-big-data-hf-92",
                     "consultant-senior-fonctionnel-epm-bi-data-hf",
                     "developpeur-etl-informatica-cloud-hf-75"):
            assert contains_any(_intitule(f"{BASE}/1/{slug}"), mots), slug

    def test_ecarte_les_missions_hors_sujet(self):
        from freelance_radar.pipeline.normalize import contains_any

        mots = ["data", "bi", "business intelligence", "analytics", "etl"]
        for slug in ("expert-endpoint-mw-brussels", "standard-manager-hf-75",
                     "responsable-d-exploitation-hf"):
            assert not contains_any(_intitule(f"{BASE}/1/{slug}"), mots), slug
