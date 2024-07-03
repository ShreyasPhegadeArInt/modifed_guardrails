import re

from guardrails.validator_base import OnFailAction
from guardrails.validators import FailResult, PassResult, RegexMatch


class TestRegexMatchValidator:
    regex = "\\w+\\d\\w+"
    p = re.compile(regex)
    fullmatch_val = RegexMatch(
        regex=regex, match_type="fullmatch", on_fail=OnFailAction.REASK
    )
    search_val = RegexMatch(
        regex=regex, match_type="search", on_fail=OnFailAction.REASK
    )

    def test_fullmatch_fail(self):
        bad_str = "abcdef"
        result = self.fullmatch_val.validate(bad_str, {})
        assert isinstance(result, FailResult)
        assert result.error_message != ""

    def test_fullmatch_pass(self):
        good_str = "ab1cd"
        result = self.fullmatch_val.validate(good_str, {})
        assert isinstance(result, PassResult)

    def test_search_fail(self):
        bad_str = "abcdef"
        result = self.search_val.validate(bad_str, {})
        assert isinstance(result, FailResult)
        assert result.error_message != ""

    def test_search_pass(self):
        good_str = "1234ab1cd5678"
        result = self.search_val.validate(good_str, {})
        assert isinstance(result, PassResult)
