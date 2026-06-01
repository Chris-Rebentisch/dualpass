# Security policy

## Status

dualpass is at **v1.0.0** (feature-complete, production-ready). The threat surface is small but real — security input is welcome, and any vulnerability will be patched on a prioritized schedule.

## Reporting a vulnerability

For security issues, please **do not open a public GitHub issue**. Instead:

1. Open a [private security advisory](https://github.com/Chris-Rebentisch/dualpass/security/advisories/new) on GitHub, OR
2. Email the maintainer (see profile contact on https://github.com/Chris-Rebentisch).

Include:

- A description of the vulnerability and its potential impact
- Reproduction steps (if applicable)
- The dualpass version (`dualpass --version`)
- Your suggested remediation (optional)

We will acknowledge within 5 business days and aim to confirm + fix within 30 days for high-severity issues.

## Threat model — design-level considerations

dualpass orchestrates LLM agent invocations against your filesystem, your shell, and (transitively) any API your CLIs can reach. The threats we design against are documented in [docs/CONCEPTS.md](docs/CONCEPTS.md) and CONTRIBUTING.md, but the load-bearing ones to know:

### The lethal trifecta (per Simon Willison)

An agent is dangerous when it has:

- **Exposure to untrusted input** (e.g. an email body, a webpage, a downloaded document)
- **Access to private data** (your filesystem, credentials, internal APIs)
- **Ability to exfiltrate** (network egress, email send, file write to a shared location)

Any single leg is fine. All three together is catastrophic — a malicious instruction hidden in untrusted input can hijack the agent into leaking private data.

dualpass does **not** attempt to make models robust to prompt injection (an unsolved problem). Instead, it gives you the primitives to break the trifecta architecturally:

- Tiered permissions (`config/permissions.yaml`) — default to asking before mutating actions
- Forbidden-action regex blocklists
- Explicit `opt_in_skips` (each safety gate is on by default; bypasses are per-line opt-in)

### Cross-vendor reviewer as a defense-in-depth signal

The signature feature of dualpass — a different-vendor reviewer judging the author's output — is not primarily a security feature, but it does function as one. A second model that does not share the first model's training data is less likely to be subverted by the same prompt-injection payload.

### What dualpass does not protect against

- **Compromised CLI binaries.** If your `claude` or `cursor-agent` binary is compromised, dualpass is compromised. Verify your CLI installations.
- **Malicious skills.** A `SKILL.md` you download from an untrusted source can instruct the agent to do anything within its tool surface. Treat skills like code — review before use.
- **Bypass-mode users.** If you opt into `default_posture: bypass` and run an agent against an untrusted prompt, the harness will not save you. Don't do that.

## Supported versions

| Version | Supported |
|---|---|
| 1.0.x  | Yes — current. Security fixes land on minor/patch bumps. |
| 0.x.x  | No — pre-v1 prereleases are not security-supported. Upgrade to 1.0+. |

Semver applies from v1.0.0 forward. Breaking API changes require a major version bump.
