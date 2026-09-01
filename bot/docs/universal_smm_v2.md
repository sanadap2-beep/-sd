# SMM V2 provider protocol

The provider is configured from the admin API/provider wizard with one API URL and an API key.
Requests are POST form-data with `key` plus one of these actions:

- `balance`: returns `balance` and `currency`.
- `services`: returns `service`, `name`, `category`, `rate`, `min`, `max`.
- `add`: accepts `service`, `link`, and `quantity` and returns `order`.
- `status`: accepts `order` and returns `status`, `charge`, `start_count`, `remains`.
- `cancel`: accepts `orders`.
- `refill`: accepts `order`.

All responses are normalized by `SmmV2Protocol` before they reach orders or the catalog.
Provider keys are never printed in logs or returned by the admin API.
