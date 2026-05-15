from osint_tool.names import (
    family_first,
    has_token_word_boundary,
    variants,
)


def test_variants_includes_nicknames_both_directions():
    assert "robert" in variants("Bob")
    assert "bob" in variants("Robert")


def test_variants_includes_ascii_fold():
    assert "muller" in variants("Müller")


def test_variants_includes_spelling_alts():
    v = variants("Aleksandr")
    assert "alexander" in v
    assert "aleksander" in v


def test_word_boundary_rejects_substring():
    # "jane" must not match inside "janet"
    assert has_token_word_boundary("janet doersen wins prize", "jane") == []


def test_word_boundary_matches_whole_word():
    pos = has_token_word_boundary("jane doe wins prize", "jane")
    assert pos == [0]


def test_nickname_match_keeps_variant():
    pos = has_token_word_boundary("robert smith said", "bob")
    assert pos == [0]


def test_fuzzy_catches_typo_when_long_enough():
    pos = has_token_word_boundary("the knuthe foundation grew", "knuth", fuzzy=True)
    assert pos != []


def test_fuzzy_off_by_default():
    assert has_token_word_boundary("knuthe was here", "knuth") == []


def test_fuzzy_skips_short_tokens():
    # "li" is too short for fuzzy; "lo" should NOT match it.
    assert has_token_word_boundary("the lo museum", "li", fuzzy=True) == []


def test_family_first_swaps_east_asian():
    assert family_first("Wang Xiaoming") == "Xiaoming Wang"
    assert family_first("Kim Jong") == "Jong Kim"


def test_family_first_skips_western_names():
    assert family_first("Donald Knuth") is None
