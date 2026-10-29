"""Run with RC_API_KEY set. This lookup can consume credits."""

from reversecontact import ReverseContact

with ReverseContact() as client:
    result = client.enrich.person(email="jane@example.com", full_profile=True)
    if result is None:
        print("No match found")
    elif result.data is not None:
        print(result.data.to_dict())
        print("Quotas:", result.quotas)
