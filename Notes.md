Modelfile.toml
Declares the model for the orchestrator. Single input (hurricane_losses) wired from the CLIMADA step; single output (insured_losses) that FinancePy consumes. Optional policy block in the input JSON lets you override FM defaults without changing the pipeline config.

Dockerfile
Builds from python:3.11-slim, installs OasisLMF from local source (pip install .), then copies runner.py. Follows the same pattern as CLIMADA — layer-ordered so the heavy dep install is cached separately from runner changes.

runner.py
The FM core: reads CLIMADA's event_loss_table, scales losses to the insured portfolio, applies the three-step OasisLMF Financial Module formula (covered → net of deductible → capped at limit), then computes an insured AAL and EP curve. Default policy terms mirror a typical cat bond risk layer (25% TIV insured, 5% deductible, 25% per-occurrence limit).

To wire this into the full chain, add a step in the orchestrator pipeline config like:


[pipeline.map.oasis_fm]
model_file = "https://raw.githubusercontent.com/OasisLMF/OasisLMF/refs/heads/main/Modelfile.toml"
hurricane_losses = { from = "climada.hurricane_losses" }

[pipeline.map.finance_pricer]
...
insured_losses = { from = "oasis_fm.insured_losses" }