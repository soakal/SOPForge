"""consistency.py: deterministic proper-noun spelling *consistency* (not
correctness) across a generated document's fields -- see the real-world
case that motivated this (three different spellings of the same company
name across one photo-mode document's title/steps)."""

from pipeline.consistency import canonicalize_terms


def test_three_phonetically_similar_misspellings_merge_into_one():
    fields = [
        "HILLSHIRE DRIVER AND SOFTWARE INSTALLATION",
        "Select the 'Hilsshier Windows 11.7z' archive and click Extract all.",
        "Extract the contents of the 'Hilschier Windows 11.7z' archiving file.",
    ]
    canonicalized, actions = canonicalize_terms(fields)

    spellings = {"hillshire", "hilsshier", "hilschier"}
    surviving = {
        stripped.lower()
        for field in canonicalized
        for word in field.split()
        for stripped in [word.strip("'.,")]
        if stripped.lower() in spellings
    }
    assert len(surviving) == 1
    assert len(actions) == 1
    assert len(actions[0]["variants"]) == 2


def test_windows_and_window_are_not_merged():
    fields = ["Open the Windows folder.", "Close the Window when done."]
    canonicalized, actions = canonicalize_terms(fields)

    assert canonicalized == fields
    assert actions == []


def test_quoted_filename_context_still_merges_and_replaces_in_place():
    fields = [
        "Select the 'Hilsshier Windows 11.7z' archive in the folder.",
        "Double-click the 'Hilschier Windows 11.7z' file to open it.",
    ]
    canonicalized, _actions = canonicalize_terms(fields)

    # Still quoted, still followed by " Windows 11.7z" -- only the
    # misspelled word itself changed, nothing around it.
    assert "Windows 11.7z" in canonicalized[0]
    assert "Windows 11.7z" in canonicalized[1]
    first_word = canonicalized[0].split("'")[1].split(" Windows")[0]
    second_word = canonicalized[1].split("'")[1].split(" Windows")[0]
    assert first_word == second_word


def test_anchor_text_wins_over_frequency():
    fields = [
        "Select Hilsshier from the list.",
        "Select Hilsshier from the list again.",
        "Select Hilschier from the list once.",
    ]
    # "Hilsshier" is more frequent (2 vs 1), but the user-typed title
    # (anchor_text) spells it "Hilschier" -- the one piece of real ground
    # truth this pipeline has should win over raw frequency.
    canonicalized, actions = canonicalize_terms(fields, anchor_text="Hilschier Setup Guide")

    assert actions[0]["canonical"] == "Hilschier"
    assert all("Hilsshier" not in field for field in canonicalized)


def test_most_frequent_variant_wins_without_an_anchor():
    fields = ["Select Hilsshier once.", "Select Hilsshier twice.", "Select Hilschier thrice."]
    canonicalized, actions = canonicalize_terms(fields)

    assert actions[0]["canonical"] == "Hilsshier"
    assert all("Hilschier" not in field for field in canonicalized)


def test_tie_breaks_on_earliest_document_order_occurrence():
    fields = ["Select Hilschier first.", "Select Hilsshier second."]
    canonicalized, actions = canonicalize_terms(fields)

    # Equal frequency (1 each) -- the one seen first in document order wins.
    assert actions[0]["canonical"] == "Hilschier"
    assert canonicalized[1] == "Select Hilschier second."


def test_idempotent_second_pass_is_a_no_op():
    fields = [
        "HILLSHIRE DRIVER AND SOFTWARE INSTALLATION",
        "Select the 'Hilsshier Windows 11.7z' archive.",
        "Extract the 'Hilschier Windows 11.7z' archiving file.",
    ]
    once, _actions = canonicalize_terms(fields)
    twice, actions_again = canonicalize_terms(once)

    assert once == twice
    assert actions_again == []


def test_no_similar_terms_returns_fields_unchanged_and_no_actions():
    fields = ["Open Notepad.", "Save the document.", "Close the window."]
    canonicalized, actions = canonicalize_terms(fields)

    assert canonicalized == fields
    assert actions == []


def test_all_caps_title_does_not_get_injected_verbatim_into_lowercase_context():
    """Regression (found in review): a naive canonical pick could choose
    the ALL-CAPS title spelling and substitute it verbatim into a
    mid-sentence occurrence elsewhere, e.g. "the 'HILLSHIRE Windows...'
    archive" -- jarring and wrong. Replacement casing must match the
    occurrence being replaced, not the winning candidate's own casing."""
    fields = [
        "HILLSHIRE DRIVER AND SOFTWARE INSTALLATION",
        "Select the 'Hilsshier Windows 11.7z' archive and click Extract all.",
    ]
    canonicalized, _actions = canonicalize_terms(fields)

    assert "HILLSHIRE" not in canonicalized[1]
    assert "'Hillshire Windows 11.7z'" in canonicalized[1]
    # Whichever spelling won, the title (ALL-CAPS field) must stay ALL-CAPS.
    assert canonicalized[0] == canonicalized[0].upper()


def test_verb_inflection_pairs_are_never_merged():
    """Regression (found in review): naive fuzzy matching over-merges
    ordinary English inflection pairs where one form is a capitalized UI
    label (proper-noun-shaped) and the other is an ordinary lowercase
    past-tense verb -- these are NOT ASR misspellings of the same word."""
    cases = [
        ("Click Update to begin.", "The driver is now updated."),
        ("Click Close to exit.", "The dialog is now closed."),
        ("Click Enable to proceed.", "The feature is now enabled."),
        ("Click Remove to delete it.", "The file was removed."),
    ]
    for field_a, field_b in cases:
        canonicalized, actions = canonicalize_terms([field_a, field_b])
        assert actions == [], f"unexpected merge for {field_a!r} / {field_b!r}"
        assert canonicalized == [field_a, field_b]


def test_manage_and_manager_are_not_merged_despite_both_being_capitalized():
    """Regression (found in review): "Manage" (a button) and "Manager" (as
    in "Device Manager") can both be legitimately capitalized mid-sentence
    -- the proper-noun-context gate alone doesn't separate them (both look
    proper-noun-shaped), so the suffix guard must also catch this
    agentive-suffix pair."""
    fields = ["Click Manage to view options.", "Open Device Manager to continue."]
    canonicalized, actions = canonicalize_terms(fields)

    assert actions == []
    assert canonicalized == fields


def test_unrelated_same_length_words_are_not_merged():
    """ "pointer" and "printer" are two genuinely different common nouns
    that happen to be edit-distance close at the same length -- neither is
    ever capitalized mid-sentence in ordinary SOP prose, so the
    proper-noun-context gate should keep them apart even though nothing
    else in the module would."""
    fields = ["Move the pointer to the icon.", "Select the printer from the list."]
    canonicalized, actions = canonicalize_terms(fields)

    assert actions == []
    assert canonicalized == fields
