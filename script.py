#!/usr/bin/env python3
## reqiures 3.8+
import asyncio
from datetime import datetime, timedelta
import re
import httpx
import json
import subprocess as sp
from configparser import ConfigParser
import logging
import sys
from pprint import pprint

import audible
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


def captcha(url):
	sp.run(['python', '-m', 'webbrowser', url])
	return input(f'CAPTCHA {url} :')


def login():
	logger.info('logging in')
	auth_file = '.audible_auth'
	try:
		auth = audible.Authenticator.from_file(auth_file)
		client = audible.Client(auth=auth)
		client.get('library', num_results=1)
	except (FileNotFoundError, audible.exceptions.AuthFlowError) as e:
		from getpass import getpass
		auth = audible.Authenticator.from_login(
			input('Username: '),
			getpass(),
			locale="AU",
			with_username=False,
			captcha_callback=captcha,
		)
		auth.to_file(auth_file)
		client = audible.Client(auth=auth)
	return client

# ================================== core =====================================
	
def get_owned_series(client):
	logger.info('retrieving library')
	library = client.get(
		'library',
		num_results=1000,
		response_groups=','.join([
			'series',
			'product_desc',
			'product_attrs',
		]),
		sort_by='Author',
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

def format_release(release):
	today = datetime.today()
	if release <= today: return ''
	diff = release - today
	days = timedelta(days=diff.days)
	minor = diff - days
	if days.days > 0: return f' in {days}'
	else: return f' in {minor}'

async def check_releases(http_client, series):
	url = series['url']\
		.replace('/pd/', 'https://audible.com.au/series/')\
		.replace('Audiobook/', 'Audiobooks/')
	response = await http_client.get(url)
	logger.info(f"checking {series['title']}")
	page = BeautifulSoup(response.content, 'html.parser')
	releases = page.select('.releaseDateLabel')
	today = datetime.today()
	return [
		node.find_parent('ul')
			.select('.bc-heading a.bc-link')[0]
			.get_text()
		 + format_release(release_date)
		for node in releases
		if (release_date := datetime.strptime(
			re.search(
				r'\d+-\d+-\d+',
				node.get_text()
			).group(0),
			'%d-%m-%Y',
		)) > series['latest']['release_date']
	]

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
	try:
		config.read('config.ini')
		return {
			'ignore_series': [
				item for key, item in config.items('ignore_series')
			],
		}
	except:
		return {}

# ============================= main ==================================

async def main():
	config = get_config()

	client = login()
	owned = {
		title:owned
		for title,owned in get_owned_series(client).items()
		if title not in config.get('ignore_series', [])
	}
	async with httpx.AsyncClient() as http_client:
		new_releases = await asyncio.gather(*(
			check_releases(http_client, series)
			for series in owned.values()
		))

	new_releases = {
		title: new_books for title, new_books in zip(owned.keys(), new_releases)
		if new_books
	}
	pprint(new_releases)


if __name__ == '__main__':
	logging.basicConfig(stream=sys.stderr, level=logging.INFO)
	asyncio.run(main())
