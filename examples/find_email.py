"""Find contact details asynchronously using RC_API_KEY."""

import asyncio

from reversecontact import AsyncReverseContact


async def main() -> None:
    async with AsyncReverseContact() as client:
        job = await client.contact.find_email(
            first_name="Jane", last_name="Doe", company_domain="example.com"
        )
        print("Save this job id:", job.id)
        snapshot = await job.wait()
        for contact in (snapshot.data.result or []) if snapshot.data else []:
            if contact is None:
                continue
            print(contact.value, contact.type)


if __name__ == "__main__":
    asyncio.run(main())
