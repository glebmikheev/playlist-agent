import asyncio
import time

from google.api_core.exceptions import GoogleAPICallError, RetryError

from playlist_agent.gemini import GeminiClient, ModelError

from .policy import Denied


class BudgetedGemini(GeminiClient):
    def __init__(self, settings, store, owner, request_id):
        super().__init__(settings)
        self.store = store
        self.owner = owner
        self.request_id = request_id
        self.deadline = time.time() + settings.timeout_s

    async def _generate_once(self, instruction, payload, schema):
        for check in range(2):
            try:
                await asyncio.to_thread(
                    self.store.attempt, self.owner, self.request_id, time.time()
                )
                break
            except Denied as error:
                delay = error.next_at - time.time() + 0.05 if error.next_at else 0
                if (
                    check == 0
                    and error.reason == "provider"
                    and 0 < delay <= 61
                    and time.time() + delay < self.deadline
                ):
                    # Wait for our own minute gate; this does not retry a provider 429.
                    await asyncio.sleep(delay)
                    continue
                raise ModelError(str(error)) from error
            except (GoogleAPICallError, RetryError) as error:
                raise ModelError(
                    "The demo could not verify the allowance for the next AI call. Please try again later."
                ) from error
        return await super()._generate_once(instruction, payload, schema)
