@echo off
REM Re-edit one clip with your own idea.
set /p "VOD=VOD id: "
set /p "NO=Clip number (e.g. 2): "
set /p "IDEA=Your idea (any language): "
call "%~dp0run.bat" %VOD% --only %NO% --replan --idea "%IDEA%"
