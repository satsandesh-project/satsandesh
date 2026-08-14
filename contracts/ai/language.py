from enum import Enum


class LanguageCode(str, Enum):
    """
    Wire-level language tag for every AI contract in this package.

    Standard: BCP-47 primary language subtags (RFC 5646), restricted to the
    languages SatSandesh v1 actually supports. This is a closed enum, not a bare
    string — callers get validation and IDE autocomplete, and typos fail loudly
    instead of silently mis-routing a message.

    Why BCP-47 and not something more "correct" for speech/MT tooling: none of the
    models in this pipeline agree with each other. faster-whisper uses its own
    short codes, IndicTrans2 uses FLORES-200 codes (eng_Latn, hin_Deva, tel_Telu),
    and the TTS engine uses its own voice ids. There is no single model-native
    standard to defer to. BCP-47 is the standard the rest of the stack (browsers,
    HTTP Accept-Language, the eventual chat client) already speaks, so the contract
    speaks it too, and a per-model code-mapping table lives in the service layer
    where it belongs — model vocabulary is an implementation detail, not part of
    the interface. See DECISIONS.md.

    Tamil (ta) and Kannada (kn) are stretch goals for v1 and are deliberately NOT
    included yet. Do not add them speculatively — see Open Questions for how the
    team wants to handle that addition without breaking exhaustive `match`
    statements downstream.
    """

    ENGLISH = "en"
    HINDI = "hi"
    TELUGU = "te"
