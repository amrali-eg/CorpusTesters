using System.Diagnostics;
using System.IO.Enumeration;
using System.Text;
using CorpusTesting;

// Usage:
//   ChardetDataTester.exe <CorpusFolder> [FilenamePattern] [-report <ReportFile>]
//
//   FilenamePattern   Wildcard filter applied to filenames (default: *).
//   -report           Path to write the text report (default: a timestamped
//                      file next to ChardetDataTester.exe).
//
//   CorpusFolder must contain CATALOG.md (the chardet test-data repository's
//   own manifest) at its root - see
//   https://github.com/chardet/test-data.

namespace ChardetDataTester;

internal sealed class Program
{
    private const string CatalogFileName = "CATALOG.md";

    /// <summary>
    /// Display order for the ground-truth population summary, matching the
    /// buckets used by AmroDetector's UTS report so the two suites' reports
    /// read consistently.
    /// </summary>
    private static readonly string[] ChardetCategoryOrder =
    [
        "utf-8",
        "utf-16BE",
        "utf-16LE",
        "utf-32BE",
        "utf-32LE",
        "us-ascii",
        "Legacy",
        "Binary",
    ];

    /// <summary>
    /// Recursively scans a chardet test-data corpus, compares
    /// EncodingDetector's result for each matching file against the ground
    /// truth parsed from the corpus's own CATALOG.md, and writes an
    /// accuracy/FPR/FNR report.
    /// </summary>
    private static void Main(string[] args)
    {
        if (!TryParseArguments(args, out string corpusFolder, out string filenamePattern, out string? reportPath))
        {
            PrintUsage();
            return;
        }

        corpusFolder = Path.GetFullPath(corpusFolder);

        if (!Directory.Exists(corpusFolder))
        {
            Console.WriteLine("Folder not found:");
            Console.WriteLine(corpusFolder);
            return;
        }

        string catalogPath = Path.Combine(corpusFolder, CatalogFileName);

        if (!File.Exists(catalogPath))
        {
            Console.WriteLine($"{CatalogFileName} not found under:");
            Console.WriteLine(corpusFolder);
            return;
        }

        // Written next to the executable (not inside corpusFolder) so the
        // report itself is never picked up as a corpus file on a later run.
        reportPath ??= Path.Combine(
            AppContext.BaseDirectory,
            $"ChardetDetectionReport_{DateTime.Now:yyyyMMdd_HHmmss}.txt");

        // Required for legacy code pages such as windows-1253.
        Encoding.RegisterProvider(CodePagesEncodingProvider.Instance);

        Dictionary<string, string> groundTruth = ChardetCatalogParser.Parse(catalogPath);

        CorpusStatistics stats = new();
        DateTime testRunDate = DateTime.Now;

        // Measures only time spent inside EncodingDetector.DetectFromFile,
        // excluding directory traversal, catalog parsing, statistics, and
        // report generation.
        Stopwatch detectorStopwatch = new();

        List<string> allFiles =
        [
            .. Directory
                .EnumerateFiles(corpusFolder, "*", SearchOption.AllDirectories)
                .OrderBy(f => f, StringComparer.Ordinal)
        ];

        stats.FilesDiscovered = allFiles.Count;

        foreach (string file in allFiles)
        {
            string relativePath =
                Path.GetRelativePath(corpusFolder, file).Replace('\\', '/');

            if (!FileSystemName.MatchesSimpleExpression(filenamePattern, Path.GetFileName(file)))
            {
                stats.FilesSkippedByFilter++;
                continue;
            }

            // CATALOG.md itself, README.md, etc. are not catalogued test
            // files - nothing to compare the detector's result against.
            if (!groundTruth.TryGetValue(relativePath, out string? encodingToken))
            {
                stats.FilesSkippedNoGroundTruth++;
                continue;
            }

            UnicodeClass expected = ChardetGroundTruth.Resolve(encodingToken, file);
            stats.RecordCategory(ChardetGroundTruth.CategorizeForSummary(encodingToken, expected));

            Encoding? detected = null;
            Exception? detectionError = null;

            detectorStopwatch.Start();
            try
            {
                detected = TextEncoding.DetectFromFile(file);
            }
            catch (Exception ex)
            {
                detectionError = ex;
            }
            finally
            {
                detectorStopwatch.Stop();
            }

            if (detectionError != null)
            {
                stats.RecordError(relativePath, detectionError.Message);
                continue;
            }

            UnicodeClass actual = EncodingClassifier.Classify(detected);

            stats.RecordResult(relativePath, expected, encodingToken, actual);
        }

        string report = ReportWriter.BuildReport(
            corpusFolder,
            filenamePattern,
            "chardet test-data",
            ChardetCategoryOrder,
            stats,
            testRunDate,
            detectorStopwatch.Elapsed);

        File.WriteAllText(reportPath, report);

        Console.WriteLine(ReportWriter.BuildConsoleSummary(stats, detectorStopwatch.Elapsed));
        Console.WriteLine($"Report written to: {reportPath}");
    }


    /// <summary>
    /// Parses the corpus folder, optional wildcard filename filter, and
    /// optional -report path from the command line.
    /// </summary>
    private static bool TryParseArguments(
        string[] args,
        out string corpusFolder,
        out string filenamePattern,
        out string? reportPath)
    {
        corpusFolder = "";
        filenamePattern = "*";
        reportPath = null;

        string? pattern = null;

        for (int i = 0; i < args.Length; i++)
        {
            string arg = args[i];

            if (string.Equals(arg, "-report", StringComparison.OrdinalIgnoreCase))
            {
                if (i + 1 >= args.Length)
                    return false;

                reportPath = args[++i];
                continue;
            }

            if (corpusFolder.Length == 0)
            {
                corpusFolder = arg;
            }
            else if (pattern is null)
            {
                pattern = arg;
            }
            else
            {
                // Too many positional arguments.
                return false;
            }
        }

        if (corpusFolder.Length == 0)
            return false;

        filenamePattern = pattern ?? "*";
        return true;
    }


    private static void PrintUsage()
    {
        Console.WriteLine("Usage:");
        Console.WriteLine("    ChardetDataTester.exe <CorpusFolder> [FilenamePattern] [-report <ReportFile>]");
        Console.WriteLine();
        Console.WriteLine("    CorpusFolder      Root of the chardet test-data repository (must contain CATALOG.md).");
        Console.WriteLine("    FilenamePattern   Wildcard filter applied to filenames (default: *).");
        Console.WriteLine("    -report           Path to write the text report (default: a timestamped file next to ChardetDataTester.exe).");
    }
}
