"""The closed lists of the API, published as string enums."""

import json
from enum import StrEnum
from pathlib import Path

import pytest

import reversecontact
from conftest import ok
from reversecontact import Area, ContractType, CountryCode, EmailType, Industry, Seniority, catalogs

ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = ROOT.parent / "openapi/public-v2.json"


@pytest.mark.skipif(not SPEC_PATH.is_file(), reason="Needs the monorepo API snapshot")
def test_every_named_list_of_the_spec_is_an_enum_with_its_values():
    schemas = json.loads(SPEC_PATH.read_text())["components"]["schemas"]
    named = {
        name
        for name, schema in schemas.items()
        if schema.get("type") == "string" and isinstance(schema.get("enum"), list)
    }
    assert set(catalogs.__all__) == named
    for name in named:
        assert [member.value for member in getattr(catalogs, name)] == schemas[name]["enum"]


def test_lists_are_exported_from_the_package():
    for name in catalogs.__all__:
        assert name in reversecontact.__all__
        assert getattr(reversecontact, name) is getattr(catalogs, name)
        assert issubclass(getattr(catalogs, name), StrEnum)


def test_a_member_is_its_value():
    assert Industry.SOFTWARE_DEVELOPMENT == "Software Development"
    assert Seniority.OWNER_FOUNDER == "Owner / Founder"
    assert ContractType.FULL_TIME == "full-time"
    assert Area.MC_DACH == "MC:DACH"
    assert CountryCode.FR == "FR"
    assert EmailType.PERSONAL == "personal"


async def test_members_and_plain_strings_go_out_as_plain_values(h):
    h.responses.append(ok({"count": 1}))
    await h.call(
        h.client.search.count_organizations,
        industry=[Industry.SOFTWARE_DEVELOPMENT, "A value added after this release"],
        location={"area": Area.MR_EMEA, "country_code": [CountryCode.FR]},
    )
    assert json.loads(h.requests[-1].content) == {
        "industry": ["Software Development", "A value added after this release"],
        "location": {"area": "MR:EMEA", "countryCode": ["FR"]},
    }
