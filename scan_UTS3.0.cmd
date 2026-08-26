@echo off
setlocal EnableExtensions

:: Usage:
::   UnicodeSuiteTester.exe <CorpusFolder> [FilenamePattern] [-report <ReportFile>]
::
::   FilenamePattern   Wildcard filter applied to filenames (default: *).
::
::   -report           Path to write the text report (default: a timestamped
::                     file next to UnicodeSuiteTester.exe).

:: CORPUS_ROOT is the directory holding the corpora. Set it in the
:: environment, or edit the default below to match your layout.
if "%CORPUS_ROOT%"=="" set "CORPUS_ROOT=%USERPROFILE%\Desktop\Corpus"

if not exist "%CORPUS_ROOT%\UnicodeTestSuite-v3.0" (
    echo Corpus not found: "%CORPUS_ROOT%\UnicodeTestSuite-v3.0"
    echo Set CORPUS_ROOT to the directory holding the corpora.
    echo See README.md for where to obtain them.
    pause
    exit /b 2
)

pushd "%~dp0"

.\UnicodeSuiteTester\bin\Release\net8.0\UnicodeSuiteTester.exe ^
    "%CORPUS_ROOT%\UnicodeTestSuite-v3.0" * -report UTS_report.txt

popd
pause
