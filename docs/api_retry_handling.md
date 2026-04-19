# API Retry Handling

This bot now applies retry rules by endpoint class instead of treating all exchange calls the same.

## Retry Classes

- `timeout`
- `rate_limit`
- `network`
- `server`
- `client`
- `validation`
- `auth`

## Retry Rules

- public market reads: bounded exponential backoff, max 3 attempts
- balance/account reads: cautious retry, max 2 attempts
- open-order/status reads: cautious retry, max 2 attempts
- create-order: max 1 attempt, no blind retry for ambiguous failures
- cancel-order: max 1 direct attempt; if the result is ambiguous, refresh exchange status first and retry once only when the order still appears open
- Telegram polling and delivery: retry only for safe transient failures

## Safe / Unsafe Cases

Safe automatic retries:

- timeouts
- transient network failures
- HTTP 429 rate limit responses
- HTTP 5xx / invalid upstream responses

No automatic retry:

- auth failures
- validation failures
- unsupported endpoint or other clear client-side errors
- ambiguous create-order failures

## Ambiguous Write Handling

Create order:

- do not retry automatically
- probe open orders and recent order history
- require reconciliation before a manual/operator retry

Cancel order:

- if the cancel result is ambiguous, refresh order status first
- if the order is already canceled or filled, stop there
- if it still appears open, retry cancel once and refresh again

## Structured Logging

Retry activity is recorded in `runtime_events` with `event_type=api_retry`.

Each record includes:

- endpoint
- action
- attempt
- retry policy
- classification category
- reason
- outcome
- delay
- status code when available
- correlation id when supplied by the caller
