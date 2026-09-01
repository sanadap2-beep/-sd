# SMS provider integration

SMS providers are intentionally isolated behind `providers.base.BaseProvider`.
A provider must implement:

- `get_balance()`
- `get_price(country, service)`
- `buy_number(country, service, operator)`
- `check_status(order_id)`
- `cancel_order(order_id)`
- `finish_order(order_id)`

`ProviderManager` selects the cheapest online provider with a configured country/service mapping,
then fails over to the next price when the first purchase fails. A first request is allowed before
the first scheduled health check; later requests respect the provider health state.
