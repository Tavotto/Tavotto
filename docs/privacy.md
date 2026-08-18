# Privacy policy

Last updated: 2026-08-18

Magplot is a local-first scientific-figure editor. Rendering, composition, project
files, scripts, figures and exports stay on the user's machine unless the user
explicitly chooses another tool or destination.

## Data Magplot does not upload

Magplot does not upload figure files, source scripts, project files, layouts,
exports, or the contents of a user's scientific data to the Magplot project or to
any service operated by the maintainers.

## Update checks

When enabled, Magplot checks GitHub Releases for the newest public version. The
request contains the public repository endpoint and standard network metadata
handled by GitHub; it does not intentionally include the user's project files,
figures, scripts, exports, or account credentials. Automatic checks can be
disabled in the application settings, and `MAGPLOT_NO_UPDATE_CHECK=1` disables
them completely.

## Optional AI assistant

The optional assistant is invoked only by the user. Magplot passes the request to
a Codex or Claude command-line tool selected on the user's machine. That tool may
have its own network, account and privacy behavior; it is not a background upload
performed by Magplot. Users should review the policy of the selected tool before
using it with sensitive material.

## Local service

The desktop application uses a local service bound to `127.0.0.1` for the user
interface and rendering bridge. It is not intended to expose the user's projects
to other computers.

## Changes and contact

Changes to this policy will be recorded in the repository history. Privacy or
security questions can be raised through the public issue tracker:
<https://github.com/erwanjun/magplot/issues>.
