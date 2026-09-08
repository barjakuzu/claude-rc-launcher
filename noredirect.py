"""One shared urllib opener that refuses every redirect outright.

urllib.request.urlopen's default opener follows a 3xx and RE-SENDS every
request header, credentials included, to wherever the redirect points.
Every module in this codebase that sends a credential in an outbound
request header (limits.py's OAuth bearer token; fleetpoll.py, overview.py
and server.py's hub-to-device Basic auth password) must go through
NO_REDIRECT_OPENER below instead of bare urlopen, so a device (or an
attacker who can make a device answer with a redirect) can never have a
credential handed to it a second time at a location of its choosing.

This lives in its own module, not duplicated per file and not folded into
any one of its callers, specifically so limits.py, fleetpoll.py,
overview.py and server.py can all import the exact same opener instance
without any of them depending on each other.
"""
import urllib.request


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """redirect_request() returning None tells urllib not to build a
    follow-up request at all -- a 3xx surfaces as a plain
    urllib.error.HTTPError carrying the original status, and no second
    request (with the original request's headers, credentials included)
    is ever made."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


# Built once at import time (handler instances are stateless) and reused
# by every caller.
NO_REDIRECT_OPENER = urllib.request.build_opener(NoRedirectHandler)
