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

A browser that is logged in to the station carries a `PHPSESSID` cookie, and the
sessions live in the `sessions` table of the Postgres database both applications
share. The API reads that row, decodes the identity the legacy `Zend_Auth`
storage wrote into it, and authenticates the request as the matching user - no
second login, and no copy of any credential.

## Signing in

`POST /api/v2/session` with json credentials opens that same kind of session:

```
POST /api/v2/session  {"username": "...", "password": "..."}
     -> 200  Set-Cookie: PHPSESSID=…; HttpOnly; Max-Age=1440; Path=/; SameSite=Lax
     -> {"id": 5, "username": "…", "role": "A", "csrf_token": "…"}
```

The cookie is scoped to `/` on purpose: a session started by the API is a session
of the station, and the legacy screens behind nginx read the same row and let the
user through. Logging out (`DELETE /api/v2/session`) clears that row the way the
legacy logout does - identity gone, csrf namespace dropped, row left for the
garbage collector - and the legacy screens bounce to the login page again
immediately. One login, one logout, whichever door was used.

`GET /api/v2/session` answers who the caller is, and
`GET /api/v2/session/permissions` answers what the caller's role may do, in the
same permission names the API checks requests against - which is what an
interface needs to draw its own menus.

Two details that are security rather than taste:

- **json only.** A login form on another site can post a form body to this URL
  using the visitor's browser; it cannot post json. Accepting nothing else is what
  makes a cross-site login attempt impossible rather than merely unusual.
- **a fresh session id every time**, with the previous one logged out. The legacy
  login regenerates its id for the same reason: an id that existed before the
  password was proved must not survive the proof, and two live sessions for one
  person means a logout that does not log out.

Failed logins increment `cc_subjs.login_attempts` and `cc_login_attempts` exactly
as the legacy form does. Neither counter blocks anything today, in php or here -
they are recorded for whoever is watching the station - and a wrong username and a
wrong password get the same answer, so the endpoint is not a way to list accounts.

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

## Who may change what

Authentication says who you are. What you may then do is decided by two questions,
asked in this order:

1. **Can your role do this to this kind of thing?** Checked on every request,
   including a request for a list. The role lists live in
   `api/libretime_api/permission_constants.py`: guest reads everything, host reads
   and owns media, manager also reaches the schedule, admin and superadmin reach
   everything.
2. **If it is an owner's permission, does this row belong to you?** Checked on the
   detail request, against the row. A show belongs to its hosts - the
   `cc_show_hosts` rows behind the legacy `Application_Model_User::canSchedule` -
   a file, playlist, smart block, webstream or podcast belongs to whoever created
   it, and the rows inside those belong to the same person as their parent.

What that adds up to, in the terms the station already uses:

| you are           | you may                                                                                 |
| ----------------- | --------------------------------------------------------------------------------------- |
| guest             | read                                                                                    |
| host              | read, create media, and change what you created - plus the content of any show you host |
| host              | **not** move, resize, cancel or delete a show, not even your own                        |
| manager           | all of the above, for anyone's                                                          |
| admin, superadmin | everything                                                                              |

The rules are the legacy application's, kept honest by `tools/permission-equivalence`, which asks php and the api the same 336 questions over a fixture station whose
objects belong to different people. One difference is deliberate and listed there:
`Application_Model_Webstream::deleteStreams` leaves the superadmin out of its
manager list, so a superadmin who did not create a webstream cannot delete it in
the legacy application. That reads as a slip rather than an intention, and the api
does not copy it.

## Where the port stands

Authentication is one part of replacing the legacy php application. What else `/api/v2`
can do now - and the list of what it still cannot - is in
[Porting the legacy application to the API](../contributor-manual/api-porting.md).
