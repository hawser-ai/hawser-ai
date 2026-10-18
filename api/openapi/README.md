# api/openapi

`openapi.json` is **generated** from the `hawserd` FastAPI application (`make openapi`, or
`hawserd openapi --out api/openapi/openapi.json`). CI fails when it is stale
(`hawserd openapi --check`). Component schemas are the same Pydantic contract models exported
to `../schemas/`; `info.version` is `<contract version>+hawserd.<package version>`.

Browse it live at `http://<server>/docs`.
