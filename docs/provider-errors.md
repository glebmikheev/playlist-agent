# Gemini availability

Google documents 503 as temporary service overload/unavailability. Quota and
rate-limit errors use 429. Our initial 3.1 Flash-Lite diagnostic also returned a
high-demand 503 for a tiny JSON request, while metadata and token counting worked.
Prompt size alone therefore does not explain the observed failures.

The explicit switch to 3.5 Flash-Lite yielded successful runs but did not remove
all 503s. Model changes can help availability; they do not establish a guarantee.

## Application policy

- SDK automatic retries remain disabled so each generation attempt is visible.
- A generation returning 503 can receive one retry after 2–4 seconds with jitter.
  Both attempts count toward the three-generation ceiling and agent deadline.
  Only the failed stage repeats; successful preceding stages are retained.
- The retry does not change models, billing or request content. There is no
  automatic paid fallback. A third-attempt 503 cannot start a fourth attempt.
- A 429, invalid structured response, network timeout or failed token preflight
  returns an error without a generation retry. Preserve the last accepted result.
- Missing usage on failed attempts stays unknown. A later success does not prove
  that the failed attempt consumed zero quota; cloud reservations must remain
  conservative.

A live 50-minute run recovered from an initial 503 under this policy: the retry
and subsequent ranking succeeded, for three generation attempts in 71.394 seconds.
This verifies one recovery, not a measured service reliability rate.

## References

- [Google troubleshooting and bounded backoff](https://aistudio.google.com/docs/troubleshooting#retry-strategy)
- [Google's 429/503 explanation](https://discuss.ai.google.dev/t/handling-429-503-errors-from-the-gemini-api/124640)
