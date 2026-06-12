@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
set "PYTHON_EXE=%STREAM_CURATOR_PYTHON_EXECUTABLE%"
if not defined PYTHON_EXE set "PYTHON_EXE=python"
set "PYTHONPATH=%SCRIPT_DIR%..\@zhihu-cli;%PYTHONPATH%"
"%PYTHON_EXE%" -X utf8 -m zhihu_cli.cli %*
