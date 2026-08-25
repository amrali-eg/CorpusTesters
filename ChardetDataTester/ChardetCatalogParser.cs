using System.Text.RegularExpressions;

namespace ChardetDataTester;

/// <summary>
/// Parses the chardet test-data repository's CATALOG.md at runtime into a
/// per-file ground-truth encoding token lookup.
///
/// CATALOG.md documents its own guarantee: every subdirectory is named
/// `{encoding}` or `{encoding}-{language}` and contains only files encoded
/// in that encoding. Rather than guess where the encoding token ends and
/// the language suffix begins (ambiguous - some language codes collide
/// with encoding suffixes, e.g. "utf-16-be" vs. Belarusian "-be"), this
/// resolves each directory's encoding by longest-prefix match against the
/// authoritative encoding token list parsed from the catalog's own
/// "## Provenance" summary table.
/// </summary>
internal static partial class ChardetCatalogParser
{
    // "#### `dirname/` — N files" or "### `dirname/` — N files": the
    // backtick-quoted directory name follows the heading markers directly.
    [GeneratedRegex(@"^#{2,4}\s*`([^`/]+)/`")]
    private static partial Regex DirectoryHeaderLeadingPattern();

    // "## Binary Test Files (`None-None/`)": the backtick-quoted directory
    // name is embedded inside parentheses instead.
    [GeneratedRegex(@"\(`([^`/]+)/`\)\s*$")]
    private static partial Regex DirectoryHeaderParenPattern();

    [GeneratedRegex(@"^#{1,6}\s")]
    private static partial Regex AnyHeadingPattern();

    // A markdown table row whose first column is a backtick-quoted token,
    // e.g. "| `filename.txt` | Source | Provenance | Size | Notes |" or
    // "| `utf-8` | 268 | 107 | 140 | 21 |".
    [GeneratedRegex(@"^\|\s*`([^`]+)`\s*\|")]
    private static partial Regex TableRowPattern();

    /// <summary>
    /// Ground-truth encoding token per catalogued file, keyed by
    /// "{directory}/{filename}" (forward slashes, matching how disk-scanned
    /// relative paths are normalized in Program.cs).
    /// </summary>
    internal static Dictionary<string, string> Parse(string catalogPath)
    {
        List<(string Directory, string FileName)> catalogedFiles = [];
        HashSet<string> encodingTokens = new(StringComparer.Ordinal);

        string? currentDirectory = null;
        bool inProvenanceSummary = false;

        foreach (string rawLine in File.ReadLines(catalogPath))
        {
            string line = rawLine.TrimEnd();

            if (AnyHeadingPattern().IsMatch(line))
            {
                Match dirMatch = DirectoryHeaderLeadingPattern().Match(line);
                if (!dirMatch.Success)
                    dirMatch = DirectoryHeaderParenPattern().Match(line);

                if (dirMatch.Success)
                {
                    currentDirectory = dirMatch.Groups[1].Value;
                    inProvenanceSummary = false;
                }
                else
                {
                    currentDirectory = null;
                    inProvenanceSummary =
                        line.TrimStart('#').Trim() == "Provenance";
                }

                continue;
            }

            Match rowMatch = TableRowPattern().Match(line);
            if (!rowMatch.Success)
                continue;

            string token = rowMatch.Groups[1].Value;

            if (currentDirectory != null)
                catalogedFiles.Add((currentDirectory, token));
            else if (inProvenanceSummary)
                encodingTokens.Add(token);
        }

        Dictionary<string, string> directoryToEncoding = new(StringComparer.Ordinal);
        Dictionary<string, string> fileToEncoding = new(StringComparer.OrdinalIgnoreCase);

        foreach ((string directory, string fileName) in catalogedFiles)
        {
            if (!directoryToEncoding.TryGetValue(directory, out string? encodingToken))
            {
                encodingToken = ResolveDirectoryEncoding(directory, encodingTokens);
                directoryToEncoding[directory] = encodingToken;
            }

            fileToEncoding[$"{directory}/{fileName}"] = encodingToken;
        }

        return fileToEncoding;
    }

    /// <summary>
    /// "utf-16-be" and "utf-32-be" are catalog artifacts, not real
    /// encodings: "be" is both the big-endian marker and the language code
    /// for Belarusian, so the catalog's own directory-naming tool couldn't
    /// split these two directories into encoding + language and left them
    /// as one-off literal names, which then leaked into its summary table
    /// as spurious single-directory "encodings". Verified by content: both
    /// directories actually contain little-endian, BOM'd data - the bare
    /// "utf-16"/"utf-32" family (Belarusian-language instance), not the
    /// explicit no-BOM big-endian codec their name suggests. Excluded here
    /// so prefix matching falls back to "utf-16"/"utf-32", which resolves
    /// correctly by sniffing the real BOM bytes.
    /// </summary>
    private static readonly HashSet<string> SpuriousTokens = new(StringComparer.Ordinal)
    {
        "utf-16-be",
        "utf-32-be",
    };

    /// <summary>
    /// Resolves a directory's encoding token as the longest entry in
    /// <paramref name="encodingTokens"/> that is a prefix of
    /// <paramref name="directory"/>, ending either at the full directory
    /// name or at a '-' boundary (the start of a language suffix).
    /// </summary>
    private static string ResolveDirectoryEncoding(
        string directory,
        HashSet<string> encodingTokens)
    {
        string? best = null;

        foreach (string token in encodingTokens)
        {
            if (SpuriousTokens.Contains(token))
                continue;

            if (token.Length > directory.Length)
                continue;

            if (!directory.StartsWith(token, StringComparison.Ordinal))
                continue;

            bool atBoundary =
                token.Length == directory.Length ||
                directory[token.Length] == '-';

            if (!atBoundary)
                continue;

            if (best == null || token.Length > best.Length)
                best = token;
        }

        // Falls back to the literal directory name if no catalog token
        // matches - defensive only, every real catalog directory resolves.
        return best ?? directory;
    }
}
