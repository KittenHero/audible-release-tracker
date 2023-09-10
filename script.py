# reqiures 3.8+
import asyncio
from datetime import datetime
import re
import httpx
from getpass import getpass
import subprocess as sp
from configparser import ConfigParser
import logging
import sys
from typing import Dict, Any
from contextlib import suppress

import audible
from audible.exceptions import AuthFlowError
from bs4 import BeautifulSoup
from tqdm.asyncio import tqdm

logger = logging.getLogger(__name__)


def captcha(url):
    '''
    param: url - captcha url
    returns: captcha answer
    '''
    sp.run(['python', '-m', 'webbrowser', url])
    return input(f'CAPTCHA {url} :')


def login():
    '''
    asks user for login credentials
    returns: audible client (logged in)
    '''
    logger.info('logging in')
    auth_file = '.audible_auth'
    try:
        auth = audible.Authenticator.from_file(auth_file)
        client = audible.Client(auth=auth)
        client.get('library', num_results=1)
    except (FileNotFoundError, AuthFlowError):
        user = input('Username (email): ')
        passwd = getpass()
        auth = audible.Authenticator.from_login(
            user,
            passwd,
            locale="AU",
            with_username=False,
            captcha_callback=captcha,
        )
        auth.register_device()
        auth.to_file(auth_file)
        client = audible.Client(auth=auth)
    return client

# ================================== core =====================================


def get_owned_series(client: audible.Client):
    '''
    param: client - audbile client
    returns: mapping from title of series in library to series info
            including the latest owned book
    '''
    logger.info('retrieving library')
    library = client.get(
        'library',
        num_results=1000,
        response_groups=','.join([
            'series',
            'product_desc',
            'product_attrs',
        ]),
        sort_by='-PurchaseDate',
    )
    has_series = [book for book in library['items'] if book.get('series')]
    owned = {}
    for book in has_series:
        series = book['series'][0]
        book = {
            'asin': book['asin'],
            'title': book['title'],
            'subtitle': book.get('subtitle'),
            'release_date': datetime.strptime(
                book['release_date'],
                '%Y-%m-%d'
            )
        }
        series['latest'] = book
        series = owned.setdefault(series['title'], series)
        if series['latest']['release_date'] < book['release_date']:
            series['latest'] = book
    return owned


def format_release(release: datetime):
    '''
    param: release date (datetime)
    returns: if book is released: empty string
                     or time till release rounded down by days
                     or not rounded if less than 1 day
    '''
    release = release.replace(hour=17)
    today = datetime.today().replace(microsecond=0)
    if release <= today:
        return ''
    diff = release - today
    if diff.days > 0:
        return f': in {diff.days} days'
    else:
        return f': in {diff}'


async def check_releases(http_client: httpx.AsyncClient, series: Dict[str, Any]):
    '''
    params:
            http_client: httpx async client
            series: a list of book series from audble client
                    containing url, title, and the custom property 'latest'
    returns: a list of unowned books older than 'latest' in series
    '''
    url = series['url']\
        .replace('/series/', 'https://www.audible.com.au/series/')

    response = await http_client.get(url, timeout=30)
    logger.info(f"checking {series['title']} {response.status_code}")
    page = BeautifulSoup(response.content, 'html.parser')
    releases = page.select('.releaseDateLabel')
    return series['title'], {
        node.find_parent('ul')
        .select('.bc-heading a.bc-link')[0]
        .get_text():
        release_date
        for node in releases
        if (release_date := datetime.strptime(
            re.search(
                r'\d+-\d+-\d+',
                node.get_text()
            ).group(0),
            '%d-%m-%Y',
        )) > series['latest']['release_date']
    }


def display(releases: Dict[str, Dict[str, datetime]]):
    sorted_releases = sorted(
        [(s, r) for s, r in releases.items() if r],
        key=lambda sr: min(sr[1].values())
    )
    for series, books in sorted_releases:
        print(f'# {series}')
        for book, release_date in books.items():
            print(f'- {book}' + format_release(release_date))
        print()


# ======================== config =================================
'''
# format
[ignore_series]
1 = series title
2 = series2 title
...
'''


def get_config(filename='config.ini'):
    config = ConfigParser()
    with suppress():
        config.read(filename)
        return {
            'ignore_series': [
                item for _, item in config.items('ignore_series')
            ],
        }
    return {}

# ============================= main ==================================


async def main():
    config = get_config()

    client = login()
    owned = {
        title: owned
        for title, owned in get_owned_series(client).items()
        if title not in config.get('ignore_series', [])
    }
    async with httpx.AsyncClient() as http_client:
        new_releases = await tqdm.gather(*(
            check_releases(http_client, series)
            for series in owned.values()
        ))
    display(dict(new_releases))


if __name__ == '__main__':
    logging.basicConfig(stream=sys.stderr, level=logging.ERROR)
    asyncio.run(main())
