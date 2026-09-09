# Run configurations

This directory contains offline example configurations only. Generated runs are written to `runs/out/<tag>/` and ignored by Git.

- `demo_two_arm.json`: deterministic text-versus-visual-card mock run.
- `demo_three_arm.json`: deterministic text, image-as-text, and visual-card mock run.
- `demo_null.json`: rule-based policy without model calls.

Copy one of these files to create a local configuration. Do not commit credentials, paid-run configurations, response caches, event logs, or private data paths.
