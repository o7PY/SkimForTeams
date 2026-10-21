from skim.pairing import pairing_code


def test_deterministic_and_shaped():
    c = pairing_code("secret", "team-1")
    assert c == pairing_code("secret", "team-1")
    assert len(c) == 6 and c == c.upper() and c.isalnum()


def test_differs_per_team_and_secret():
    assert pairing_code("secret", "team-1") != pairing_code("secret", "team-2")
    assert pairing_code("secret", "team-1") != pairing_code("other", "team-1")
