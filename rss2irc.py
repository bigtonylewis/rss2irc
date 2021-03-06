#!/usr/bin/env python2
"""2015/Jul/5 @ Zdenek Styblik <stybla@turnovfree.net>
Desc: Fetch RSS and pipe it into IRC bot.
"""
import argparse
import logging
import os
import pickle
import signal
import sys
import time
import traceback

import feedparser
import requests

EXPIRATION = 86400  # seconds
HTTP_TIMEOUT = 30  # seconds


def format_message(url, msg_attrs, handle=None):
    """Return pre-formatted message.

    :type url: str
    :type msg_attrs: tuple
    :type handle: str
    """
    if handle:
        if msg_attrs[1]:
            tag = '%s-%s' % (handle, msg_attrs[1])
        else:
            tag = '%s' % handle

        msg = '[%s] %s | %s\n' % (tag, msg_attrs[0], url)
    else:
        msg = '%s\n' % url

    return msg


def get_rss(logger, url, timeout):
    """Fetch contents of given URL.

    :type logger: `logging.Logger`
    :type url: str
    :type timeout: int

    :rtype: str
    """
    try:
        rsp = requests.get(url, timeout=timeout)
        rsp.raise_for_status()
        data = rsp.text
        del rsp
        logger.debug('Got RSS data.')
    except Exception:
        logger.debug('Failed to get RSS data.')
        logger.debug(traceback.format_exc())
        data = None

    return data


def main():
    """Main."""
    logging.basicConfig(stream=sys.stdout)
    logger = logging.getLogger('rss2irc')
    args = parse_args()
    if args.verbosity:
        logger.setLevel(logging.DEBUG)

    if args.cache_expiration < 0:
        logger.error("Cache expiration can't be less than 0.")
        sys.exit(1)

    if not os.path.exists(args.output):
        logger.error("Ouput '%s' doesn't exist.", args.output)
        sys.exit(1)

    news = {}
    for rss_url in args.rss_urls:
        data = get_rss(logger, rss_url, args.rss_http_timeout)
        if not data:
            logger.error('Failed to get RSS from %s', rss_url)
            sys.exit(1)

        parse_news(data, news)

    if not news:
        logger.info('No news?')
        sys.exit(0)

    cache = read_cache(logger, args.cache)
    scrub_cache(logger, cache)

    for key in news.keys():
        if key in cache:
            logger.debug('Key %s found in cache', key)
            cache[key] = int(time.time()) + args.cache_expiration
            news.pop(key)

    if not args.cache_init:
        write_data(logger, news, args.output, args.handle, args.sleep)

    expiration = int(time.time()) + args.cache_expiration
    for key in news.keys():
        cache[key] = expiration

    write_cache(cache, args.cache)


def parse_args():
    """Return parsed CLI args.

    :rtype: `argparse.Namespace`
    """
    parser = argparse.ArgumentParser()
    parser.add_argument('-v', '--verbose',
                        dest='verbosity', action='store_true', default=False,
                        help='Increase logging verbosity.')
    parser.add_argument('--rss-url',
                        dest='rss_urls', action='append', required=True,
                        help='URL of RSS Feed.')
    parser.add_argument('--rss-http-timeout',
                        dest='rss_http_timeout', type=int,
                        default=HTTP_TIMEOUT,
                        help=('HTTP Timeout. Defaults to %i seconds.'
                              % HTTP_TIMEOUT))
    parser.add_argument('--handle',
                        dest='handle', type=str, default=None,
                        help='IRC handle of this feed.')
    parser.add_argument('--output',
                        dest='output', type=str, required=True,
                        help='Where to output formatted news.')
    parser.add_argument('--cache',
                        dest='cache', type=str, default=None,
                        help='Path to cache file.')
    parser.add_argument('--cache-expiration',
                        dest='cache_expiration', type=int, default=EXPIRATION,
                        help='Time, in seconds, for how long to keep items '
                             'in cache.')
    parser.add_argument('--cache-init',
                        dest='cache_init', action='store_true', default=False,
                        help='Prevents posting news to IRC. This is useful '
                             'when bootstrapping new RSS feed.')
    parser.add_argument('--sleep',
                        dest='sleep', type=int, default=2,
                        help='Sleep between messages in order to avoid '
                             'Excess Flood at IRC.')
    return parser.parse_args()


def parse_news(data, news):
    """Parse-out link and title out of XML."""
    if not isinstance(news, dict):
        raise ValueError

    feed = feedparser.parse(data)
    for entry in feed['entries']:
        link = entry.pop('link', None)
        title = entry.pop('title', None)
        if not 'link' and not 'title':
            continue

        category = entry.pop('category', None)
        news[link] = (title, category)


def read_cache(logger, cache_file):
    """Read file with Py pickle in it.

    :type logger: `logging.Logger`
    :type cache_file: str

    :rtype: dict
    """
    if not cache_file:
        return {}
    elif not os.path.exists(cache_file):
        logger.warn("Cache file '%s' doesn't exist.", cache_file)
        return {}

    with open(cache_file, 'r') as fhandle:
        cache = pickle.load(fhandle)

    logger.debug(cache)
    return cache


def scrub_cache(logger, cache):
    """Scrub cache and remove expired items.

    :type logger: `logging.Logger`
    :type cache: dict
    """
    time_now = time.time()
    for key in cache.keys():
        try:
            expiration = int(cache[key])
        except ValueError:
            logger.error(traceback.format_exc())
            logger.error("Invalid cache entry will be removed: '%s'",
                         cache[key])
            cache.pop(key)
            continue

        if expiration < time_now:
            logger.debug('URL %s has expired.', key)
            cache.pop(key)


def signal_handler(signum, frame):
    """Handle SIGALRM signal."""
    raise ValueError


def write_cache(data, cache_file):
    """Dump data into file as a pickle.

    :type data: dict
    :type cache_file: str
    """
    if not cache_file:
        return

    with open(cache_file, 'w') as fhandle:
        pickle.dump(data, fhandle, pickle.HIGHEST_PROTOCOL)


def write_data(logger, data, output, handle=None, sleep=2):
    """Write data into file.

    :type logger: `logging.Logger`
    :type data: dict
    :type output: str
    :type handle: str
    :type sleep: int
    """
    with open(output, 'a') as fhandle:
        for url in data.keys():
            msg = format_message(url, data[url], handle)
            signal.signal(signal.SIGALRM, signal_handler)
            signal.alarm(5)
            try:
                logger.debug('Will write %s', repr(msg))
                fhandle.write(msg.encode('utf-8'))
                signal.alarm(0)
                time.sleep(sleep)
            except ValueError:
                logger.debug(traceback.format_exc())
                logger.debug('Failed to write %s, %s', url, data[url])
                data.pop(url)

            signal.alarm(0)


if __name__ == '__main__':
    main()
