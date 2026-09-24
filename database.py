from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
import yaml

# 读取配置
with open("/home/xh/BioFLow/config.yaml", "r", encoding="utf-8") as f:
    cfg = yaml.safe_load(f)
db_cfg = cfg["database"]

# 拼接数据库连接URL
DB_URL = f"postgresql+psycopg2://{db_cfg['user']}:{db_cfg['password']}@{db_cfg['host']}:{db_cfg['port']}/{db_cfg['db_name']}"

# 创建引擎
engine = create_engine(DB_URL, pool_pre_ping=True)
# 会话工厂
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
# ORM基类
Base = declarative_base()

# 获取数据库会话依赖（给FastAPI用）
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
