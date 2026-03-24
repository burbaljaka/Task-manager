"""Board scope: personal vs team Kanban."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BoardScope:
    """Personal board: team_id is None. Team board: team_id set (user must be a member)."""

    user_id: int
    team_id: int | None

    @property
    def is_team(self) -> bool:
        return self.team_id is not None
