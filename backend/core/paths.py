"""项目与后端根路径；模块搬迁不改变配置、数据和模型的位置。"""
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
PLAN_DB_PATH = BACKEND_ROOT / "data" / "plan_db.json"
