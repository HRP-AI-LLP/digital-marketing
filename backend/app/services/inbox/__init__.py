"""Unified inbox and social listening.

  base.py       shared types (Incoming), InboxPermissionError, per-platform capabilities and help texts
  meta.py       Facebook Page comments, Instagram comments and @mentions (Graph API)
  threads.py    Threads replies (conversation endpoint) and replies via reply_to_id
  youtube.py    YouTube comment threads (allThreadsRelatedToChannelId) and comments.insert
  bluesky.py    Bluesky notifications (reply, mention, quote) and replies with root/parent refs
  mastodon.py   Mastodon mention notifications and replies (in_reply_to_id)
  listening.py  keyword monitoring: web, news, Bluesky search, Mastodon hashtags, YouTube search, Instagram hashtags
  sentiment.py  AI sentiment / urgency in batches, with a keyword heuristic fallback
  suggest.py    AI reply suggestions in the brand voice
  store.py      upsert with dedupe and threading
  sync.py       orchestration, sync state, reply/like actions and notifications

Every HTTP call goes through app.services.social.net.client (patched in tests).
"""
