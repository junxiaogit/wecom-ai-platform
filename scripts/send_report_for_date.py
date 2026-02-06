"""手动发送指定日期的日报"""
import os, sys
sys.stdout.reconfigure(encoding='utf-8')
os.chdir(r'd:\Coze\wecom-ai-platform')
sys.path.insert(0, r'd:\Coze\wecom-ai-platform')
from dotenv import load_dotenv
load_dotenv()

from datetime import datetime
from app.services.report_service import ReportService
from app.services.dingtalk_service import DingTalkService

# 2/6 周期：2/6 9:00 ~ 2/7 9:00
since = datetime(2026, 2, 6, 9, 0, 0)
until = datetime(2026, 2, 7, 9, 0, 0)

stats = ReportService.get_report_stats(since, until)
markdown = ReportService.format_daily_report(stats)

print(markdown)
print()
print("--- Sending to DingTalk ---")
DingTalkService.send_report("2/6 daily report", markdown)
print("Done!")
