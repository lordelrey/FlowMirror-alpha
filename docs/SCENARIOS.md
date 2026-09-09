# Scenarios

A scenario is a validated bundle that connects a population, a content platform, market inputs, and a regulator. The schema is `config/schemas/scenario.schema.json`.

The bundled `scenarios/cn_xhs_2025q4/scenario.yaml` demonstrates a Chinese-language social-feed setup whose organisations, posts, attention signals, product codes, and NAVs are synthetic. It is an executable example, not a representation of a live platform or a real market forecast.

To create another scenario, copy the example, point every input to a file you are allowed to use, and validate it before running:

```bash
python -m flowmirror.cli validate path/to/scenario.yaml --schema scenario
```

Keep raw captures, proprietary datasets, credentials, and creative images outside the repository.
