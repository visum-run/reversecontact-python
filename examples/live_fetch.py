"""Save the task id before waiting so the task can be resumed after a restart."""

import os

from reversecontact import ReverseContact, TaskTimeoutError

with ReverseContact(api_key=os.environ["RC_API_KEY"]) as client:
    task = client.fetch.person_live(url="https://social.com/in/janedoe")
    print("Save this task id:", task.id)
    try:
        snapshot = task.wait(timeout=1800)
        if snapshot.data and snapshot.data.result:
            print(snapshot.data.result.to_dict())
    except TaskTimeoutError as error:
        print("Resume later with client.tasks.wait:", error.task_id)
