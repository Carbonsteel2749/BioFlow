"""Reuse writing business handlers, excluding the retired frontend routes."""
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'article_writing'))
from frontend.server import app

app.router.routes[:]=[r for r in app.router.routes if getattr(r,'path','').startswith('/api/')]
