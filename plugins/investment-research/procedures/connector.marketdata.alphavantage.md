---
id: connector.marketdata.alphavantage
title: Use Alpha Vantage for daily market data
applies_to: {connector: marketdata/alphavantage}
audience: project_owner
version: 3
reviewed: 2026-11-23
vendor_docs:
  - {title: "Alpha Vantage: get a free API key", url: "https://www.alphavantage.co/support/#api-key"}
egress: [www.alphavantage.co]
fields:
  api_key: "your Alpha Vantage API key, typed once"
  timeout_s: "request timeout in seconds"
  outputsize: "auto, or compact for a free key: always the last 100 trading days, a cross-check only"
  key_rotation: "with several keys (api_key.2 …): failover, the default, uses the first that answers; round_robin spreads the calls"
---

## Before you start

- A free Alpha Vantage API key. The free tier allows about **25 requests a day** and gives **unadjusted**
  daily bars: a backtest over many tickers will exhaust it. A paid plan raises the limit.
- The free key also refuses a range of more than about 100 trading days (`outputsize=full` is a premium
  feature). With `outputsize: compact` it always answers the last 100 trading days: enough to cross-check
  another source in a composite, never enough to be the source of a multi-year series.

## At the vendor

Request a key at alphavantage.co (the support page above). It is shown once on the page and sent by email.

## Permissions

The key only reads market data. It is yours: do not share one key between organisations.

## In Tranibor

Connectors, **Market data**, type **Alpha Vantage**. `api_key` in the password field (sealed, shown once).

**Several keys.** `api_key` holds up to ten keys: **Add a key** under the field types the next one in its
own slot (`api_key.2` … `api_key.10`), sealed like the first. `key_rotation` says how they are used:
`failover` (the default) uses the first that answers, `round_robin` spreads the calls. A key whose limit
is reached rests (for 60 seconds) and the call is made again at once with the next one. To replace a key
without a gap, add the new one, then clear the old one: **Test** checks the configuration only (it calls
no Alpha Vantage endpoint), so the new key is first used once the old one is cleared or resting. Use only keys your organisation
is entitled to: the free tier is one key per person, so several free keys do not add up their limits.

## Test it

Press **Test**, then call `get_instrument` for a known ticker.

## When it fails

- A note about the call frequency or "standard API call frequency": the daily limit is reached; wait or
  upgrade.
- "alphavantage: every key is resting (about N s)": with several keys, each one reached its limit; wait
  that long.
- An invalid key message: the key is wrong or not activated.
- Prices that look off after a split: the bars are unadjusted.
