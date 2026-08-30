"""Couche de persistance (SQLite, sans ORM).

Le volume attendu se compte en milliers de lignes : sqlite3 de la stdlib suffit
et evite une dependance de plus. Les colonnes structurees (competences, detail
du score) sont stockees en JSON.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..models import Application, ApplicationStatus, ContractType, JobOffer, RemotePolicy

SCHEMA = """
CREATE TABLE IF NOT EXISTS offers (
    id              TEXT PRIMARY KEY,
    source          TEXT NOT NULL,
    source_id       TEXT,
    url             TEXT,
    title           TEXT NOT NULL,
    company         TEXT,
    description     TEXT,
    location        TEXT,
    remote          TEXT,
    contract        TEXT,
    daily_rate_min  INTEGER,
    daily_rate_max  INTEGER,
    duration_months REAL,
    start_date      TEXT,
    skills          TEXT,
    published_at    TEXT,
    scraped_at      TEXT,
    first_seen_at   TEXT,
    score           REAL DEFAULT 0,
    score_detail    TEXT,
    status          TEXT DEFAULT 'new',
    notes           TEXT DEFAULT '',
    starred         INTEGER DEFAULT 0,
    discarded       INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_offers_score  ON offers(score DESC);
CREATE INDEX IF NOT EXISTS idx_offers_status ON offers(status);
CREATE INDEX IF NOT EXISTS idx_offers_source ON offers(source);

CREATE TABLE IF NOT EXISTS applications (
    offer_id      TEXT PRIMARY KEY REFERENCES offers(id) ON DELETE CASCADE,
    status        TEXT NOT NULL,
    subject       TEXT,
    cover_letter  TEXT,
    email_body    TEXT,
    highlights    TEXT,
    gaps          TEXT,
    proposed_rate INTEGER,
    generator     TEXT,
    file_path     TEXT,
    created_at    TEXT,
    sent_at       TEXT,
    notes         TEXT
);

CREATE TABLE IF NOT EXISTS runs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT,
    sources    TEXT,
    fetched    INTEGER,
    kept       INTEGER,
    new_offers INTEGER,
    detail     TEXT
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        """Ajoute les colonnes apparues apres coup.

        `CREATE TABLE IF NOT EXISTS` ne touche pas une table deja creee : sans
        ceci, une base existante resterait sans les colonnes d'annotation.
        """
        existantes = {r["name"] for r in self.conn.execute("PRAGMA table_info(offers)")}
        for colonne, definition in (
            ("notes", "TEXT DEFAULT ''"),
            ("starred", "INTEGER DEFAULT 0"),
            ("discarded", "INTEGER DEFAULT 0"),
        ):
            if colonne not in existantes:
                self.conn.execute(f"ALTER TABLE offers ADD COLUMN {colonne} {definition}")

    # ------------------------------------------------------------------ #
    #  Offres
    # ------------------------------------------------------------------ #
    def upsert_offers(self, offers: list[JobOffer]) -> tuple[int, int]:
        """Insere ou met a jour. Rend (nouvelles, mises a jour).

        Une offre deja connue conserve son `status` et son `first_seen_at` : on
        ne veut pas repasser en 'new' une offre deja traitee lors d'une campagne
        precedente.
        """
        new_count = updated = 0
        for offer in offers:
            existing = self.conn.execute(
                "SELECT status, first_seen_at, notes, starred, discarded "
                "FROM offers WHERE id = ?", (offer.id,)
            ).fetchone()
            row = self._offer_to_row(offer)
            if existing:
                row["status"] = existing["status"]
                row["first_seen_at"] = existing["first_seen_at"]
                # Les annotations sont le travail de l'utilisateur : une
                # campagne ne doit jamais les ecraser, ni ressusciter une offre
                # ecartee a la main.
                row["notes"] = existing["notes"] or ""
                row["starred"] = existing["starred"] or 0
                row["discarded"] = existing["discarded"] or 0
                # On repercute le statut conserve sur l'objet en memoire : sinon
                # le recapitulatif affiche apres une campagne montrerait "new"
                # pour des offres deja traitees.
                offer.status = ApplicationStatus(existing["status"])
                updated += 1
            else:
                row["first_seen_at"] = _now()
                row["notes"], row["starred"], row["discarded"] = "", 0, 0
                new_count += 1
            columns = ", ".join(row)
            placeholders = ", ".join(f":{k}" for k in row)
            self.conn.execute(
                f"INSERT OR REPLACE INTO offers ({columns}) VALUES ({placeholders})", row
            )
        self.conn.commit()
        return new_count, updated

    def list_offers(
        self,
        *,
        min_score: float = 0,
        status: str | None = None,
        source: str | None = None,
        limit: int = 50,
        new_only: bool = False,
        starred_only: bool = False,
        include_discarded: bool = False,
    ) -> list[JobOffer]:
        sql = "SELECT * FROM offers WHERE score >= ?"
        params: list[Any] = [min_score]
        if not include_discarded:
            sql += " AND COALESCE(discarded, 0) = 0"
        if starred_only:
            sql += " AND COALESCE(starred, 0) = 1"
        if status:
            sql += " AND status = ?"
            params.append(status)
        if source:
            sql += " AND source = ?"
            params.append(source)
        if new_only:
            sql += " AND status = 'new'"
        sql += " ORDER BY score DESC, published_at DESC LIMIT ?"
        params.append(limit)
        return [self._row_to_offer(r) for r in self.conn.execute(sql, params)]

    def get_offer(self, offer_id: str) -> JobOffer | None:
        row = self.conn.execute("SELECT * FROM offers WHERE id = ?", (offer_id,)).fetchone()
        if row is None:
            # tolerance sur un prefixe d'identifiant (confort en CLI)
            row = self.conn.execute(
                "SELECT * FROM offers WHERE id LIKE ? LIMIT 2", (f"{offer_id}%",)
            ).fetchone()
        return self._row_to_offer(row) if row else None

    def set_status(self, offer_id: str, status: ApplicationStatus) -> bool:
        cur = self.conn.execute(
            "UPDATE offers SET status = ? WHERE id = ?", (status.value, offer_id)
        )
        self.conn.execute(
            "UPDATE applications SET status = ?, sent_at = COALESCE(sent_at, ?) "
            "WHERE offer_id = ? AND ? = 'sent'",
            (status.value, _now(), offer_id, status.value),
        )
        self.conn.commit()
        return cur.rowcount > 0

    # ------------------------------------------------------------------ #
    #  Annotations
    # ------------------------------------------------------------------ #
    def set_note(self, offer_id: str, note: str) -> bool:
        cur = self.conn.execute("UPDATE offers SET notes = ? WHERE id = ?", (note, offer_id))
        self.conn.commit()
        return cur.rowcount > 0

    def toggle_star(self, offer_id: str) -> bool | None:
        """Bascule la selection. Rend le nouvel etat, ou None si l'offre est inconnue."""
        row = self.conn.execute(
            "SELECT COALESCE(starred, 0) AS s FROM offers WHERE id = ?", (offer_id,)
        ).fetchone()
        if row is None:
            return None
        nouveau = 0 if row["s"] else 1
        self.conn.execute("UPDATE offers SET starred = ? WHERE id = ?", (nouveau, offer_id))
        self.conn.commit()
        return bool(nouveau)

    def discard(self, offer_id: str, discarded: bool = True) -> bool:
        """Ecarte une offre sans la supprimer.

        Une suppression reelle serait annulee a la campagne suivante : le
        scraper reinsererait la meme annonce. On garde donc la ligne comme
        memoire de la decision, et on la masque partout.
        """
        cur = self.conn.execute(
            "UPDATE offers SET discarded = ? WHERE id = ?", (1 if discarded else 0, offer_id)
        )
        self.conn.commit()
        return cur.rowcount > 0

    def counts_by_status(self) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT status, COUNT(*) AS n FROM offers "
            "WHERE COALESCE(discarded, 0) = 0 GROUP BY status"
        ).fetchall()
        return {r["status"]: r["n"] for r in rows}

    def counts_by_source(self) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT source, COUNT(*) AS n FROM offers "
            "WHERE COALESCE(discarded, 0) = 0 GROUP BY source ORDER BY n DESC"
        ).fetchall()
        return {r["source"]: r["n"] for r in rows}

    # ------------------------------------------------------------------ #
    #  Candidatures
    # ------------------------------------------------------------------ #
    def save_application(self, app: Application) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO applications
               (offer_id, status, subject, cover_letter, email_body, highlights, gaps,
                proposed_rate, generator, file_path, created_at, sent_at, notes)
               VALUES (:offer_id, :status, :subject, :cover_letter, :email_body,
                       :highlights, :gaps, :proposed_rate, :generator, :file_path,
                       :created_at, :sent_at, :notes)""",
            {
                "offer_id": app.offer_id,
                "status": app.status.value,
                "subject": app.subject,
                "cover_letter": app.cover_letter,
                "email_body": app.email_body,
                "highlights": json.dumps(app.highlights, ensure_ascii=False),
                "gaps": json.dumps(app.gaps, ensure_ascii=False),
                "proposed_rate": app.proposed_rate,
                "generator": app.generator,
                "file_path": app.file_path,
                "created_at": app.created_at.isoformat(),
                "sent_at": app.sent_at.isoformat() if app.sent_at else None,
                "notes": app.notes,
            },
        )
        self.conn.execute(
            "UPDATE offers SET status = ? WHERE id = ? AND status = 'new'",
            (app.status.value, app.offer_id),
        )
        self.conn.commit()

    def get_application(self, offer_id: str) -> Application | None:
        row = self.conn.execute(
            "SELECT * FROM applications WHERE offer_id = ?", (offer_id,)
        ).fetchone()
        if not row:
            return None
        return Application(
            offer_id=row["offer_id"],
            status=ApplicationStatus(row["status"]),
            subject=row["subject"] or "",
            cover_letter=row["cover_letter"] or "",
            email_body=row["email_body"] or "",
            highlights=json.loads(row["highlights"] or "[]"),
            gaps=json.loads(row["gaps"] or "[]"),
            proposed_rate=row["proposed_rate"],
            generator=row["generator"] or "template",
            file_path=row["file_path"] or "",
            created_at=datetime.fromisoformat(row["created_at"]),
            sent_at=datetime.fromisoformat(row["sent_at"]) if row["sent_at"] else None,
            notes=row["notes"] or "",
        )

    def pipeline(self) -> list[tuple[JobOffer, Application]]:
        """Toutes les offres ayant une candidature, pour le suivi (`radar track`)."""
        rows = self.conn.execute(
            """SELECT o.* FROM offers o
               JOIN applications a ON a.offer_id = o.id
               ORDER BY CASE o.status
                          WHEN 'interview' THEN 0 WHEN 'replied' THEN 1
                          WHEN 'sent' THEN 2 WHEN 'drafted' THEN 3 ELSE 4 END,
                        o.score DESC"""
        ).fetchall()
        out = []
        for row in rows:
            offer = self._row_to_offer(row)
            app = self.get_application(offer.id)
            if app:
                out.append((offer, app))
        return out

    # ------------------------------------------------------------------ #
    #  Campagnes
    # ------------------------------------------------------------------ #
    def log_run(self, sources: list[str], fetched: int, kept: int,
                new_offers: int, detail: str = "") -> None:
        self.conn.execute(
            "INSERT INTO runs (started_at, sources, fetched, kept, new_offers, detail) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (_now(), ",".join(sources), fetched, kept, new_offers, detail),
        )
        self.conn.commit()

    def last_runs(self, limit: int = 10) -> list[sqlite3.Row]:
        return list(self.conn.execute(
            "SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)
        ))

    # ------------------------------------------------------------------ #
    #  Conversions
    # ------------------------------------------------------------------ #
    @staticmethod
    def _offer_to_row(offer: JobOffer) -> dict[str, Any]:
        return {
            "id": offer.id,
            "source": offer.source,
            "source_id": offer.source_id,
            "url": offer.url,
            "title": offer.title,
            "company": offer.company,
            "description": offer.description,
            "location": offer.location,
            "remote": offer.remote.value,
            "contract": offer.contract.value,
            "daily_rate_min": offer.daily_rate_min,
            "daily_rate_max": offer.daily_rate_max,
            "duration_months": offer.duration_months,
            "start_date": offer.start_date.isoformat() if offer.start_date else None,
            "skills": json.dumps(offer.skills, ensure_ascii=False),
            "published_at": offer.published_at.isoformat() if offer.published_at else None,
            "scraped_at": offer.scraped_at.isoformat(),
            "first_seen_at": _now(),
            "score": offer.score,
            "score_detail": json.dumps(offer.score_detail, ensure_ascii=False, default=str),
            "status": offer.status.value,
            "notes": "",
            "starred": 0,
            "discarded": 0,
        }

    @staticmethod
    def _row_to_offer(row: sqlite3.Row) -> JobOffer:
        from datetime import date as _date

        return JobOffer(
            id=row["id"],
            source=row["source"],
            source_id=row["source_id"] or "",
            url=row["url"] or "",
            title=row["title"],
            company=row["company"] or "",
            description=row["description"] or "",
            location=row["location"] or "",
            remote=RemotePolicy(row["remote"] or "unknown"),
            contract=ContractType(row["contract"] or "unknown"),
            daily_rate_min=row["daily_rate_min"],
            daily_rate_max=row["daily_rate_max"],
            duration_months=row["duration_months"],
            start_date=_date.fromisoformat(row["start_date"]) if row["start_date"] else None,
            skills=json.loads(row["skills"] or "[]"),
            published_at=(datetime.fromisoformat(row["published_at"])
                          if row["published_at"] else None),
            scraped_at=(datetime.fromisoformat(row["scraped_at"])
                        if row["scraped_at"] else datetime.now(timezone.utc)),
            score=row["score"] or 0.0,
            score_detail=json.loads(row["score_detail"] or "{}"),
            status=ApplicationStatus(row["status"] or "new"),
            notes=row["notes"] or "",
            starred=bool(row["starred"]),
            discarded=bool(row["discarded"]),
        )

    # ------------------------------------------------------------------ #
    def close(self) -> None:
        self.conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def __enter__(self) -> Database:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
