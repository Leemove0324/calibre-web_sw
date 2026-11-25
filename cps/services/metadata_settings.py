# -*- coding: utf-8 -*-

#  This file is part of the Calibre-Web (https://github.com/janeczku/calibre-web)
#    Copyright (C) 2026 Calibre-Web contributors
#
#  This program is free software: you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program. If not, see <http://www.gnu.org/licenses/>.

"""F8: metadata settings read at call time, so the admin page takes effect without a restart.

API keys come from the admin page (stored encrypted in app.db) first; an environment
variable is the fallback, so setups that already export the key keep working.
"""

import os

from cps import config

DEFAULT_TIMEOUT = 5
DEFAULT_THRESHOLD = 0.85

# provider id -> (config field, environment variable)
KEY_SOURCES = {
    "kakao": ("config_kakao_api_key_e", "KAKAO_REST_API_KEY"),
    "nlgo": ("config_nlgo_api_key_e", "NLGO_CERT_KEY"),
}

SOURCE_CONFIG = "config"
SOURCE_ENV = "env"


def _saved_key(provider_id):
    field = KEY_SOURCES[provider_id][0]
    return (getattr(config, field, "") or "").strip()


def key_source(provider_id):
    """Where the key comes from right now: "config", "env" or "" (none)."""
    if _saved_key(provider_id):
        return SOURCE_CONFIG
    if os.environ.get(KEY_SOURCES[provider_id][1], "").strip():
        return SOURCE_ENV
    return ""


def api_key(provider_id):
    """The key to use for this provider now ("" when none is configured)."""
    return _saved_key(provider_id) or os.environ.get(KEY_SOURCES[provider_id][1], "").strip()


def env_name(provider_id):
    return KEY_SOURCES[provider_id][1]


def timeout():
    """Seconds a provider waits for one request."""
    value = getattr(config, "config_metadata_timeout", None)
    try:
        value = int(value)
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT
    return value if value > 0 else DEFAULT_TIMEOUT


def threshold():
    """F6 bulk fetch: a best match scoring at least this is applied automatically."""
    value = getattr(config, "config_metadata_threshold", None)
    try:
        value = float(value)
    except (TypeError, ValueError):
        return DEFAULT_THRESHOLD
    return value if 0.0 <= value <= 1.0 else DEFAULT_THRESHOLD
