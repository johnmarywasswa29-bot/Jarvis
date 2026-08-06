# Jarvis Ollama Performance Report

## Hardware
- Intel Core i7-8650U
- 16 GB RAM
- CPU-only inference
- Network: ~1.3 MB/s download

## Before Optimization

| Metric | Value |
|---|---|
| Startup time | ~19.5 ms |
| First token latency | ~9.3–11.7 s (entire response blocked) |
| Total response time (Hello) | ~9.3–11.7 s |
| Total response time (Open Notepad) | ~215–230 ms |
| Tokens/sec | ~1.4–1.6 tok/s |
| Streaming | No |
| Client reuse | No |
| Benchmark logging | No |

Bottlenecks identified:
- Blocking non-streaming LLM calls
- New `ollama.Client()` created on every call
- No benchmark instrumentation
- Tool path in `run_stream` still went through LLM first

## After Optimization

| Metric | Value |
|---|---|
| Startup time | ~27.7 ms |
| First token latency | ~2.0–2.6 s |
| Total response time (Hello, streamed) | ~8.4 s |
| Total response time (Tell me a joke, streamed) | ~8.4 s |
| Total response time (Open Notepad) | ~239 ms |
| Tokens/sec | ~2.3–2.5 tok/s |
| Streaming | Yes |
| Client reuse | Yes |
| Benchmark logging | Yes (`logs/ollama_benchmark.log`) |

### Improvements
1. **Perceived latency**: Users see "Thinking..." immediately, then tokens start arriving after ~2s first token. This is much better than a 9s blank wait.
2. **Actual throughput**: Slight improvement from ~1.4–1.6 to ~2.3–2.5 tok/s due to persistent client and keep_alive.
3. **Tool path**: `run_stream` now bypasses LLM for deterministic tool commands.
4. **Observability**: `logs/ollama_benchmark.log` records model, prompt size, first token, total time, tokens/sec.

### Remaining bottleneck
The dominant cost is still CPU-bound model inference. On this hardware, generating ~18–21 tokens at ~2.3–2.5 tok/s means ~8–9s total for short responses. The only way to reduce this further is a smaller/faster model or more CPU resources.

## Network Constraint for Model Replacement
Measured download speed: ~1.3 MB/s. At this rate:
- llama3.2:3b (~2.0 GB): ~26 minutes
- qwen2.5:3b (~2.0 GB): ~26 minutes
- gemma3:4b (~2.5 GB): ~32 minutes

Current downloads have timed out at 5–10 minutes. Full model benchmarking is not feasible in this session due to bandwidth limits.

## Recommendation
Recommended replacement: **llama3.2:3b**
- Smaller context window reduces memory pressure
- 3B params significantly faster on CPU than 8B
- Good general assistant quality for simple tasks
- Smaller disk footprint (~2 GB vs ~4.6 GB)

## Next Steps for User
1. Run: `ollama pull llama3.2:3b`
2. After download completes, update `config.yaml`: `llm_model: llama3.2:3b`
3. Restart Jarvis
4. Once verified, optionally remove old model: `ollama rm llama3:latest`

Do not remove `llama3:latest` until the replacement is verified working, because it is currently the only installed model and Jarvis needs a working LLM.
