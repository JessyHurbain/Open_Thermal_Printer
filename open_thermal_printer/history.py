"""Pile d'annulation : une suite d'instantanés du projet.

Chaque modification aboutie ajoute un instantané (le JSON de l'étiquette) ;
annuler revient à l'état précédent, rétablir avance dans la même suite. Comme les
états sont comparés sous forme de texte, une modification sans effet réel
n'encombre pas la pile.
"""

from __future__ import annotations

DEFAULT_LIMIT = 100  # états conservés : au-delà, les plus anciens sont oubliés


class History:
    """Instantanés d'un projet, avec annulation et rétablissement."""

    def __init__(self, state: str, limit: int = DEFAULT_LIMIT) -> None:
        self.limit = max(2, int(limit))
        self._states: list[str] = [state]
        self._index = 0

    @property
    def state(self) -> str:
        """État courant (celui que la pile considère comme affiché)."""
        return self._states[self._index]

    @property
    def can_undo(self) -> bool:
        return self._index > 0

    @property
    def can_redo(self) -> bool:
        return self._index < len(self._states) - 1

    def reset(self, state: str) -> None:
        """Repart d'un état unique : nouveau document, ouverture, modèle."""
        self._states = [state]
        self._index = 0

    def push(self, state: str) -> bool:
        """Ajoute un état ; renvoie False si c'est déjà l'état courant."""
        if state == self.state:
            return False
        del self._states[self._index + 1:]  # modifier abandonne ce qui était annulé
        self._states.append(state)
        if len(self._states) > self.limit:
            del self._states[0]
        self._index = len(self._states) - 1
        return True

    def undo(self) -> str | None:
        """État précédent, ou None si l'on est déjà au début."""
        if not self.can_undo:
            return None
        self._index -= 1
        return self.state

    def redo(self) -> str | None:
        """État suivant, ou None si l'on est déjà au bout."""
        if not self.can_redo:
            return None
        self._index += 1
        return self.state

    def __len__(self) -> int:
        return len(self._states)
