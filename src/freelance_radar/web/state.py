"""Etat partage des campagnes lancees depuis l'interface.

Une campagne dure plusieurs minutes : elle tourne donc en tache de fond et
l'interface interroge cet objet pour afficher l'avancement. Un seul run a la
fois — en lancer deux en parallele solliciterait les memes sites deux fois.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class CampaignState:
    running: bool = False
    step: str = ""
    detail: str = ""
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str = ""
    result: dict[str, Any] = field(default_factory=dict)

    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def start(self) -> bool:
        """Rend False si une campagne est deja en cours."""
        with self._lock:
            if self.running:
                return False
            self.running = True
            self.step = "demarrage"
            self.detail = ""
            self.error = ""
            self.result = {}
            self.started_at = datetime.now()
            self.finished_at = None
            return True

    def progress(self, step: str, detail: str = "") -> None:
        with self._lock:
            self.step, self.detail = step, detail

    def finish(self, result: dict[str, Any] | None = None, error: str = "") -> None:
        with self._lock:
            self.running = False
            self.step = "termine" if not error else "erreur"
            self.detail = ""
            self.error = error
            self.result = result or {}
            self.finished_at = datetime.now()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "running": self.running,
                "step": self.step,
                "detail": self.detail,
                "error": self.error,
                "result": dict(self.result),
                "started_at": self.started_at.isoformat() if self.started_at else None,
                "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            }
