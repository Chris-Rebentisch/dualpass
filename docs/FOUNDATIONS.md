# FOUNDATIONS

A plain-language primer on what an LLM agent is, why a harness exists, and the vocabulary the rest of dualpass's documentation assumes. Audience: a software architect or engineer who has shipped production systems but hasn't built an agent before.

No vendor-specific examples. Every claim is backed by a named primary source — the names are in the "Further reading" section at the end, and load-bearing claims inside the body cite the source inline so you can find the original.

---

## 1. What an LLM is

A large language model is, mathematically, a function from text to text. You give it a sequence of tokens (roughly: words, sub-words, and punctuation), it returns a sequence of tokens.

That is the whole of what an LLM does, on its own:

- No state across calls — every invocation starts from scratch.
- No I/O — it cannot read files, hit URLs, run code, or query a database by itself.
- No persistence — it does not remember anything between calls unless you stuff that memory back into the next prompt.
- No clock, no random number generator (beyond its own sampling temperature), no network.

If you call the same LLM twice with the same input and the same sampling parameters, you get the same output (modulo any provider-side stochasticity in their sampler). It is a pure function with a very large weight matrix.

This is the foundation that everything else builds on. The cleverness in an "AI agent" is not in the LLM. The cleverness is in the deterministic software wrapped around it.

---

## 2. What an agent harness is

An agent harness is the deterministic software that turns an LLM into something useful. The harness owns everything the LLM cannot do on its own:

- Reading files, writing files, calling APIs.
- Storing and retrieving state across calls.
- Deciding when to call the LLM, what to put in its prompt, and what to do with the response.
- Enforcing safety boundaries — permissions, sandboxing, audit logs.
- Handling errors, retries, and time-outs.

A useful frame from Anthropic's "Building Effective Agents" is the distinction between **workflows** (predefined sequences of LLM calls glued together by deterministic code) and **agents** (the LLM is in the loop deciding what to do next). Most production systems are workflows, not agents — even when they're marketed as agents. The workflow form is easier to debug, cheaper to run, and easier to make reliable.

A harness is the engineering that makes either form work in production. dualpass is a harness biased toward the workflow form: each stage is one author call plus one (or two) reviewer calls, and the controller — not the LLM — decides what happens next.

---

## 3. Tool calling

A tool call is the protocol by which an LLM asks the harness to do something the LLM cannot do itself.

The shape:

1. The harness includes a description of available tools in the prompt (typically as a structured JSON schema).
2. When the LLM wants to use a tool, instead of returning normal text it returns a structured token sequence — usually JSON — describing the tool name and arguments.
3. The harness parses that response, executes the requested tool (read a file, run a query, call an API), and feeds the result back into the next LLM call as part of the conversation history.
4. The LLM sees the tool result on the next turn and reasons about what to do next.

Tool calling is how a stateless function-from-text-to-text participates in I/O. The harness does the actual reading, writing, and calling; the LLM just emits requests and observes results.

Two practical consequences:

- **Tool descriptions live in the prompt.** They consume context-window budget. More tools means more prompt overhead and more opportunity for the LLM to pick the wrong one.
- **The harness is the trust boundary.** The LLM can request anything its tool schema permits; the harness decides whether to actually do it. This is where permission models matter (see §9 below).

---

## 4. Context window

The context window is the LLM's working memory for one call: everything it can "see." Concretely it is the system prompt plus the conversation history plus any retrieved documents plus the tool descriptions plus any tool results so far, all serialized into a single token sequence.

Two facts about context windows that drive most of an agent harness's design:

- **They are finite.** Even very large windows — hundreds of thousands of tokens — eventually run out.
- **They degrade over distance.** Long context is mediocre context. The model attends well to the start and end of its window and worse to the middle. On long tasks, quality drops well before the hard token limit.

Anthropic's engineering team has documented this in "Effective harnesses for long-running agents": the naive approach (one long conversation, let context grow forever) plateaus around 70-80% reliability on multi-step tasks and cannot be pushed higher without architectural intervention. The remedy is explicit context curation — write durable artifacts to disk, reset the context window when work crosses natural boundaries, and re-seed the next call from those artifacts.

A harness that takes context seriously will: keep prompts small and well-shaped, store progress in files (not in chat history), and treat each LLM call as if it might be the last one in this conversation.

---

## 5. RAG — retrieval-augmented generation

RAG is the pattern of fetching relevant documents from an external store and injecting them into the prompt so the LLM has the context it needs without being trained on your data.

The flow:

1. Take the user's query (or the agent's current sub-goal).
2. Retrieve relevant documents — usually via vector similarity search over an index of your corpus, sometimes via keyword search or a hybrid.
3. Paste the retrieved documents into the prompt with the query.
4. Let the LLM answer using the supplied context.

RAG matters here because an agent harness almost always does some form of it — even if not under that name. dualpass's `build_stage_context` and `build_precedent_cache` (see [CONCEPTS.md](CONCEPTS.md) §4) are file-based RAG: they retrieve the relevant project-doc slices and prior peer artifacts at the start of each stage and write them into a bundle the author reads.

The hard parts of RAG, in roughly increasing difficulty:

- Getting the index right (chunking strategy, embedding choice).
- Getting the retrieval right (recall versus precision tradeoffs).
- Getting the context budget right (you can't paste everything; what do you cut?).
- Knowing when retrieval failed (the model will confidently answer from whatever you gave it, even if you gave it the wrong documents).

---

## 6. The loop — what "agentic" means

An agent runs a loop. The simplest form, popularized by the ReAct paper and recapitulated in nearly every agent framework, is:

```
while not done:
    think  = LLM(observation_so_far)        # decide what to do next
    action = parse_tool_call(think)         # extract the requested action
    obs    = execute(action)                # harness does the work
    observation_so_far += (think, action, obs)
```

The LLM is consulted at every "think" step. The harness drives the loop. The loop ends when the LLM emits a "done" signal — or, in practice, when it hits an iteration cap or the harness's circuit-breaker logic.

This is what makes a system "agentic": the LLM decides what the next action is, not the programmer. A workflow, by contrast, has the next action hard-coded; the LLM is consulted only for the content of each fixed step.

Both shapes are useful. Workflows are more reliable but less flexible. Agents are more flexible but harder to make reliable. Most production systems are mostly-workflow with narrow agent-shaped sections at the decision points that genuinely benefit from open-ended choice.

---

## 7. Why a harness, not just a loop

The simplest agent — "an LLM in a while loop with some tools" — works for demos and breaks under load. Anthropic's engineering team has documented the failure pattern in "Effective harnesses for long-running agents": naive loops plateau around 70-80% reliability on real multi-step work and refuse to go higher.

The plateau has several causes that all show up together:

- **Context degradation.** As the loop runs, the context window fills with intermediate reasoning and tool outputs. The model attends worse to it. Quality drops.
- **Reasoning drift.** Without an external check, the model can spend many turns convinced it is making progress while actually going in circles.
- **Silent failures.** A tool call returns an error; the model interprets the error narratively, decides to try something adjacent, and the original failure is buried in chat history.
- **Cost runaway.** The naive loop has no budget. A confused agent will burn an unbounded number of tokens chasing a misconception.

A harness fixes the plateau by adding deterministic structure around the loop: explicit context curation, external artifacts that survive context resets, hard budgets, and — the load-bearing dualpass-specific move — an independent reviewer that judges each artifact before the harness advances. Dex Horthy's "12-factor-agents" framework is the most coherent published statement of these principles; it is the normative source dualpass draws from most heavily.

The takeaway: a harness is what you build when you want to ship an agent to production, not just demo it.

---

## 8. The dual-pass reviewer pattern

A specific reliability pattern, important enough that dualpass productizes it as a default.

The pattern: every artifact the author agent produces is judged by a *different* reviewer agent before the harness advances. The reviewer's only job is to catch errors. The author and the reviewer should ideally come from different vendors so they fail in different ways.

Why this works:

- **Self-evaluation bias is real.** A model grading its own work is systematically over-confident. A different model — trained on different data, with different fine-tuning signal, with different failure modes — catches what a same-vendor reviewer misses.
- **Two cheap calls beat one expensive call.** A reviewer can be smaller and faster than the author. The author produces; the reviewer judges. The combined cost is often less than running the author with maximum reasoning effort.
- **The reviewer's verdict is auditable.** A reviewer writes its findings to disk. The author cannot. A human reading the artifact later sees both what was produced and what an independent judge thought of it.

dualpass extends this with a parallel dual-pass option (two reviewers concurrently, both must approve) and a cross-vendor fallback (if the primary reviewer's CLI is down, the harness retries with a different vendor automatically). See [CONCEPTS.md](CONCEPTS.md) §1 and §2 for the implementation, and "The four dualpass-specific reliability patterns" at the bottom of CONCEPTS.md for the full set.

---

## 9. Defaults: ask, don't act

Modern agent CLIs ship with permission models because LLM-driven action on a developer's machine is intrinsically dangerous. The consensus posture, as embodied in Claude Code, Cursor, and similar tools, is:

- **Read operations are usually fine.** Reading files, listing directories, querying read-only APIs.
- **Write operations should require approval by default.** Editing files, running shell commands, calling external services with side effects.
- **Bypass mode exists but is opt-in.** Flags like `--dangerously-skip-permissions` or `--yolo` let an operator turn off approval prompts, but they have to be passed explicitly and named in a way that makes the choice obvious.

Simon Willison's "The Lethal Trifecta" framing is useful for thinking about the worst-case shape of a permission failure: an agent with (1) access to private data, (2) access to untrusted content, and (3) the ability to communicate externally is a prompt-injection delivery system. Any one of the three legs being present is fine; all three together is dangerous. A harness's permission model exists to keep at least one leg missing in any given task.

dualpass takes the same posture: default to asking, make autonomy opt-in, make full bypass deliberately inconvenient. The harness is honest about which flags it passes — agent CLI flags appear in the same config file you can read and edit. See [CONCEPTS.md](CONCEPTS.md) §8 for the dualpass-specific implementation.

---

## Further reading

The primary sources cited above, with stable URLs:

- **"Building Effective Agents"** — Anthropic. <https://www.anthropic.com/research/building-effective-agents>. The workflows-versus-agents distinction.
- **"Effective harnesses for long-running agents"** — Anthropic engineering. <https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents>. The 70-80% reliability ceiling of the naive loop and the artifacts pattern that fixes it.
- **"Harness design for long-running apps"** — Anthropic engineering. <https://www.anthropic.com/engineering/harness-design-long-running-apps>. The Planner / Generator / Evaluator decomposition.
- **"12-factor-agents"** — Dex Horthy / HumanLayer. <https://github.com/humanlayer/12-factor-agents>. The most coherent normative framework for agent reliability.
- **"Don't Build Multi-Agents"** — Cognition Labs. <https://cognition.ai/blog/dont-build-multi-agents>. The credible counter-position on sub-agent decomposition.
- **"The Lethal Trifecta"** — Simon Willison. <https://simonw.substack.com/p/the-lethal-trifecta-for-ai-agents>. The security frame for tool-using agents.

---

When you're ready for the dualpass-specific implementation — the nine canonical components, the four reliability patterns, the file layouts — read [CONCEPTS.md](CONCEPTS.md).
