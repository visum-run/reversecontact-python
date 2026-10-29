"""Save the job id before waiting so the job can be resumed after a restart."""

from reversecontact import JobTimeoutError, ReverseContact

with ReverseContact() as client:
    job = client.fetch.person_live(url="https://social.com/in/janedoe")
    print("Save this job id:", job.id)
    try:
        snapshot = job.wait(timeout=1800)
        if snapshot.data and snapshot.data.result:
            print(snapshot.data.result.to_dict())
    except JobTimeoutError as error:
        print("Resume later with client.jobs.wait:", error.job_id)
