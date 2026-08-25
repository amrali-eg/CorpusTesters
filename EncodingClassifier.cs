using System.Text;

namespace UnicodeSuiteTester;

/// <summary>
/// Maps the <see cref="Encoding"/> returned by <see cref="TextEncoding"/>
/// onto a <see cref="UnicodeClass"/>.
///
/// Two stages, because the pipeline has two sources. Anything
/// <see cref="UnicodeDetector"/> resolves is one of its cached singletons, so
/// reference equality identifies it exactly - including the BOM/no-BOM
/// distinction, which nothing else in the result carries. Anything UtfUnknown
/// resolves is an arbitrary instance, so it is matched by code page instead.
///
/// Reference equality alone is not enough once the harness runs the full
/// pipeline rather than UnicodeDetector by itself: UtfUnknown returns its own
/// Encoding objects, and matching only singletons would file every one of them
/// under Legacy - including a genuine ASCII or UTF-8 answer.
/// </summary>
internal static class EncodingClassifier
{
    private const int CodePageAscii = 20127;
    private const int CodePageUtf8 = 65001;
    private const int CodePageUtf16Le = 1200;
    private const int CodePageUtf16Be = 1201;
    private const int CodePageUtf32Le = 12000;
    private const int CodePageUtf32Be = 12001;

    internal static UnicodeClass Classify(Encoding? encoding)
    {
        if (encoding is null)
            return UnicodeClass.None;

        UnicodeClass singleton = ClassifySingleton(encoding);

        if (singleton != UnicodeClass.None)
            return singleton;

        // Not a UnicodeDetector singleton, so it came from UtfUnknown. Only
        // the code page is meaningful here; a BOM would have been caught by
        // UnicodeDetector first, so the BOM-less variant is the right answer
        // for any Unicode code page that reaches this point.
        return encoding.CodePage switch
        {
            CodePageAscii => UnicodeClass.Ascii,
            CodePageUtf8 => UnicodeClass.Utf8NoBom,
            CodePageUtf16Le => UnicodeClass.Utf16LeNoBom,
            CodePageUtf16Be => UnicodeClass.Utf16BeNoBom,
            CodePageUtf32Le => UnicodeClass.Utf32LeNoBom,
            CodePageUtf32Be => UnicodeClass.Utf32BeNoBom,
            _ => UnicodeClass.Legacy,
        };
    }

    /// <summary>
    /// Exact match against UnicodeDetector's cached instances, which is the
    /// only way to recover the BOM/no-BOM distinction from the result.
    /// </summary>
    private static UnicodeClass ClassifySingleton(Encoding encoding)
    {
        if (ReferenceEquals(encoding, UnicodeDetector.Utf8Bom))
            return UnicodeClass.Utf8Bom;

        if (ReferenceEquals(encoding, UnicodeDetector.Utf8NoBom))
            return UnicodeClass.Utf8NoBom;

        if (ReferenceEquals(encoding, UnicodeDetector.Utf16LittleEndianBom))
            return UnicodeClass.Utf16LeBom;

        if (ReferenceEquals(encoding, UnicodeDetector.Utf16LittleEndianNoBom))
            return UnicodeClass.Utf16LeNoBom;

        if (ReferenceEquals(encoding, UnicodeDetector.Utf16BigEndianBom))
            return UnicodeClass.Utf16BeBom;

        if (ReferenceEquals(encoding, UnicodeDetector.Utf16BigEndianNoBom))
            return UnicodeClass.Utf16BeNoBom;

        if (ReferenceEquals(encoding, UnicodeDetector.Utf32LittleEndianBom))
            return UnicodeClass.Utf32LeBom;

        if (ReferenceEquals(encoding, UnicodeDetector.Utf32LittleEndianNoBom))
            return UnicodeClass.Utf32LeNoBom;

        if (ReferenceEquals(encoding, UnicodeDetector.Utf32BigEndianBom))
            return UnicodeClass.Utf32BeBom;

        if (ReferenceEquals(encoding, UnicodeDetector.Utf32BigEndianNoBom))
            return UnicodeClass.Utf32BeNoBom;

        return UnicodeClass.None;
    }
}
