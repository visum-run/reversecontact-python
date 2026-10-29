"""Opt-in staging smoke: only free endpoints, never part of offline test runs."""

import os

import pytest

from reversecontact import ReverseContact


@pytest.mark.skipif(
    os.environ.get("RC_SDK_LIVE") != "1", reason="Set RC_SDK_LIVE=1 to run the free-endpoint smoke"
)
def test_live_free_endpoints():
    with ReverseContact(max_retries=0) as client:
        assert client.usage.retrieve().data is None
        assert client.search.people_fields().data is not None
        assert client.search.count_people(first_name="Jane").data is not None
        assert client.search.count_organizations(company_name="Acme").data is not None
        assert client.fetch.check_person(url="https://social.com/in/janedoe").data is not None
