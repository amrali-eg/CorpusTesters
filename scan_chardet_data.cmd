@echo off
setlocal EnableExtensions

:: Usage:
::   ChardetDataTester.exe <CorpusFolder> [FilenamePattern] [-report <ReportFile>]
::
::   FilenamePattern   Wildcard filter applied to filenames (default: *).
::
::   -report           Path to write the text report (default: a timestamped
::                     file next to ChardetDataTester.exe).

:: CORPUS_ROOT is the directory holding the corpora. Set it in the
:: environment, or edit the default below to match your layout.
if "%CORPUS_ROOT%"=="" set "CORPUS_ROOT=%USERPROFILE%\Desktop\Corpus"

if not exist "%CORPUS_ROOT%\test-data-main" (
    echo Corpus not found: "%CORPUS_ROOT%\test-data-main"
    echo Set CORPUS_ROOT to the directory holding the corpora.
    echo See README.md for where to obtain them.
    pause
    exit /b 2
)

pushd "%~dp0"

.\ChardetDataTester\bin\Release\net8.0\ChardetDataTester.exe ^
    "%CORPUS_ROOT%\test-data-main" * -report Chardet_report.txt

popd
pause



