"""Turn the fixed script into N pre-rendered wordings, ahead of the show.

Only Kora's six narration lines change; the punchlines, the callbacks and every
non-English line stay exactly as written, because the sketch runs on setups
paying off later (the USB-port joke in line 14 is what makes line 31 land).

Variant 1 is always the verbatim script. That is what makes the LLM optional:
if the model is unreachable or answers badly, generation still produces a
complete, correct show.

    uv run python -m boma_exhibit.variator --dry-run
    uv run python -m boma_exhibit.variator --dry-run -n 3
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from boma_exhibit.config import Config, load_config
from boma_exhibit.llm_client import LLMError, chat, extract_json_object
from boma_exhibit.personas import PROTECTED_TERMS, SYSTEM_PROMPT, build_user_prompt
from boma_exhibit.script_data import LINES, line_by_number

# A rewrite may run a little long, but not so long that the timing drifts.
MAX_LENGTH_RATIO = 1.35

# Things a rewrite must never contain -- signs the model slipped into writing
# a document instead of a spoken line.
BANNED_SUBSTRINGS = ("**", "##", "\n-", "Kora:", "[", "]")


def _acceptable(original: str, candidate: Any) -> tuple[bool, str]:
    """Reject rewrites that would break the sketch. Returns (ok, reason)."""
    if not isinstance(candidate, str):
        return False, "not a string"

    text = candidate.strip()
    if not text:
        return False, "empty"

    if len(text) > len(original) * MAX_LENGTH_RATIO:
        return False, f"too long ({len(text)} vs {len(original)})"

    for banned in BANNED_SUBSTRINGS:
        if banned in text:
            return False, f"contains {banned!r}"

    # Every proper noun present in the original must survive.
    for term in PROTECTED_TERMS:
        if term in original and term not in text:
            return False, f"dropped {term!r}"

    return True, ""


def _clean_pool(original: str, candidates: Any, verbose: bool) -> list[str]:
    """Keep the usable rewrites, in order, without duplicates."""
    kept: list[str] = []
    seen = {original.strip().lower()}

    if not isinstance(candidates, list):
        return kept

    for candidate in candidates:
        ok, reason = _acceptable(original, candidate)
        text = candidate.strip() if isinstance(candidate, str) else str(candidate)
        if not ok:
            if verbose:
                print(f"      rejected ({reason}): {text[:70]}")
            continue
        key = text.lower()
        if key in seen:
            if verbose:
                print(f"      rejected (duplicate): {text[:70]}")
            continue
        seen.add(key)
        kept.append(text)
    return kept


def _ask(config: Config, targets: list[dict[str, Any]], count: int) -> dict[str, Any]:
    """One request covering `targets`. Returns the rewrites map, or {}."""
    reply = chat(
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_prompt(LINES, targets, count)},
        ],
        base_url=config.llm_base_url,
        model=config.llm_model,
        api_key=config.llm_api_key,
        # Warm enough to phrase things differently, cool enough to stay in
        # everyday English. Higher than this and it starts reaching for odd
        # synonyms, which reads worse on stage than repeating a line verbatim.
        temperature=0.7,
    )
    rewrites = extract_json_object(reply).get("rewrites", {})
    return rewrites if isinstance(rewrites, dict) else {}


def _pool_from(rewrites: dict[str, Any], number: int, original: str,
               verbose: bool) -> list[str]:
    """Usable rewrites for one line. Models key by int or by string; take both."""
    return _clean_pool(
        original, rewrites.get(str(number), rewrites.get(number, [])), verbose
    )


def build_variants(
    config: Config,
    *,
    variant_count: int | None = None,
    verbose: bool = False,
) -> dict[int, list[str]]:
    """Wordings for every varied line: {line number: [v1, v2, ... vN]}.

    v1 is always the original. Slots the model could not fill fall back to the
    original too, so every line always ends up with exactly N entries and the
    variant directories stay aligned.

    Two passes. The first asks for all six lines at once, which is cheap and
    lets the model see the whole sketch while it works. But this model thinks at
    length before answering, and a long answer on top of that can run past the
    token budget and come back as truncated JSON -- losing all six lines. So any
    line the batch did not fill is retried on its own, where the reply is short
    enough to be reliable. The model is already resident by then, so the retries
    are fast.
    """
    count = variant_count or config.variant_count
    targets = [line_by_number(n) for n in config.vary_lines]

    originals = {line["n"]: line["text"] for line in targets}
    if count <= 1 or not targets:
        return {n: [text] for n, text in originals.items()}

    wanted = count - 1
    pools: dict[int, list[str]] = {n: [] for n in originals}

    try:
        rewrites = _ask(config, targets, count)
        for number, original in originals.items():
            pools[number] = _pool_from(rewrites, number, original, verbose)
    except LLMError as exc:
        print(f"  Batch request failed ({exc}); retrying line by line.", flush=True)

    for line in targets:
        number = line["n"]
        if len(pools[number]) >= wanted:
            continue
        try:
            print(f"  Re-asking for line {number} on its own...", flush=True)
            rewrites = _ask(config, [line], count)
            extra = _pool_from(rewrites, number, originals[number], verbose)
            have = {text.lower() for text in pools[number]}
            pools[number].extend(t for t in extra if t.lower() not in have)
        except LLMError as exc:
            print(f"  Line {number} could not be rewritten ({exc}); keeping the original.",
                  flush=True)

    result: dict[int, list[str]] = {}
    for number, original in originals.items():
        wordings = [original] + pools[number][:wanted]
        while len(wordings) < count:
            wordings.append(original)
        result[number] = wordings

    return result


def _report(variants: dict[int, list[str]]) -> None:
    count = len(next(iter(variants.values()))) if variants else 0
    filled = 0
    total = 0
    for number, wordings in sorted(variants.items()):
        original = wordings[0]
        print(f"\nLine {number}")
        for index, text in enumerate(wordings, start=1):
            if index == 1:
                print(f"  v{index:02d} (original) {text}")
                continue
            total += 1
            if text == original:
                print(f"  v{index:02d} (fallback) {text}")
            else:
                filled += 1
                print(f"  v{index:02d}            {text}")
    if total:
        print(f"\n{filled}/{total} alternative slots filled by the model "
              f"({count} variants per line).")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-n", "--variants", type=int, default=None,
                        help="how many wordings per line (default: VARIANT_COUNT)")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the wordings instead of rendering anything")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="show rewrites that were rejected, and why")
    args = parser.parse_args(argv)

    config = load_config()
    print(f"Model: {config.llm_model} at {config.llm_base_url}")
    print(f"Varying lines: {', '.join(str(n) for n in config.vary_lines) or '(none)'}")
    print("First request cold-loads the model; this can take a minute or two.\n",
          flush=True)

    variants = build_variants(config, variant_count=args.variants, verbose=args.verbose)
    _report(variants)

    if not args.dry_run:
        print("\n(--dry-run only prints. Use menu 1 to render audio.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
