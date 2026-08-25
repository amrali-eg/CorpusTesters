using System.Text;

namespace UnicodeSuiteTester;

internal static class EncodingExtensions
{
    /// <summary>
    /// Strictly validates that the specified bytes are valid for the specified
    /// encoding. It does not attempt to detect the encoding.
    /// Invalid byte sequences are rejected instead of being replaced by a
    /// fallback character. Good negative test (can reject), but not a good
    /// positive test (cannot prove encoding; not specific).
    /// Incomplete trailing byte sequence is treated as incomplete rather than invalid.
    /// </summary>
    public static bool Validate(
        this Encoding encoding,
        ReadOnlySpan<byte> buffer)
    {
        ArgumentNullException.ThrowIfNull(encoding);

        if (buffer.IsEmpty)
            return false;

        // Force strict decoding regardless of the supplied Encoding instance.
        Decoder decoder = encoding.GetDecoder();
        decoder.Fallback = DecoderFallback.ExceptionFallback;

        try
        {
            //
            // The buffer is a detection sample, not necessarily the complete file.
            // Keep flush=false so an incomplete sequence at the sample boundary is
            // retained rather than treated as invalid. Invalid sequences occurring
            // within the sample still trigger DecoderFallbackException.
            //
            decoder.GetCharCount(buffer, flush: false);
            return true;
        }
        catch (DecoderFallbackException)
        {
            return false;
        }
    }


    /// <summary>
    ///     Returns <see cref="Encoding.WebName" />, appending "-bom"
    ///     when the encoding emits a byte order mark (BOM).
    /// </summary>
    internal static string GetWebNameWithBom(this Encoding encoding)
    {
        ArgumentNullException.ThrowIfNull(encoding);
        if (encoding.GetPreamble().Length == 0) return encoding.WebName;
        return encoding.WebName + "-bom";
    }
}
