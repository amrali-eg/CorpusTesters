using CorpusTesting;

namespace UnicodeSuiteTester;

/// <summary>
/// Ground truth for a Unicode Test Suite corpus, read from its own
/// <c>Manifest.csv</c>.
///
/// This replaces parsing the encoding out of the filename. The filename
/// carries only the encoding a file was *written* with, which is the wrong
/// question to score a detector against: a byte sequence is frequently valid,
/// and decodes identically, under several encodings at once. Pure-ASCII
/// content is legitimately readable as us-ascii, utf-8, every Windows code
/// page and every ISO-8859 part simultaneously, and the corpus contains 27
/// byte sequences filed twice under contradictory labels - once as us-ascii,
/// once as utf-8. No detector can satisfy both rows.
///
/// UTS 3.0 answers this with an <c>AlsoValidAs</c> column listing every
/// encoding that decodes the same bytes to the same characters. Scoring set
/// membership against it removes false negatives that were never detector
/// errors, without loosening the test: every accepted alternative produces
/// identical text.
///
/// Older manifests without that column still load; their equivalence sets are
/// simply empty, which reproduces the previous strict behaviour.
/// </summary>
internal sealed class ManifestGroundTruth
{
    internal const string ManifestFileName = "Manifest.csv";

    private readonly Dictionary<string, ManifestEntry> _byRelativePath;

    internal bool HasEquivalenceSets { get; }

    internal int EntryCount => _byRelativePath.Count;

    private ManifestGroundTruth(
        Dictionary<string, ManifestEntry> byRelativePath,
        bool hasEquivalenceSets)
    {
        _byRelativePath = byRelativePath;
        HasEquivalenceSets = hasEquivalenceSets;
    }

    /// <summary>
    /// One manifest row, reduced to what the harness scores against.
    /// </summary>
    internal sealed record ManifestEntry(
        string DeclaredEncoding,
        string Bom,
        UnicodeClass Expected,
        IReadOnlySet<UnicodeClass> Accepted,
        string Category)
    {
        /// <summary>
        /// What the report prints in the "expected" column: the declared
        /// encoding, with its BOM state when the encoding has one.
        /// </summary>
        internal string Describe() =>
            Bom is "BOM" or "NoBOM"
                ? $"{DeclaredEncoding}-{Bom}"
                : DeclaredEncoding;
    }

    /// <summary>
    /// Loads <c>Manifest.csv</c> from the root of a corpus folder.
    /// </summary>
    /// <returns>
    /// <see langword="null"/> when the file is absent, so the caller can
    /// report that clearly rather than silently scoring against nothing.
    /// </returns>
    internal static ManifestGroundTruth? Load(string corpusFolder)
    {
        string path = Path.Combine(corpusFolder, ManifestFileName);

        if (!File.Exists(path))
            return null;

        using StreamReader reader = new(path, System.Text.Encoding.UTF8);

        string? headerLine = reader.ReadLine();

        if (headerLine is null)
            return null;

        string[] header = SplitCsvLine(headerLine);

        int encodingIndex = IndexOf(header, "Encoding");
        int bomIndex = IndexOf(header, "BOM");
        int pathIndex = IndexOf(header, "RelativePath");
        int alsoIndex = IndexOf(header, "AlsoValidAs");
        int categoryIndex = IndexOf(header, "Category");

        if (encodingIndex < 0 || pathIndex < 0)
            return null;

        Dictionary<string, ManifestEntry> entries =
            new(StringComparer.OrdinalIgnoreCase);

        string? line;

        while ((line = reader.ReadLine()) is not null)
        {
            if (line.Length == 0)
                continue;

            string[] fields = SplitCsvLine(line);

            if (fields.Length <= Math.Max(encodingIndex, pathIndex))
                continue;

            string declared = fields[encodingIndex];
            string bom = bomIndex >= 0 && bomIndex < fields.Length ? fields[bomIndex] : "";
            string relativePath = fields[pathIndex];
            string category =
                categoryIndex >= 0 && categoryIndex < fields.Length ? fields[categoryIndex] : "";

            string alsoValidAs =
                alsoIndex >= 0 && alsoIndex < fields.Length ? fields[alsoIndex] : "";

            UnicodeClass expected = Resolve(declared, bom);

            HashSet<UnicodeClass> accepted = [expected];

            foreach (string alternative in alsoValidAs.Split(
                         ';', StringSplitOptions.RemoveEmptyEntries))
            {
                // An alternative encoding decodes to the same characters, so
                // it is an equally correct answer. BOM state is not carried
                // per alternative; a BOM-bearing file is already pinned by
                // its declared entry.
                accepted.Add(Resolve(alternative, "NoBOM"));
            }

            entries[Normalize(relativePath)] = new ManifestEntry(
                declared, bom, expected, accepted, category);
        }

        return new ManifestGroundTruth(entries, alsoIndex >= 0);
    }

    internal ManifestEntry? Find(string relativePath) =>
        _byRelativePath.GetValueOrDefault(Normalize(relativePath));

    /// <summary>
    /// Ground-truth population bucket for a manifest row, used by the
    /// "Files per Encoding" summary. Unicode families combine their BOM and
    /// BOM-less variants, matching the corpus's own statistics.
    /// </summary>
    internal static string CategorizeForSummary(ManifestEntry entry) =>
        entry.DeclaredEncoding.ToLowerInvariant() switch
        {
            "binary" => "Binary",
            "us-ascii" => "us-ascii",
            "utf-8" => "utf-8",
            "utf-16le" => "utf-16LE",
            "utf-16be" => "utf-16BE",
            "utf-32le" => "utf-32LE",
            "utf-32be" => "utf-32BE",
            _ => "Legacy",
        };

    private static UnicodeClass Resolve(string encoding, string bom)
    {
        bool hasBom = string.Equals(bom, "BOM", StringComparison.OrdinalIgnoreCase);

        return encoding.ToLowerInvariant() switch
        {
            "utf-8" => hasBom ? UnicodeClass.Utf8Bom : UnicodeClass.Utf8NoBom,
            "utf-16le" => hasBom ? UnicodeClass.Utf16LeBom : UnicodeClass.Utf16LeNoBom,
            "utf-16be" => hasBom ? UnicodeClass.Utf16BeBom : UnicodeClass.Utf16BeNoBom,
            "utf-32le" => hasBom ? UnicodeClass.Utf32LeBom : UnicodeClass.Utf32LeNoBom,
            "utf-32be" => hasBom ? UnicodeClass.Utf32BeBom : UnicodeClass.Utf32BeNoBom,
            "us-ascii" => UnicodeClass.Ascii,

            // "Binary" marks the invalid-Unicode and binary-signature
            // fixtures: the pipeline is expected to name no encoding at all.
            "binary" => UnicodeClass.None,

            _ => UnicodeClass.Legacy,
        };
    }

    private static string Normalize(string relativePath) =>
        relativePath.Replace('\\', '/').TrimStart('/');

    private static int IndexOf(string[] header, string name)
    {
        for (int i = 0; i < header.Length; i++)
        {
            if (string.Equals(header[i], name, StringComparison.OrdinalIgnoreCase))
                return i;
        }

        return -1;
    }

    /// <summary>
    /// Splits one CSV line, honouring double-quoted fields.
    /// </summary>
    /// <remarks>
    /// The generator writes no field containing a comma - AlsoValidAs is
    /// semicolon-separated precisely so it never needs quoting - so a plain
    /// Split would work today. Quote handling is here so a future column
    /// containing a comma cannot silently shift every field after it.
    /// </remarks>
    private static string[] SplitCsvLine(string line)
    {
        List<string> fields = [];
        System.Text.StringBuilder current = new();
        bool inQuotes = false;

        for (int i = 0; i < line.Length; i++)
        {
            char c = line[i];

            if (inQuotes)
            {
                if (c == '"')
                {
                    if (i + 1 < line.Length && line[i + 1] == '"')
                    {
                        current.Append('"');
                        i++;
                    }
                    else
                    {
                        inQuotes = false;
                    }
                }
                else
                {
                    current.Append(c);
                }

                continue;
            }

            switch (c)
            {
                case '"':
                    inQuotes = true;
                    break;

                case ',':
                    fields.Add(current.ToString());
                    current.Clear();
                    break;

                default:
                    current.Append(c);
                    break;
            }
        }

        fields.Add(current.ToString());

        return [.. fields];
    }
}
