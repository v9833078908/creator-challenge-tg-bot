import pytest
from pydantic import ValidationError

from bot.config import Settings


@pytest.mark.parametrize("value", ["24:00", "12:60", "noon"])
def test_invalid_deadline_is_rejected(value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, report_deadline=value)


def test_invalid_timezone_is_rejected():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, challenge_timezone="Nowhere/Unknown")
