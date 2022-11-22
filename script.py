## reqiures 3.8+
import asyncio
from datetime import datetime, timedelta
import re
import httpx
import json
from getpass import getpass
import subprocess as sp
from configparser import ConfigParser
import logging
import sys
from typing import Dict, List

import audible
from bs4 import BeautifulSoup

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
	except (FileNotFoundError, audible.exceptions.AuthFlowError) as e:
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
	
def get_owned_series(client):
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
	'''
	param: release date (datetime)
	returns: if book is released: empty string
			 or time till release rounded down by days
			 or not rounded if less than 1 day
	'''
	release = release.replace(hour=17)
	today = datetime.today().replace(microsecond=0)
	if release <= today: return ''
	diff = release - today
	if diff.days > 0: return f': in {diff.days} days'
	else: return f': in {diff}'

async def check_releases(http_client, series):
	'''
	params:
		http_client: httpx async client
		series: a list of book series from audble client
			containing url, title, and the custom property 'latest'
	returns: a list of unowned books older than the 'latest' entries in the series
	'''
	url = series['url']\
		.replace('/pd/', 'https://audible.com.au/series/')\
		.replace('Audiobook/', 'Audiobooks/')
	response = await http_client.get(url, timeout=30)
	logger.info(f"checking {series['title']}")
	page = BeautifulSoup(response.content, 'html.parser')
	releases = page.select('.releaseDateLabel')
	today = datetime.today()
	new_releases = [
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
	if new_releases:
		display({series['title']: new_releases})

def display(releases: Dict[str, List[str]]):
	for series, books in releases.items():
		print(f'# {series}')
		for book in books:
			print(f'- {book}')
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


if __name__ == '__main__':
	logging.basicConfig(stream=sys.stderr, level=logging.ERROR)
	asyncio.run(main())
