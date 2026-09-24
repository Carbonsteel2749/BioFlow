from crontab import CronTab
import os
from config import CRAWL_LOG_PATH


def set_crawl_cron():
    # 获取当前项目绝对路径
    project_path = "/home/xh/BioFLow"
    venv_python = f"{project_path}/.venv/bin/python"
    crawl_script = f"{project_path}/crawl_pubmed.py"
    # 定时命令：每天凌晨2点自动抓取5篇肠道菌群相关文献
    cmd = f"{venv_python} -u {crawl_script} 'gut microbiome autism' 5 >> {CRAWL_LOG_PATH} 2>&1"

    # 创建用户级定时任务
    cron = CronTab(user=True)
    # 清除旧的爬虫定时任务，避免重复
    for job in cron.find_comment("pubmed auto crawl"):
        cron.remove(job)
    # 创建新定时任务
    job = cron.new(command=cmd, comment="pubmed auto crawl")
    # 每天02:00执行
    job.setall("0 2 * * *")
    cron.write()
    print("定时任务配置完成：每日凌晨2点自动抓取PubMed文献，日志输出至crawl_log.txt")

def delete_crawl_cron():
    cron = CronTab(user=True)
    for job in cron.find_comment("pubmed auto crawl"):
        cron.remove(job)
    cron.write()
    print("已删除自动抓取定时任务")

if __name__ == "__main__":
    # 执行set_crawl_cron开启定时任务
    set_crawl_cron()
    # 如需删除定时任务，注释上行，启用下行
    # delete_crawl_cron()
