"""Run with RC_API_KEY set. This lookup can consume credits."""

import os

from reversecontact import ReverseContact

with ReverseContact(api_key=os.environ["RC_API_KEY"]) as client:
    result = client.enrich.person(email="jane@example.com")
    if result is None:
        print("No match found")
    elif result.data is not None:
        print(result.data.to_dict())
        print("Quotas:", result.quotas)
