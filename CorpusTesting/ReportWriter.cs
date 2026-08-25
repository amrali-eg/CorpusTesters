using System.Text;

namespace CorpusTesting;

/// <summary>
/// Formats a <see cref="CorpusStatistics"/> run into the text report and the
/// condensed console summary.
/// </summary>
public static class ReportWriter
{
    private static string CountPercent(int count, int total) =>
        total == 0
            ? $"{count} (0.00%)"
            : $"{count} ({100.0 * count / total:F2}%)";

    /// <summary>
    /// The run summary, in exactly the form written to the console.
    /// </summary>
    /// <remarks>
    /// Emitted by both <see cref="BuildReport"/> and
    /// <see cref="BuildConsoleSummary"/> from this single method, so the block
    /// at the top of the report is the same text that appeared on screen and
    /// the two can never drift apart.
    /// </remarks>
    private static void AppendRunSummary(
        StringBuilder sb,
        CorpusStatistics stats,
        TimeSpan elapsed)
    {
        sb.AppendLine($"Processed {stats.FilesProcessed} file(s); detector time: {elapsed.TotalMilliseconds:F3} ms.");
        sb.AppendLine($"Skipped by filter : {stats.FilesSkippedByFilter}");
        sb.AppendLine($"Skipped (no ground truth): {stats.FilesSkippedNoGroundTruth}");
        sb.AppendLine($"Errors            : {stats.FilesErrored}");
        sb.AppendLine($"Overall accuracy  : {stats.OverallAccuracyPercent():F2}%");
        sb.AppendLine($"Overall FPR       : {stats.OverallFalsePositiveRatePercent():F2}%");
        sb.AppendLine($"Overall FNR       : {stats.OverallFalseNegativeRatePercent():F2}%");
        sb.AppendLine($"Mismatches        : {stats.Mismatches.Count}");
    }

    /// <summary>
    /// Builds the full text report.
    /// </summary>
    /// <param name="filenamePattern"></param>
    /// <param name="suiteName">
    /// Short name of the test suite (e.g. "UTS", "chardet test-data"), used
    /// to label the ground-truth population summary section.
    /// </param>
    /// <param name="categoryOrder">
    /// Display order for the ground-truth population summary buckets
    /// (e.g. "utf-8", "Legacy", "Binary").
    /// </param>
    /// <param name="corpusFolder"></param>
    /// <param name="stats"></param>
    /// <param name="testRunDate"></param>
    /// <param name="elapsed"></param>
    public static string BuildReport(
        string corpusFolder,
        string filenamePattern,
        string suiteName,
        string[] categoryOrder,
        CorpusStatistics stats,
        DateTime testRunDate,
        TimeSpan elapsed)
    {
        StringBuilder sb = new();

        sb.AppendLine("Unicode Encoding Detection Suite - Report");
        sb.AppendLine("==========================================");
        sb.AppendLine();
        sb.AppendLine($"Corpus folder     : {corpusFolder}");
        sb.AppendLine($"Filename pattern  : {filenamePattern}");
        sb.AppendLine($"Test run date     : {testRunDate:yyyy-MM-dd HH:mm:ss}");
        sb.AppendLine($"Detector time     : {elapsed.TotalMilliseconds:F3} ms");
        sb.AppendLine();

        sb.AppendLine("Summary");
        sb.AppendLine("-------");
        AppendRunSummary(sb, stats, elapsed);
        sb.AppendLine();

        sb.AppendLine("File Counts");
        sb.AppendLine("-----------");
        sb.AppendLine($"Files discovered               : {stats.FilesDiscovered}");
        sb.AppendLine($"Files skipped (filter)         : {stats.FilesSkippedByFilter}");
        sb.AppendLine($"Files skipped (no ground truth): {stats.FilesSkippedNoGroundTruth}");
        sb.AppendLine($"Files processed                : {stats.FilesProcessed}");
        sb.AppendLine($"Files not processed (errors)   : {stats.FilesErrored}");
        sb.AppendLine();

        sb.AppendLine("Overall Results");
        sb.AppendLine("---------------");
        sb.AppendLine($"Accuracy : {stats.OverallAccuracyPercent():F2}%  (files whose reported encoding matches the ground truth)");
        sb.AppendLine($"FPR      : {stats.OverallFalsePositiveRatePercent():F2}%  (claims asserting an encoding the file is not, summed over every class)");
        sb.AppendLine($"FNR      : {stats.OverallFalseNegativeRatePercent():F2}%  (files whose true encoding the detector failed to report, summed over every class)");
        sb.AppendLine();

        sb.AppendLine($"Files per Encoding in {suiteName}");
        sb.AppendLine("------------------------------------------");
        sb.AppendLine("Ground-truth population, independent of what was detected.");
        sb.AppendLine();

        foreach (string category in categoryOrder)
        {
            sb.AppendLine($"  {category,-22}{stats.CategoryCounts.GetValueOrDefault(category),6}");
        }
        sb.AppendLine();

        sb.AppendLine("Encodings Reported by the Detector");
        sb.AppendLine("-----------------------------------");
        sb.AppendLine("What the detector claimed, before any judgement is applied.");
        sb.AppendLine();

        foreach (UnicodeClass cls in UnicodeClassLabels.DetectableClasses)
        {
            sb.AppendLine($"  {UnicodeClassLabels.Label(cls),-22}{stats.ClaimCounts.GetValueOrDefault(cls),6}");
        }

        sb.AppendLine(
            $"  {UnicodeClassLabels.Label(UnicodeClass.None),-22}" +
            $"{stats.ClaimCounts.GetValueOrDefault(UnicodeClass.None),6}");
        sb.AppendLine();

        sb.AppendLine("Per-Encoding Results");
        sb.AppendLine("---------------------");
        sb.AppendLine(
            $"{"Encoding",-14}{"Accuracy (%)",15}{"FNR (%)",15}{"FPR (%)",15}{"TP",7}{"FN",7}{"FP",7}{"TN",7}");

        foreach (UnicodeClass cls in UnicodeClassLabels.DetectableClasses)
        {
            ClassConfusion c = stats.ByClass[cls];

            // Accuracy: correct calls (TP+TN) over every file processed.
            // FNR: misses over every file that IS this encoding (FN+TP).
            // FPR: false alarms over every file NOT of this encoding (FP+TN).
            sb.AppendLine(string.Format(
                "{0,-14}{1,15}{2,15}{3,15}{4,7}{5,7}{6,7}{7,7}",
                UnicodeClassLabels.Label(cls),
                CountPercent(c.TruePositive + c.TrueNegative, stats.FilesProcessed),
                CountPercent(c.FalseNegative, c.FalseNegative + c.TruePositive),
                CountPercent(c.FalsePositive, c.FalsePositive + c.TrueNegative),
                c.TruePositive,
                c.FalseNegative,
                c.FalsePositive,
                c.TrueNegative));
        }

        sb.AppendLine();

        sb.AppendLine($"False Positive / False Negative Files by Encoding ({stats.Mismatches.Count} total)");
        sb.AppendLine("---------------------------------------------------------------------");
        sb.AppendLine("A misclassified file (wrong Unicode variant) is listed twice: as a");
        sb.AppendLine("false negative under the encoding it should have been, and as a false");
        sb.AppendLine("positive under the encoding it was wrongly reported as.");

        bool anyListed = false;

        foreach (UnicodeClass cls in UnicodeClassLabels.DetectableClasses)
        {
            List<MismatchRecord> falsePositives =
            [
                .. stats.Mismatches
                    .Where(m => m.Actual == cls)
                    .OrderBy(m => m.RelativePath, StringComparer.Ordinal)
            ];

            List<MismatchRecord> falseNegatives =
            [
                .. stats.Mismatches
                    .Where(m => m.Expected == cls)
                    .OrderBy(m => m.RelativePath, StringComparer.Ordinal)
            ];

            if (falsePositives.Count == 0 && falseNegatives.Count == 0)
                continue;

            anyListed = true;

            sb.AppendLine();
            sb.AppendLine($"{UnicodeClassLabels.Label(cls)}  (FP={falsePositives.Count}, FN={falseNegatives.Count})");

            foreach (MismatchRecord m in falseNegatives)
                sb.AppendLine($"  [FN]{m.RelativePath}  [reported: {UnicodeClassLabels.Label(m.Actual)}], [expected: {m.ExpectedToken}]");

            foreach (MismatchRecord m in falsePositives)
                sb.AppendLine($"  [FP]{m.RelativePath}  [reported: {UnicodeClassLabels.Label(m.Actual)}], [expected: {m.ExpectedToken}]");
        }

        if (!anyListed)
        {
            sb.AppendLine();
            sb.AppendLine("(none)");
        }

        if (stats.Errors.Count > 0)
        {
            sb.AppendLine();
            sb.AppendLine($"Files Not Processed ({stats.Errors.Count})");
            sb.AppendLine("--------------------");

            foreach (ProcessingError e in stats.Errors
                .OrderBy(e => e.RelativePath, StringComparer.Ordinal))
            {
                sb.AppendLine($"{e.RelativePath}: {e.Message}");
            }
        }

        return sb.ToString();
    }

    public static string BuildConsoleSummary(CorpusStatistics stats, TimeSpan elapsed)
    {
        StringBuilder sb = new();

        AppendRunSummary(sb, stats, elapsed);

        return sb.ToString();
    }
}
