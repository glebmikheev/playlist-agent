# Gemini availability

Google documents 503 as temporary service overload/unavailability. Quota and
rate-limit errors use 429. Our initial 3.1 Flash-Lite diagnostic also returned a
high-demand 503 for a tiny JSON request, while metadata and token counting worked.
Prompt size alone therefore does not explain the observed failures.

The explicit switch to 3.5 Flash-Lite yielded successful runs but did not remove
all 503s. Model changes can help availability; they do not establish a guarantee.

## Application policy

- SDK automatic retries remain disabled so each generation attempt is visible.
- A 503 can receive two retries after 2–4 and 4–8 seconds with jitter. The three
  logical operations can therefore make at most nine generation attempts within
  the unchanged agent deadline. Token-count preflights are recorded separately.
  Only the failed stage repeats; successful preceding stages are retained.
- The retry does not change models, billing or request content. There is no
  automatic paid fallback. A third consecutive 503 ends that operation.
- A 429, invalid structured response or network timeout returns an error without
  an automatic retry. A preflight 503 uses the same bounded retry policy but does
  not fabricate a generation attempt. Preserve the last accepted result.
- Exhausted availability failures return an English retry-later message and do
  not increment the user's `runs_used`. Physical attempts remain in metrics and
  the separate cumulative provider-attempt counter. This is not a provider-quota
  refund; global public-demo admission must count these resource attempts.
- Missing usage on failed attempts stays unknown. A later success does not prove
  that the failed attempt consumed zero quota; cloud reservations must remain
  conservative.

A live 50-minute run recovered from an initial 503 under the earlier one-retry policy: the retry
and subsequent ranking succeeded, for three generation attempts in 71.394 seconds.
This verifies one recovery, not a measured service reliability rate.

## References

- [Google troubleshooting and bounded backoff](https://aistudio.google.com/docs/troubleshooting#retry-strategy)
- [Google's 429/503 explanation](https://discuss.ai.google.dev/t/handling-429-503-errors-from-the-gemini-api/124640)
