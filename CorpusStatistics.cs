namespace UnicodeSuiteTester;

internal enum MismatchType
{
    FalsePositive,
    FalseNegative,
    Misclassified,
}

internal readonly record struct MismatchRecord(
    string RelativePath,
    UnicodeClass Expected,
    string ExpectedToken,
    UnicodeClass Actual,
    MismatchType Type);

internal readonly record struct ProcessingError(
    string RelativePath,
    string Message);

/// <summary>
/// One-vs-rest confusion-matrix counts for a single <see cref="UnicodeClass"/>.
/// </summary>
internal sealed class ClassConfusion
{
    internal int TruePositive;
    internal int FalsePositive;
    internal int FalseNegative;
    internal int TrueNegative;
}

/// <summary>
/// Accumulates per-class confusion-matrix counts and overall pass/fail
/// statistics across a corpus verification run.
/// </summary>
internal sealed class CorpusStatistics
{
    internal int FilesDiscovered;
    internal int FilesSkippedByFilter;
    internal int FilesSkippedNoGroundTruth = 0;
    internal int FilesProcessed;
    internal int FilesErrored;

    internal int OverallCorrect;

    internal readonly Dictionary<UnicodeClass, ClassConfusion> ByClass =
        UnicodeClassLabels.DetectableClasses.ToDictionary(
            c => c,
            _ => new ClassConfusion());

    internal readonly List<MismatchRecord> Mismatches = [];
    internal readonly List<ProcessingError> Errors = [];

    /// <summary>
    /// Ground-truth population per display bucket (e.g. "utf-8", "Legacy",
    /// "Binary"), independent of whether detection later succeeded.
    /// </summary>
    internal readonly Dictionary<string, int> CategoryCounts = [];

    /// <summary>
    /// What the detector actually claimed, per class, across every file
    /// processed - including <see cref="UnicodeClass.None"/> for the files it
    /// declined to name.
    ///
    /// Reported before any judgement is applied. The per-class table that
    /// follows classifies those same claims against the ground truth; this
    /// says what was claimed in the first place, which is otherwise only
    /// visible for the files that turned out to be wrong.
    /// </summary>
    internal readonly Dictionary<UnicodeClass, int> ClaimCounts = [];

    internal void RecordCategory(string category)
    {
        CategoryCounts[category] = CategoryCounts.GetValueOrDefault(category) + 1;
    }

    internal void RecordError(string relativePath, string message)
    {
        FilesErrored++;
        Errors.Add(new ProcessingError(relativePath, message));
    }

    /// <summary>
    /// Records one file's outcome.
    /// </summary>
    /// <param name="expected">
    /// The declared ground truth, used when reporting a miss.
    /// </param>
    /// <param name="accepted">
    /// Every class that is a correct answer for this file. Usually just
    /// <paramref name="expected"/>, but a corpus that models encoding
    /// equivalence (UTS 3.0's AlsoValidAs) supplies the full set: pure-ASCII
    /// content really is valid as us-ascii, utf-8 and every ASCII-superset
    /// code page at once, and a detector naming any of them is right.
    /// Pass <see langword="null"/> to score strict equality.
    /// </param>
    internal void RecordResult(
        string relativePath,
        UnicodeClass expected,
        string expectedToken,
        UnicodeClass actual,
        IReadOnlySet<UnicodeClass>? accepted = null)
    {
        FilesProcessed++;

        ClaimCounts[actual] = ClaimCounts.GetValueOrDefault(actual) + 1;

        bool isCorrect = accepted is null
            ? expected == actual
            : accepted.Contains(actual);

        // Credit a correct answer to the class actually reported, not to the
        // declared one: when the two differ the reported class is still a
        // legitimate reading of the same bytes, and charging it as a miss
        // would penalise the detector for being right.
        UnicodeClass creditedExpectation = isCorrect ? actual : expected;

        foreach (UnicodeClass candidate in UnicodeClassLabels.DetectableClasses)
        {
            bool expectedIsCandidate = creditedExpectation == candidate;
            bool actualIsCandidate = actual == candidate;
            ClassConfusion confusion = ByClass[candidate];

            if (expectedIsCandidate && actualIsCandidate)
                confusion.TruePositive++;
            else if (expectedIsCandidate)
                confusion.FalseNegative++;
            else if (actualIsCandidate)
                confusion.FalsePositive++;
            else
                confusion.TrueNegative++;
        }

        if (isCorrect)
        {
            OverallCorrect++;
            return;
        }

        // Every mismatch is scored against all classes alike, so the label
        // here only describes its shape for the reader: nothing was found,
        // something was invented, or one class was mistaken for another.
        // The rates themselves come from the per-class matrix above.
        MismatchType type =
            actual == UnicodeClass.None ? MismatchType.FalseNegative
            : expected == UnicodeClass.None ? MismatchType.FalsePositive
            : MismatchType.Misclassified;

        Mismatches.Add(new MismatchRecord(
            relativePath, expected, expectedToken, actual, type));
    }

    internal double OverallAccuracyPercent() =>
        FilesProcessed == 0 ? 0.0 : 100.0 * OverallCorrect / FilesProcessed;

    /// <summary>
    /// Micro-averaged false-positive rate across every detectable class:
    /// all false alarms over every opportunity to raise one.
    /// </summary>
    /// <remarks>
    /// This measures the detector against the ground truth for all reported
    /// encodings, rather than collapsing the question to "Unicode or not".
    /// Every wrong positive assertion counts the same way, whether a legacy
    /// file was claimed as ASCII, a binary fixture was claimed as a code
    /// page, or an ASCII file was claimed as UTF-16.
    ///
    /// Summing the per-class matrix is what makes that work: a file reported
    /// as the wrong class is a false positive for the class asserted and a
    /// false negative for the class it truly is, so a single misdetection is
    /// counted once on each side rather than being classified as only one or
    /// the other.
    /// </remarks>
    internal double OverallFalsePositiveRatePercent()
    {
        long falsePositives = 0;
        long trueNegatives = 0;

        foreach (ClassConfusion c in ByClass.Values)
        {
            falsePositives += c.FalsePositive;
            trueNegatives += c.TrueNegative;
        }

        long opportunities = falsePositives + trueNegatives;

        return opportunities == 0 ? 0.0 : 100.0 * falsePositives / opportunities;
    }

    /// <summary>
    /// Micro-averaged false-negative rate across every detectable class:
    /// all misses over every file that belongs to some class.
    /// </summary>
    internal double OverallFalseNegativeRatePercent()
    {
        long falseNegatives = 0;
        long truePositives = 0;

        foreach (ClassConfusion c in ByClass.Values)
        {
            falseNegatives += c.FalseNegative;
            truePositives += c.TruePositive;
        }

        long population = falseNegatives + truePositives;

        return population == 0 ? 0.0 : 100.0 * falseNegatives / population;
    }
}
