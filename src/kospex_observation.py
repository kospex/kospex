"""
Observation dataclass for kospex
A database for the observations table structure defined in kospex_schema.py
"""
import json
from dataclasses import dataclass, asdict, field
from typing import Optional
from datetime import datetime, timezone
import uuid


@dataclass
class Observation:
    """
    Dataclass representing the observations table structure
    """
    # Mandatory fields
    hash: str
    file_path: str
    _repo_id: str
    observation_key: str

    uuid: str = field(default_factory=lambda: str(uuid.uuid4()))

    # Optional fields with defaults
    format: str = None
    data: str = None
    raw: str = None
    source: str = None
    observation_type: str = None
    line_number: Optional[int] = None
    command: Optional[str] = None
    latest: int = 1
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    _git_server: str = None
    _git_owner: str = None
    _git_repo: str = None

    def to_json(self):
        return json.dumps(asdict(self))

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        return cls(**data)

    def update_from_dict(self, data):
        for key, value in data.items():
            if hasattr(self, key):
                setattr(self, key, value)
