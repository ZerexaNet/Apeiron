"""Qt translation loading (.qm produced from .ts by Qt Linguist tools).

The manager never *fabricates* a translation: when a ``.qm`` file is missing or
fails to load it reports ``translated=False`` and the UI keeps the English
source strings, which is the documented fallback behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from apeiron.core.logging import get_logger
from apeiron.i18n.languages import DEFAULT_LANGUAGE, resolve_language

log = get_logger("i18n")


@dataclass
class TranslationState:
    language: str
    translated: bool
    resource: str = ""
    missing: bool = False


class TranslationManager:
    """Installs/removes QTranslator instances on the running application."""

    def __init__(self, i18n_dir: Path) -> None:
        self.i18n_dir = Path(i18n_dir)
        self._current: object | None = None
        self.state = TranslationState(language=DEFAULT_LANGUAGE, translated=False)

    def resource_for(self, language: str) -> Path:
        return self.i18n_dir / f"apeiron_{language}.qm"

    def available(self) -> list[str]:
        if not self.i18n_dir.exists():
            return []
        return sorted(p.stem.removeprefix("apeiron_") for p in self.i18n_dir.glob("apeiron_*.qm"))

    def apply(self, setting: str, *, raw_locale: str | None = None) -> TranslationState:
        """Load the translation for ``setting`` (a settings value)."""
        from PySide6.QtCore import QCoreApplication, QTranslator

        language = resolve_language(setting, raw_locale)
        app = QCoreApplication.instance()
        if app is None:
            raise RuntimeError("a QCoreApplication is required before loading translations")

        if self._current is not None:
            app.removeTranslator(self._current)  # type: ignore[arg-type]
            self._current = None

        resource = self.resource_for(language)
        if not resource.exists():
            log.warning("translation missing for %s (%s); using source strings", language, resource)
            self.state = TranslationState(language=language, translated=False, missing=True)
            return self.state

        translator = QTranslator()
        if not translator.load(str(resource)):
            log.error("failed to load translation file %s", resource)
            self.state = TranslationState(language=language, translated=False, missing=True)
            return self.state

        app.installTranslator(translator)
        self._current = translator
        self.state = TranslationState(language=language, translated=True, resource=str(resource))
        log.info("translation loaded: %s", language)
        return self.state

    @property
    def language(self) -> str:
        return self.state.language
