# Connecting an AI model

Ask and the morning review can use an AI model. Without one, Ember & Tide uses its built-in analysis and nothing
leaves the house. Connect one in **Studio, AI model** (owner only): pick a choice, check the address and model,
press **Load models** to see what is available, **Test**, then **Save**. No environment variables are needed.

The key you paste is kept on the server, never shown again (the app shows only "ends in ••1234"), and is not part
of any export. **Use the server defaults** goes back to the `ET_LLM_*` environment variables.

A chat subscription (Claude Pro or Max, ChatGPT Plus, Copilot Chat) can't be signed into from other apps. Use an
API key from the provider's developer console, or, for GitHub, a token for GitHub Models.

Hosted choices send the night summaries used for Ask and the morning review to that provider. Local choices send
nothing out of the house.

## Running Ember & Tide in Docker

Inside a container, `localhost` is the container itself. For a model on another computer at home use that
computer's address on your network (for example `http://192.168.1.20:11434/v1`). For a model on the same machine
as Docker use `http://host.docker.internal:11434/v1` (on Linux add `extra_hosts: ["host.docker.internal:host-gateway"]`
to the service). Make sure the model server listens on the network, not only on localhost (Ollama: set
`OLLAMA_HOST=0.0.0.0`).

## On your network

### Ollama
1. Install Ollama and pull a model, for example `ollama pull qwen2.5:7b`.
2. Address: `http://<computer>:11434/v1`. Model: `qwen2.5:7b` (or any you pulled). No key.

### LM Studio
1. In LM Studio load a model and start the local server (Developer tab).
2. Address: `http://<computer>:1234/v1`. Model: press **Load models** and pick one. No key.

### Other OpenAI-compatible server
llama.cpp server, vLLM, LocalAI, Jan and similar. Address ends in `/v1`, for example `http://<computer>:8080/v1`.
Add a key only if the server asks for one.

## Hosted

### Claude
1. Create an API key in the Anthropic Console (console.anthropic.com).
2. Paste it as the key. Default model `claude-opus-5-5`; `claude-sonnet-5-5` and `claude-haiku-5-5` are cheaper.

The Docker image includes the Anthropic package. Outside Docker install it with `pip install "embertide[claude]"`.

### OpenAI
1. Create an API key on the OpenAI platform (platform.openai.com, API keys).
2. Address `https://api.openai.com/v1`, model for example `gpt-4.1` or `gpt-4.1-mini`.

### GitHub Models (Copilot plans)
1. On GitHub: Settings, Developer settings, Personal access tokens, Fine-grained tokens, Generate new token.
2. Give it the **Models: read** permission and paste it as the key.
3. Address `https://models.github.ai/inference`. Model ids look like `openai/gpt-4.1`; **Load models** lists them.

### Google Gemini
1. Create an API key in Google AI Studio.
2. Address `https://generativelanguage.googleapis.com/v1beta/openai/`, model for example `gemini-2.5-flash`.

### OpenRouter
1. Create a key at openrouter.ai.
2. Address `https://openrouter.ai/api/v1`. Model ids look like `anthropic/claude-sonnet-5-5`; **Load models** lists them.

## If the test fails

- "Couldn't reach ...": the server can't connect. Check the model is running, the address, and (in Docker) that you
  didn't use `localhost`.
- "The key was rejected (401)": the key is wrong, expired or lacks the permission (GitHub: Models: read).
- "Model not found": pick a name from **Load models**.
- "Too many requests (429)": no credit left or rate limit; check the provider's plan.

Some hosted models reject a request setting (for example temperature). Ember & Tide retries once without it
automatically.
