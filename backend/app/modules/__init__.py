"""Background modules. Importing a module registers its job handlers (@handler),
periodic tasks (@periodic) and connection testers (@tester)."""

import importlib

MODULES = [
    "app.modules.notifications",
    "app.modules.intelligence",
    "app.modules.content",
    "app.modules.publishing",
    "app.modules.media",
    "app.modules.seo",
    "app.modules.analytics",
    "app.modules.analytics_growth",
    "app.modules.ai_models",
]


def load_all() -> None:
    for m in MODULES:
        importlib.import_module(m)
