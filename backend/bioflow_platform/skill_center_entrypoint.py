"""Use the existing total-system session for the native Skill workspace."""
import os
from pathlib import Path
from starlette.responses import JSONResponse
from . import auth
from .app import app as total_app,store
from .skill_center_gateway import IntegratedApp
from .skill_center_access import CenterAccess


def authenticated(request):
    user=auth.session_user(store,request.cookies.get(auth.COOKIE))
    if not user:return JSONResponse({"detail":"请先在总系统登录"},status_code=401)
    if user.get("role") not in {"developer","user"}:return JSONResponse({"detail":"账户角色无效"},status_code=403)
    return user


if os.getenv("BIOFLOW_SKILL_CENTER_ENABLED")=="1":
    root=Path(__file__).resolve().parents[2]
    frontend=Path(os.getenv("BIOFLOW_SKILL_CENTER_FRONTEND",root/"frontend/skill-center-native-dist"))
    if not (frontend/".vite/manifest.json").is_file():raise RuntimeError("Native Skill center frontend is not built")
    access=CenterAccess(os.getenv("BIOFLOW_SKILL_CENTER_DATABASE","/home/xh/BioFLow/skill_center/storage/database/center.sqlite"),
                        os.getenv("BIOFLOW_SKILL_CENTER_OWNERSHIP",root/"runtime/skill-center-accounts.sqlite3"))
    app=IntegratedApp(total_app,authenticated,access,frontend,os.getenv("BIOFLOW_SKILL_CENTER_URL","http://127.0.0.1:18120"))
else:app=total_app
