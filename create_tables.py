from database import engine, Base
from models.article import Article

def create_all_tables():
    try:
        Base.metadata.create_all(bind=engine)
        print("数据表创建完成！表名：articles")
    except Exception as e:
        print("建表出错：", repr(e))

if __name__ == "__main__":
    create_all_tables()
