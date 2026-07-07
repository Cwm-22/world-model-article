@echo off
REM ====================================================================
REM 世界模型日报 · 每日任务启动脚本
REM 用 Windows 任务计划程序 每天 06:00 触发本脚本
REM
REM 做三件事：
REM   1. 运行 main.py（拉 arXiv -> 评估 -> 生成推文 md）
REM   2. 把当天 top3_推文.md 转 PDF
REM   3. 用 wxauto 把 PDF 发到微信「文件传输助手」
REM 日志：daily_job.log + run.log
REM ====================================================================

setlocal

set PROJ=%~dp0
set PY=E:\software\Anaconda\python.exe

cd /d "%PROJ%"

REM 追加日志分隔行
echo. >> "%PROJ%daily_job.log"
echo ========== %date% %time% ========== >> "%PROJ%daily_job.log"

"%PY%" "%PROJ%daily_job.py"
set RC=%ERRORLEVEL%

endlocal & exit /b %RC%