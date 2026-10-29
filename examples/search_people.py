"""Count first, then explicitly cap billed search results."""

import os

from reversecontact import ReverseContact

with ReverseContact(api_key=os.environ["RC_API_KEY"]) as client:
    count = client.search.count_people(current_position_title="Engineer")
    print("Available matches:", count.data.count if count.data else None)
    for person in client.search.iter_people(current_position_title="Engineer", max_results=25):
        if person is None:
            continue
        print(person.id, person.first_name, person.last_name)
