"""Web and social listening (beyond the inbox, which only covers our own connected accounts).

  text.py      term matching (the deterministic prefilter), canonical URLs, content hashes, top terms
  sources.py   collectors: Google News RSS, news and web search via the configured search providers, Reddit,
               Hacker News (Algolia), Bluesky search, Mastodon hashtags, YouTube Data API, X recent search
  enrich.py    relevance, sentiment, topic tags and "needs a response" in cheap AI batches, keyword fallback
  engine.py    orchestration: run due queries, dedupe and store, enrich, spike alerts, digests
  insights.py  analytics (volume, sentiment, share of voice, top sources/authors/terms) and the AI summary
  assist.py    starter topics from the company profile, reply drafts in the brand voice, post ideas

Every HTTP call goes through app.services.social.net.client (patched in tests); web/news search goes through
app.services.search and app.services.inbox.listening.news_search.
"""
