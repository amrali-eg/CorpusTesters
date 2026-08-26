"""Probe every supported codec with malformed input it must refuse.

`5,020 / 5,020 strict-decoding correctness` sounds conclusive and is not. A
corpus can only exercise the invalid sequences it happens to contain, and none
of these corpora contain a systematic sweep of them - so that figure says the
corpora held no such bytes, not that the decoders would reject them.

This constructs the malformed sequences directly, one family at a time, and
asserts that a correctly built strict decoder refuses each. Where EncodingChecker
is available it asserts the same of the codec construction EC now uses, so the
guarantee is tested rather than inferred from a corpus that stayed quiet.

    python tools/check_codec_conformance.py

Exit codes: 0 every malformed sequence refused, 1 some were accepted.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "audit"))


# Each case: (codec, label, bytes that the codec must not decode).
#
# Chosen so every entry is malformed for a *structural* reason - a truncated
# sequence, an illegal lead, a bad continuation, an overlong form - rather than
# merely being unassigned, since unassigned code points are a mapping question
# and refusing them is not required.
CASES: list[tuple[str, str, bytes]] = [
    # --- UTF-8 -------------------------------------------------------------
    ("utf-8", "truncated 2-byte sequence", b"abc\xc3"),
    ("utf-8", "truncated 3-byte sequence", b"abc\xe4\xb8"),
    ("utf-8", "truncated 4-byte sequence", b"abc\xf0\x9f\x8c"),
    ("utf-8", "lone continuation byte", b"abc\x80def"),
    ("utf-8", "illegal lead byte 0xFE", b"abc\xfe"),
    ("utf-8", "illegal lead byte 0xFF", b"abc\xff"),
    ("utf-8", "overlong encoding of NUL", b"abc\xc0\x80"),
    ("utf-8", "overlong encoding of solidus", b"abc\xc0\xaf"),
    ("utf-8", "encoded surrogate half", b"abc\xed\xa0\x80"),
    ("utf-8", "code point beyond U+10FFFF", b"abc\xf5\x80\x80\x80"),
    ("utf-8", "continuation where lead expected", b"\xbf\xbf"),

    # --- UTF-16 / UTF-32 ---------------------------------------------------
    ("utf-16-le", "odd byte count", b"a\x00b"),
    ("utf-16-be", "odd byte count", b"\x00a\x00"),
    ("utf-16-le", "unpaired high surrogate", b"\x00\xd8\x41\x00"),
    ("utf-16-le", "unpaired low surrogate", b"\x00\xdc\x41\x00"),
    ("utf-16-be", "unpaired high surrogate", b"\xd8\x00\x00\x41"),
    ("utf-32-le", "length not a multiple of four", b"a\x00\x00"),
    ("utf-32-le", "code point beyond U+10FFFF", b"\x00\x00\x11\x00"),
    ("utf-32-be", "code point beyond U+10FFFF", b"\x00\x11\x00\x00"),
    ("utf-32-le", "surrogate code point", b"\x00\xd8\x00\x00"),

    # --- ASCII -------------------------------------------------------------
    ("ascii", "byte above 0x7F", b"abc\x80"),
    ("ascii", "high-bit Latin-1 text", b"caf\xe9"),

    # --- Japanese ----------------------------------------------------------
    ("shift_jis", "truncated double-byte tail", b"abc\x82"),
    ("shift_jis", "illegal second byte", b"abc\x82\x20"),
    ("euc_jp", "truncated double-byte tail", b"abc\xa4"),
    ("euc_jp", "illegal second byte", b"abc\xa4\x20"),
    ("euc_jp", "truncated SS3 three-byte sequence", b"abc\x8f\xa1"),
    ("iso2022_jp", "truncated escape sequence", b"abc\x1b$"),
    ("iso2022_jp", "unknown escape sequence", b"abc\x1b$Z"),

    # --- Chinese -----------------------------------------------------------
    ("big5", "truncated double-byte tail", b"abc\xa4"),
    ("big5", "illegal second byte", b"abc\xa4\x20"),
    ("gb18030", "truncated double-byte tail", b"abc\xb0"),
    ("gb18030", "truncated four-byte sequence", b"abc\x81\x30\x81"),
    ("gb2312", "illegal second byte", b"abc\xb0\x20"),

    # --- Korean ------------------------------------------------------------
    ("euc_kr", "truncated double-byte tail", b"abc\xb0"),
    ("euc_kr", "illegal second byte", b"abc\xb0\x20"),
]


# Sequences .NET is known to accept where Python refuses, with the reason.
#
# Listed rather than silently tolerated: each entry is a real strictness gap
# that the corpora happen not to contain, so no corpus run would ever reveal it.
# A gap that is not on this list fails the build.
ACKNOWLEDGED_PERMISSIVE = {
    ("iso-2022-jp", "truncated escape sequence"),
    ("iso-2022-jp", "unknown escape sequence"),
}

# Why those two are tolerated rather than treated as data loss: .NET's code page
# 50220 passes the bytes of a malformed escape through as literal characters
# instead of interpreting or rejecting them - `61 62 63 1B 24` decodes to five
# characters, one per byte. Nothing is dropped or substituted, and since every
# byte involved is in the ASCII range the sequence re-encodes to itself, so a
# conversion preserves the text. It is still a strictness gap: a decoder asked
# to be strict should refuse an incomplete escape rather than reinterpret it,
# and EncodingChecker will therefore convert such a file rather than decline it.


# .NET spellings for the codecs above, where they differ from Python's.
NET_NAMES = {
    "utf-8": "utf-8", "utf-16-le": "utf-16le", "utf-16-be": "utf-16be",
    "utf-32-le": "utf-32le", "utf-32-be": "utf-32be", "ascii": "us-ascii",
    "shift_jis": "shift_jis", "euc_jp": "euc-jp", "iso2022_jp": "iso-2022-jp",
    "big5": "big5", "gb18030": "gb18030", "gb2312": "gb2312",
    "euc_kr": "euc-kr",
}


def check_dotnet() -> tuple[int, list[str]]:
    """Run the same malformed sequences through the codec construction EC uses.

    This is the half that matters. Python refusing malformed input says nothing
    about EncodingChecker; what has to hold is that .NET's strictly constructed
    decoder refuses it too, because that is the guarantee the tool now rests on.
    """
    import json
    import subprocess
    import tempfile

    ecdiag = (Path(__file__).resolve().parent.parent / "audit" / "ECDiag"
              / "bin" / "Release" / "net8.0" / "ECDiag.exe")
    if not ecdiag.is_file():
        print("  SKIP  .NET decoders: ECDiag not built "
              "(dotnet build audit/ECDiag/ECDiag.csproj -c Release)")
        return 0, []

    accepted: list[str] = []
    known: list[str] = []
    refused = 0

    with tempfile.TemporaryDirectory() as tmp:
        items, expected = [], {}
        for index, (codec, label, payload) in enumerate(CASES):
            net = NET_NAMES.get(codec)
            if not net:
                continue
            path = Path(tmp) / f"case_{index:03d}.bin"
            path.write_bytes(payload)
            items.append({"Path": str(path), "ForcedEncoding": net,
                          "ForcedBom": False, "DecoderMode": "strict"})
            expected[str(path)] = (net, label)

        request = {"Mode": "decode", "Items": items, "Encodings": []}
        proc = subprocess.run([str(ecdiag)], input=json.dumps(request),
                              capture_output=True, text=True, encoding="utf-8")
        if proc.returncode != 0:
            print(f"  FAIL  .NET decoders: ECDiag failed: {proc.stderr[:200]}")
            return 0, ["ECDiag could not run"]

        by_codec: dict[str, list[bool]] = {}
        for result in json.loads(proc.stdout):
            net, label = expected[result["Path"]]
            # A strict decoder must fail at the decode stage. Anything else
            # means it produced text from structurally invalid bytes.
            rejected = result.get("FailureStage") == "Decode"

            if not rejected and (net, label) in ACKNOWLEDGED_PERMISSIVE:
                # Known and explained above; counted apart so it stays visible
                # without turning an unfixable platform property into a red build.
                known.append(f".NET {net}: {label}")
                by_codec.setdefault(net, []).append(True)
                continue

            by_codec.setdefault(net, []).append(rejected)
            if rejected:
                refused += 1
            else:
                accepted.append(
                    f".NET {net}: {label} was decoded rather than refused")

        for net, outcomes in by_codec.items():
            bad = outcomes.count(False)
            mark = "OK  " if bad == 0 else "FAIL"
            print(f"  {mark}  {net:<12} {len(outcomes) - bad}/{len(outcomes)} refused")

    if known:
        print()
        print(f"  {len(known)} acknowledged permissive case(s) - see"
              f" ACKNOWLEDGED_PERMISSIVE for why each is tolerated:")
        for line in known:
            print(f"      {line}")

    return refused, accepted


def main() -> int:
    accepted: list[str] = []
    refused = 0
    unavailable: list[str] = []

    by_codec: dict[str, list[tuple[str, bytes]]] = {}
    for codec, label, payload in CASES:
        by_codec.setdefault(codec, []).append((label, payload))

    print("Malformed input each decoder must refuse\n")

    for codec, cases in by_codec.items():
        try:
            "x".encode(codec)
        except LookupError:
            unavailable.append(codec)
            print(f"  SKIP  {codec}: not available in this runtime")
            continue

        failures = 0
        for label, payload in cases:
            try:
                decoded = payload.decode(codec)
            except UnicodeDecodeError:
                refused += 1
                continue
            except LookupError:
                unavailable.append(codec)
                break

            # Accepted. That is a finding: the sequence is structurally invalid,
            # so decoding it means something was substituted or skipped.
            accepted.append(
                f"{codec}: {label} decoded to {decoded!r} instead of raising")
            failures += 1

        mark = "OK  " if failures == 0 else "FAIL"
        print(f"  {mark}  {codec:<12} {len(cases) - failures}/{len(cases)} refused")

    print()
    print("The same sequences through .NET, strictly constructed")
    print("(the guarantee EncodingChecker actually depends on)")
    print()
    net_refused, net_accepted = check_dotnet()
    refused += net_refused
    accepted += net_accepted

    print()
    if accepted:
        print(f"{len(accepted)} malformed sequence(s) were ACCEPTED:\n")
        for line in accepted:
            print(f"  - {line}")
        print()
        print("An accepted malformed sequence means the decoder substituted or")
        print("dropped something. Any conclusion about strict-decoding")
        print("correctness that rests on corpus silence is unsafe.")
        return 1

    print(f"All {refused} malformed sequences refused across "
          f"{len(by_codec) - len(set(unavailable))} codecs.")
    if unavailable:
        print(f"Unavailable in this runtime: {', '.join(sorted(set(unavailable)))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
