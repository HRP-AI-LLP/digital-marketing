"""One adapter per social platform.

Every platform module exposes:
  async publish(session, post, account, media) -> (external_id, external_url)
  async fetch_metrics(session, post, account) -> dict | None
  async refresh(session, account) -> None   (raise PublishError(reconnect=True) when the account is unusable)
OAuth modules (meta, threads, linkedin, pinterest, google) also expose:
  authorize_url(creds, redirect_uri, state, options, code_challenge) -> str
  async complete(session, org_id, user_id, creds, redirect_uri, code, data) -> list[SocialAccount]
All HTTP goes through app.services.social.net.client (patched in tests).
"""
