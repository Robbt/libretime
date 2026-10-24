---
title: API authentication
---

Every endpoint under `/api/v2` accepts three kinds of credentials, tried in this
order.

## System token

The value of `general.api_key` from the config file, sent as:

```
Authorization: Api-Key <key>
```

This is what services talk to the API with, and what the API itself uses when it
needs to call another service. It is not a browser credential: anyone who can read
the config file can mint requests for any user.

## Legacy session

A browser that logged in to the station carries a `PHPSESSID` cookie, and the
legacy service keeps its sessions in the `sessions` table of the same Postgres
database. The API reads that row, decodes the identity the legacy
`Zend_Auth` storage wrote into it, and authenticates the request as the matching
user - no second login, and no copy of any credential.

Reads need nothing else. A request that **changes** something also has to present
the CSRF token its own session holds, because a cookie proves which user the
request came from without proving that this page meant to send it:

```
GET  /api/v2/session/csrf-token
     -> {"csrf_token": "6e46dbfe…"}

POST /api/v2/schedule
     X-CSRFToken: 6e46dbfe…
```

That token is the one the legacy application already keeps in
`$_SESSION['csrf_namespace']['authtoken']`, so nothing new has to be configured or
rotated. It is handed out only to a caller authenticated by a session, and only
its own: an API key or a password gets 403, because those callers have no session
to check and do not need this.

For a form body, the legacy field name `csrf_token` is accepted as well. JSON
bodies must use the header - reading a body during authentication would mean
choosing a parser before the view has - and a header is the stronger of the two
anyway, since a forged cross-site form cannot set one.

Django's own CSRF middleware is not what enforces any of this: it validates the
API's `django_session` secret, which a legacy session does not have. The check
inside `PhpSessionAuthentication` is the only thing standing between a forged
request and a write.

An anonymous request keeps answering 403 rather than 401, as it always has: rest
framework derives that status from whether the first authentication class
advertises a scheme, so the bridge deliberately advertises none.

## Django session and basic auth

The API has its own session store (`django_session`) used by the login screens at
`/api/browser/login/`, and HTTP basic authentication against the same users. Both
are ordinary, and both support writes, because CSRF protection applies to them.
