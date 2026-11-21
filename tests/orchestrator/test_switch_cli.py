"""Output checks of switch commands sent outside a config patch."""

import pytest

from orchestrator.switch_cli import SAVE, Step, SwitchCliError, check_output

# seen on V300R025 (the question line is answered by gnetcli)
SAVED = ("Warning: The current configuration will be written to the device. Continue? [Y/N]:Y\n"
         "Now saving the current configuration to the slot 1 ..\nInfo: Save the configuration successfully.\n")


def test_save_answers_only_the_question_seen_on_the_lab_switch():
    assert SAVE.questions == (("Warning: The current configuration will be written to the device. "
                               "Continue? [Y/N]:", "Y"),)
    check_output(SAVE, SAVED)


def test_save_without_its_success_line_fails():
    with pytest.raises(SwitchCliError, match="did not answer"):
        check_output(SAVE, "Now saving the current configuration to the slot 1 ..\n")


def test_a_refused_command_fails_with_the_switch_error():
    with pytest.raises(SwitchCliError, match="Wrong parameter found"):
        check_output(Step("startup patch flash:/x.PAT"), "                ^\nError: Wrong parameter found at '^' position.\n")


def test_a_secret_command_is_never_shown():
    step = Step("sftp client-transfile get ... password s3cret ...", secret=True)
    with pytest.raises(SwitchCliError) as raised:
        check_output(step, "Error: Failed to verify the server's public key s3cret\n")
    assert "s3cret" not in str(raised.value)


def test_a_download_error_is_shown_with_its_login_and_password_masked():
    from orchestrator.switch_cli import download

    step = download("198.51.100.2", 2222, "up0123456789ab", "Pa55word", "x_V300R025SPH120.PAT", 1000)
    with pytest.raises(SwitchCliError) as raised:
        check_output(step, "Error: Failed to log in up0123456789ab with Pa55word.\n")
    text = str(raised.value)
    assert "Pa55word" not in text and "up0123456789ab" not in text
    assert "username <hidden> password <hidden> sourcefile x_V300R025SPH120.PAT" in text and "Failed to log in" in text
