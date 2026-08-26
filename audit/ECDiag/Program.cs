using System.Globalization;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;
using CorpusTesting;

namespace ECDiag;

/// <summary>
/// Instruments EncodingChecker's codec stages so the audit can attribute a
/// failure to Detection, Decode or Encode rather than inferring a cause from
/// the final files. EC's own CSV cannot answer this: its columns are
/// File,Encoding,BOM,Target,TargetBOM,Result, and ConversionReportEntry's
/// Diagnostic field is documented as "not included in CSV output".
///
/// Two decoder constructions are supported, and the difference between them
/// is the point of PHASE 0:
///
///   Production - Decoder d = encoding.GetDecoder();
///                d.Fallback = DecoderFallback.ExceptionFallback;
///
///                This is what EncodingConverter.MakeStrictDecoder does. For
///                CodePagesEncodingProvider encodings the assignment has no
///                effect: d.Fallback is null beforehand and stays ineffective,
///                so undecodable bytes are silently best-fitted instead of
///                throwing.
///
///   Strict     - Encoding.GetEncoding(codePage,
///                    EncoderFallback.ExceptionFallback,
///                    DecoderFallback.ExceptionFallback)
///
///                Fallbacks supplied at construction, which does take effect.
///
/// Running both over the same bytes is what demonstrates silent decode loss:
/// strict throws, production returns altered text, and EC reports Converted.
///
/// Reads a JSON request on stdin and writes a JSON array on stdout so the
/// orchestrator can hand over thousands of files per process launch.
/// </summary>
internal static class Program
{
    private sealed record Request(string Mode, List<RequestItem>? Items, List<string>? Encodings);

    private sealed record RequestItem(
        string Path,
        string? ForcedEncoding,
        bool ForcedBom,
        string? DecoderMode);

    private sealed record Result
    {
        public required string Path { get; init; }
        public string? DetectedEncoding { get; init; }
        public int DetectedCodePage { get; init; }
        public string? DetectedBom { get; init; }
        public string? DecoderMode { get; init; }

        /// <summary>Detection, Decode, Encode, Read, or null when all stages ran.</summary>
        public string? FailureStage { get; init; }
        public string? ExceptionType { get; init; }
        public string? ExceptionMessage { get; init; }

        /// <summary>SHA-256 over the decoded text's code points as UTF-32LE.</summary>
        public string? TextSha256 { get; init; }
        public int TextLength { get; init; }
        public int EncodedLength { get; init; }

        /// <summary>First 80 code points, for diagnostics.</summary>
        public string? TextHead { get; init; }
    }

    /// <summary>PHASE 0: what a given encoding's codecs actually do.</summary>
    private sealed record StrictnessResult
    {
        public required string Encoding { get; init; }
        public int CodePage { get; init; }
        public bool Available { get; init; }

        /// <summary>Strict, NonStrict, or Unknown.</summary>
        public required string DecoderStrictness { get; init; }
        public required string EncoderStrictness { get; init; }
        public string? Note { get; init; }
    }

    private static readonly JsonSerializerOptions Json = new()
    {
        PropertyNameCaseInsensitive = true,
        DefaultIgnoreCondition = JsonIgnoreCondition.Never,
    };

    private static int Main()
    {
        Encoding.RegisterProvider(CodePagesEncodingProvider.Instance);
        Console.OutputEncoding = new UTF8Encoding(false);

        Request? request;

        try
        {
            request = JsonSerializer.Deserialize<Request>(Console.In.ReadToEnd(), Json);
        }
        catch (JsonException ex)
        {
            // Exit 2 is the audit's infrastructure-failure code: a malformed
            // request is a fault in the harness, never evidence about EC.
            Console.Error.WriteLine($"ECDiag: malformed request: {ex.Message}");
            return 2;
        }

        if (request is null)
        {
            Console.Error.WriteLine("ECDiag: empty request.");
            return 2;
        }

        if (request.Mode == "strictness")
        {
            List<StrictnessResult> probes = [];

            foreach (string name in request.Encodings ?? [])
                probes.Add(ProbeStrictness(name));

            Console.Out.Write(JsonSerializer.Serialize(probes));
            return 0;
        }

        List<Result> results = [];

        foreach (RequestItem item in request.Items ?? [])
        {
            bool production = !string.Equals(item.DecoderMode, "strict", StringComparison.OrdinalIgnoreCase);

            results.Add(request.Mode switch
            {
                "pipeline" => RunPipeline(item.Path, forced: null, false, production),
                "forced" => RunPipeline(item.Path, item.ForcedEncoding, item.ForcedBom, production),
                "decode" => RunPipeline(item.Path, item.ForcedEncoding, item.ForcedBom, production, decodeOnly: true),
                _ => new Result
                {
                    Path = item.Path,
                    FailureStage = "Unknown",
                    ExceptionType = "ArgumentException",
                    ExceptionMessage = $"unknown mode '{request.Mode}'",
                },
            });
        }

        Console.Out.Write(JsonSerializer.Serialize(results));
        return 0;
    }

    /// <summary>
    /// PHASE 0. Determines empirically whether each construction enforces
    /// strict fallback, using bytes/characters chosen to be unmappable for the
    /// encoding under test. Never assumes an assignment took effect.
    /// </summary>
    private static StrictnessResult ProbeStrictness(string name)
    {
        Encoding encoding;

        try
        {
            encoding = Encoding.GetEncoding(name);
        }
        catch (Exception ex)
        {
            return new StrictnessResult
            {
                Encoding = name, Available = false,
                DecoderStrictness = "Unknown", EncoderStrictness = "Unknown",
                Note = $"{ex.GetType().Name}: {ex.Message}",
            };
        }

        return new StrictnessResult
        {
            Encoding = name,
            CodePage = encoding.CodePage,
            Available = true,
            DecoderStrictness = ProbeDecoder(encoding),
            EncoderStrictness = ProbeEncoder(encoding),
        };
    }

    /// <summary>
    /// Feeds bytes that the strictly-constructed encoding rejects. If the
    /// production construction accepts them, its fallback assignment did not
    /// take effect.
    /// </summary>
    private static string ProbeDecoder(Encoding encoding)
    {
        // Probes chosen to be invalid in at least one family; the first that
        // the strict construction rejects becomes the test case.
        byte[][] candidates =
        [
            [0x8F, 0xB0, 0xDF],             // EUC-JP SS3 / JIS X 0212
            [0xFF, 0xFE, 0xFD, 0xFC],       // invalid almost everywhere
            [0x81, 0x20],                   // lone lead byte
            [0xC0, 0xAF],                   // overlong UTF-8
            [0xED, 0xA0, 0x80],             // encoded surrogate
        ];

        Encoding strict;

        try
        {
            strict = Encoding.GetEncoding(
                encoding.CodePage,
                EncoderFallback.ExceptionFallback,
                DecoderFallback.ExceptionFallback);
        }
        catch
        {
            return "Unknown";
        }

        foreach (byte[] probe in candidates)
        {
            bool strictRejects;

            try
            {
                strict.GetString(probe);
                strictRejects = false;
            }
            catch (DecoderFallbackException)
            {
                strictRejects = true;
            }
            catch
            {
                continue;
            }

            if (!strictRejects)
                continue;   // not a discriminating probe for this encoding

            // The strict construction rejects it. Does EC's production one?
            try
            {
                Decoder decoder = encoding.GetDecoder();
                decoder.Fallback = DecoderFallback.ExceptionFallback;

                char[] buffer = new char[encoding.GetMaxCharCount(probe.Length)];
                decoder.GetChars(probe, 0, probe.Length, buffer, 0, flush: true);

                return "NonStrict";   // accepted what strict rejects
            }
            catch (DecoderFallbackException)
            {
                return "Strict";
            }
            catch
            {
                return "Unknown";
            }
        }

        return "Unknown";   // no probe discriminated
    }

    private static string ProbeEncoder(Encoding encoding)
    {
        // Characters unlikely to be representable outside their own family.
        foreach (string probe in new[] { "café", "日本語", "Ωμέγα", "\U0001F600" })
        {
            Encoding strict;

            try
            {
                strict = Encoding.GetEncoding(
                    encoding.CodePage,
                    EncoderFallback.ExceptionFallback,
                    DecoderFallback.ExceptionFallback);
            }
            catch
            {
                return "Unknown";
            }

            bool strictRejects;

            try
            {
                strict.GetBytes(probe);
                strictRejects = false;
            }
            catch (EncoderFallbackException)
            {
                strictRejects = true;
            }
            catch
            {
                continue;
            }

            if (!strictRejects)
                continue;

            try
            {
                Encoder encoder = encoding.GetEncoder();
                encoder.Fallback = EncoderFallback.ExceptionFallback;

                byte[] buffer = new byte[encoding.GetMaxByteCount(probe.Length)];
                encoder.GetBytes(probe.ToCharArray(), 0, probe.Length, buffer, 0, flush: true);

                return "NonStrict";
            }
            catch (EncoderFallbackException)
            {
                return "Strict";
            }
            catch
            {
                return "Unknown";
            }
        }

        return "Unknown";
    }

    private static Result RunPipeline(
        string path,
        string? forced,
        bool forcedBom,
        bool production,
        bool decodeOnly = false)
    {
        string modeLabel = production ? "production" : "strict";
        byte[] bytes;

        try
        {
            bytes = File.ReadAllBytes(path);
        }
        catch (Exception ex)
        {
            return new Result
            {
                Path = path, DecoderMode = modeLabel, FailureStage = "Read",
                ExceptionType = ex.GetType().Name, ExceptionMessage = Trim(ex.Message),
            };
        }

        Encoding source;

        if (forced is null)
        {
            Encoding? detected = TextEncoding.DetectFromFile(path);

            if (detected is null)
            {
                // Not an error: EC declines to convert what it cannot name.
                return new Result
                {
                    Path = path, DecoderMode = modeLabel, FailureStage = "Detection",
                    ExceptionType = "Undetected",
                    ExceptionMessage = "the detector named no encoding",
                };
            }

            source = detected;
        }
        else
        {
            try
            {
                source = Encoding.GetEncoding(forced);
            }
            catch (Exception ex)
            {
                return new Result
                {
                    Path = path, DecoderMode = modeLabel, FailureStage = "Detection",
                    ExceptionType = ex.GetType().Name,
                    ExceptionMessage = $"forced encoding '{forced}' unavailable: {ex.Message}",
                };
            }
        }

        byte[] preamble = source.GetPreamble();
        bool hasPreamble = preamble.Length > 0 && bytes.AsSpan().StartsWith(preamble);
        ReadOnlySpan<byte> payload = hasPreamble ? bytes.AsSpan(preamble.Length) : bytes.AsSpan();

        string detectedBom = forced is null
            ? (hasPreamble ? "BOM" : "NoBOM")
            : (forcedBom ? "BOM" : "NoBOM");

        string text;

        try
        {
            text = production
                ? DecodeAsEcDoes(source, payload)
                : DecodeStrictly(source, payload);
        }
        catch (Exception ex)
        {
            return new Result
            {
                Path = path,
                DetectedEncoding = source.WebName,
                DetectedCodePage = source.CodePage,
                DetectedBom = detectedBom,
                DecoderMode = modeLabel,
                FailureStage = "Decode",
                ExceptionType = ex.GetType().Name,
                ExceptionMessage = Trim(ex.Message),
            };
        }

        if (decodeOnly)
        {
            return new Result
            {
                Path = path,
                DetectedEncoding = source.WebName,
                DetectedCodePage = source.CodePage,
                DetectedBom = detectedBom,
                DecoderMode = modeLabel,
                TextSha256 = HashText(text),
                TextLength = text.Length,
                TextHead = Head(text),
            };
        }

        int encodedLength;

        try
        {
            Encoding utf8 = new UTF8Encoding(false, throwOnInvalidBytes: true);
            encodedLength = utf8.GetBytes(text).Length;
        }
        catch (Exception ex)
        {
            return new Result
            {
                Path = path,
                DetectedEncoding = source.WebName,
                DetectedCodePage = source.CodePage,
                DetectedBom = detectedBom,
                DecoderMode = modeLabel,
                TextSha256 = HashText(text),
                TextLength = text.Length,
                TextHead = Head(text),
                FailureStage = "Encode",
                ExceptionType = ex.GetType().Name,
                ExceptionMessage = Trim(ex.Message),
            };
        }

        return new Result
        {
            Path = path,
            DetectedEncoding = source.WebName,
            DetectedCodePage = source.CodePage,
            DetectedBom = detectedBom,
            DecoderMode = modeLabel,
            TextSha256 = HashText(text),
            TextLength = text.Length,
            EncodedLength = encodedLength,
            TextHead = Head(text),
        };
    }

    /// <summary>
    /// Reproduces EncodingConverter.MakeStrictDecoder exactly, including its
    /// ineffective post-construction fallback assignment. This is the shipped
    /// behaviour, not a corrected one.
    /// </summary>
    private static string DecodeAsEcDoes(Encoding encoding, ReadOnlySpan<byte> bytes)
    {
        Decoder decoder = encoding.GetDecoder();
        decoder.Fallback = DecoderFallback.ExceptionFallback;

        char[] buffer = new char[encoding.GetMaxCharCount(Math.Max(bytes.Length, 1))];
        int written = decoder.GetChars(bytes.ToArray(), 0, bytes.Length, buffer, 0, flush: true);

        return new string(buffer, 0, written);
    }

    /// <summary>
    /// Fallbacks supplied at construction, which is the form that actually
    /// enforces strictness for CodePagesEncodingProvider encodings.
    /// </summary>
    private static string DecodeStrictly(Encoding encoding, ReadOnlySpan<byte> bytes)
    {
        Encoding strict = Encoding.GetEncoding(
            encoding.CodePage,
            EncoderFallback.ExceptionFallback,
            DecoderFallback.ExceptionFallback);

        return strict.GetString(bytes);
    }

    /// <summary>
    /// SHA-256 over the code-point sequence, each as a little-endian uint32.
    /// No normalization is applied; the hash is evidence, and equality is
    /// still decided by direct comparison.
    /// </summary>
    private static string HashText(string text)
    {
        using IncrementalHash hash = IncrementalHash.CreateHash(HashAlgorithmName.SHA256);
        Span<byte> scalar = stackalloc byte[4];

        for (int i = 0; i < text.Length;)
        {
            int codePoint;

            if (char.IsHighSurrogate(text[i]) && i + 1 < text.Length && char.IsLowSurrogate(text[i + 1]))
            {
                codePoint = char.ConvertToUtf32(text[i], text[i + 1]);
                i += 2;
            }
            else
            {
                codePoint = text[i];
                i++;
            }

            scalar[0] = (byte)codePoint;
            scalar[1] = (byte)(codePoint >> 8);
            scalar[2] = (byte)(codePoint >> 16);
            scalar[3] = (byte)(codePoint >> 24);
            hash.AppendData(scalar);
        }

        return Convert.ToHexString(hash.GetHashAndReset()).ToLower(CultureInfo.InvariantCulture);
    }

    private static string Head(string text) =>
        text.Length <= 80 ? text : text[..80];

    private static string Trim(string message) =>
        message.Length <= 300 ? message : message[..300];
}
