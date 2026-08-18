import asyncio
from guardrails_layer import check_input


text = (
    "My name is Ganesh. "
    "My email is ganesh@gmail.com. "
    "My phone number is +91 9876543210."
)


async def main():
    allowed, refusal = await check_input(text)

    print("ORIGINAL:")
    print(text)

    print("\nALLOWED:")
    print(allowed)

    print("\nREFUSAL:")
    print(refusal)


asyncio.run(main())