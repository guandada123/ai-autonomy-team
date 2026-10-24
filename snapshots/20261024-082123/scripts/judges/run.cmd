@echo off
rem run.cmd —— 硬裁判 JD-01 的 Windows 薄封装
rem 职责单一：找到解释器 -> 转发参数。不做任何业务逻辑。
setlocal
set "DIR=%~dp0"

rem 优先 py launcher（多版本共存时最可靠），退而求其次用 python
where py >nul 2>nul
if %errorlevel%==0 goto :usepy

where python >nul 2>nul
if %errorlevel%==0 goto :usepython

echo [ERR] 未找到 Python (py 或 python)。请安装 Python 3.9+ 并加入 PATH。 1>&2
exit /b 2

:usepy
py -3 "%DIR%verify.py" %*
exit /b %errorlevel%

:usepython
python "%DIR%verify.py" %*
exit /b %errorlevel%
