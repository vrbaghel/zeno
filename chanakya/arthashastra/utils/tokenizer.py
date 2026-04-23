from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from chanakya.arthashastra.models import TokenEstimationMethod


@dataclass(frozen=True)
class TokenCountResult:
    tokens: int | None
    method: TokenEstimationMethod


def _try_sentencepiece_count(text: str, model_path: Path) -> int:
    # Imported lazily so sentencepiece is optional at runtime.
    import sentencepiece as spm  # type: ignore

    sp = spm.SentencePieceProcessor()
    sp.load(str(model_path))
    return len(sp.encode(text, out_type=int))


def count(text: str, *, provider: str, model: str | None = None, sp_model_path: str | None = None) -> TokenCountResult:
    """
    Token counting strategy per provider.

    For Gemini, if a SentencePiece model is provided, returns exact count.
    If not available, returns unavailable (rather than pretending to be exact).
    """

    provider_norm = provider.strip().lower()

    if provider_norm == "gemini":
        if sp_model_path:
            try:
                tokens = _try_sentencepiece_count(text, Path(sp_model_path))
                return TokenCountResult(tokens=tokens, method=TokenEstimationMethod.exact)
            except Exception:
                return TokenCountResult(tokens=None, method=TokenEstimationMethod.unavailable)
        return TokenCountResult(tokens=None, method=TokenEstimationMethod.unavailable)

    return TokenCountResult(tokens=None, method=TokenEstimationMethod.unavailable)

