#!/usr/bin/env python3
## reqiures 3.8+
from datetime import datetime
import re
import json
import requests
import logging
import sys
from pprint import pprint

import audible
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

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
			captcha_callback=lambda url: input(f'CAPTCHA {url} :'),
		)
		auth.to_file(auth_file)
		client = audible.Client(auth=auth)
	return client

	
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

def check_releases(series):
	logger.info(f"checking {series['title']}")
	url = series['url']\
		.replace('/pd/', 'https://audible.com.au/series/')\
		.replace('Audiobook/', 'Audiobooks/')
	page = BeautifulSoup(requests.get(url).content, 'html.parser')
	releases = page.select('.releaseDateLabel')
	return [
		node.find_parent('ul')
			.select('.bc-heading a.bc-link')[0]
			.get_text()
		for node in releases
		if datetime.strptime(
			re.search(
				r'\d+-\d+-\d+',
				node.get_text()
			).group(0),
			'%d-%m-%Y',
		) > series['latest']['release_date']
	]
	
	

if __name__ == '__main__':
	logging.basicConfig(stream=sys.stderr, level=logging.INFO)

	client = login()
	owned = get_owned_series(client)
	new_releases = {
		series['title']: new_releases
		for series in owned.values()
		if (new_releases := check_releases(series))
	}
	pprint(new_releases)
