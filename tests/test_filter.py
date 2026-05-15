from osint_tool.filter import classify
from osint_tool.schema import Finding, Query


def _f(**kw):
    base = dict(
        module="m", category="search", type="mention",
        title="t", source_url="https://example.com/x", data={}, signals={},
    )
    base.update(kw)
    return Finding(**base)


def test_drop_mention_without_name_tokens():
    q = Query(name="Tim Berners-Lee")
    f = _f(title="Italian telecom TIM raises prices", data={"snippet": "TIM company news"})
    assert classify(f, q) == "drop"


def test_substring_match_no_longer_passes():
    """Regression: 'Jane Doe' previously matched 'Janet Doersen' because both
    tokens were substrings. Word-boundary check must reject it."""
    q = Query(name="Jane Doe")
    f = _f(title="Janet Doersen wins Kaggle prize",
           data={"snippet": "Janet Doersen, a data scientist..."})
    assert classify(f, q) == "drop"


def test_word_boundary_match_keeps_real_hit():
    q = Query(name="Jane Doe")
    f = _f(title="Jane Doe publishes paper",
           data={"snippet": "Jane Doe of Stanford ..."})
    assert classify(f, q) == "keep"


def test_inverted_name_order_keeps():
    q = Query(name="Donald Knuth")
    f = _f(title="Knuth, Donald — Selected papers",
           data={"snippet": "by Knuth, Donald (Stanford)"})
    assert classify(f, q) == "keep"


def test_name_tokens_far_apart_dropped():
    """Tokens both present but separated by ~200 chars of unrelated text → drop."""
    q = Query(name="Jane Doe")
    filler = "x" * 200
    f = _f(title="Various mentions",
           data={"snippet": f"Jane Smith won the prize. {filler} The Doe family responded."})
    assert classify(f, q) == "drop"


def test_middle_name_within_cluster_keeps():
    q = Query(name="Donald Knuth")
    f = _f(title="Donald Ervin Knuth lecture",
           data={"snippet": "Prof. Donald E. Knuth speaks at conference"})
    assert classify(f, q) == "keep"


def test_keep_mention_with_all_name_tokens():
    q = Query(name="Tim Berners-Lee")
    f = _f(title="Sir Tim Berners-Lee speaks at conference",
           data={"snippet": "founder of the web"})
    assert classify(f, q) == "keep"


def test_keep_via_strong_signal_username():
    q = Query(username="janedoe", name="Some Other Name")
    f = _f(type="profile", signals={"username": ["janedoe"]})
    assert classify(f, q) == "keep"


def test_sherlock_demote_username_partial_name_overlap():
    # Title contains "tim" only — missing "berners" and "lee" — but username has "tim"
    q = Query(name="Tim Berners-Lee")
    f = _f(module="sherlock", category="social", type="profile",
           title="HackerNews: tim42",
           signals={"username": ["tim42"]})
    assert classify(f, q) == "demote"


def test_sherlock_username_squash_demoted_not_kept():
    # Username contains name tokens as substrings but they aren't whole words
    # in the title. With the stricter v3 filter this is "demote", not "keep" —
    # the username overlap salvages it but doesn't promote it to high confidence.
    q = Query(name="Tim Berners-Lee")
    f = _f(module="sherlock", category="social", type="profile",
           title="HackerNews: timbernerslee",
           signals={"username": ["timbernerslee"]})
    assert classify(f, q) == "demote"


def test_sherlock_drop_unrelated_username():
    q = Query(name="Tim Berners-Lee")
    f = _f(module="sherlock", category="social", type="profile",
           title="Wikipedia: foobar",
           signals={"username": ["foobar"]})
    assert classify(f, q) == "drop"


def test_publication_without_name_demoted():
    q = Query(name="Donald Knuth")
    f = _f(type="publication", title="A note on something obscure",
           signals={"doi_author_pair": ["donald-knuth"]})
    # name not in title; strong signal isn't input-field match → demote
    assert classify(f, q) == "demote"
