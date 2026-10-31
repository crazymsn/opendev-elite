"""Small, bounded adapter for the public GitHub Trending page.

The adapter deliberately treats the page as an optional discovery source. It
never authenticates, follows redirects to another host, or invents a ranking
when the HTML cannot be parsed. Search API discovery remains the explicit
fallback configured by the operator.
"""
import html
import re
import urllib.error
import urllib.parse
import urllib.request


class TrendingError(ValueError):
    pass


def parse_trending_html(raw):
    if not isinstance(raw, str) or len(raw) > 8 * 1024 * 1024:
        raise TrendingError('Trending response is missing or exceeds the size limit')
    rows = []
    articles = re.findall(r'<article\b[^>]*class=["\'][^"\']*Box-row[^"\']*["\'][^>]*>(.*?)</article>', raw, re.I | re.S)
    for number, article in enumerate(articles, 1):
        match = re.search(r'href=["\']/([A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100})["\']', article, re.I)
        if not match:
            continue
        full_name = html.unescape(match.group(1)).strip().strip('/')
        if full_name.lower().endswith(('.git', '/')):
            full_name = full_name.rstrip('/').removesuffix('.git')
        rows.append({'full_name': full_name, 'rank': number})
    if not rows:
        # Keep a strict shape check so a GitHub layout change becomes a
        # partial/failed run instead of an apparently empty successful run.
        raise TrendingError('No repository rows were found in the Trending page')
    return rows


def fetch_trending(language=None, period='daily', transport=None):
    if language is not None and (not isinstance(language, str) or len(language) > 80):
        raise TrendingError('language must be a short string')
    if period not in ('daily', 'weekly', 'monthly'):
        raise TrendingError('period must be daily, weekly or monthly')
    query = {'since': period}
    if language:
        query['spoken_language_code'] = language
    url = 'https://github.com/trending?' + urllib.parse.urlencode(query)
    request = urllib.request.Request(url, headers={
        'Accept': 'text/html',
        'User-Agent': 'OpenDev-Project-Discovery/1.0 (+public-read-only)',
    })
    opener = transport or urllib.request.urlopen
    try:
        with opener(request, timeout=30) as response:
            if getattr(response, 'status', 200) != 200:
                raise TrendingError('Trending source returned HTTP ' + str(response.status))
            payload = response.read(8 * 1024 * 1024 + 1)
    except (urllib.error.URLError, OSError) as exc:
        raise TrendingError('Trending source is temporarily unavailable') from exc
    if len(payload) > 8 * 1024 * 1024:
        raise TrendingError('Trending response exceeds the size limit')
    return parse_trending_html(payload.decode('utf-8', errors='replace')), url
