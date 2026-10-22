"""Configuration loading for the article repository scaffold."""
import os

# 定义全局Storage根目录
STORAGE_ROOT = "/home/xh/BioFLow/Article_repository/article_repository/storage"

# 划分storage下子目录
LOG_DIR = os.path.join(STORAGE_ROOT, "logs")
CACHE_DIR = os.path.join(STORAGE_ROOT, "cache")
RAW_DATA_DIR = os.path.join(STORAGE_ROOT, "raw_pubmed_data")

# 自动创建所有文件夹，不存在则新建
os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(CACHE_DIR, exist_ok=True)
os.makedirs(RAW_DATA_DIR, exist_ok=True)

# 日志完整路径常量
CRAWL_LOG_PATH = os.path.join(LOG_DIR, "crawl_log.txt")
API_LOG_PATH = os.path.join(LOG_DIR, "api.log")

# 数据库配置文件路径
DB_CONFIG_YAML = "config.yaml"
