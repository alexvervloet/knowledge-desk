# Auth and roles

Proving who you are, holding that proof across requests, and making sure that
what you are allowed to do is decided somewhere a new feature cannot forget to
ask.

---

## Level 1: high school intro to CS

Two separate questions. Who are you, and what are you allowed to do. The first is
authentication, the second is authorisation, and mixing them up causes a lot of
bugs.

Passwords first. The database does not store your password. If it did, anyone who
got a copy of the database would have everyone's password, and since people reuse
passwords, they would also have their email and their bank. Instead it stores the
result of running the password through a one-way function. Same password in,
same scrambled output every time, and no way to run it backwards. When you log
in, the program scrambles what you typed and compares.

The function used here is called bcrypt, and it is deliberately slow. That sounds
like a bug and it is the whole point. If checking one password takes a quarter of
a second, someone trying to guess a billion passwords needs eight years per
account instead of an afternoon.

Now a genuinely sneaky attack, which I think is the best thing in this file.

Suppose someone wants to find out whether `alex@company.com` has an account. They
try to log in with a made-up password. They get "wrong email or password", which
tells them nothing. Good. Except: if the account does not exist, the program has
nothing to compare against, so it gives up right away. If the account does exist,
the program runs slow bcrypt and takes a quarter of a second. The message is the
same. The timing is not. Four milliseconds means no account, 240 milliseconds
means there is one. That measured difference is real, it is in the code comment
at [auth.py:40-51](../../../knowledge_desk/auth.py#L40-L51 "dummy_hash"), and it lets somebody
build a list of who works at the company.

The fix is small and slightly funny. When there is no such user, the program
compares the password against a fake stored hash of a random string nobody knows.
It fails, obviously. But it takes just as long, so the timing gives nothing away.

After you log in, you get a long random string called a session token. Your
browser sends it with every request instead of your password. The database stores
only a scrambled version of that too, so somebody reading the database cannot
steal a live login.

---

## Level 2: second-year CS undergraduate

[auth.py](../../../knowledge_desk/auth.py) is 65 lines and each part is there for a
stated reason.

Password hashing is bcrypt over a sha256 pre-hash, base64 encoded:

```python
def _prehash(password: str) -> bytes:
    digest = hashlib.sha256(password.encode("utf-8")).digest()
    return base64.b64encode(digest)
```

The pre-hash exists because bcrypt only reads the first 72 bytes of its input and
silently ignores the rest. Without it, two distinct passphrases sharing a 72-byte
prefix are the same password to your system. That is a correctness bug, not a
theoretical one, and it hits exactly the users who took your advice about long
passphrases. Base64 is there because bcrypt also stops at a null byte, and a raw
sha256 digest can contain one.

Timing-safe misses, via [`dummy_hash()`](../../../knowledge_desk/auth.py#L41-L52). A
real bcrypt hash of a random string, cached with `functools.cache`, verified
against on the user-not-found path so a miss costs the same as a hit. The
docstring gives the measured numbers: about 4ms versus about 240ms. That is a
clean oracle needing no difference in the error message. The caching is neat
reasoning too, since generating it costs exactly as much as the verification it
stands in for.

Sessions. `new_session_token()` returns a pair: the raw token for the client and
its sha256 for storage. The `sessions` table holds only the hash, plus
`user_id`, `org_id`, and `expires_at`. Same argument as passwords, applied to
bearer tokens, which people forget because a token feels less precious than a
password. It is not, for as long as it is valid. Expired rows are refused on
sight in `resolve_session` and swept hourly by the worker.

Roles. Three of them, ranked:

```python
ROLE_RANK = {"member": 1, "admin": 2, "owner": 3}
```

`role_at_least(actual, required)` compares ranks, with a default of 0 for unknown
actual and 99 for unknown required, so a typo fails closed in both directions.
That is worth copying.

The placement decision matters more than the mechanism. Role gates live in the
data layer, on `TenantScope`, at
[tenancy.py:46-62](../../../knowledge_desk/tenancy.py#L46-L62), not in the route
handlers. A new endpoint that calls `scope.create_group()` gets the admin check
whether or not its author remembered one. Put the check in the route and you are
relying on every future route author.

Errors, in [errors.py](../../../knowledge_desk/errors.py). Five domain exception
types mapped to HTTP status codes at one edge. Handlers raise `Forbidden`, not
`HTTPException(403)`. One mapping means status codes cannot drift, and the data
layer does not have to know it is behind HTTP.

---

## Level 3: CS graduate learning AI

Nothing here is AI-specific, and that is the observation worth carrying: an LLM
product's authorisation model is ordinary, it just has an unusual consumer. The
output of `principals()` in retrieval is derived from the same group membership
this file establishes, so a privilege escalation here becomes a retrieval leak two
files away, which becomes a fluent cited paragraph in front of the wrong person.

The piece I would single out is
[`require_can_grant`](../../../knowledge_desk/tenancy.py#L50-L62), because the
reasoning is not obvious until it is written down:

```python
def require_can_grant(self, role: str) -> None:
    self.require_role("admin")
    if not role_at_least(self.ctx.role, role):
        raise Forbidden(...)
```

Admin or better, and never a role above your own rank. The second clause is the
interesting one. Without it, an admin can create an owner account. They also
choose that account's password. So they log in as it and hold every owner power,
including the irreversible `DELETE /org`. The admin role's boundary would be
cosmetic, defeated by two API calls and no exploit.

That is a transitive-closure problem, and it is the general shape of privilege
escalation review: not "what can this role do" but "what can this role cause to
exist that can do more". Account creation, invitations, API key issuance, and
impersonation features all have this property, and they all look like ordinary
CRUD in a code review.

The other thing at this level is failing closed. `role_at_least` returns
`ROLE_RANK.get(actual, 0) >= ROLE_RANK.get(required, 99)`. An unrecognised actual
role has rank 0 and passes nothing. An unrecognised required role needs rank 99
and nothing satisfies it. So a typo in either argument denies rather than
permits. It is one line and it converts a category of typo from a security hole
into a visible bug report.

Two gaps worth naming honestly. Session tokens are opaque bearer tokens with no
rotation and no binding to a client, so a stolen token is valid until its TTL
expires, and the only revocation is deleting the row. And `authenticate` takes an
optional `org_slug`, which means the same email can exist in several orgs, so
"which tenant am I acting in" is part of the login, not derived from it. That is
the right model for a multi-tenant product and it is also an extra field an
attacker can vary while probing.

---

## Level 4: engineering manager interviewing for AI engineering

Nothing in this concept is AI, which is exactly why it is worth checking. The
team is excited about retrieval quality. Auth is the boring part that ends the
company.

The three things to verify exist, in any system, AI or not:

Passwords are hashed with a slow function built for the job: bcrypt, scrypt, or
Argon2. Not sha256 on its own, which is fast and therefore useless here. If a
candidate says "we hash with SHA-256", that is a finding.

Session tokens are stored hashed. People consistently get this right for
passwords and wrong for tokens, on the reasoning that a token expires anyway. It
is a valid credential until it does.

Authorisation checks live below the route layer, in one place, so a new endpoint
inherits them. Checks scattered across handlers are correct in fourteen places
and wrong in the fifteenth, and the fifteenth is your incident.

Questions I would ask:

"Can an admin create an owner?" This is my favourite auth question because it
sounds like a product detail. If they can, the admin role is decorative: create
the owner, set its password, log in, do owner things. A candidate who has thought
about privilege escalation gets there in about five seconds and looks pleased.
One who has not will tell you about their permissions matrix.

"How long does a failed login take compared to a successful one?" Advanced, and a
strong signal. If they know the answer, they have thought about side channels. If
they say "the error message is the same for both", that is the standard answer
and it is incomplete: the timing is a separate channel that leaks whether an
account exists, which is how attackers build target lists.

"What happens to a session token when someone is removed from a group?" You are
probing for cached authorisation. In this codebase, permissions are recomputed
per query and there is no cache, so removal takes effect on the next request. If
the answer involves a TTL, that TTL is a window during which a removed employee
can still read things, and somebody should have written it down.

"Where would a new endpoint's permission check come from?" The answer you want is
"the data layer, automatically". The answer you will often get is "the developer
adds it", which is a process, and processes have a defect rate.

For your own team: privilege escalation review should ask what a role can cause
to exist, not just what it can do. Anything that creates accounts, issues keys,
or grants roles needs a ceiling at the granter's own level, and that rule is
cheap to state and easy to forget.

---

## Level 5: senior AI engineer

Sixty-five lines, and the density of stated reasoning per line is the thing to
copy rather than the crypto, which is standard.

The sha256-then-base64 pre-hash before bcrypt is correct and is the pair of
details people get half right. The 72-byte truncation is widely known; the null
byte terminating bcrypt's input, which is why the digest is base64 encoded rather
than passed raw, is less so, and a raw digest containing 0x00 gives you an
effective password of however many bytes preceded it. The docstring names the
collision consequence rather than just citing the limit, which is the right way
to write that comment.

`dummy_hash()` with `functools.cache` on the miss path, with measured numbers in
the docstring: roughly 4ms versus 240ms. The measurement is what makes it a
finding rather than a habit. I would add one caveat that the code does not: the
constant-time property here holds for the user-not-found branch, and other early
returns on the login path, an inactive membership, a bad org slug, a rate-limit
rejection, can reintroduce a timing difference. Nothing currently checks that.

The `auth_limiter` split from the general limiter is right and the reasoning is
in [ratelimit.py](../../../knowledge_desk/ratelimit.py): the auth routes are the only
endpoints an anonymous caller can reach, each costs a bcrypt verification, and
keying by client address rather than user id is the only option before identity
exists. That also means a slow hash is a denial-of-service amplifier without it,
which is the part people miss when they turn the bcrypt cost factor up.

`require_can_grant`'s rank ceiling, with the escalation path spelled out in the
docstring including the password-choosing step. That docstring is doing real work:
the vulnerability is in the composition of two allowed operations, so anyone
reviewing either one alone would find nothing.

`role_at_least`'s asymmetric defaults, 0 for actual and 99 for required, fail
closed in both directions. One line, and it is the difference between a typo
being a bug report and a typo being a bypass.

Where I would push:

Session tokens have no rotation on privilege change. Change someone's role and
their existing session keeps working, picking up the new role on the next
`resolve_session` because the role is joined from `memberships` rather than
stored on the session. So privilege gain is immediate, which is fine, and
privilege loss is also immediate, which is better than most systems manage.
Worth noting that this is a consequence of joining rather than denormalising, and
someone optimising that join later would silently break it.

No `secrets.compare_digest` on the session token hash lookup, though it is an
indexed equality on a sha256, so the exposure is a database timing side channel
on a value the attacker would already need to be guessing at 2^256. Not worth
changing.

Nothing binds a session to a client. No device binding, no rotation on use, no
family-based reuse detection. A stolen bearer token is valid for the full TTL and
the only revocation is a row delete. That is a reasonable place to stop for this
project, and it is the first thing I would change if it took real customers.

The structural decision I would most want other people to copy is that role gates
live on `TenantScope` rather than in routes. Combined with the module docstring's
rule that any org-scoped query not on that class is a bug, you get a codebase
where both tenancy and authorisation have exactly one place to review. That is
worth more than any individual control in the file.
