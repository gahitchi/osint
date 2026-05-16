from specter.username_gen import candidates_from_name


def test_basic_first_last():
    out = candidates_from_name("Jane Doe")
    assert "janedoe" in out
    assert "jane.doe" in out
    assert "jane_doe" in out
    assert "jdoe" in out
    assert "janed" in out


def test_unicode_stripped():
    out = candidates_from_name("Renée Fleming")
    assert all(c.isascii() for c in out)
    assert "reneefleming" in out


def test_single_token():
    out = candidates_from_name("Madonna")
    assert "madonna" in out


def test_empty():
    assert candidates_from_name("") == []
    assert candidates_from_name("   ") == []


def test_capped():
    out = candidates_from_name("Anne Marie de la Cruz", max_candidates=5)
    assert len(out) <= 5
