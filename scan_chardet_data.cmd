@echo off
setlocal EnableExtensions

:: Usage:
::   ChardetDataTester.exe <CorpusFolder> [FilenamePattern] [-report <ReportFile>]
::
::   FilenamePattern   Wildcard filter applied to filenames (default: *).
::
::   -report           Path to write the text report (default: a timestamped
::                     file next to ChardetDataTester.exe).

pushd "%~dp0"

.\ChardetDataTester\bin\Release\net8.0\ChardetDataTester.exe ^
    "C:\Users\Amr\Desktop\Corpus\test-data-main" * -report Chardet_report.txt

popd
pause



