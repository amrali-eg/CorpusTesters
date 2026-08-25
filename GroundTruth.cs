namespace UnicodeSuiteTester;

/// <summary>
/// The encoding classes the detection pipeline can report: the 10 Unicode
/// variants, plus ASCII and Legacy, plus a catch-all for data it leaves
/// undetected entirely (invalid or binary).
///
/// ASCII and Legacy used to be folded into a single "non-Unicode" bucket,
/// which made three very different outcomes indistinguishable in the report:
/// "correctly identified as a legacy code page", "correctly identified as
/// ASCII", and "nothing identified at all". Since the harness runs the full
/// <see cref="TextEncoding"/> pipeline - UnicodeDetector, then UtfUnknown,
/// then independent validation - it genuinely answers all three, so each is
/// its own class and every claim is scored the same way.
///
/// <see cref="None"/> is not a category of file; it is the absence of a
/// claim, recorded when the pipeline names no encoding at all.
/// </summary>
internal enum UnicodeClass
{
    None,
    Utf8NoBom,
    Utf8Bom,
    Utf16LeNoBom,
    Utf16LeBom,
    Utf16BeNoBom,
    Utf16BeBom,
    Utf32LeNoBom,
    Utf32LeBom,
    Utf32BeNoBom,
    Utf32BeBom,
    Ascii,
    Legacy,
}

internal static class UnicodeClassLabels
{
    /// <summary>
    /// Every class the pipeline can report, in display order: the 5 BOM
    /// variants, then the 5 BOM-less variants, then ASCII and Legacy.
    /// <see cref="UnicodeClass.None"/> is intentionally excluded: it
    /// means "nothing was detected", not a detectable class.
    /// </summary>
    internal static readonly UnicodeClass[] DetectableClasses =
    [
        UnicodeClass.Utf8Bom,
        UnicodeClass.Utf16LeBom,
        UnicodeClass.Utf16BeBom,
        UnicodeClass.Utf32LeBom,
        UnicodeClass.Utf32BeBom,
        UnicodeClass.Utf8NoBom,
        UnicodeClass.Utf16LeNoBom,
        UnicodeClass.Utf16BeNoBom,
        UnicodeClass.Utf32LeNoBom,
        UnicodeClass.Utf32BeNoBom,
        UnicodeClass.Ascii,
        UnicodeClass.Legacy,
    ];

    internal static string Label(UnicodeClass value) => value switch
    {
        UnicodeClass.None => "(undetected)",
        UnicodeClass.Utf8NoBom => "utf-8",
        UnicodeClass.Utf8Bom => "utf-8-bom",
        UnicodeClass.Utf16LeNoBom => "utf-16LE",
        UnicodeClass.Utf16LeBom => "utf-16LE-bom",
        UnicodeClass.Utf16BeNoBom => "utf-16BE",
        UnicodeClass.Utf16BeBom => "utf-16BE-bom",
        UnicodeClass.Utf32LeNoBom => "utf-32LE",
        UnicodeClass.Utf32LeBom => "utf-32LE-bom",
        UnicodeClass.Utf32BeNoBom => "utf-32BE",
        UnicodeClass.Utf32BeBom => "utf-32BE-bom",
        UnicodeClass.Ascii => "ascii",
        UnicodeClass.Legacy => "legacy",
        _ => value.ToString(),
    };
}
