from conftest import CV_TEXT, NEED_TEXT

from talentflow.privacy.pseudonymizer import Pseudonymizer, initials, mask_with_vault, reidentify


def test_direct_identifiers_are_masked():
    res = Pseudonymizer().pseudonymize(CV_TEXT)
    for value in ["Camille", "Martin", "camille.martin@mail.fr", "06 12 34 56 78",
                  "12 rue des Lilas"]:
        assert value not in res.text
    assert res.vault["[PERSONNE_1]"] == "Camille Martin"
    assert "[EMAIL_1]" in res.text and "[TEL_1]" in res.text and "[ADRESSE_1]" in res.text


def test_sensitive_data_is_removed_not_masked():
    res = Pseudonymizer().pseudonymize(CV_TEXT)
    assert "12/03/1990" not in res.text
    assert "française" not in res.text
    assert "Mariée" not in res.text
    assert {"DATE_NAISSANCE", "NATIONALITE", "SITUATION_FAMILIALE"} <= set(res.removed)
    # rien de tout cela n'est conservé dans le coffre
    assert "12/03/1990" not in res.vault.values()


def test_useful_information_is_kept():
    res = Pseudonymizer().pseudonymize(CV_TEXT)
    for keep in ["Data Engineer Senior", "Toulouse", "Airflow", "520 €", "03/2021", "Aeronov"]:
        assert keep in res.text


def test_contact_name_in_need_is_masked():
    res = Pseudonymizer().pseudonymize(NEED_TEXT)
    assert "Paul Durand" not in res.text and "paul.durand@aeronov.fr" not in res.text
    assert "Data Engineer senior" in res.text


def test_job_title_header_is_not_taken_for_a_name():
    text = "Data Engineer Senior\nCompétences : Python"
    res = Pseudonymizer().pseudonymize(text)
    assert "PERSONNE" not in res.text


def test_age_line_removed_but_experience_years_kept():
    text = "Jean Dupont\nÂge : 34 ans\nAnnées d'expérience : 12 ans\n"
    res = Pseudonymizer().pseudonymize(text)
    assert "34 ans" not in res.text
    assert "12 ans" in res.text


def test_reidentify_and_initials():
    res = Pseudonymizer().pseudonymize(CV_TEXT)
    back = reidentify(res.text, res.vault, allowed={"PERSONNE"})
    assert "Camille Martin" in back and "[EMAIL_1]" in back
    assert initials("Jean-Marc Durand") == "J.-M. D."


def test_filename_masked_with_vault():
    res = Pseudonymizer().pseudonymize(CV_TEXT)
    assert "Martin" not in mask_with_vault("CV_Camille_Martin_2026.pdf", res.vault)
