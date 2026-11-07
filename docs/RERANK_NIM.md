# RERANK_LLM with NVIDIA NIM

## Verdict

**Config-only: yes.** No NVIDIA provider row or code change is required.
`RERANK_LLM` already accepts the standard `provider:model` string, and an
`openai:<model>` value is passed straight to pydantic-ai's OpenAI-compatible
chat model. Its default provider reads `OPENAI_BASE_URL`, so pointing that
variable at NIM routes the rerank agent to
`https://integrate.api.nvidia.com/v1`.

## `.env` lines

Use a placeholder key here, never a real one:

```dotenv
# Use the NIM model for the rerank pass (LLM can stay on another provider).
RERANK_LLM=openai:meta/llama-3.1-8b-instruct
OPENAI_API_KEY=nim-placeholder-key
OPENAI_BASE_URL=https://integrate.api.nvidia.com/v1
```

Replace `meta/llama-3.1-8b-instruct` with any chat model ID your NIM account
serves. If the main transcript pass should also use NIM, set `LLM` to the same
`openai:<model>` value.

## Why it works

- `backend/src/config.py` reads `RERANK_LLM` and falls back to `LLM`.
- `backend/src/ai.py::get_rerank_agent` passes that model string to
  `_build_transcript_model`; non-special providers such as `openai` are
  returned as-is.
- pydantic-ai resolves `openai:<model>` to its OpenAI chat model, whose default
  provider honours `OPENAI_BASE_URL` and `OPENAI_API_KEY`.

The `OPENAI_BASE_URL` value must be present in the process environment (for
example via `env_file` in Compose or the repository `.env` that `config.py`
loads). It is intentionally not stored as a runtime setting, so an admin
override of `LLM`/`RERANK_LLM` cannot change the endpoint by itself.

## Test

`backend/tests/unit/test_rerank_nim.py` constructs the rerank agent with NIM
environment variables and asserts the resulting OpenAI client's base URL. It
does not make a network request.
