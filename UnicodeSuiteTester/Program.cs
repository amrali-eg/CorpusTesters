using System.Diagnostics;
using System.IO.Enumeration;
using System.Text;

// Usage:
//   UnicodeSuiteTester.exe <CorpusFolder> [FilenamePattern] [-report <ReportFile>]
//
//   FilenamePattern   Wildcard filter applied to filenames (default: *).
//
//   -report           Path to write the text report (default: a timestamped
//                     file next to UnicodeSuiteTester.exe).

using CorpusTesting;

namespace UnicodeSuiteTester;

internal sealed class Program
{
    /// <summary>
    /// Display order for the "Files per Encoding in UTS" ground-truth
    /// population summary, matching the corpus's own statistics grouping
    /// (Unicode families combine their BOM and non-BOM variants).
    /// </summary>
    private static readonly string[] UtsCategoryOrder =
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
    /// Recursively scans a Unicode Test Suite corpus, compares
    /// EncodingDetector's result for each matching file against the ground
    /// truth encoded in its filename, and writes an accuracy/FPR/FNR report.
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

        // Written next to the executable (not inside corpusFolder) so the
        // report itself is never picked up as a corpus file on a later run.
        reportPath ??= Path.Combine(
            AppContext.BaseDirectory,
            $"UTS_Report_{DateTime.Now:yyyyMMdd_HHmmss}.txt");

        // Required for legacy code pages such as windows-1253.
        Encoding.RegisterProvider(CodePagesEncodingProvider.Instance);

        // Ground truth comes from the corpus's own Manifest.csv rather than
        // from filenames. A filename records only the encoding a file was
        // written with; the manifest also records which other encodings
        // decode the same bytes to the same characters, which is what a
        // detector should actually be scored against.
        ManifestGroundTruth? manifest = ManifestGroundTruth.Load(corpusFolder);

        if (manifest is null)
        {
            Console.WriteLine($"{ManifestGroundTruth.ManifestFileName} not found in:");
            Console.WriteLine(corpusFolder);
            Console.WriteLine();
            Console.WriteLine("Point the tester at the root of a generated corpus, which");
            Console.WriteLine("contains Manifest.csv alongside the numbered folders.");
            return;
        }

        Console.WriteLine(
            $"Ground truth: {ManifestGroundTruth.ManifestFileName} ({manifest.EntryCount:N0} entries)" +
            (manifest.HasEquivalenceSets
                ? ", scoring encoding equivalence via AlsoValidAs."
                : ", no AlsoValidAs column - scoring strict equality (pre-3.0 corpus)."));

        CorpusStatistics stats = new();
        DateTime testRunDate = DateTime.Now;

        // Measures only time spent inside EncodingDetector.DetectFromFile,
        // excluding directory traversal, ground-truth parsing, statistics,
        // and report generation.
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

            ManifestGroundTruth.ManifestEntry? entry = manifest.Find(relativePath);

            if (entry is null)
            {
                // Present on disk but absent from the manifest. The corpus's
                // own `verify` rejects such a file outright, so scoring it
                // here would be inventing a ground truth the corpus does not
                // claim.
                stats.FilesSkippedNoGroundTruth++;
                continue;
            }

            stats.RecordCategory(ManifestGroundTruth.CategorizeForSummary(entry));

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

            stats.RecordResult(
                relativePath,
                entry.Expected,
                entry.Describe(),
                actual,
                entry.Accepted);
        }

        string report = ReportWriter.BuildReport(
            corpusFolder,
            filenamePattern,
            "UTS",
            UtsCategoryOrder,
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
        Console.WriteLine("    UnicodeSuiteTester.exe <CorpusFolder> [FilenamePattern] [-report <ReportFile>]");
        Console.WriteLine();
        Console.WriteLine("    FilenamePattern   Wildcard filter applied to filenames (default: *).");
        Console.WriteLine("    -report           Path to write the text report (default: a timestamped file next to UnicodeSuiteTester.exe).");
    }
}
