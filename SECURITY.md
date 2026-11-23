# Security

## Reporting a vulnerability

Please report security problems privately, through GitHub's private
vulnerability reporting: the **Report a vulnerability** button on this
repository's **Security** tab
(<https://github.com/mrkrsl/ombro/security/advisories/new>), or by email to
[hello@ombro.app](mailto:hello@ombro.app). Don't open a public issue or pull
request.

Say what you found, how to reproduce it, which version (`ombro --version`, or
the app's About box) and platform, and what an attacker could do with it.

We aim to reply within five working days, and to agree with you when the fix
and the report are published. We're glad to credit you.

## Supported versions

Ombro is young: only the latest release gets security fixes.

## Scope

Ombro runs models that read untrusted material (web pages, emails, files) and
act through tools. Reports of ways that material can get past Ombro's limits
are in scope and welcome, for example: a role escaping its tools, folders or
limits; data sent out without the approval it should need; a key reaching a
model, a journal or a report; a way into `ombro serve` without its token or
pairing; or a script getting out of the `code.run` sandbox. The protections
are described in the README, under Untrusted material, Guardrails and
Permissions.

Out of scope: a model doing something unwise that Ombro's limits allowed, and
problems in model providers or the services you connect.
