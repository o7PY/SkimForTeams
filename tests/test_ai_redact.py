import pytest

from skim.ai.redact import Redactor


def test_names_titles_mentions_emails_phones_ids_are_replaced_and_restored():
    text = ("Prof. Menon: mail asha@iisc.ac.in or call +91 98765 43210. Roll no 20231234, SR No. 22-1234. "
            "Asha Rao asked; @Asha Rao please come. Later Menon said the quiz is on 2026-10-26 at 3-5 PM in Room 213.")
    r = Redactor(["Asha Rao"])
    out = r.redact(text)
    for leak in ("Menon", "asha@", "98765", "20231234", "22-1234", "Asha", "Rao"):
        assert leak not in out, (leak, out)
    for keep in ("2026-10-26", "3-5 PM", "Room 213", "quiz"):
        assert keep in out
    back = r.restore(out)
    assert "Prof. Menon" in back and "asha@iisc.ac.in" in back and "+91 98765 43210" in back and "Asha Rao" in back
    ben = Redactor(["Ben"])
    assert ben.restore(ben.redact("Ben, hi")) == "Ben, hi"


def test_same_person_gets_same_placeholder_within_a_call():
    r = Redactor(["Asha Rao"])
    out = r.redact("Asha Rao and asha rao and Rao")
    assert len(set(out.replace(" and ", " ").split())) == 1


def test_mapping_is_per_instance():
    a, b = Redactor(["Asha"]), Redactor(["Ben"])
    assert a.redact("Asha") == "[PERSON_1]" and b.redact("Ben") == "[PERSON_1]"
    assert a.restore("[PERSON_1]") == "Asha" and b.restore("[PERSON_1]") == "Ben"


@pytest.mark.parametrize("text", ["Quiz 2 on 4 November at 11:59 PM", "Lectures 1-12, 10:00-11:00 AM in LHC 104",
                                  "Assignment 3 due 2026-11-20", "marks out of 100, weight 0.25"])
def test_ordinary_course_text_is_untouched(text):
    assert Redactor().redact(text) == text


def test_empty_and_none():
    assert Redactor().redact("") == "" and Redactor().redact(None) == "" and Redactor().restore(None) is None
