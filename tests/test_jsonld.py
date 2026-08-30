"""Extraction schema.org : la strategie par defaut des sources HTML."""

from __future__ import annotations

from freelance_radar.models import ContractType
from freelance_radar.scrapers.jsonld import find_job_posting, offer_from_jsonld

PAGE = """
<html><head>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"WebPage","name":"Page"}
</script>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"JobPosting",
 "title":"Data Engineer Senior",
 "description":"<p>Mission <b>freelance</b> de 6 mois.</p><p>Stack : dbt, Snowflake.</p>",
 "datePosted":"2026-08-28T10:06:54+02:00",
 "employmentType":["CONTRACTOR"],
 "hiringOrganization":{"@type":"Organization","name":"ACME Conseil"},
 "jobLocation":{"@type":"Place","address":{"@type":"PostalAddress",
   "addressLocality":"Paris","addressRegion":"Ile-de-France","addressCountry":"FR"}},
 "baseSalary":{"@type":"MonetaryAmount","currency":"EUR",
   "value":{"@type":"QuantitativeValue","minValue":600,"maxValue":700,"unitText":"DAY"}}}
</script>
</head><body></body></html>
"""


class TestExtraction:
    def test_trouve_le_bloc_jobposting_parmi_les_autres(self):
        node = find_job_posting(PAGE)
        assert node is not None and node["title"] == "Data Engineer Senior"

    def test_conversion_complete(self):
        offre = offer_from_jsonld(find_job_posting(PAGE), source="test",
                                  url="https://example.com/x")
        assert offre.title == "Data Engineer Senior"
        assert offre.company == "ACME Conseil"
        assert offre.contract == ContractType.FREELANCE
        assert offre.location == "Paris, Ile-de-France, FR"
        assert (offre.daily_rate_min, offre.daily_rate_max) == (600, 700)
        assert offre.published_at.year == 2026
        # Le HTML de la description est aplati en texte lisible
        assert "<b>" not in offre.description and "freelance" in offre.description

    def test_ignore_un_salaire_non_journalier(self):
        page = PAGE.replace('"unitText":"DAY"', '"unitText":"YEAR"')
        offre = offer_from_jsonld(find_job_posting(page), source="t", url="u")
        assert offre.daily_rate_min is None

    def test_page_sans_jsonld(self):
        assert find_job_posting("<html><body>rien</body></html>") is None

    def test_json_invalide_ne_leve_pas(self):
        page = '<script type="application/ld+json">{ casse </script>'
        assert find_job_posting(page) is None

    def test_graph_aplati(self):
        page = ('<script type="application/ld+json">'
                '{"@graph":[{"@type":"JobPosting","title":"Data Analyst"}]}</script>')
        assert find_job_posting(page)["title"] == "Data Analyst"
