# Custom JSON provider

Use `ApiProtocolType.CUSTOM` for REST JSON providers with a non-standard schema.
The `custom_config` object supports:

```json
{
  "auth": "bearer",
  "request_format": "json",
  "methods": {"balance": "GET", "services": "GET", "order": "POST", "status": "GET"},
  "endpoints": {
    "balance": "/balance",
    "services": "/services",
    "order": "/orders",
    "status": "/orders/{order_id}",
    "cancel": "/orders/{order_id}/cancel"
  },
  "fields": {
    "balance": "data.balance",
    "currency": "data.currency",
    "services": "data.services",
    "order_id": "data.id",
    "status": "data.status",
    "remains": "data.remains"
  },
  "service_fields": {
    "id": "id", "name": "name", "rate": "price",
    "min": "min", "max": "max"
  },
  "order_payload": {
    "service_id": "{service_id}",
    "target": "{target}",
    "quantity": "{quantity}"
  }
}
```

Supported authentication modes are `bearer`, `x-api-key`, `header`, and `query`.
Use only providers you are authorized to connect to; test the schema with a sandbox account first.
