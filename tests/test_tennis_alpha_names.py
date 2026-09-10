from names import match_name, normalize


def test_comma_and_accent_names():
    catalog = ["Novak Djokovic", "Carlos Alcaraz", "Iga Swiatek"]
    assert match_name("Djokovic, Novak", catalog) == "Novak Djokovic"
    assert match_name("C Alcaraz", catalog) == "Carlos Alcaraz"
    assert normalize("Iga \u015awi\u0105tek") == "iga swiatek"
    assert match_name("Iga Swiatek", catalog) == "Iga Swiatek"


def test_unknown_returns_none():
    assert match_name("Random Qualifier", ["Novak Djokovic"]) is None
