@echo off
setlocal EnableExtensions

:: Usage:
::   UnicodeSuiteTester.exe <CorpusFolder> [FilenamePattern] [-report <ReportFile>]
::
::   FilenamePattern   Wildcard filter applied to filenames (default: *).
::
::   -report           Path to write the text report (default: a timestamped
::                     file next to UnicodeSuiteTester.exe).

pushd "%~dp0"

.\UnicodeSuiteTester\bin\Release\net8.0\UnicodeSuiteTester.exe ^
    "C:\Users\Amr\Desktop\Corpus\UnicodeTestSuite-v3.0" * -report UTS_report.txt

popd
pause
