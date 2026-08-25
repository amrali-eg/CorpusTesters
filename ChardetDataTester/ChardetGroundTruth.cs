using CorpusTesting;

namespace ChardetDataTester;

/// <summary>
/// Resolves a chardet catalog encoding token (as parsed from CATALOG.md) to
/// the <see cref="UnicodeClass"/> EncodingDetector is expected to report.
///
/// Most tokens map directly (Python's "-le"/"-be" codecs never carry a
/// BOM). The two bare tokens "utf-16" and "utf-32" are ambiguous by name
/// alone - Python's bare codec always writes a BOM but its endianness
/// depends on the generating machine - so those are resolved by sniffing
/// the file's actual leading bytes instead of guessing.
/// </summary>
internal static class ChardetGroundTruth
{
    internal static UnicodeClass Resolve(string encodingToken, string filePath) =>
        encodingToken.ToLowerInvariant() switch
        {
            // The catalog's marker for data that is not text in any encoding.
            "none-none" => UnicodeClass.None,

            "utf-8" => UnicodeClass.Utf8NoBom,
            "utf-8-sig" => UnicodeClass.Utf8Bom,
            "utf-16le" or "utf-16-le" => UnicodeClass.Utf16LeNoBom,
            "utf-16be" or "utf-16-be" => UnicodeClass.Utf16BeNoBom,
            "utf-32le" or "utf-32-le" => UnicodeClass.Utf32LeNoBom,
            "utf-32be" or "utf-32-be" => UnicodeClass.Utf32BeNoBom,
            "utf-16" => SniffUtf16Bom(filePath),
            "utf-32" => SniffUtf32Bom(filePath),

            "ascii" or "us-ascii" => UnicodeClass.Ascii,

            // Every other catalog token names a real code page the pipeline
            // is expected to identify through UtfUnknown. These used to fall
            // into None, which scored a correct legacy detection as
            // though nothing had been found.
            _ => UnicodeClass.Legacy,
        };

    /// <summary>
    /// Buckets a resolved result into the same coarse display categories
    /// used by the UTS report ("utf-8", "Legacy", "Binary", etc.), so both
    /// suites' reports read consistently.
    /// </summary>
    internal static string CategorizeForSummary(string encodingToken, UnicodeClass resolvedClass)
    {
        string token = encodingToken.ToLowerInvariant();

        if (token == "none-none")
            return "Binary";

        return resolvedClass switch
        {
            UnicodeClass.Utf8Bom or UnicodeClass.Utf8NoBom => "utf-8",
            UnicodeClass.Utf16LeBom or UnicodeClass.Utf16LeNoBom => "utf-16LE",
            UnicodeClass.Utf16BeBom or UnicodeClass.Utf16BeNoBom => "utf-16BE",
            UnicodeClass.Utf32LeBom or UnicodeClass.Utf32LeNoBom => "utf-32LE",
            UnicodeClass.Utf32BeBom or UnicodeClass.Utf32BeNoBom => "utf-32BE",
            UnicodeClass.Ascii => "us-ascii",
            UnicodeClass.None => "Binary",
            _ => "Legacy",
        };
    }

    private static UnicodeClass SniffUtf16Bom(string filePath)
    {
        Span<byte> header = stackalloc byte[2];
        if (!TryReadHeader(filePath, header))
            return UnicodeClass.None;

        if (header[0] == 0xFF && header[1] == 0xFE)
            return UnicodeClass.Utf16LeBom;

        if (header[0] == 0xFE && header[1] == 0xFF)
            return UnicodeClass.Utf16BeBom;

        return UnicodeClass.None;
    }

    private static UnicodeClass SniffUtf32Bom(string filePath)
    {
        Span<byte> header = stackalloc byte[4];
        if (!TryReadHeader(filePath, header))
            return UnicodeClass.None;

        if (header[0] == 0xFF && header[1] == 0xFE && header[2] == 0x00 && header[3] == 0x00)
            return UnicodeClass.Utf32LeBom;

        if (header[0] == 0x00 && header[1] == 0x00 && header[2] == 0xFE && header[3] == 0xFF)
            return UnicodeClass.Utf32BeBom;

        return UnicodeClass.None;
    }

    private static bool TryReadHeader(string filePath, Span<byte> buffer)
    {
        try
        {
            using FileStream stream = new(
                filePath, FileMode.Open, FileAccess.Read, FileShare.ReadWrite);

            return stream.Read(buffer) == buffer.Length;
        }
        catch (Exception)
        {
            return false;
        }
    }
}
