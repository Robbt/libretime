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

**This only covers reads.** A `POST`, `PUT`, `PATCH` or `DELETE` carrying a legacy
session is refused with 403 and a message saying so. Trusting a cookie as proof of
identity is precisely what makes cross-site request forgery possible, and the
legacy service defends itself with a token in `$_SESSION['csrf_namespace']` that
this API cannot check the way Django's CSRF middleware checks its own - the two
secrets are in different stores. Until one of the two is settled - the browser
logging in to the API as well, or the API learning to verify the legacy token -
writes are not accepted this way.

An anonymous request keeps answering 403 rather than 401, as it always has: rest
framework derives that status from whether the first authentication class
advertises a scheme, so the bridge deliberately advertises none.

## Django session and basic auth

The API has its own session store (`django_session`) used by the login screens at
`/api/browser/login/`, and HTTP basic authentication against the same users. Both
are ordinary, and both support writes, because CSRF protection applies to them.
