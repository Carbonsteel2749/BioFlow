"""Independent gateway for the approved library update while analysis keeps running."""
from pathlib import Path
import importlib.util
from fastapi.responses import Response
from fastapi.routing import APIRoute
from backend.bioflow_platform.app import app as total_app
if importlib.util.find_spec('backend.bioflow_platform.skill_center_entrypoint'):
    from backend.bioflow_platform.skill_center_entrypoint import app
else:
    app=total_app

ROOT=Path(__file__).resolve().parents[1]


def javascript():
    original=(ROOT/'frontend/project-space.js').read_text()
    base=original.split('/* Row-based literature library and retrieval/QA workspace. */',1)[0]
    return Response(base+(ROOT/'frontend/public-literature.js').read_text(),media_type='text/javascript',headers={'Cache-Control':'no-store'})


def stylesheet():
    return Response((ROOT/'frontend/project-space.css').read_text()+(ROOT/'frontend/public-literature.css').read_text(),media_type='text/css',headers={'Cache-Control':'no-store'})


def application_javascript():
    content=(ROOT/'frontend/app.js').read_text().replace("!['home','settings','models'].includes(state.page)","!['home','settings','models','literature','personal-library'].includes(state.page)")
    content=content.replace('AbortSignal.timeout(12000)',"AbortSignal.timeout(path.startsWith('/api/public-literature/')?45000:12000)")
    return Response(content,media_type='text/javascript',headers={'Cache-Control':'no-store'})


# Insert before the generic /static mount; all other UI and service routes stay the same.
total_app.router.routes.insert(0,APIRoute('/static/project-space.js',javascript,methods=['GET']))
total_app.router.routes.insert(0,APIRoute('/static/project-space.css',stylesheet,methods=['GET']))
total_app.router.routes.insert(0,APIRoute('/static/app.js',application_javascript,methods=['GET']))
