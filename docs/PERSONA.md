# Persona model

FlowMirror represents each simulated investor through a population cell, an individual record, and a mutable runtime state.

## Population cell

Cells define age and asset bands, reported suitability class, latent risk tolerance, sampling weight, behavioural parameters, and a mixture of decision styles. The schema is `config/schemas/persona.schema.json`.

## Individual record

Each individual references one cell and carries a stable ID, demographics, initial wealth, entry time, reported class, latent risk state, and sampling weight. Bundled individuals are synthetic and contain no real identity fields.

## Runtime state

The engine maintains holdings, cash, cost bases, familiarity, attention, memory, market view, risk mood, social relationships, and periodic reflections. Numeric state is computed by the engine; the agent receives a compact natural-language rendering rather than unrestricted access to source tables.

## Decision boundary

An agent can request engagement, a fund action, or no action. The game master independently applies platform, suitability, settlement, and accounting rules before changing state. Model text is therefore a proposal, not the authoritative ledger.
